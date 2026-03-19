"""Unit tests for the ingestor service."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Allow importing main.py from the parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import get_embedding, process_message, store_defect  # noqa: E402


# ── get_embedding ─────────────────────────────────────────────────────────────


def test_get_embedding_returns_vector():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"embedding": [0.1, 0.2, 0.3]}

    with patch("main.requests.post", return_value=mock_resp) as mock_post:
        result = get_embedding("login failure")

    assert result == [0.1, 0.2, 0.3]
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "api/embeddings" in call_kwargs[0][0]
    assert call_kwargs[1]["json"]["prompt"] == "login failure"


def test_get_embedding_raises_on_http_error():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("HTTP 500")

    with patch("main.requests.post", return_value=mock_resp):
        with pytest.raises(Exception, match="HTTP 500"):
            get_embedding("some text")


# ── store_defect ──────────────────────────────────────────────────────────────


def test_store_defect_calls_chroma_add():
    defect = {
        "id": "bug-001",
        "title": "Memory leak",
        "description": "RSS grows unboundedly",
        "project": "backend",
        "severity": "high",
        "status": "open",
    }

    mock_collection = MagicMock()

    with patch("main.get_embedding", return_value=[0.1, 0.2]) as mock_embed, patch(
        "main.chromadb.HttpClient"
    ) as mock_client:
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        store_defect(defect)

    mock_embed.assert_called_once()
    embedded_text = mock_embed.call_args[0][0]
    assert "Memory leak" in embedded_text
    assert "backend" in embedded_text

    mock_collection.add.assert_called_once()
    add_kwargs = mock_collection.add.call_args[1]
    assert add_kwargs["ids"] == ["bug-001"]
    assert add_kwargs["embeddings"] == [[0.1, 0.2]]
    assert add_kwargs["metadatas"][0]["severity"] == "high"


def test_store_defect_generates_id_when_missing():
    defect = {"title": "No-id bug", "description": "desc", "project": "x"}
    mock_collection = MagicMock()

    with patch("main.get_embedding", return_value=[0.0]), patch(
        "main.chromadb.HttpClient"
    ) as mock_client:
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        store_defect(defect)

    ids = mock_collection.add.call_args[1]["ids"]
    assert len(ids) == 1
    assert ids[0]  # not empty


# ── process_message ───────────────────────────────────────────────────────────


def _make_mq_mocks():
    ch = MagicMock()
    method = MagicMock()
    method.delivery_tag = 42
    props = MagicMock()
    return ch, method, props


def test_process_message_success():
    defect = {
        "id": "d-100",
        "title": "NullPointer",
        "description": "NPE in PaymentService",
        "project": "payments",
    }
    ch, method, props = _make_mq_mocks()

    with patch("main.store_defect") as mock_store:
        process_message(ch, method, props, json.dumps(defect).encode())

    mock_store.assert_called_once_with(defect)
    ch.basic_ack.assert_called_once_with(delivery_tag=42)
    ch.basic_nack.assert_not_called()


def test_process_message_nacks_on_invalid_json():
    ch, method, props = _make_mq_mocks()

    process_message(ch, method, props, b"not { valid json }")

    ch.basic_nack.assert_called_once_with(delivery_tag=42, requeue=False)
    ch.basic_ack.assert_not_called()


def test_process_message_nacks_on_store_failure():
    defect = {"id": "d-200", "title": "Crash", "description": "boom", "project": "core"}
    ch, method, props = _make_mq_mocks()

    with patch("main.store_defect", side_effect=RuntimeError("ChromaDB down")):
        process_message(ch, method, props, json.dumps(defect).encode())

    ch.basic_nack.assert_called_once_with(delivery_tag=42, requeue=False)
    ch.basic_ack.assert_not_called()
