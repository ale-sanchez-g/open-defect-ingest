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
from datadog import initialize, statsd
from ddtrace import tracer
from shared.logging_utils import get_datadog_logger

logger = get_datadog_logger(__name__, "open-defect-ingestor")

# ── Configuration ────────────────────────────────────────────────────────────
RABBITMQ_URL: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL: str = os.getenv("EMBED_MODEL", "nomic-embed-text")
QUEUE_NAME: str = os.getenv("QUEUE_NAME", "defects")
COLLECTION_NAME: str = "defects"
PREFETCH_COUNT: int = int(os.getenv("PREFETCH_COUNT", "1"))
DLX_EXCHANGE: str = os.getenv("DLX_EXCHANGE", f"{QUEUE_NAME}.dlx")
DLQ_NAME: str = os.getenv("DLQ_NAME", f"{QUEUE_NAME}.dlq")
METRICS_LOG_INTERVAL_SECONDS: int = int(os.getenv("METRICS_LOG_INTERVAL_SECONDS", "30"))
DD_TELEMETRY_ENABLED: bool = os.getenv("DD_TELEMETRY_ENABLED", "true").lower() == "true"
DD_AI_TELEMETRY_ENABLED: bool = os.getenv("DD_AI_TELEMETRY_ENABLED", "true").lower() == "true"
DD_AGENT_HOST: str = os.getenv("DD_AGENT_HOST", "datadog-agent")
DD_DOGSTATSD_PORT: int = int(os.getenv("DD_DOGSTATSD_PORT", "8125"))
DD_METRICS_NAMESPACE: str = os.getenv("DD_METRICS_NAMESPACE", "open_defect_ingest.ingestor")

if DD_TELEMETRY_ENABLED:
    initialize(statsd_host=DD_AGENT_HOST, statsd_port=DD_DOGSTATSD_PORT)


def _dd_increment(metric_name: str, value: int = 1, tags: list[str] | None = None) -> None:
    if not DD_TELEMETRY_ENABLED:
        return
    try:
        statsd.increment(f"{DD_METRICS_NAMESPACE}.{metric_name}", value=value, tags=tags)
    except Exception:  # noqa: BLE001
        logger.debug("Datadog metric increment failed for %s", metric_name)


def _dd_timing(metric_name: str, value_ms: float, tags: list[str] | None = None) -> None:
    if not DD_TELEMETRY_ENABLED:
        return
    try:
        statsd.timing(f"{DD_METRICS_NAMESPACE}.{metric_name}", value_ms, tags=tags)
    except Exception:  # noqa: BLE001
        logger.debug("Datadog metric timing failed for %s", metric_name)

METRICS: dict[str, float] = {
    "processed_total": 0.0,
    "success_total": 0.0,
    "failure_total": 0.0,
    "latency_total_ms": 0.0,
    "max_latency_ms": 0.0,
    "window_started_at": time.monotonic(),
    "window_processed_start": 0.0,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_embedding(
    text: str,
    ollama_host: str = OLLAMA_HOST,
    model: str = EMBED_MODEL,
) -> list[float]:
    """Return a vector embedding for *text* produced by Ollama."""
    started_at = time.monotonic()
    with tracer.trace("ai.ollama.embeddings", resource="embeddings") as span:
        span.set_tag("ai.provider", "ollama")
        span.set_tag("ai.model", model)
        span.set_tag("ai.operation", "embeddings")
        span.set_tag("ai.telemetry.enabled", DD_AI_TELEMETRY_ENABLED)
        span.set_metric("ai.prompt.characters", float(len(text)))

        try:
            response = requests.post(
                f"{ollama_host}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            embedding = payload["embedding"]

            elapsed_ms = (time.monotonic() - started_at) * 1000
            _dd_increment("ollama.embedding.requests", tags=["model:" + model, "status:success"])
            _dd_timing("ollama.embedding.latency_ms", elapsed_ms, tags=["model:" + model])
            span.set_metric("ai.response.vector_size", float(len(embedding)))
            return embedding
        except Exception:
            _dd_increment("ollama.embedding.requests", tags=["model:" + model, "status:error"])
            raise


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


def _record_processing_metrics(success: bool, elapsed_ms: int) -> None:
    """Track throughput and emit periodic metrics logs for worker observability."""
    METRICS["processed_total"] += 1
    if success:
        METRICS["success_total"] += 1
    else:
        METRICS["failure_total"] += 1

    METRICS["latency_total_ms"] += float(elapsed_ms)
    METRICS["max_latency_ms"] = max(METRICS["max_latency_ms"], float(elapsed_ms))

    now = time.monotonic()
    window_seconds = now - METRICS["window_started_at"]
    if window_seconds < METRICS_LOG_INTERVAL_SECONDS:
        return

    window_processed = METRICS["processed_total"] - METRICS["window_processed_start"]
    window_rate = window_processed / window_seconds if window_seconds > 0 else 0.0
    avg_latency_ms = (
        METRICS["latency_total_ms"] / METRICS["processed_total"]
        if METRICS["processed_total"] > 0
        else 0.0
    )
    logger.info(
        "Throughput metrics: processed_total=%d success_total=%d failure_total=%d avg_latency_ms=%.2f max_latency_ms=%.0f window_rate_msgs_per_sec=%.2f window_seconds=%.1f",
        int(METRICS["processed_total"]),
        int(METRICS["success_total"]),
        int(METRICS["failure_total"]),
        avg_latency_ms,
        METRICS["max_latency_ms"],
        window_rate,
        window_seconds,
    )
    METRICS["window_started_at"] = now
    METRICS["window_processed_start"] = METRICS["processed_total"]


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

        logger.warning(
            "Queue '%s' already exists without DLX configuration; continuing without DLX on this queue. "
            "Delete and recreate queue to enforce DLQ routing.",
            queue_name,
        )
        fallback_channel = channel.connection.channel()
        fallback_channel.queue_declare(queue=queue_name, durable=True)
        return fallback_channel


# ── RabbitMQ callback ─────────────────────────────────────────────────────────

def process_message(ch: Any, method: Any, properties: Any, body: bytes) -> None:
    """Handle a single RabbitMQ delivery."""
    started_at = time.monotonic()
    with tracer.trace("ingestor.process_message", resource=QUEUE_NAME) as span:
        span.set_tag("queue.name", QUEUE_NAME)
        span.set_tag("ai.telemetry.enabled", DD_AI_TELEMETRY_ENABLED)
        try:
            defect: dict[str, Any] = json.loads(body)
            span.set_tag("defect.id", defect.get("id", "unknown"))
            logger.info("Processing defect id=%s", defect.get("id", "unknown"))
            store_defect(defect)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            _record_processing_metrics(success=True, elapsed_ms=elapsed_ms)
            _dd_increment("queue.processed", tags=["status:success"])
            _dd_timing("queue.processing_latency_ms", float(elapsed_ms), tags=["status:success"])
            logger.info("Defect %s stored successfully in %sms", defect.get("id"), elapsed_ms)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            logger.exception("Failed to process defect: %s", exc)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            _record_processing_metrics(success=False, elapsed_ms=elapsed_ms)
            _dd_increment("queue.processed", tags=["status:error"])
            _dd_timing("queue.processing_latency_ms", float(elapsed_ms), tags=["status:error"])
            logger.error("Defect delivery failed and routed to DLQ in %sms", elapsed_ms)


# ── Consumer loop ─────────────────────────────────────────────────────────────

def start_consumer(
    rabbitmq_url: str = RABBITMQ_URL,
    queue_name: str = QUEUE_NAME,
    prefetch_count: int = PREFETCH_COUNT,
) -> None:
    """Connect to RabbitMQ and start consuming (blocks until disconnected)."""
    params = pika.URLParameters(rabbitmq_url)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.exchange_declare(exchange=DLX_EXCHANGE, exchange_type="direct", durable=True)
    channel.queue_declare(queue=DLQ_NAME, durable=True)
    channel.queue_bind(queue=DLQ_NAME, exchange=DLX_EXCHANGE, routing_key=queue_name)
    channel = _declare_primary_queue(channel, queue_name)
    channel.basic_qos(prefetch_count=prefetch_count)
    channel.basic_consume(queue=queue_name, on_message_callback=process_message)
    logger.info(
        "Ingestor ready — waiting for defects on queue '%s' with prefetch_count=%s…",
        queue_name,
        prefetch_count,
    )
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
