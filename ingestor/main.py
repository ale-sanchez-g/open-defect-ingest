"""
Defect Ingestor
───────────────
Consumes defect messages from RabbitMQ, generates vector embeddings via Ollama,
and stores them in ChromaDB for later querying.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import chromadb
import pika
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
RABBITMQ_URL: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL: str = os.getenv("EMBED_MODEL", "nomic-embed-text")
QUEUE_NAME: str = os.getenv("QUEUE_NAME", "defects")
COLLECTION_NAME: str = "defects"


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_embedding(
    text: str,
    ollama_host: str = OLLAMA_HOST,
    model: str = EMBED_MODEL,
) -> list[float]:
    """Return a vector embedding for *text* produced by Ollama."""
    response = requests.post(
        f"{ollama_host}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["embedding"]


def store_defect(
    defect: dict[str, Any],
    chroma_host: str = CHROMA_HOST,
    chroma_port: int = CHROMA_PORT,
) -> None:
    """Embed the defect text and persist it in ChromaDB."""
    text = " ".join(
        filter(
            None,
            [defect.get("title"), defect.get("description"), defect.get("project")],
        )
    )
    embedding = get_embedding(text)

    client = chromadb.HttpClient(host=chroma_host, port=chroma_port)
    collection = client.get_or_create_collection(COLLECTION_NAME)

    defect_id = str(defect.get("id") or datetime.now(timezone.utc).timestamp())
    collection.add(
        ids=[defect_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[
            {
                "id": defect_id,
                "title": defect.get("title", ""),
                "project": defect.get("project", ""),
                "severity": defect.get("severity", "medium"),
                "status": defect.get("status", "open"),
                "created_at": defect.get(
                    "created_at", datetime.now(timezone.utc).isoformat()
                ),
            }
        ],
    )


# ── RabbitMQ callback ─────────────────────────────────────────────────────────

def process_message(ch: Any, method: Any, properties: Any, body: bytes) -> None:
    """Handle a single RabbitMQ delivery."""
    try:
        defect: dict[str, Any] = json.loads(body)
        logger.info("Processing defect id=%s", defect.get("id", "unknown"))
        store_defect(defect)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logger.info("Defect %s stored successfully", defect.get("id"))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to process defect: %s", exc)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


# ── Consumer loop ─────────────────────────────────────────────────────────────

def start_consumer(
    rabbitmq_url: str = RABBITMQ_URL,
    queue_name: str = QUEUE_NAME,
) -> None:
    """Connect to RabbitMQ and start consuming (blocks until disconnected)."""
    params = pika.URLParameters(rabbitmq_url)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue=queue_name, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=process_message)
    logger.info("Ingestor ready — waiting for defects on queue '%s'…", queue_name)
    channel.start_consuming()


def main() -> None:
    """Entry point with automatic reconnect on transient failures."""
    while True:
        try:
            start_consumer()
        except KeyboardInterrupt:
            logger.info("Shutting down ingestor — goodbye.")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("Connection error: %s — retrying in 5 s…", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
