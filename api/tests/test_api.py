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


def test_config_reports_feature_flags():
    with patch("main.USE_LANGGRAPH_QUERY", False), patch("main.USE_LANGGRAPH_SUMMARY", True), patch(
        "main.GRAPH_TRACE_ENABLED", True
    ), patch("main.PROMPT_STRATEGY", "local_versioned"), patch("main.PROMPT_VERSION", "1.0.0"), patch(
        "main.PROMPT_SOURCE", "local"
    ):
        resp = client.get("/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["features"]["use_langgraph_query"] is False
    assert data["features"]["use_langgraph_summary"] is True
    assert data["features"]["graph_trace_enabled"] is True
    assert data["prompts"]["strategy"] == "local_versioned"
    assert data["prompts"]["version"] == "1.0.0"
    assert data["prompts"]["source"] == "local"


def test_trace_node_records_timing_and_trace_event():
    from main import _trace_node

    def _node_fn(_state):
        return {"analysis": "ok"}

    wrapped = _trace_node("query.synthesize", _node_fn)
    out = wrapped({"trace_id": "", "node_timings_ms": {}, "trace_events": []})

    assert out["trace_id"]
    assert "query.synthesize" in out["node_timings_ms"]
    assert out["node_timings_ms"]["query.synthesize"] >= 0
    assert out["trace_events"][0]["node"] == "query.synthesize"


def test_render_prompt_injects_variables():
    from main import _render_prompt

    with patch.dict("main.PROMPTS", {"query_synthesis": "route={route} query={query}"}, clear=False):
        rendered = _render_prompt("query_synthesis", route="risk_focus", query="payment failed")

    assert rendered == "route=risk_focus query=payment failed"


def test_normalize_opm_docs_url_to_api_root():
    from main import _normalize_opm_api_base_url

    assert _normalize_opm_api_base_url("http://localhost:8001/api/docs") == "http://localhost:8001/api"
    assert _normalize_opm_api_base_url("http://localhost:8001") == "http://localhost:8001/api"


def test_opm_candidates_include_docker_host_for_localhost():
    from main import _opm_api_candidates

    candidates = _opm_api_candidates("http://localhost:8001/api/docs")
    assert "http://localhost:8001/api" in candidates
    assert "http://host.docker.internal:8001/api" in candidates


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


def test_query_uses_langgraph_when_flag_enabled():
    graph_payload = {
        "query": "login issues",
        "results": [{"id": "id-1", "document": "doc", "metadata": {"title": "Bug"}, "distance": None}],
        "route": "semantic_search",
        "analysis": "Likely auth-session defects.",
    }
    with patch("main.USE_LANGGRAPH_QUERY", True), patch(
        "main._run_langgraph_query", return_value=graph_payload
    ):
        resp = client.post("/defects/query", json={"query": "login issues", "n_results": 1})

    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "semantic_search"
    assert data["analysis"]
    assert data["results"][0]["id"] == "id-1"


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


def test_summary_uses_langgraph_when_flag_enabled():
    mock_collection = MagicMock()
    mock_collection.count.return_value = 2

    with patch("main.USE_LANGGRAPH_SUMMARY", True), patch(
        "main.chromadb.HttpClient"
    ) as mock_client, patch("main._run_langgraph_summary", return_value="Graph summary output"):
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        resp = client.get("/defects/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["summary"] == "Graph summary output"


class _ImmediateThread:
    def __init__(self, target, args=(), daemon=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def test_summary_job_endpoints_complete_and_return_result():
    mock_collection = MagicMock()
    mock_collection.count.return_value = 2

    with patch("main.threading.Thread", _ImmediateThread), patch(
        "main.chromadb.HttpClient"
    ) as mock_client, patch("main._run_langgraph_summary", return_value="Async graph summary"):
        mock_client.return_value.get_or_create_collection.return_value = mock_collection
        created = client.post("/defects/summary/jobs")

    assert created.status_code == 200
    job_id = created.json()["job_id"]

    status_resp = client.get(f"/defects/summary/jobs/{job_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "completed"

    result_resp = client.get(f"/defects/summary/jobs/{job_id}/result")
    assert result_resp.status_code == 200
    result_data = result_resp.json()
    assert result_data["status"] == "completed"
    assert result_data["result"]["summary"] == "Async graph summary"


def test_summary_job_status_404_for_unknown_job():
    resp = client.get("/defects/summary/jobs/does-not-exist")
    assert resp.status_code == 404


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
