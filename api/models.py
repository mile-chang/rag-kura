"""Model and service status routes."""

from fastapi import APIRouter

import config

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models/{model_id}/status")
async def api_model_status(model_id: str, use_reasoning: bool = False):
    """Return the warm/cold state of the specified model.

    * **Ollama models** — queries ``ollama.ps()`` to check whether the
      model is currently resident in VRAM.  Returns ``is_loaded=false``
      when Ollama is not installed so the UI shows the loading indicator.
    * **Gemini models** — always returns ``is_loaded=true`` because cloud
      APIs have no local warm-up phase.

    The ``model_id`` path segment must be URL-encoded when it contains
    special characters such as colons or dots (e.g. ``qwen3.5%3A2b``).
    FastAPI automatically decodes the value before matching.
    """
    model_config = config.MODEL_REGISTRY.get(model_id)

    # Unknown model — respond optimistically so the UI isn't blocked
    if not model_config:
        return {"is_loaded": True, "actual_model": model_id}

    provider = model_config.get("provider", "ollama")

    # Cloud models need no local warm-up
    if provider == "gemini":
        return {"is_loaded": True, "actual_model": model_id}

    # Resolve the actual model variant (handles phi4-mini → phi4-mini-reasoning)
    actual_model = model_id
    strategy = model_config.get("reasoning_strategy")
    if strategy == "model_switch" and use_reasoning:
        actual_model = model_config.get("switch_to", model_id)

    if not config.OLLAMA_AVAILABLE:
        return {"is_loaded": False, "actual_model": actual_model}

    try:
        ps_data = config.ollama_lib.ps()
        running_models = [m.get("model", "") for m in ps_data.get("models", [])]
        is_loaded = any(actual_model in m for m in running_models)
    except Exception:
        # Ollama daemon unreachable — fall back to True so the UI doesn't stall
        is_loaded = True

    return {"is_loaded": is_loaded, "actual_model": actual_model}


@router.get("/status")
async def api_status():
    """Return service health and per-provider availability.

    The frontend calls this endpoint on startup to determine which models
    to enable in the model-picker UI.  No credentials are exposed.
    """
    return {
        "status": "ok",
        "ollama_available": config.OLLAMA_AVAILABLE,
        "gemini_available": config.GEMINI_AVAILABLE,
        "registered_models": list(config.MODEL_REGISTRY.keys()),
    }
