# Functional Test Suite (Legacy Paths, No LangChain)

## Goal
Validate that the application works end-to-end with legacy AI paths before enabling LangGraph/LangChain flags.

## How to run the tests

Use this exact sequence to reproduce the same process (API + UI + Datadog) in another agent/chat.

### 1) Start in legacy mode
Run from repo root:

```bash
export USE_LANGGRAPH_QUERY=false
export USE_LANGGRAPH_SUMMARY=false
export DD_ENV=local
docker compose up -d --build
docker compose ps
```

Expected: all services are `Up` (especially `api`, `ingestor`, `ui`, `rabbitmq`, `chromadb`, `ollama`, `datadog-agent`).

### 2) Execute API functional pass (TC-01..TC-10)
Preferred approach is a single scripted run that writes a JSON artifact:

```bash
python3 /tmp/rerun_api.py > /tmp/rerun_api_stdout.txt 2>&1
cat /tmp/rerun_api_results.json
```

Required artifact shape:
- `run_tag`
- `ids`
- `TC-01` through `TC-10`

Pass criteria for this repo baseline:
- TC-01..TC-08 return `200`.
- TC-09 may return `500` around 120s (known timeout baseline).
- TC-10 may fail around 180s (known timeout baseline).

### 3) Execute UI validation pass (TC-11..TC-14)
Open UI at `http://localhost:3000` and validate in this order:
1. Queue Dashboard renders with queue counters.
2. Ingest Defect tab can submit one test defect (for reruns, use an id like `RERUN-UI-1`).
3. Query tab returns non-empty results.
4. AI Summary tab is triggered and observed long enough to capture success or timeout behavior.

Capture:
- Network outcomes for `/api/queue/stats`, `/api/defects/ingest`, `/api/defects/query`, summary endpoints.
- Browser console error count.

### 4) Execute Datadog validation pass (TC-15..TC-17)
Use these queries as the baseline checks:

- Logs:
	- `(service:open-defect-api OR service:open-defect-ingestor) env:local ("LEGACY-TC-A" OR "LEGACY-TC-B" OR "LEGACY-UI-1" OR "RERUN")`
- APM spans:
	- `service:open-defect-ingestor operation_name:ingestor.process_message`
	- `service:open-defect-ingestor operation_name:ai.ollama.embeddings`
	- `service:open-defect-api operation_name:ai.ollama.completion`
	- `service:open-defect-api operation_name:fastapi.request @http.status_code:500`
	- `service:open-defect-api operation_name:fastapi.request @http.status_code:200 @http.path_group:/defects/query`
- Metrics prefix:
	- `open_defect_ingest.`

### 5) MCP tool sequence to replicate in another chat
Run the same orchestration pattern used in this conversation:

1. `run_in_terminal`
	- Start stack in legacy mode, run API script, and collect JSON artifact.
2. Browser tools (`open_browser_page`, then interaction/capture tools)
	- Validate UI queue/ingest/query/summary behavior and collect network + console evidence.
3. Datadog APM tool (`mcp_datadog_search_datadog_spans`)
	- Validate success spans and summary-timeout error traces.
4. Datadog logs/metrics tools (from activated Datadog tool groups)
	- Validate log markers and custom metrics presence.
5. `apply_patch`
	- Update this report with latest run summary and evidence snapshot.

### 6) Update report consistently
When writing rerun results, keep this format:
1. Update/append a dated summary block with `PASS`, `FAIL`, `KNOWN_BASELINE`, `TOTAL`.
2. Include run identifiers (`run_tag`, defect ids, async `job_id`).
3. Include at least one API timeout evidence line for TC-09/TC-10 if reproduced.
4. Include at least one UI evidence line and one Datadog evidence line.


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

## Previous Run Summary (2026-03-23)

| Category | Count | Notes |
|---|---:|---|
| PASS | 14 | API core, ingest pipeline, UI queue/ingest/query, Datadog logs/APM/metrics validated. |
| FAIL | 1 | TC-10 async summary job still fails on Ollama read timeout at ~180s. |
| KNOWN_BASELINE | 2 | TC-09 and TC-14 summary timeout behavior captured as baseline risk. |
| TOTAL | 17 | Full suite executed, including second focused isolation pass. |

## Latest Rerun Summary (2026-03-23, run_tag=RERUN-053600)

| Category | Count | Notes |
|---|---:|---|
| PASS | 14 | TC-01..TC-08 and TC-11..TC-13 plus TC-15..TC-17 validated again in rerun. |
| FAIL | 1 | TC-10 async summary job `65bf6b29-a092-429a-a3f0-56cf5c12b0a0` failed with Ollama read timeout at 180s. |
| KNOWN_BASELINE | 2 | TC-09 and TC-14 summary timeout behavior reproduced in rerun. |
| TOTAL | 17 | Full suite rerun after latest code changes. |

### Rerun Evidence Snapshot
- API rerun artifact (`/tmp/rerun_api_results.json`):
	- TC-01..TC-08 returned HTTP `200`.
	- TC-09 returned HTTP `500` after `120.42s` with `read timeout=120`.
	- TC-10 async summary job failed (`status=failed`) and result endpoint returned HTTP `500` with `read timeout=180`.
- UI rerun checks:
	- Queue Dashboard loaded and updated.
	- Ingest success confirmed for `RERUN-UI-1`.
	- Query tab returned results.
	- Summary tab stayed in `Analyzing...` during observation window (consistent with summary timeout baseline).
	- Browser console errors observed: `0`.
- Datadog rerun validation:
	- Ingestor spans present for rerun defects: `ingestor.process_message` showed `defect.id=RERUN-053600-A` and `defect.id=RERUN-UI-1`.
	- API success traces present: `POST /defects/query` with HTTP `200`.
	- API error traces present: `GET /defects/summary` and `GET /defects/summary/jobs/{job_id}/result` with HTTP `500`.
	- AI spans remained present for rerun traffic: `ai.ollama.embeddings` and `ai.ollama.completion`.

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

