
import ddtrace.auto  # Enables Datadog auto-instrumentation
"""
Defect Ingest — REST API
────────────────────────
Provides endpoints for:
    • Publishing defects to RabbitMQ
    • Querying ChromaDB with semantic search
    • Fetching an AI-generated summary via the local LLM (Ollama)
    • Listing ingested defects
    • Reporting RabbitMQ queue statistics
"""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TypedDict
from urllib.parse import urlparse, urlunparse

import chromadb
import pika
import requests
from datadog import initialize, statsd
from ddtrace import tracer
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_chroma import Chroma as LangChainChroma
from langchain_ollama import OllamaEmbeddings
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from shared.logging_utils import get_datadog_logger

# ── Configuration ─────────────────────────────────────────────────────────────
RABBITMQ_HOST: str = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_USER: str = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS: str = os.getenv("RABBITMQ_PASS", "guest")
CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL: str = os.getenv("EMBED_MODEL", "nomic-embed-text")
LLM_MODEL: str = os.getenv("LLM_MODEL", "llama3.2")
QUEUE_NAME: str = os.getenv("QUEUE_NAME", "defects")
COLLECTION_NAME: str = "defects"
USE_LANGGRAPH_QUERY: bool = os.getenv("USE_LANGGRAPH_QUERY", "false").lower() == "true"
USE_LANGGRAPH_SUMMARY: bool = os.getenv("USE_LANGGRAPH_SUMMARY", "false").lower() == "true"
SUMMARY_JOB_TIMEOUT_SECONDS: int = int(os.getenv("SUMMARY_JOB_TIMEOUT_SECONDS", "180"))
DLX_EXCHANGE: str = os.getenv("DLX_EXCHANGE", f"{QUEUE_NAME}.dlx")
DLQ_NAME: str = os.getenv("DLQ_NAME", f"{QUEUE_NAME}.dlq")
GRAPH_TRACE_ENABLED: bool = os.getenv("GRAPH_TRACE_ENABLED", "true").lower() == "true"
PROMPT_STRATEGY: str = os.getenv("PROMPT_STRATEGY", "local_versioned")
PROMPTS_DIR: str = os.getenv("PROMPTS_DIR", "/app/prompts")
OPM_BASE_URL: str = os.getenv("OPM_BASE_URL", "")
OPM_TIMEOUT_SECONDS: int = int(os.getenv("OPM_TIMEOUT_SECONDS", "5"))
DD_TELEMETRY_ENABLED: bool = os.getenv("DD_TELEMETRY_ENABLED", "true").lower() == "true"
DD_AI_TELEMETRY_ENABLED: bool = os.getenv("DD_AI_TELEMETRY_ENABLED", "true").lower() == "true"
DD_AGENT_HOST: str = os.getenv("DD_AGENT_HOST", "datadog-agent")
DD_DOGSTATSD_PORT: int = int(os.getenv("DD_DOGSTATSD_PORT", "8125"))
DD_METRICS_NAMESPACE: str = os.getenv("DD_METRICS_NAMESPACE", "open_defect_ingest.api")

SUMMARY_JOBS: dict[str, dict[str, Any]] = {}
SUMMARY_JOBS_LOCK = threading.Lock()
logger = get_datadog_logger(__name__, "open-defect-api")

if DD_TELEMETRY_ENABLED:
    initialize(statsd_host=DD_AGENT_HOST, statsd_port=DD_DOGSTATSD_PORT)


def _dd_increment(metric_name: str, value: int = 1, tags: Optional[list[str]] = None) -> None:
    if not DD_TELEMETRY_ENABLED:
        return
    try:
        statsd.increment(f"{DD_METRICS_NAMESPACE}.{metric_name}", value=value, tags=tags)
    except Exception:  # noqa: BLE001
        logger.debug("Datadog metric increment failed for %s", metric_name)


def _dd_timing(metric_name: str, value_ms: float, tags: Optional[list[str]] = None) -> None:
    if not DD_TELEMETRY_ENABLED:
        return
    try:
        statsd.timing(f"{DD_METRICS_NAMESPACE}.{metric_name}", value_ms, tags=tags)
    except Exception:  # noqa: BLE001
        logger.debug("Datadog metric timing failed for %s", metric_name)

PROMPT_VERSION: str = "inline-defaults"
PROMPT_SOURCE: str = "inline-defaults"
OPM_PROMPT_NAMES: dict[str, str] = {}
DEFAULT_PROMPTS: dict[str, str] = {
    "legacy_summary": (
        "You are a software quality engineer. Analyze the following list of software defects "
        "and provide:\n"
        "1. A brief overall summary of the main issues.\n"
        "2. Common patterns or recurring problems across different defects.\n"
        "3. The most critical areas that need attention.\n\n"
        "Defects:\n{context}\n\n"
        "Provide a concise, actionable analysis."
    ),
    "query_synthesis": (
        "You are a software quality engineer. Summarize the likely issue cluster from the retrieved defects "
        "and provide one immediate next action. Keep it under 100 words.\n\n"
        "Query route: {route}\n"
        "User query: {query}\n"
        "Retrieved defects:\n{context}"
    ),
    "summary_analyze_patterns": (
        "You are a software quality engineer. Identify recurring defect patterns and likely root causes. "
        "Return concise bullets.\n\n"
        "Defects:\n{context}"
    ),
    "summary_analyze_severity": (
        "You are a software quality engineer. Assess defect severity trends and risk concentration. "
        "Return concise bullets with priority guidance.\n\n"
        "Defects:\n{context}"
    ),
    "summary_analyze_critical_areas": (
        "You are a software quality engineer. Identify critical components and areas needing immediate action. "
        "Return concise bullets.\n\n"
        "Defects:\n{context}"
    ),
    "summary_synthesize": (
        "You are a software quality engineer. Synthesize the following analysis into a concise actionable report "
        "with sections: Overview, Recurring Patterns, Severity Trends, Critical Areas, and Next Actions.\n\n"
        "Patterns:\n{patterns}\n\n"
        "Severity:\n{severity}\n\n"
        "Critical Areas:\n{critical_areas}\n"
    ),
}
PROMPTS: dict[str, str] = dict(DEFAULT_PROMPTS)

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Defect Ingest API",
    description="Central API for the Open Defect Ingest system.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic models ────────────────────────────────────────────────────────────


class Defect(BaseModel):
    id: Optional[str] = None
    title: str
    description: str
    project: str
    severity: Optional[str] = "medium"
    status: Optional[str] = "open"
    created_at: Optional[str] = None


class QueryRequest(BaseModel):
    query: str
    n_results: Optional[int] = 5


class SummaryState(TypedDict):
    documents: list[str]
    selected_documents: list[str]
    patterns: str
    severity: str
    critical_areas: str
    summary: str
    trace_id: str
    node_timings_ms: dict[str, float]
    trace_events: list[dict[str, Any]]


class QueryState(TypedDict):
    query: str
    n_results: int
    route: str
    results: list[dict[str, Any]]
    analysis: str
    trace_id: str
    node_timings_ms: dict[str, float]
    trace_events: list[dict[str, Any]]


# ── Helpers ────────────────────────────────────────────────────────────────────


def _chroma_collection() -> Any:
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    return client.get_or_create_collection(COLLECTION_NAME)


def _get_embedding(text: str) -> list[float]:
    started_at = time.monotonic()
    with tracer.trace("ai.ollama.embeddings", resource="embeddings") as span:
        span.set_tag("ai.provider", "ollama")
        span.set_tag("ai.model", EMBED_MODEL)
        span.set_tag("ai.operation", "embeddings")
        span.set_tag("ai.telemetry.enabled", DD_AI_TELEMETRY_ENABLED)
        span.set_metric("ai.prompt.characters", float(len(text)))

        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
                timeout=60,
            )
            resp.raise_for_status()
            payload = resp.json()
            embedding = payload["embedding"]

            elapsed_ms = (time.monotonic() - started_at) * 1000
            _dd_increment("ollama.embedding.requests", tags=["model:" + EMBED_MODEL, "status:success"])
            _dd_timing("ollama.embedding.latency_ms", elapsed_ms, tags=["model:" + EMBED_MODEL])
            span.set_metric("ai.response.vector_size", float(len(embedding)))
            return embedding
        except Exception:
            _dd_increment("ollama.embedding.requests", tags=["model:" + EMBED_MODEL, "status:error"])
            raise


def _langchain_retriever(k: int) -> Any:
    chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_HOST)
    vector_store = LangChainChroma(
        client=chroma_client,
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
    )
    return vector_store.as_retriever(search_kwargs={"k": k})


def _trace_node(node_name: str, fn: Any) -> Any:
    """Wrap a graph node to emit lightweight trace events and per-node timings."""

    def _wrapped(state: dict[str, Any]) -> dict[str, Any]:
        trace_id = state.get("trace_id") or str(uuid.uuid4())
        started_at = time.monotonic()
        try:
            updates = fn(state) or {}
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = round((time.monotonic() - started_at) * 1000, 3)
            logger.error(
                "Graph trace id=%s node=%s status=error elapsed_ms=%.3f error=%s",
                trace_id,
                node_name,
                elapsed_ms,
                exc,
            )
            raise

        elapsed_ms = round((time.monotonic() - started_at) * 1000, 3)

        node_timings = dict(state.get("node_timings_ms") or {})
        node_timings[node_name] = elapsed_ms

        trace_events = list(state.get("trace_events") or [])
        trace_events.append(
            {
                "node": node_name,
                "elapsed_ms": elapsed_ms,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

        if GRAPH_TRACE_ENABLED:
            logger.info("Graph trace id=%s node=%s status=ok elapsed_ms=%.3f", trace_id, node_name, elapsed_ms)

        updates["trace_id"] = trace_id
        updates["node_timings_ms"] = node_timings
        updates["trace_events"] = trace_events
        return updates

    return _wrapped


def _load_versioned_prompts() -> None:
    """Load versioned prompts from local files if available."""
    global PROMPT_VERSION, PROMPT_SOURCE, OPM_PROMPT_NAMES

    metadata_path = Path(PROMPTS_DIR) / "prompt-metadata.json"
    if not metadata_path.exists():
        logger.warning("Prompt metadata file not found at %s; using inline defaults", metadata_path)
        return

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        OPM_PROMPT_NAMES = dict(metadata.get("opm_names", {}))
        prompt_files = metadata.get("prompts", {})
        loaded_prompts: dict[str, str] = {}
        for key, rel_path in prompt_files.items():
            prompt_path = Path(PROMPTS_DIR) / rel_path
            loaded_prompts[key] = prompt_path.read_text(encoding="utf-8").strip()

        for key, value in loaded_prompts.items():
            PROMPTS[key] = value

        PROMPT_VERSION = str(metadata.get("version", "local-unknown"))
        PROMPT_SOURCE = "local"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load versioned prompts: %s. Using inline defaults.", exc)


def _render_prompt(prompt_key: str, **kwargs: Any) -> str:
    template = PROMPTS.get(prompt_key, DEFAULT_PROMPTS[prompt_key])
    rendered = template
    for key, value in kwargs.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def _extract_prompt_content(payload: dict[str, Any]) -> str:
    """Extract prompt text from OPM payload using tolerant key lookup."""
    for key in ("content", "template", "prompt", "body"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _normalize_opm_api_base_url(raw_base_url: str) -> str:
    """Normalize OPM base URL so docs/UI URLs resolve to API root."""
    base_url = raw_base_url.strip().rstrip("/")
    parsed = urlparse(base_url)
    path = parsed.path or ""

    # Accept docs/redoc URLs and map them back to the API root.
    for suffix in ("/docs", "/redoc", "/swagger"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    path = path.rstrip("/")
    if path.endswith("/api"):
        normalized_path = path
    elif path == "":
        normalized_path = "/api"
    else:
        normalized_path = f"{path}/api"

    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))


def _opm_api_candidates(raw_base_url: str) -> list[str]:
    """Build candidate OPM API base URLs with Docker localhost fallback."""
    primary = _normalize_opm_api_base_url(raw_base_url)
    in_container = Path("/.dockerenv").exists()
    candidates = [primary]

    parsed = urlparse(primary)
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        netloc = parsed.netloc
        if "@" in netloc:
            auth, host_port = netloc.rsplit("@", 1)
            if ":" in host_port:
                _, port = host_port.split(":", 1)
                host_port = f"host.docker.internal:{port}"
            else:
                host_port = "host.docker.internal"
            fallback_netloc = f"{auth}@{host_port}"
        else:
            if ":" in netloc:
                _, port = netloc.split(":", 1)
                fallback_netloc = f"host.docker.internal:{port}"
            else:
                fallback_netloc = "host.docker.internal"

        fallback = urlunparse((parsed.scheme, fallback_netloc, parsed.path, "", "", ""))
        if fallback not in candidates:
            if in_container:
                candidates = [fallback, *candidates]
            else:
                candidates.append(fallback)

    return candidates


def _load_prompts_from_opm_with_fallback() -> None:
    """Load prompt templates from OPM by name, falling back to local prompts on failures."""
    global PROMPT_SOURCE, PROMPT_VERSION
    if PROMPT_STRATEGY != "opm_with_fallback":
        return
    if not OPM_BASE_URL:
        logger.warning("PROMPT_STRATEGY=opm_with_fallback but OPM_BASE_URL is not configured; using local prompts")
        return

    candidates = _opm_api_candidates(OPM_BASE_URL)
    last_error: str = ""
    for base_url in candidates:
        try:
            list_resp = requests.get(f"{base_url}/prompts/", timeout=OPM_TIMEOUT_SECONDS)
            list_resp.raise_for_status()
            items = list_resp.json()
            if not isinstance(items, list):
                logger.warning("Unexpected OPM prompt list response from %s; trying next candidate", base_url)
                continue

            by_name = {str(item.get("name", "")).strip().lower(): item for item in items}
            applied = 0
            versions: list[str] = []
            for key, opm_name in OPM_PROMPT_NAMES.items():
                entry = by_name.get(opm_name.lower())
                if not entry:
                    continue

                prompt_id = entry.get("id")
                if not prompt_id:
                    continue

                detail_resp = requests.get(
                    f"{base_url}/prompts/{prompt_id}",
                    timeout=OPM_TIMEOUT_SECONDS,
                )
                detail_resp.raise_for_status()
                payload = detail_resp.json()
                content = _extract_prompt_content(payload if isinstance(payload, dict) else {})
                if not content:
                    continue

                PROMPTS[key] = content.strip()
                applied += 1
                versions.append(str(entry.get("version", "unknown")))

            if applied > 0:
                PROMPT_SOURCE = "opm"
                PROMPT_VERSION = f"opm:{','.join(sorted(set(versions)))}"
                logger.info("Loaded %s prompt(s) from OPM endpoint %s", applied, base_url)
                return

            logger.warning("No OPM prompts were applied from %s; trying next candidate", base_url)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning("Failed to load prompts from OPM endpoint %s: %s", base_url, exc)

    if last_error:
        logger.warning("All OPM prompt loading attempts failed. Using local prompts. Last error: %s", last_error)
    else:
        logger.warning("All OPM prompt loading attempts yielded no prompt updates. Using local prompts.")


_load_versioned_prompts()
_load_prompts_from_opm_with_fallback()


def _query_route_node(state: QueryState) -> QueryState:
    query_text = state["query"].lower()
    route = "semantic_search"
    if "critical" in query_text or "urgent" in query_text:
        route = "risk_focus"
    elif "trend" in query_text or "pattern" in query_text:
        route = "pattern_focus"
    return {"route": route}


def _query_retrieve_node(state: QueryState) -> QueryState:
    retriever = _langchain_retriever(k=state["n_results"])
    docs = retriever.invoke(state["query"])

    results: list[dict[str, Any]] = []
    for i, doc in enumerate(docs):
        metadata = dict(doc.metadata or {})
        doc_id = str(metadata.get("id") or f"doc-{i + 1}")
        results.append(
            {
                "id": doc_id,
                "document": doc.page_content,
                "metadata": metadata,
                "distance": None,
            }
        )
    return {"results": results}


def _query_synthesize_node(state: QueryState) -> QueryState:
    if not state.get("results"):
        return {"analysis": "No matching defects were found for the provided query."}

    context = "\n".join(
        [
            f"- {item['metadata'].get('title', item['id'])}: {item['document'][:280]}"
            for item in state["results"][:5]
        ]
    )
    prompt = _render_prompt(
        "query_synthesis",
        route=state.get("route", "semantic_search"),
        query=state["query"],
        context=context,
    )
    return {"analysis": _ollama_generate(prompt, timeout=45)}


def _build_query_graph() -> Any:
    graph = StateGraph(QueryState)
    graph.add_node("route_node", _trace_node("query.route", _query_route_node))
    graph.add_node("retrieve_node", _trace_node("query.retrieve", _query_retrieve_node))
    graph.add_node("synthesize_node", _trace_node("query.synthesize", _query_synthesize_node))

    graph.add_edge(START, "route_node")
    graph.add_edge("route_node", "retrieve_node")
    graph.add_edge("retrieve_node", "synthesize_node")
    graph.add_edge("synthesize_node", END)
    return graph.compile()


QUERY_GRAPH = _build_query_graph()


def _run_legacy_query(query: str, n_results: int) -> list[dict[str, Any]]:
    query_embedding = _get_embedding(query)
    collection = _chroma_collection()
    raw = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
    )
    results: list[dict[str, Any]] = []
    if raw.get("ids") and raw["ids"][0]:
        for i, doc_id in enumerate(raw["ids"][0]):
            results.append(
                {
                    "id": doc_id,
                    "document": (raw["documents"][0][i] if raw.get("documents") else ""),
                    "metadata": (raw["metadatas"][0][i] if raw.get("metadatas") else {}),
                    "distance": (raw["distances"][0][i] if raw.get("distances") else None),
                }
            )
    return results


def _run_langgraph_query(query: str, n_results: int) -> dict[str, Any]:
    result: QueryState = QUERY_GRAPH.invoke(
        {
            "query": query,
            "n_results": n_results,
            "route": "",
            "results": [],
            "analysis": "",
            "trace_id": "",
            "node_timings_ms": {},
            "trace_events": [],
        }
    )
    return {
        "query": query,
        "results": result.get("results", []),
        "route": result.get("route", "semantic_search"),
        "analysis": result.get("analysis", ""),
        "trace_id": result.get("trace_id", ""),
        "node_timings_ms": result.get("node_timings_ms", {}),
    }


def _stream_query_synthesis(query: str, n_results: int):
    """SSE generator: emit retrieval results immediately then stream synthesis tokens."""
    if USE_LANGGRAPH_QUERY:
        initial_state: QueryState = {
            "query": query,
            "n_results": n_results,
            "route": "",
            "results": [],
            "analysis": "",
            "trace_id": "",
            "node_timings_ms": {},
            "trace_events": [],
        }
        route_state = _query_route_node(initial_state)
        retrieve_state = _query_retrieve_node({**initial_state, **route_state})
        results = retrieve_state.get("results", [])
        route = route_state.get("route", "semantic_search")
    else:
        results = _run_legacy_query(query, n_results)
        route = "semantic_search"

    yield f"event: results\ndata: {json.dumps({'query': query, 'results': results, 'route': route})}\n\n"

    if not results or not USE_LANGGRAPH_QUERY:
        yield "event: done\ndata: {}\n\n"
        return

    context = "\n".join(
        f"- {item['metadata'].get('title', item['id'])}: {item['document'][:280]}"
        for item in results[:5]
    )
    prompt = _render_prompt("query_synthesis", route=route, query=query, context=context)

    try:
        with requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": LLM_MODEL, "prompt": prompt, "stream": True},
            stream=True,
            timeout=SUMMARY_JOB_TIMEOUT_SECONDS,
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                chunk = json.loads(raw_line)
                token = chunk.get("response", "")
                if token:
                    yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"
                if chunk.get("done"):
                    break
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"

    yield "event: done\ndata: {}\n\n"


def _ollama_generate(prompt: str, timeout: int = SUMMARY_JOB_TIMEOUT_SECONDS) -> str:
    started_at = time.monotonic()
    with tracer.trace("ai.ollama.completion", resource="generate") as span:
        span.set_tag("ai.provider", "ollama")
        span.set_tag("ai.model", LLM_MODEL)
        span.set_tag("ai.operation", "completion")
        span.set_tag("ai.telemetry.enabled", DD_AI_TELEMETRY_ENABLED)
        span.set_metric("ai.prompt.characters", float(len(prompt)))

        try:
            llm_resp = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": LLM_MODEL, "prompt": prompt, "stream": False},
                timeout=timeout,
            )
            llm_resp.raise_for_status()
            response_text = llm_resp.json().get("response", "Unable to generate summary.")

            elapsed_ms = (time.monotonic() - started_at) * 1000
            _dd_increment("ollama.completion.requests", tags=["model:" + LLM_MODEL, "status:success"])
            _dd_timing("ollama.completion.latency_ms", elapsed_ms, tags=["model:" + LLM_MODEL])
            span.set_metric("ai.response.characters", float(len(response_text)))
            return response_text
        except Exception:
            _dd_increment("ollama.completion.requests", tags=["model:" + LLM_MODEL, "status:error"])
            raise


def _summary_select_documents(state: SummaryState) -> SummaryState:
    selected = state.get("documents", [])[:20]
    return {"selected_documents": selected}


def _summary_analyze_patterns(state: SummaryState) -> SummaryState:
    context = "\n".join(state.get("selected_documents", []))
    prompt = _render_prompt("summary_analyze_patterns", context=context)
    return {"patterns": _ollama_generate(prompt)}


def _summary_analyze_severity(state: SummaryState) -> SummaryState:
    context = "\n".join(state.get("selected_documents", []))
    prompt = _render_prompt("summary_analyze_severity", context=context)
    return {"severity": _ollama_generate(prompt)}


def _summary_analyze_critical_areas(state: SummaryState) -> SummaryState:
    context = "\n".join(state.get("selected_documents", []))
    prompt = _render_prompt("summary_analyze_critical_areas", context=context)
    return {"critical_areas": _ollama_generate(prompt)}


def _summary_synthesize(state: SummaryState) -> SummaryState:
    prompt = _render_prompt(
        "summary_synthesize",
        patterns=state.get("patterns", ""),
        severity=state.get("severity", ""),
        critical_areas=state.get("critical_areas", ""),
    )
    return {"summary": _ollama_generate(prompt)}


def _build_summary_graph() -> Any:
    graph = StateGraph(SummaryState)
    graph.add_node("select_documents", _trace_node("summary.select_documents", _summary_select_documents))
    graph.add_node("analyze_patterns", _trace_node("summary.analyze_patterns", _summary_analyze_patterns))
    graph.add_node("analyze_severity", _trace_node("summary.analyze_severity", _summary_analyze_severity))
    graph.add_node("analyze_critical_areas", _trace_node("summary.analyze_critical_areas", _summary_analyze_critical_areas))
    graph.add_node("synthesize", _trace_node("summary.synthesize", _summary_synthesize))

    graph.add_edge(START, "select_documents")
    graph.add_edge("select_documents", "analyze_patterns")
    graph.add_edge("select_documents", "analyze_severity")
    graph.add_edge("select_documents", "analyze_critical_areas")
    graph.add_edge("analyze_patterns", "synthesize")
    graph.add_edge("analyze_severity", "synthesize")
    graph.add_edge("analyze_critical_areas", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()


SUMMARY_GRAPH = _build_summary_graph()


def _run_legacy_summary(collection: Any, total: int) -> str:
    raw = collection.get(limit=50, include=["documents"])
    docs: list[str] = raw.get("documents", [])
    context = "\n".join(docs[:20])

    prompt = _render_prompt("legacy_summary", context=context)
    return _ollama_generate(prompt, timeout=120)


def _run_langgraph_summary(collection: Any) -> str:
    raw = collection.get(limit=50, include=["documents"])
    docs: list[str] = raw.get("documents", [])
    result: SummaryState = SUMMARY_GRAPH.invoke(
        {
            "documents": docs,
            "selected_documents": [],
            "patterns": "",
            "severity": "",
            "critical_areas": "",
            "summary": "",
            "trace_id": "",
            "node_timings_ms": {},
            "trace_events": [],
        }
    )
    return result.get("summary", "Unable to generate summary.")


def _generate_summary(total: int, collection: Any) -> str:
    if USE_LANGGRAPH_SUMMARY:
        return _run_langgraph_summary(collection)
    return _run_legacy_summary(collection, total)


def _summary_job_worker(job_id: str) -> None:
    with SUMMARY_JOBS_LOCK:
        SUMMARY_JOBS[job_id]["status"] = "running"
        SUMMARY_JOBS[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()

    try:
        collection = _chroma_collection()
        total = collection.count()
        if total == 0:
            payload = {"total": 0, "summary": "No defects have been ingested yet."}
        else:
            payload = {"total": total, "summary": _run_langgraph_summary(collection)}

        with SUMMARY_JOBS_LOCK:
            SUMMARY_JOBS[job_id]["status"] = "completed"
            SUMMARY_JOBS[job_id]["result"] = payload
            SUMMARY_JOBS[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:  # noqa: BLE001
        with SUMMARY_JOBS_LOCK:
            SUMMARY_JOBS[job_id]["status"] = "failed"
            SUMMARY_JOBS[job_id]["error"] = str(exc)
            SUMMARY_JOBS[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()


def _declare_primary_queue(channel: Any, queue_name: str) -> Any:
    """Declare primary queue with DLX, falling back for pre-existing legacy queues."""
    try:
        channel.queue_declare(
            queue=queue_name,
            durable=True,
            arguments={"x-dead-letter-exchange": DLX_EXCHANGE},
        )
        return channel
    except pika.exceptions.ChannelClosedByBroker as exc:
        if "x-dead-letter-exchange" not in str(exc):
            raise

        fallback_channel = channel.connection.channel()
        fallback_channel.queue_declare(queue=queue_name, durable=True)
        return fallback_channel


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config", tags=["System"])
def config() -> dict[str, Any]:
    """Return runtime feature-flag state for progressive migration rollout."""
    return {
        "features": {
            "use_langgraph_query": USE_LANGGRAPH_QUERY,
            "use_langgraph_summary": USE_LANGGRAPH_SUMMARY,
            "graph_trace_enabled": GRAPH_TRACE_ENABLED,
        }
        ,
        "prompts": {
            "strategy": PROMPT_STRATEGY,
            "version": PROMPT_VERSION,
            "source": PROMPT_SOURCE,
        },
    }


@app.get("/queue/stats", tags=["Queue"])
def queue_stats() -> dict[str, Any]:
    """Return live statistics for the defects queue from RabbitMQ's management API."""
    try:
        resp = requests.get(
            f"http://{RABBITMQ_HOST}:15672/api/queues/%2F/{QUEUE_NAME}",
            auth=(RABBITMQ_USER, RABBITMQ_PASS),
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "queue": QUEUE_NAME,
                "messages": data.get("messages", 0),
                "messages_ready": data.get("messages_ready", 0),
                "messages_unacknowledged": data.get("messages_unacknowledged", 0),
                "consumers": data.get("consumers", 0),
                "state": data.get("state", "unknown"),
            }
        return {"queue": QUEUE_NAME, "messages": 0, "state": "unknown"}
    except Exception as exc:  # noqa: BLE001
        return {"queue": QUEUE_NAME, "messages": 0, "error": str(exc)}


@app.post("/defects/ingest", tags=["Defects"])
def ingest_defect(defect: Defect) -> dict[str, Any]:
    """Publish a defect message to the RabbitMQ queue for async processing."""
    try:
        if not defect.created_at:
            defect.created_at = datetime.now(timezone.utc).isoformat()
        params = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS),
        )
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.exchange_declare(exchange=DLX_EXCHANGE, exchange_type="direct", durable=True)
        channel.queue_declare(queue=DLQ_NAME, durable=True)
        channel.queue_bind(queue=DLQ_NAME, exchange=DLX_EXCHANGE, routing_key=QUEUE_NAME)
        channel = _declare_primary_queue(channel, QUEUE_NAME)
        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=json.dumps(defect.model_dump()),
            properties=pika.BasicProperties(delivery_mode=2),
        )
        connection.close()
        return {"status": "queued", "defect_id": defect.id}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/defects/query", tags=["Defects"])
def query_defects(request: QueryRequest) -> dict[str, Any]:
    """Semantic search — find defects similar to the query using vector embeddings."""
    try:
        if USE_LANGGRAPH_QUERY:
            logger.info("Processing query with LangGraph route: %s", request.query)
            return _run_langgraph_query(request.query, int(request.n_results or 5))
        logger.info("Processing query with legacy route: %s", request.query)
        results = _run_legacy_query(request.query, int(request.n_results or 5))
        return {"query": request.query, "results": results}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/defects/query/stream", tags=["Defects"])
def query_defects_stream(request: QueryRequest) -> StreamingResponse:
    """Streaming semantic search — returns results immediately then streams synthesis tokens via SSE."""
    return StreamingResponse(
        _stream_query_synthesis(request.query, int(request.n_results or 5)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _stream_summary():
    """SSE generator: emit progress after each analysis step then stream synthesis tokens."""
    try:
        collection = _chroma_collection()
        total = collection.count()
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
        yield "event: done\ndata: {}\n\n"
        return

    if total == 0:
        yield f"event: done\ndata: {json.dumps({'total': 0, 'summary': 'No defects have been ingested yet.'})}\n\n"
        return

    yield f"event: start\ndata: {json.dumps({'total': total})}\n\n"

    raw = collection.get(limit=50, include=["documents"])
    docs: list[str] = raw.get("documents", [])[:20]
    context = "\n".join(docs)

    if USE_LANGGRAPH_SUMMARY:
        try:
            patterns = _ollama_generate(_render_prompt("summary_analyze_patterns", context=context))
            yield f"event: progress\ndata: {json.dumps({'step': 'patterns', 'content': patterns})}\n\n"

            severity = _ollama_generate(_render_prompt("summary_analyze_severity", context=context))
            yield f"event: progress\ndata: {json.dumps({'step': 'severity', 'content': severity})}\n\n"

            critical_areas = _ollama_generate(_render_prompt("summary_analyze_critical_areas", context=context))
            yield f"event: progress\ndata: {json.dumps({'step': 'critical_areas', 'content': critical_areas})}\n\n"

            synthesis_prompt = _render_prompt(
                "summary_synthesize",
                patterns=patterns,
                severity=severity,
                critical_areas=critical_areas,
            )
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
            yield "event: done\ndata: {}\n\n"
            return
    else:
        synthesis_prompt = _render_prompt("legacy_summary", context=context)

    try:
        with requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": LLM_MODEL, "prompt": synthesis_prompt, "stream": True},
            stream=True,
            timeout=SUMMARY_JOB_TIMEOUT_SECONDS,
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                chunk = json.loads(raw_line)
                token = chunk.get("response", "")
                if token:
                    yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"
                if chunk.get("done"):
                    break
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"

    yield f"event: done\ndata: {json.dumps({'total': total})}\n\n"


@app.get("/defects/summary/stream", tags=["Defects"])
def defects_summary_stream() -> StreamingResponse:
    """Streaming summary — emits progress events per analysis step then streams synthesis tokens via SSE."""
    return StreamingResponse(
        _stream_summary(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/defects/summary", tags=["Defects"])
def defects_summary() -> dict[str, Any]:
    """Return an AI-generated summary of all ingested defects (via Ollama LLM)."""
    try:
        collection = _chroma_collection()
        total = collection.count()
        if total == 0:
            return {
                "total": 0,
                "summary": "No defects have been ingested yet.",
            }
        summary_text = _generate_summary(total=total, collection=collection)
        return {"total": total, "summary": summary_text}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/defects/summary/jobs", tags=["Defects"])
def create_summary_job() -> dict[str, Any]:
    """Start a background summary job and return a job identifier."""
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with SUMMARY_JOBS_LOCK:
        SUMMARY_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }

    worker = threading.Thread(target=_summary_job_worker, args=(job_id,), daemon=True)
    worker.start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/defects/summary/jobs/{job_id}", tags=["Defects"])
def get_summary_job(job_id: str) -> dict[str, Any]:
    """Return metadata and status for a summary job."""
    with SUMMARY_JOBS_LOCK:
        job = SUMMARY_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Summary job not found")
        return {
            "job_id": job_id,
            "status": job["status"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            "error": job["error"],
        }


@app.get("/defects/summary/jobs/{job_id}/result", tags=["Defects"])
def get_summary_job_result(job_id: str) -> dict[str, Any]:
    """Return job result when available."""
    with SUMMARY_JOBS_LOCK:
        job = SUMMARY_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Summary job not found")

    if job["status"] in {"queued", "running"}:
        return {"job_id": job_id, "status": job["status"], "result": None}
    if job["status"] == "failed":
        raise HTTPException(status_code=500, detail=job.get("error", "Summary job failed"))
    return {"job_id": job_id, "status": job["status"], "result": job["result"]}


@app.get("/defects/list", tags=["Defects"])
def list_defects(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Return a paginated list of all ingested defects."""
    try:
        collection = _chroma_collection()
        raw = collection.get(
            limit=limit,
            offset=offset,
            include=["documents", "metadatas"],
        )
        defects: list[dict[str, Any]] = []
        for i, doc_id in enumerate(raw.get("ids", [])):
            defects.append(
                {
                    "id": doc_id,
                    "document": (raw["documents"][i] if raw.get("documents") else ""),
                    "metadata": (raw["metadatas"][i] if raw.get("metadatas") else {}),
                }
            )
        return {"defects": defects, "total": collection.count()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
