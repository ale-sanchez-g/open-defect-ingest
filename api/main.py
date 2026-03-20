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
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

import chromadb
import pika
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_community.vectorstores import Chroma as LangChainChroma
from langchain_ollama import OllamaEmbeddings
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

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

SUMMARY_JOBS: dict[str, dict[str, Any]] = {}
SUMMARY_JOBS_LOCK = threading.Lock()

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


class QueryState(TypedDict):
    query: str
    n_results: int
    route: str
    results: list[dict[str, Any]]
    analysis: str


# ── Helpers ────────────────────────────────────────────────────────────────────


def _chroma_collection() -> Any:
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    return client.get_or_create_collection(COLLECTION_NAME)


def _get_embedding(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def _langchain_retriever(k: int) -> Any:
    chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_HOST)
    vector_store = LangChainChroma(
        client=chroma_client,
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
    )
    return vector_store.as_retriever(search_kwargs={"k": k})


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
    prompt = (
        "You are a software quality engineer. Summarize the likely issue cluster from the retrieved defects "
        "and provide one immediate next action. Keep it under 100 words.\n\n"
        f"Query route: {state.get('route', 'semantic_search')}\n"
        f"User query: {state['query']}\n"
        f"Retrieved defects:\n{context}"
    )
    return {"analysis": _ollama_generate(prompt, timeout=45)}


def _build_query_graph() -> Any:
    graph = StateGraph(QueryState)
    graph.add_node("route_node", _query_route_node)
    graph.add_node("retrieve_node", _query_retrieve_node)
    graph.add_node("synthesize_node", _query_synthesize_node)

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
        }
    )
    return {
        "query": query,
        "results": result.get("results", []),
        "route": result.get("route", "semantic_search"),
        "analysis": result.get("analysis", ""),
    }


def _ollama_generate(prompt: str, timeout: int = SUMMARY_JOB_TIMEOUT_SECONDS) -> str:
    llm_resp = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={"model": LLM_MODEL, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    llm_resp.raise_for_status()
    return llm_resp.json().get("response", "Unable to generate summary.")


def _summary_select_documents(state: SummaryState) -> SummaryState:
    selected = state.get("documents", [])[:20]
    return {"selected_documents": selected}


def _summary_analyze_patterns(state: SummaryState) -> SummaryState:
    context = "\n".join(state.get("selected_documents", []))
    prompt = (
        "You are a software quality engineer. Identify recurring defect patterns and likely root causes. "
        "Return concise bullets.\n\n"
        f"Defects:\n{context}"
    )
    return {"patterns": _ollama_generate(prompt)}


def _summary_analyze_severity(state: SummaryState) -> SummaryState:
    context = "\n".join(state.get("selected_documents", []))
    prompt = (
        "You are a software quality engineer. Assess defect severity trends and risk concentration. "
        "Return concise bullets with priority guidance.\n\n"
        f"Defects:\n{context}"
    )
    return {"severity": _ollama_generate(prompt)}


def _summary_analyze_critical_areas(state: SummaryState) -> SummaryState:
    context = "\n".join(state.get("selected_documents", []))
    prompt = (
        "You are a software quality engineer. Identify critical components and areas needing immediate action. "
        "Return concise bullets.\n\n"
        f"Defects:\n{context}"
    )
    return {"critical_areas": _ollama_generate(prompt)}


def _summary_synthesize(state: SummaryState) -> SummaryState:
    prompt = (
        "You are a software quality engineer. Synthesize the following analysis into a concise actionable report "
        "with sections: Overview, Recurring Patterns, Severity Trends, Critical Areas, and Next Actions.\n\n"
        f"Patterns:\n{state.get('patterns', '')}\n\n"
        f"Severity:\n{state.get('severity', '')}\n\n"
        f"Critical Areas:\n{state.get('critical_areas', '')}\n"
    )
    return {"summary": _ollama_generate(prompt)}


def _build_summary_graph() -> Any:
    graph = StateGraph(SummaryState)
    graph.add_node("select_documents", _summary_select_documents)
    graph.add_node("analyze_patterns", _summary_analyze_patterns)
    graph.add_node("analyze_severity", _summary_analyze_severity)
    graph.add_node("analyze_critical_areas", _summary_analyze_critical_areas)
    graph.add_node("synthesize", _summary_synthesize)

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

    prompt = (
        "You are a software quality engineer. Analyze the following list of software defects "
        "and provide:\n"
        "1. A brief overall summary of the main issues.\n"
        "2. Common patterns or recurring problems across different defects.\n"
        "3. The most critical areas that need attention.\n\n"
        f"Defects:\n{context}\n\n"
        "Provide a concise, actionable analysis."
    )
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
        }
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
            return _run_langgraph_query(request.query, int(request.n_results or 5))

        results = _run_legacy_query(request.query, int(request.n_results or 5))
        return {"query": request.query, "results": results}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
