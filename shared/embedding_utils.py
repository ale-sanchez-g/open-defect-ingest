"""Embedding utilities for Ollama + Datadog telemetry (shared)."""

import time
import requests

from typing import Any, Callable, Optional
try:
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs.decorators import embedding
except ImportError:
    LLMObs = None
    def embedding(*args, **kwargs):
        def decorator(f):
            return f
        return decorator

# These must be set by the importing module:
EMBED_MODEL: Optional[str] = None
OLLAMA_HOST: Optional[str] = None
DD_AI_TELEMETRY_ENABLED: Optional[bool] = None
_dd_increment: Optional[Callable] = None
_dd_timing: Optional[Callable] = None


def configure_embedding_utils(embed_model: str, ollama_host: str, ai_telemetry_enabled: bool, dd_increment: Callable, dd_timing: Callable) -> None:
    global EMBED_MODEL, OLLAMA_HOST, DD_AI_TELEMETRY_ENABLED, _dd_increment, _dd_timing
    EMBED_MODEL = embed_model
    OLLAMA_HOST = ollama_host
    DD_AI_TELEMETRY_ENABLED = ai_telemetry_enabled
    _dd_increment = dd_increment
    _dd_timing = dd_timing


@embedding(model_name=lambda: EMBED_MODEL or "unknown", model_provider="ollama")
def get_embedding(text: str) -> list[float]:
    started_at = time.monotonic()
    if LLMObs:
        LLMObs.annotate(
            input_data=[{"text": text}],
            metadata={"ai.telemetry.enabled": DD_AI_TELEMETRY_ENABLED},
        )
    try:
        if not EMBED_MODEL or not OLLAMA_HOST:
            raise RuntimeError("EMBED_MODEL and OLLAMA_HOST must be configured before calling get_embedding")
        resp = requests.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        embedding_vector = payload["embedding"]
        elapsed_ms = (time.monotonic() - started_at) * 1000
        if LLMObs:
            LLMObs.annotate(
                output_data=[{"text": text, "embedding": embedding_vector}],
                metrics={"input_tokens": float(len(text.split()))},
            )
        tag_model = f"model:{EMBED_MODEL or 'unknown'}"
        if _dd_increment:
            _dd_increment("ollama.embedding.requests", tags=[tag_model, "status:success"])
        if _dd_timing:
            _dd_timing("ollama.embedding.latency_ms", elapsed_ms, tags=[tag_model])
        return embedding_vector
    except Exception:
        tag_model = f"model:{EMBED_MODEL or 'unknown'}"
        if _dd_increment:
            _dd_increment("ollama.embedding.requests", tags=[tag_model, "status:error"])
        raise
