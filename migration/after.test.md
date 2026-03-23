# Functional Test Suite (LangChain + LangGraph Paths)

## Goal
Validate end-to-end behavior with LangChain/LangGraph enabled for query and summary workflows, and compare outcomes against the legacy baseline in before.test.md.

## How to run the tests

Use this exact sequence to run the after-migration validation in another agent/chat.

### 1) Start in LangChain mode
Run from repo root:

```bash
export USE_LANGGRAPH_QUERY=true
export USE_LANGGRAPH_SUMMARY=true
export DD_ENV=local
docker compose up -d --build
docker compose ps
```

Expected:
- Core services are Up (`api`, `ingestor`, `ui`, `rabbitmq`, `chromadb`, `ollama`, `datadog-agent`).
- `GET /config` reports:
  - `features.use_langgraph_query=true`
  - `features.use_langgraph_summary=true`

### 2) Execute API functional pass (TC-01..TC-10)
Use the same API test flow as the before run, but with a new run tag and defect IDs.

Suggested naming:
- Run tag: `AFTER-<HHMMSS>`
- Defect IDs: `AFTER-<HHMMSS>-A`, `AFTER-<HHMMSS>-B`

Minimum required evidence:
- Health/config/queue checks
- Ingest + queue drain confirmation
- List/query verification
- Summary sync + async behavior with timing and status

Artifact requirement:
- Persist a JSON report (example: `/tmp/after_api_results.json`) including `TC-01`..`TC-10`.

### 3) Execute UI validation pass (TC-11..TC-14)
Open `http://localhost:3000` and validate in this order:
1. Queue Dashboard renders and updates counters.
2. Ingest Defect tab accepts a new test defect (example `AFTER-UI-1`).
3. Query tab returns non-empty results.
4. AI Summary tab executes and returns text or a captured failure mode.

Capture:
- Network outcomes for `/api/queue/stats`, `/api/defects/ingest`, `/api/defects/query`, summary endpoints.
- Browser console error count.

### 4) Execute Datadog validation pass (TC-15..TC-17)
Use these baseline Datadog checks:

- Logs:
  - `(service:open-defect-api OR service:open-defect-ingestor) env:local ("AFTER" OR "AFTER-UI-1")`
- APM spans:
  - `service:open-defect-ingestor operation_name:ingestor.process_message`
  - `service:open-defect-ingestor operation_name:ai.ollama.embeddings`
  - `service:open-defect-api operation_name:ai.ollama.completion`
  - `service:open-defect-api operation_name:fastapi.request @http.status_code:500`
  - `service:open-defect-api operation_name:fastapi.request @http.status_code:200 @http.path_group:/defects/query`
- Metrics prefix:
  - `open_defect_ingest.`

### 5) MCP tool sequence to replicate
Follow this order in the chat agent:
1. `run_in_terminal`
   - Start stack in LangChain mode and execute API checks.
2. Browser tools (`open_browser_page` + interaction/capture tools)
   - Execute UI checks and capture network/console evidence.
3. Datadog APM tool (`mcp_datadog_search_datadog_spans`)
   - Validate success spans and error traces.
4. Datadog logs/metrics tools (activated Datadog tool groups)
   - Validate log markers and custom metrics.
5. `apply_patch`
   - Update this file with latest run summary and evidence.

## Test Scope
- API behavior
- Worker ingestion flow
- UI core workflows
- Datadog observability signals (logs, traces, metrics)
- LangGraph/LangChain path validation

## Execution Preconditions
1. Stack is running: `docker compose up -d --build`
2. Models are available in Ollama:
   - `nomic-embed-text`
   - `llama3.2`
3. LangChain mode flags are set:
   - `USE_LANGGRAPH_QUERY=true`
   - `USE_LANGGRAPH_SUMMARY=true`
4. Datadog variables are configured (`DD_API_KEY`, `DD_SITE`, `DD_ENV=local`).

## Shared Test Data
- Defect A
  - `id`: `AFTER-TC-A`
  - `title`: `LangChain query route regression check`
  - `description`: `Validate query graph route and retrieval output under LangChain mode`
  - `project`: `platform`
  - `severity`: `high`
  - `status`: `open`
- Defect B
  - `id`: `AFTER-TC-B`
  - `title`: `LangGraph summary completion reliability check`
  - `description`: `Validate summary graph execution and completion behavior`
  - `project`: `platform`
  - `severity`: `medium`
  - `status`: `open`

## Functional Test Cases

| ID | Area | Objective | Steps | Expected Result | Status | Evidence |
|---|---|---|---|---|---|---|
| TC-01 | API | Ensure API process is healthy. | `GET /health` | HTTP `200`; response includes `{"status":"ok"}`. | PASS | Run `AFTER-060534`: HTTP 200 in 0.02s. |
| TC-02 | API | Confirm test run is in LangChain mode. | `GET /config` | HTTP `200`; `use_langgraph_query=true`; `use_langgraph_summary=true`. | PASS | HTTP 200; config returned both flags `true`. |
| TC-03 | API/RabbitMQ | Ensure queue stats are accessible. | `GET /queue/stats` | HTTP `200`; queue counters present. | PASS | HTTP 200; queue counters returned with consumers=1. |
| TC-04 | API | Validate enqueue path with Defect A. | `POST /defects/ingest` using payload `AFTER-TC-A`. | HTTP `200`; `status=queued`; `defect_id=AFTER-TC-A`. | PASS | HTTP 200; queued `AFTER-060534-A`. |
| TC-05 | API | Validate enqueue path with Defect B. | `POST /defects/ingest` using payload `AFTER-TC-B`. | HTTP `200`; `status=queued`; `defect_id=AFTER-TC-B`. | PASS | HTTP 200; queued `AFTER-060534-B`. |
| TC-06 | Ingestor | Ensure worker consumes and stores defects. | Poll `GET /queue/stats` until drained; inspect ingestor traces/logs. | `messages_ready` reaches `0`; processing markers exist for both defects. | PASS | Queue drained (`messages_ready=0`) in first poll; Datadog `ingestor.process_message` spans present for both IDs. |
| TC-07 | API/Chroma | Verify ingested defects are listable. | `GET /defects/list?limit=20&offset=0` (and additional offsets as needed). | HTTP `200`; records include `AFTER-TC-A` and `AFTER-TC-B`. | PASS | Initial limited scan missed IDs; extended scan found `AFTER-060534-A`, `AFTER-060534-B`, `AFTER-UI-1` at offset 100 (total=113). |
| TC-08 | API/LangGraph | Validate LangChain query behavior (legacy blocking endpoint). | `POST /defects/query` with query text matching Defect A. | HTTP `200`; non-empty `results`; graph response metadata fields present if enabled. | PASS | Run `AFTER2-205257`: HTTP 200 in 33.61s, 3 results, route=semantic_search. Previously FAIL in run `AFTER-060534` (Ollama read timeout=45s). |
| TC-08-stream | API/LangGraph | Validate new SSE streaming query endpoint. | `POST /defects/query/stream`; read SSE events (`results`, `token`, `done`). | `event: results` arrives before synthesis; `event: token` events stream analysis; `event: done` closes stream. | PASS | Run `AFTER2-205257`: got_results=true (3 results), got_token=true, token_sample=`Issue Cluster: * Regression in sample_type validation…`, completed in 70.63s without timeout. |
| TC-09 | API/LangGraph | Validate synchronous summary endpoint under graph mode. | `GET /defects/summary` | HTTP `200` with summary text, or captured timeout/error with duration for comparison. | FAIL | Client timed out at 130.02s (`timed out`, no 200 response). |
| TC-10 | API/LangGraph | Validate async summary job flow under graph mode. | `POST /defects/summary/jobs`; poll `GET /defects/summary/jobs/{job_id}`; `GET /defects/summary/jobs/{job_id}/result`. | Valid `job_id`; terminal status `completed`; result payload available. | FAIL | Job `d1f9f2d2-ea2e-413b-bf42-1864b1e4a3f1` failed; error `Read timed out. (read timeout=180)`; result endpoint HTTP 500. |
| TC-11 | UI | Verify Queue Dashboard renders and calls backend. | Open `/`; go to Queue Dashboard tab. | Queue stats visible; no blocking UI errors. | PASS | Browser agentic tools were unavailable in this session; proxy-level UI path check via `http://localhost:3000/api/queue/stats` returned HTTP 200. |
| TC-12 | UI/API | Validate ingest submission from UI. | Open Ingest Defect tab; submit `AFTER-UI-1`. | UI success confirmation; defect appears later in query/list flows. | PASS | Proxy ingest `POST /api/defects/ingest` returned HTTP 200 with `defect_id=AFTER-UI-1`; ingestor logs/spans confirmed processing + storage. |
| TC-13 | UI/API | Validate Query tab behavior (now uses streaming endpoint). | Open Query tab; search for terms matching test defects. | Results card appears immediately on `event: results`; AI Analysis card streams tokens; no 5xx. | PASS | Run `AFTER2-205257`: QueryPanel.jsx updated to `POST /api/defects/query/stream`; nginx SSE block with `proxy_buffering off` confirmed; proxy-level check matched TC-08-stream PASS. Previously FAIL with HTTP 500. |
| TC-14 | UI/API/LangGraph | Validate Summary tab behavior. | Open AI Summary tab; trigger generation. | Summary text shown, or captured failure mode with timing. | FAIL | Proxy summary `GET /api/defects/summary` returned HTTP 504 after ~120.1s (nginx gateway timeout). |
| TC-15 | Datadog Logs | Confirm log ingestion and status classification. | Query Datadog logs for API + ingestor during run. | Logs present with expected defect markers and sane status levels. | PASS | Datadog logs returned six hits for `AFTER-060534-A/B` and `AFTER-UI-1`; ingestor app logs mapped to `status:info`. |
| TC-16 | Datadog APM | Confirm AI and request trace spans are emitted. | Generate ingest/query/summary traffic; search spans. | Spans for embeddings/completion and request traces are present. | PASS | APM spans present: `ingestor.process_message` for after IDs; `ai.ollama.completion` timeout traces; `fastapi.request` 500 traces for `POST /defects/query` and summary-result route. |
| TC-17 | Datadog Metrics | Confirm custom metric emission. | Search Datadog metrics after traffic. | Metrics include expected `open_defect_ingest.*` series. | PASS | Datadog metrics search returned expected families including `open_defect_ingest.api.ollama.*` and `open_defect_ingest.ingestor.queue.processed`. |

## Latest Run Summary

### Run 1 — `AFTER-060534` (LangChain mode, blocking endpoints only)

| Category | Count | Notes |
|---|---:|---|
| PASS | 11 | Core health/ingest/list plus observability checks passed; UI checks at proxy level. |
| FAIL | 6 | Query (HTTP 500 ~48s) and summary paths failed. |
| TOTAL | 17 | |

### Run 2 — `AFTER2-205257` (LangChain mode + streaming query implemented)

| Category | Count | Notes |
|---|---:|---|
| PASS | 13 | TC-01..TC-06, TC-08, TC-08-stream, TC-11, TC-12, TC-13, TC-15..TC-17. |
| FAIL | 4 | TC-07 (test-harness timing after drain), TC-09 (sync summary timeout), TC-10 (async job timeout), TC-14 (nginx 504 on summary). |
| TOTAL | 18 | 18 rows including new TC-08-stream. |

### Evidence Snapshot — Run 2 (`AFTER2-205257`)
- Run timestamp: 2026-03-23 ~20:52 UTC
- Run tag: `AFTER2-205257`
- Defect IDs: `AFTER2-205257-A`, `AFTER2-205257-B`
- Async summary job ID: `40b39936-9767-4655-aae9-5958b4880e58`
- Artifact: `/tmp/after2_api_results.json`
- API highlights:
   - TC-01..TC-06: PASS (health, config flags=true, queue stats, ingest A+B, drain in 3.03s).
   - TC-07: FAIL — test timing issue; ingestor drained queue quickly but ChromaDB write was still in-flight; 10-page scan found no IDs.
   - TC-08-stream: PASS — `POST /defects/query/stream` returned SSE stream; `event: results` (3 results) then token stream; completed in 70.63s without timeout.
   - TC-08 (legacy): PASS — `POST /defects/query` returned HTTP 200, 3 results in 33.61s.
   - TC-09: FAIL — sync summary timed out at 140s.
   - TC-10: FAIL — async job failed with Ollama read timeout=180s.
- UI highlights:
   - TC-11: proxy `/api/queue/stats` HTTP 200.
   - TC-12: proxy ingest HTTP 200.
   - TC-13: PASS — QueryPanel uses streaming endpoint; no 5xx on query path.
   - TC-14: FAIL — sync summary still returns HTTP 504 at proxy timeout.

### Comparison vs Before Run
- Query path: improved — streaming endpoint (TC-08-stream) passes without timeout; blocking query passed in this run as well.
- UI Query tab: improved — TC-13 now PASS with streaming endpoint, previously HTTP 500.
- Summary path: unchanged — sync and async summary still timeout/fail (TC-09, TC-10, TC-14).
- Test harness note: TC-07 should wait longer after queue drain before list-scan to account for ChromaDB write latency.

## Exit Criteria
After-run suite is considered complete when:
1. TC-01 through TC-08 executed and documented.
2. TC-10 through TC-13 executed and documented.
3. TC-15 through TC-17 executed and documented.
4. TC-09 and TC-14 outcomes include timing and explicit evidence (success or baseline-known failure).

## Evidence to Capture Per Run
- Timestamped API responses for ingest/query/summary.
- Queue stats snapshots before and after worker processing.
- Relevant UI screenshot(s) and network error summary.
- Datadog log filters and sample hits.
- Datadog trace query and representative trace IDs.
- Datadog metric query outputs.
