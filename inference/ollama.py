"""Ollama inference backend — synchronous and async SSE streaming."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import config
from inference.base import _MAX_TOOL_ROUNDS, _build_rag_message, _sse
from prompts import build_system_prompt
from tools import TOOL_FUNCTIONS, TOOL_MAP


def run_inference_ollama(
    user_message: str,
    base_model: str,
    model_config: dict,
    use_reasoning: bool,
    use_rag: bool,
    has_image: bool = False,
    history: list[dict] | None = None,
) -> dict:
    """Synchronous Ollama inference with tool-use loop.

    Raises:
        ValueError: Invalid request (unsupported image, provider unavailable).
        RuntimeError: Ollama daemon error.
    """
    if not config.OLLAMA_AVAILABLE:
        raise ValueError("Ollama is not installed on this server.")

    if has_image and not model_config["vision"]:
        raise ValueError(
            f"Model '{base_model}' does not support image input. "
            "Use a vision-capable model such as qwen3.5:4b."
        )

    # Resolve the final model name and thinking flag
    actual_model = base_model
    strategy = model_config["reasoning_strategy"]
    enable_think: bool | None = None

    if strategy == "model_switch" and use_reasoning:
        actual_model = model_config.get("switch_to", base_model)
    elif strategy == "parameter":
        enable_think = use_reasoning

    tools_enabled = model_config.get("tools", False)
    if strategy == "model_switch" and use_reasoning and "reasoning" in actual_model:
        tools_enabled = False

    active_tools = TOOL_FUNCTIONS if tools_enabled else None

    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(today=today)},
    ]
    if history:
        for m in history:
            messages.append({"role": m["role"], "content": m["content"]})

    augmented = _build_rag_message(user_message) if use_rag else user_message
    messages.append({"role": "user", "content": augmented})

    tools_used: list[str] = []
    try:
        start = time.perf_counter()

        result = config.ollama_lib.chat(
            model=actual_model,
            messages=messages,
            tools=active_tools,
            think=enable_think,
            keep_alive=config.OLLAMA_KEEP_ALIVE,
            options={"num_ctx": 4096},
        )

        for _ in range(_MAX_TOOL_ROUNDS):
            tool_calls = result["message"].get("tool_calls")
            if not tool_calls:
                break

            messages.append(result["message"])

            for tc in tool_calls:
                name = tc["function"].get("name", "")
                safe_args = tc["function"].get("arguments") or {}
                handler = TOOL_MAP.get(name)

                if handler is None:
                    output = f"Error: unknown tool '{name}'"
                else:
                    try:
                        output = handler(**safe_args)
                    except Exception as exc:
                        output = f"Error executing {name}: {exc}"

                tools_used.append(name)
                messages.append({"role": "tool", "content": str(output)})

            result = config.ollama_lib.chat(
                model=actual_model,
                messages=messages,
                tools=active_tools,
                think=enable_think,
                keep_alive=config.OLLAMA_KEEP_ALIVE,
                options={"num_ctx": 4096},
            )

        elapsed = round(time.perf_counter() - start, 2)

    except Exception as exc:
        raise RuntimeError(f"Ollama error: {exc}") from exc

    display_model = actual_model
    if use_reasoning and strategy == "parameter":
        display_model = f"{actual_model} (Think)"

    return {
        "response": result["message"].get("content", ""),
        "model": display_model,
        "elapsed_seconds": elapsed,
        "tools_used": tools_used,
    }


async def run_inference_stream_ollama(
    conversation_id: str,
    user_message: str,
    base_model: str,
    model_config: dict,
    use_reasoning: bool,
    use_rag: bool,
    has_image: bool,
    history: list[dict] | None = None,
):
    """Async SSE generator for Ollama-backed models.

    Phase 1 — tool evaluation (non-streaming).
    Phase 2 — final text generation (streaming, token by token).
    The assistant turn is persisted in the DB inside the ``finally`` block
    regardless of client disconnection.
    """
    from database import add_message  # local import avoids circular deps

    if not config.OLLAMA_AVAILABLE:
        yield _sse("error", "Ollama is not installed on this server.")
        return

    if has_image and not model_config["vision"]:
        yield _sse("error", f"Model '{base_model}' does not support image input.")
        return

    actual_model = base_model
    strategy = model_config["reasoning_strategy"]
    enable_think: bool | None = None

    if strategy == "model_switch" and use_reasoning:
        actual_model = model_config.get("switch_to", base_model)
    elif strategy == "parameter":
        enable_think = use_reasoning

    tools_enabled = model_config.get("tools", False)
    if strategy == "model_switch" and use_reasoning and "reasoning" in actual_model:
        tools_enabled = False

    active_tools = TOOL_FUNCTIONS if tools_enabled else None

    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(today=today)},
    ]
    if history:
        for m in history:
            messages.append({"role": m["role"], "content": m["content"]})

    augmented = _build_rag_message(user_message) if use_rag else user_message
    messages.append({"role": "user", "content": augmented})

    tools_used: list[str] = []
    accumulated_text = ""
    start = time.perf_counter()

    client = config.ollama_lib.AsyncClient()

    try:
        # ------------------------------------------------------------------
        # Phase 1: Tool evaluation (non-streaming)
        # ------------------------------------------------------------------
        result = await client.chat(
            model=actual_model,
            messages=messages,
            tools=active_tools,
            think=enable_think,
            keep_alive=config.OLLAMA_KEEP_ALIVE,
            options={"num_ctx": 4096},
        )

        for _ in range(_MAX_TOOL_ROUNDS):
            tool_calls = result["message"].get("tool_calls")
            if not tool_calls:
                break

            messages.append(result["message"])

            for tc in tool_calls:
                name = tc["function"].get("name", "")
                safe_args = tc["function"].get("arguments") or {}
                handler = TOOL_MAP.get(name)

                yield _sse("status", f"Calling tool: {name}")

                if handler is None:
                    output = f"Error: unknown tool '{name}'"
                else:
                    try:
                        output = await asyncio.to_thread(handler, **safe_args)
                    except Exception as exc:
                        output = f"Error executing {name}: {exc}"

                tools_used.append(name)
                messages.append({"role": "tool", "content": str(output)})

            result = await client.chat(
                model=actual_model,
                messages=messages,
                tools=active_tools,
                think=enable_think,
                keep_alive=config.OLLAMA_KEEP_ALIVE,
                options={"num_ctx": 4096},
            )

        # ------------------------------------------------------------------
        # Phase 2: Final text streaming
        # ------------------------------------------------------------------
        stream_response = await client.chat(
            model=actual_model,
            messages=messages,
            tools=None,  # No tools in streaming phase
            think=enable_think,
            keep_alive=config.OLLAMA_KEEP_ALIVE,
            options={"num_ctx": 4096},
            stream=True,
        )

        async for chunk in stream_response:
            token = chunk["message"].get("content", "")
            if token:
                accumulated_text += token
                yield _sse("chunk", token)

        elapsed = round(time.perf_counter() - start, 2)
        display_model = actual_model
        if use_reasoning and strategy == "parameter":
            display_model = f"{actual_model} (Think)"

        yield _sse(
            "done",
            model=display_model,
            elapsed_seconds=elapsed,
            tools_used=tools_used,
        )

    except Exception as exc:
        yield _sse("error", f"Ollama streaming error: {exc}")

    finally:
        # Always persist the assistant turn, even on client disconnect.
        if accumulated_text:
            elapsed_final = round(time.perf_counter() - start, 2)
            display_model = actual_model
            if use_reasoning and strategy == "parameter":
                display_model = f"{actual_model} (Think)"
            add_message(
                conversation_id,
                "assistant",
                accumulated_text,
                model=display_model,
                elapsed_seconds=elapsed_final,
                tools_used=tools_used,
                use_rag=use_rag,
            )
