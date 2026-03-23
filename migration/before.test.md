# Functional Test Suite (Legacy Paths, No LangChain)

## Goal
Validate that the application works end-to-end with legacy AI paths before enabling LangGraph/LangChain flags.

## Test Scope
- API behavior
- Worker ingestion flow
- UI core workflows
- Datadog observability signals (logs, traces, metrics)

## Execution Preconditions
1. Stack is running: `docker compose up -d --build`
2. Models are available in Ollama:
	 - `nomic-embed-text`
	 - `llama3.2`
3. Legacy mode flags are set:
	 - `USE_LANGGRAPH_QUERY=false`
	 - `USE_LANGGRAPH_SUMMARY=false`
4. Datadog variables are configured (`DD_API_KEY`, `DD_SITE`, `DD_ENV=local`).

## Shared Test Data
- Defect A
	- `id`: `LEGACY-TC-A`
	- `title`: `API returns 502 behind nginx`
	- `description`: `Users receive 502 when API upstream is stale`
	- `project`: `platform`
	- `severity`: `high`
	- `status`: `open`
- Defect B
	- `id`: `LEGACY-TC-B`
	- `title`: `Ollama runner startup timeout`
	- `description`: `Model runner timed out waiting for startup`
	- `project`: `platform`
	- `severity`: `medium`
	- `status`: `open`

## Functional Test Cases

| ID | Area | Objective | Steps | Expected Result | Status | Evidence |
|---|---|---|---|---|---|---|
| TC-01 | API | Ensure API process is healthy. | `GET /health` | HTTP `200`; response includes `{"status":"ok"}`. | PASS | 2026-03-23 run: HTTP 200 with `{"status":"ok"}`. |
| TC-02 | API | Confirm test run is in legacy mode. | `GET /config` | HTTP `200`; `use_langgraph_query=false`; `use_langgraph_summary=false`. | PASS | 2026-03-23 run: config returned both legacy flags as `false`. |
| TC-03 | API/RabbitMQ | Ensure queue stats are accessible. | `GET /queue/stats` | HTTP `200`; queue counters present (`messages`, `messages_ready`). | PASS | 2026-03-23 run: HTTP 200 with queue counters (`messages=0`, `messages_ready=0`, `consumers=1`). |
| TC-04 | API | Validate enqueue path with Defect A. | `POST /defects/ingest` using payload `LEGACY-TC-A`. | HTTP `200`; `status=queued`; `defect_id=LEGACY-TC-A`. | PASS | 2026-03-23 run: HTTP 200, queued `LEGACY-TC-A`. |
| TC-05 | API | Validate enqueue path with Defect B. | `POST /defects/ingest` using payload `LEGACY-TC-B`. | HTTP `200`; `status=queued`; `defect_id=LEGACY-TC-B`. | PASS | 2026-03-23 run: HTTP 200, queued `LEGACY-TC-B`. |
| TC-06 | Ingestor | Ensure worker consumes and stores defects. | Poll `GET /queue/stats` until drained; inspect ingestor logs. | `messages_ready` trends to `0`; logs include `Processing defect id=LEGACY-TC-A` and `Processing defect id=LEGACY-TC-B`; successful storage lines present. | PASS | Ingestor logs show processing and stored-success lines for `LEGACY-TC-A`, `LEGACY-TC-B`, and `LEGACY-UI-1`; queue drained to 0. |
| TC-07 | API/Chroma | Verify ingested defects are queryable by list endpoint. | `GET /defects/list?page=1&page_size=50` | HTTP `200`; records include `LEGACY-TC-A` and `LEGACY-TC-B`. | PASS | Second pass isolated request-shape issue: endpoint contract is `limit`/`offset` (not `page`/`page_size`). Using `/defects/list?limit=20&offset=80/100` returned `LEGACY-TC-A`, `LEGACY-TC-B`, `LEGACY-UI-1`. |
| TC-08 | API | Validate legacy semantic query behavior. | `POST /defects/query` with `query="stale nginx upstream 502"`, `n_results=3`. | HTTP `200`; non-empty `results`; no graph-only fields required. | PASS | HTTP 200 in 2.19s; top result returned `LEGACY-TC-A`. |
| TC-09 | API/AI | Validate legacy synchronous summary endpoint. | `GET /defects/summary` | HTTP `200` in nominal runs; generated summary text present. If timeout, record duration/error as known baseline behavior. | KNOWN_BASELINE | Returned HTTP 500 after ~120.62s: Ollama read timeout (`read timeout=120`). |
| TC-10 | API/AI | Validate async summary job flow. | `POST /defects/summary/jobs`; poll `GET /defects/summary/jobs/{job_id}`; `GET /defects/summary/jobs/{job_id}/result`. | Valid `job_id`; terminal status `completed`; result payload available. | FAIL | Reproduced in focused pass: job `7d08d86b-7815-4f32-9d0f-7da417b077e1` failed after ~180s (`HTTPConnectionPool(host='ollama', port=11434): Read timed out. (read timeout=180)`), and result endpoint returned HTTP 500. |
| TC-11 | UI | Verify Queue Dashboard renders and calls backend. | Open `/`; go to Queue Dashboard tab. | Queue stats visible; no blocking console/network errors. | PASS | Playwright: queue stats rendered (`Total Messages 0`, `Consumers 1`); console errors=0. |
| TC-12 | UI/API | Validate ingest submission from UI. | Open Ingest Defect tab; submit `LEGACY-UI-1`. | UI success confirmation; defect appears later in list/query flows. | PASS | Playwright displayed `Defect queued successfully! ID: LEGACY-UI-1`; ingestor logs + Datadog logs confirmed processing/stored. |
| TC-13 | UI/API | Validate Query tab behavior. | Open Query tab; search `timeout` or `502`. | Results displayed; no 5xx responses in browser network panel. | PASS | Playwright query returned 5 results including `API returns 502 behind nginx`; network showed POST `/api/defects/query` => 200. |
| TC-14 | UI/API/AI | Validate Summary tab behavior. | Open AI Summary tab; trigger generation. | Summary text shown; if timeout occurs, capture timestamp as known baseline issue. | KNOWN_BASELINE | UI remained `Analyzing…`; concurrent API/async summary paths timed out on Ollama during run. |
| TC-15 | Datadog Logs | Confirm log ingestion and status classification. | Query Datadog logs for API + ingestor during run; validate defect markers. | Logs present for both services; fresh app `[INFO]` lines show `status:info`. | PASS | Datadog logs show `LEGACY-TC-A/B` and `LEGACY-UI-1` events with `status:info`; API query logs also `status:info` for new entries. |
| TC-16 | Datadog APM | Confirm AI trace spans are emitted. | Generate ingest/query/summary traffic; search spans. | Spans `ai.ollama.embeddings` and `ai.ollama.completion` are present. | PASS | Focused pass confirmed spans with correct Datadog query syntax: `service:open-defect-ingestor operation_name:ai.ollama.embeddings` (count 6) and `service:open-defect-api operation_name:ai.ollama.completion` (count 21, including timeout error spans). |
| TC-17 | Datadog Metrics | Confirm custom metric emission. | Search Datadog metrics after test traffic. | Metrics include `open_defect_ingest.api.ollama.embedding.requests`, `open_defect_ingest.api.ollama.completion.requests`, `open_defect_ingest.ingestor.queue.processed`. | PASS | Datadog metrics search returned all expected custom metrics plus latency series. |

## Latest Run Summary (2026-03-23)

| Category | Count | Notes |
|---|---:|---|
| PASS | 14 | API core, ingest pipeline, UI queue/ingest/query, Datadog logs/APM/metrics validated. |
| FAIL | 1 | TC-10 async summary job still fails on Ollama read timeout at ~180s. |
| KNOWN_BASELINE | 2 | TC-09 and TC-14 summary timeout behavior captured as baseline risk. |
| TOTAL | 17 | Full suite executed, including second focused isolation pass. |

### Outstanding Issue
- TC-10 remains unresolved: async summary jobs fail with `HTTPConnectionPool(host='ollama', port=11434): Read timed out. (read timeout=180)`.

### Datadog Validation Queries Used
- Logs markers:
	- `(service:open-defect-api OR service:open-defect-ingestor) env:local ("LEGACY-TC-A" OR "LEGACY-TC-B" OR "LEGACY-UI-1")`
- APM spans:
	- `service:open-defect-ingestor operation_name:ai.ollama.embeddings`
	- `service:open-defect-api operation_name:ai.ollama.completion`
- Metrics prefix:
	- `open_defect_ingest.`

## Exit Criteria
Test suite is considered passed when:
1. TC-01 through TC-08 pass.
2. TC-10 through TC-13 pass.
3. TC-15 through TC-17 pass.
4. Any TC-09 or TC-14 timeout is documented as baseline-known behavior with evidence.

## Evidence to Capture Per Run
- Timestamped API responses for ingest/query/summary.
- Queue stats snapshots before and after worker processing.
- Relevant UI screenshot(s) and network error summary.
- Datadog log filters and sample hits.
- Datadog trace query and representative trace IDs.
- Datadog metric query outputs.

