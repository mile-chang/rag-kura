"""Google Gemini inference backend — synchronous and async SSE streaming."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import config
from inference.base import _MAX_TOOL_ROUNDS, _build_rag_message, _sse
from prompts import build_system_prompt
from tools import TOOL_FUNCTIONS, TOOL_MAP


def run_inference_gemini(
    user_message: str,
    base_model: str,
    model_config: dict,
    use_reasoning: bool,
    use_rag: bool,
    history: list[dict] | None = None,
) -> dict:
    """Synchronous Gemini inference with tool-use loop.

    Unlike Ollama, Gemini requires the system prompt to be passed separately
    via the content config, not as a standard chat message.

    Raises:
        ValueError: Gemini unavailable (missing key or package).
        RuntimeError: Gemini API error.
    """
    if not config.GEMINI_AVAILABLE or config.gemini_client is None:
        raise ValueError(
            "Gemini is unavailable. Please set GEMINI_API_KEY as an environment variable."
        )

    strategy = model_config["reasoning_strategy"]
    tools_enabled = model_config.get("tools", False)

    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    system_prompt = build_system_prompt(today=today)
    augmented = _build_rag_message(user_message) if use_rag else user_message

    contents: list = []
    if history:
        for m in history:
            r = "model" if m["role"] == "assistant" else "user"
            contents.append(
                config.genai_types.Content(
                    role=r,
                    parts=[config.genai_types.Part(text=m["content"])],
                )
            )

    contents.append(
        config.genai_types.Content(
            role="user",
            parts=[config.genai_types.Part(text=augmented)],
        )
    )

    thinking_cfg = None
    if strategy == "thinking":
        if use_reasoning:
            thinking_cfg = config.genai_types.ThinkingConfig(thinking_budget=-1)
    elif strategy == "thinking_level":
        # Gemini 3: "high" if UI toggle ON, "low" to suppress complex thinking if OFF
        level = "high" if use_reasoning else "low"
        thinking_cfg = config.genai_types.ThinkingConfig(thinking_level=level)
    elif strategy == "thinking_level_optional":
        # Gemma 4: supports "high" or omitting the config entirely (no "low")
        if use_reasoning:
            thinking_cfg = config.genai_types.ThinkingConfig(thinking_level="high")

    active_tools = TOOL_FUNCTIONS if tools_enabled else None

    cfg = config.genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=active_tools,
        thinking_config=thinking_cfg,
        # Disable SDK auto-execution so we can track tool usage and show UI status.
        automatic_function_calling=config.genai_types.AutomaticFunctionCallingConfig(
            disable=True
        ) if active_tools else None,
    )

    tools_used: list[str] = []
    try:
        start = time.perf_counter()

        response = config.gemini_client.models.generate_content(
            model=base_model,
            contents=contents,
            config=cfg,
        )

        for _ in range(_MAX_TOOL_ROUNDS):
            if not response.candidates:
                break

            candidate_parts = response.candidates[0].content.parts
            fn_call_parts = [p for p in candidate_parts if p.function_call is not None]

            if not fn_call_parts:
                break

            contents.append(response.candidates[0].content)

            tool_result_parts: list = []
            for part in fn_call_parts:
                fc = part.function_call
                handler = TOOL_MAP.get(fc.name)

                if handler is None:
                    result_text = f"Error: unknown tool '{fc.name}'"
                else:
                    try:
                        safe_args = fc.args or {}
                        result_text = str(handler(**safe_args))
                    except Exception as exc:
                        result_text = f"Error executing {fc.name}: {exc}"

                tools_used.append(fc.name)
                tool_result_parts.append(
                    config.genai_types.Part(
                        function_response=config.genai_types.FunctionResponse(
                            name=fc.name,
                            response={"output": result_text},
                        )
                    )
                )

            # Gemini expects tool responses under the "user" role
            contents.append(
                config.genai_types.Content(role="user", parts=tool_result_parts)
            )

            response = config.gemini_client.models.generate_content(
                model=base_model,
                contents=contents,
                config=cfg,
            )

        elapsed = round(time.perf_counter() - start, 2)

    except Exception as exc:
        raise RuntimeError(f"Gemini error: {exc}") from exc

    final_text = response.text or ""
    display_model = model_config.get("display_name", base_model)
    if use_reasoning and strategy == "thinking":
        display_model = f"{display_model} (Think)"

    return {
        "response": final_text,
        "model": display_model,
        "elapsed_seconds": elapsed,
        "tools_used": tools_used,
    }


async def run_inference_stream_gemini(
    conversation_id: str,
    user_message: str,
    base_model: str,
    model_config: dict,
    use_reasoning: bool,
    use_rag: bool,
    history: list[dict] | None = None,
):
    """Async SSE generator for Google Gemini cloud models.

    Phase 1 — tool evaluation via non-streaming aio call.
    Phase 2 — final text yielded as a single chunk (from Phase 1 response).
    The assistant turn is persisted in the DB inside the ``finally`` block
    regardless of client disconnection.
    """
    from database import add_message  # local import avoids circular deps

    if not config.GEMINI_AVAILABLE or config.gemini_client is None:
        yield _sse("error", "Gemini is unavailable. Please set GEMINI_API_KEY.")
        return

    strategy = model_config["reasoning_strategy"]
    tools_enabled = model_config.get("tools", False)

    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    system_prompt = build_system_prompt(today=today)
    augmented = _build_rag_message(user_message) if use_rag else user_message

    contents: list = []
    if history:
        for m in history:
            r = "model" if m["role"] == "assistant" else "user"
            contents.append(
                config.genai_types.Content(
                    role=r,
                    parts=[config.genai_types.Part(text=m["content"])],
                )
            )

    contents.append(
        config.genai_types.Content(
            role="user",
            parts=[config.genai_types.Part(text=augmented)],
        )
    )

    strategy = model_config.get("reasoning_strategy", "none")
    thinking_cfg = None

    if strategy == "thinking":
        if use_reasoning:
            thinking_cfg = config.genai_types.ThinkingConfig(thinking_budget=-1)
    elif strategy == "thinking_level":
        # Gemini 3: "high" if UI toggle ON, "low" to suppress complex thinking if OFF
        level = "high" if use_reasoning else "low"
        thinking_cfg = config.genai_types.ThinkingConfig(thinking_level=level)
    elif strategy == "thinking_level_optional":
        # Gemma 4: supports "high" or omitting the config entirely
        if use_reasoning:
            thinking_cfg = config.genai_types.ThinkingConfig(thinking_level="high")

    active_tools = TOOL_FUNCTIONS if tools_enabled else None

    cfg = config.genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=active_tools,
        thinking_config=thinking_cfg,
        # Disable SDK auto-execution so we can track tool usage and show UI status.
        automatic_function_calling=config.genai_types.AutomaticFunctionCallingConfig(
            disable=True
        ) if active_tools else None,
    )

    tools_used: list[str] = []
    accumulated_text = ""
    start = time.perf_counter()

    try:
        is_thinking = False

        for _ in range(_MAX_TOOL_ROUNDS):
            response_stream = await config.gemini_client.aio.models.generate_content_stream(
                model=base_model,
                contents=contents,
                config=cfg,
            )

            has_tools = False
            round_parts = []

            async for chunk in response_stream:
                if not chunk.candidates:
                    continue

                for part in chunk.candidates[0].content.parts:
                    round_parts.append(part)

                    if part.function_call:
                        has_tools = True
                    elif getattr(part, "thought", False):
                        if not is_thinking:
                            yield _sse("chunk", "<think>\n")
                            accumulated_text += "<think>\n"
                            is_thinking = True
                        if part.text:
                            yield _sse("chunk", part.text)
                            accumulated_text += part.text
                    elif getattr(part, "text", ""):
                        if is_thinking:
                            yield _sse("chunk", "\n</think>\n\n")
                            accumulated_text += "\n</think>\n\n"
                            is_thinking = False
                        yield _sse("chunk", part.text)
                        accumulated_text += part.text

            if not has_tools:
                break

            # If there are tools, we append the model's parts to history and process them.
            contents.append(
                config.genai_types.Content(role="model", parts=round_parts)
            )

            tool_result_parts = []
            for part in round_parts:
                if not part.function_call:
                    continue

                fc = part.function_call
                handler = TOOL_MAP.get(fc.name)
                yield _sse("status", f"Calling tool: {fc.name}")

                if handler is None:
                    result_text = f"Error: unknown tool '{fc.name}'"
                else:
                    try:
                        safe_args = fc.args or {}
                        result_text = str(await asyncio.to_thread(handler, **safe_args))
                    except Exception as exc:
                        result_text = f"Error executing {fc.name}: {exc}"

                tools_used.append(fc.name)
                tool_result_parts.append(
                    config.genai_types.Part(
                        function_response=config.genai_types.FunctionResponse(
                            name=fc.name,
                            response={"output": result_text},
                        )
                    )
                )

            contents.append(
                config.genai_types.Content(role="user", parts=tool_result_parts)
            )

        if is_thinking:
            yield _sse("chunk", "\n</think>\n")
            accumulated_text += "\n</think>\n"

        elapsed = round(time.perf_counter() - start, 2)
        display_model = model_config.get("display_name", base_model)
        if use_reasoning and strategy == "thinking":
            display_model = f"{display_model} (Think)"

        yield _sse(
            "done",
            model=display_model,
            elapsed_seconds=elapsed,
            tools_used=tools_used,
        )

    except Exception as exc:
        yield _sse("error", f"Gemini streaming error: {exc}")

    finally:
        if accumulated_text:
            elapsed_final = round(time.perf_counter() - start, 2)
            display_model = model_config.get("display_name", base_model)
            if use_reasoning and strategy == "thinking":
                display_model = f"{display_model} (Think)"
            add_message(
                conversation_id,
                "assistant",
                accumulated_text,
                model=display_model,
                elapsed_seconds=elapsed_final,
                tools_used=tools_used,
                use_rag=use_rag,
            )
