"""Inference package — public dispatcher.

Usage::

    from inference import run_inference, run_inference_stream

``run_inference`` is synchronous and should always be called via
``asyncio.to_thread()`` to keep the FastAPI event loop free.

``run_inference_stream`` returns an async generator of SSE strings and
should be wrapped in a ``StreamingResponse``.
"""

from fastapi import HTTPException

import config
from inference.ollama import run_inference_ollama, run_inference_stream_ollama
from inference.gemini import run_inference_gemini, run_inference_stream_gemini


def run_inference(
    user_message: str,
    base_model: str,
    use_reasoning: bool,
    use_rag: bool,
    has_image: bool = False,
    history: list[dict] | None = None,
) -> dict:
    """Route a synchronous inference request to the correct provider backend.

    Raises:
        HTTPException(400): Model not registered.
        HTTPException(503): Provider unavailable.
        HTTPException(500): Provider runtime error.
    """
    model_config = config.MODEL_REGISTRY.get(base_model)
    if model_config is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{base_model}' is not registered. "
                f"Available: {list(config.MODEL_REGISTRY.keys())}"
            ),
        )

    provider = model_config.get("provider", "ollama")

    try:
        if provider == "gemini":
            return run_inference_gemini(
                user_message=user_message,
                base_model=base_model,
                model_config=model_config,
                use_reasoning=use_reasoning,
                use_rag=use_rag,
                history=history,
            )
        else:
            return run_inference_ollama(
                user_message=user_message,
                base_model=base_model,
                model_config=model_config,
                use_reasoning=use_reasoning,
                use_rag=use_rag,
                has_image=has_image,
                history=history,
            )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def run_inference_stream(
    conversation_id: str,
    user_message: str,
    base_model: str,
    use_reasoning: bool,
    use_rag: bool,
    has_image: bool = False,
    history: list[dict] | None = None,
):
    """Return an async SSE generator routed to the correct provider.

    Raises:
        HTTPException(400): Model not registered.
    """
    model_config = config.MODEL_REGISTRY.get(base_model)
    if model_config is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model '{base_model}' is not registered. "
                f"Available: {list(config.MODEL_REGISTRY.keys())}"
            ),
        )

    provider = model_config.get("provider", "ollama")

    if provider == "gemini":
        return run_inference_stream_gemini(
            conversation_id=conversation_id,
            user_message=user_message,
            base_model=base_model,
            model_config=model_config,
            use_reasoning=use_reasoning,
            use_rag=use_rag,
            history=history,
        )
    else:
        return run_inference_stream_ollama(
            conversation_id=conversation_id,
            user_message=user_message,
            base_model=base_model,
            model_config=model_config,
            use_reasoning=use_reasoning,
            use_rag=use_rag,
            has_image=has_image,
            history=history,
        )
