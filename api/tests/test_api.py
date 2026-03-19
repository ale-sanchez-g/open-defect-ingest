"""Unit tests for the FastAPI defect ingest service."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app  # noqa: E402

client = TestClient(app)


# ── /health ───────────────────────────────────────────────────────────────────


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── /queue/stats ──────────────────────────────────────────────────────────────


def test_queue_stats_returns_data():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "messages": 7,
        "messages_ready": 5,
        "messages_unacknowledged": 2,
        "consumers": 1,
        "state": "running",
    }
    with patch("main.requests.get", return_value=mock_resp):
        resp = client.get("/queue/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"] == 7
    assert data["state"] == "running"
    assert data["consumers"] == 1


def test_queue_stats_returns_error_key_on_failure():
    with patch("main.requests.get", side_effect=ConnectionError("refused")):
        resp = client.get("/queue/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert data["messages"] == 0


# ── /defects/ingest ───────────────────────────────────────────────────────────


def test_ingest_defect_queues_message():
    mock_channel = MagicMock()
    mock_conn = MagicMock()
    mock_conn.channel.return_value = mock_channel

    with patch("main.pika.BlockingConnection", return_value=mock_conn):
        resp = client.post(
            "/defects/ingest",
            json={
                "id": "bug-42",
                "title": "Login broken",
                "description": "Users get 500",
                "project": "auth",
                "severity": "high",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["defect_id"] == "bug-42"
    mock_channel.basic_publish.assert_called_once()


def test_ingest_defect_missing_required_fields():
    resp = client.post("/defects/ingest", json={"title": "No project"})
    assert resp.status_code == 422  # Pydantic validation error


def test_ingest_defect_returns_500_when_rabbitmq_unavailable():
    with patch(
        "main.pika.BlockingConnection",
        side_effect=Exception("RabbitMQ unavailable"),
    ):
        resp = client.post(
            "/defects/ingest",
            json={"title": "Bug", "description": "desc", "project": "x"},
        )
    assert resp.status_code == 500


# ── /defects/query ────────────────────────────────────────────────────────────


def test_query_defects_returns_results():
    mock_embed_resp = MagicMock()
    mock_embed_resp.json.return_value = {"embedding": [0.1, 0.2, 0.3]}

    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "ids": [["id1", "id2"]],
        "documents": [["Login fails on mobile", "OAuth token expired"]],
        "metadatas": [[{"title": "Mobile login bug"}, {"title": "Token expiry"}]],
        "distances": [[0.05, 0.12]],
    }

    with patch("main.requests.post", return_value=mock_embed_resp), patch(
        "main.chromadb.HttpClient"
    ) as mock_client:
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        resp = client.post("/defects/query", json={"query": "login issues", "n_results": 2})

    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "login issues"
    assert len(data["results"]) == 2
    assert data["results"][0]["id"] == "id1"
    assert data["results"][0]["distance"] == pytest.approx(0.05)


def test_query_defects_empty_collection():
    mock_embed_resp = MagicMock()
    mock_embed_resp.json.return_value = {"embedding": [0.0]}

    mock_collection = MagicMock()
    mock_collection.query.return_value = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    with patch("main.requests.post", return_value=mock_embed_resp), patch(
        "main.chromadb.HttpClient"
    ) as mock_client:
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        resp = client.post("/defects/query", json={"query": "nonexistent"})

    assert resp.status_code == 200
    assert resp.json()["results"] == []


# ── /defects/summary ─────────────────────────────────────────────────────────


def test_summary_returns_no_defects_when_empty():
    mock_collection = MagicMock()
    mock_collection.count.return_value = 0

    with patch("main.chromadb.HttpClient") as mock_client:
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        resp = client.get("/defects/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert "No defects" in data["summary"]


def test_summary_calls_llm_and_returns_text():
    mock_collection = MagicMock()
    mock_collection.count.return_value = 3
    mock_collection.get.return_value = {
        "documents": ["Bug A", "Bug B", "Bug C"],
        "ids": ["1", "2", "3"],
    }

    mock_llm_resp = MagicMock()
    mock_llm_resp.json.return_value = {"response": "Three memory leaks detected."}

    with patch("main.chromadb.HttpClient") as mock_client, patch(
        "main.requests.post", return_value=mock_llm_resp
    ):
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        resp = client.get("/defects/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert "memory leaks" in data["summary"].lower()


# ── /defects/list ─────────────────────────────────────────────────────────────


def test_list_defects_returns_paginated_data():
    mock_collection = MagicMock()
    mock_collection.count.return_value = 2
    mock_collection.get.return_value = {
        "ids": ["id1", "id2"],
        "documents": ["Bug one description", "Bug two description"],
        "metadatas": [{"title": "Bug One", "project": "api"}, {"title": "Bug Two", "project": "ui"}],
    }

    with patch("main.chromadb.HttpClient") as mock_client:
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        resp = client.get("/defects/list?limit=10&offset=0")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["defects"]) == 2
    assert data["defects"][0]["id"] == "id1"
    assert data["defects"][0]["metadata"]["title"] == "Bug One"
