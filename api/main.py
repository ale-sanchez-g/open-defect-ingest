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
from datetime import datetime, timezone
from typing import Any, Optional

import chromadb
import pika
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "ok"}


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
        channel.queue_declare(queue=QUEUE_NAME, durable=True)
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
        query_embedding = _get_embedding(request.query)
        collection = _chroma_collection()
        raw = collection.query(
            query_embeddings=[query_embedding],
            n_results=request.n_results,
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

        llm_resp = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": LLM_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        llm_resp.raise_for_status()
        summary_text = llm_resp.json().get("response", "Unable to generate summary.")

        return {"total": total, "summary": summary_text}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
