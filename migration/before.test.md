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
| TC-01 | API | Ensure API process is healthy. | `GET /health` | HTTP `200`; response includes `{"status":"ok"}`. | TODO |  |
| TC-02 | API | Confirm test run is in legacy mode. | `GET /config` | HTTP `200`; `use_langgraph_query=false`; `use_langgraph_summary=false`. | TODO |  |
| TC-03 | API/RabbitMQ | Ensure queue stats are accessible. | `GET /queue/stats` | HTTP `200`; queue counters present (`messages`, `messages_ready`). | TODO |  |
| TC-04 | API | Validate enqueue path with Defect A. | `POST /defects/ingest` using payload `LEGACY-TC-A`. | HTTP `200`; `status=queued`; `defect_id=LEGACY-TC-A`. | TODO |  |
| TC-05 | API | Validate enqueue path with Defect B. | `POST /defects/ingest` using payload `LEGACY-TC-B`. | HTTP `200`; `status=queued`; `defect_id=LEGACY-TC-B`. | TODO |  |
| TC-06 | Ingestor | Ensure worker consumes and stores defects. | Poll `GET /queue/stats` until drained; inspect ingestor logs. | `messages_ready` trends to `0`; logs include `Processing defect id=LEGACY-TC-A` and `Processing defect id=LEGACY-TC-B`; successful storage lines present. | TODO |  |
| TC-07 | API/Chroma | Verify ingested defects are queryable by list endpoint. | `GET /defects/list?page=1&page_size=50` | HTTP `200`; records include `LEGACY-TC-A` and `LEGACY-TC-B`. | TODO |  |
| TC-08 | API | Validate legacy semantic query behavior. | `POST /defects/query` with `query="stale nginx upstream 502"`, `n_results=3`. | HTTP `200`; non-empty `results`; no graph-only fields required. | TODO |  |
| TC-09 | API/AI | Validate legacy synchronous summary endpoint. | `GET /defects/summary` | HTTP `200` in nominal runs; generated summary text present. If timeout, record duration/error as known baseline behavior. | TODO |  |
| TC-10 | API/AI | Validate async summary job flow. | `POST /defects/summary/jobs`; poll `GET /defects/summary/jobs/{job_id}`; `GET /defects/summary/jobs/{job_id}/result`. | Valid `job_id`; terminal status `completed`; result payload available. | TODO |  |
| TC-11 | UI | Verify Queue Dashboard renders and calls backend. | Open `/`; go to Queue Dashboard tab. | Queue stats visible; no blocking console/network errors. | TODO |  |
| TC-12 | UI/API | Validate ingest submission from UI. | Open Ingest Defect tab; submit `LEGACY-UI-1`. | UI success confirmation; defect appears later in list/query flows. | TODO |  |
| TC-13 | UI/API | Validate Query tab behavior. | Open Query tab; search `timeout` or `502`. | Results displayed; no 5xx responses in browser network panel. | TODO |  |
| TC-14 | UI/API/AI | Validate Summary tab behavior. | Open AI Summary tab; trigger generation. | Summary text shown; if timeout occurs, capture timestamp as known baseline issue. | TODO |  |
| TC-15 | Datadog Logs | Confirm log ingestion and status classification. | Query Datadog logs for API + ingestor during run; validate defect markers. | Logs present for both services; fresh app `[INFO]` lines show `status:info`. | TODO |  |
| TC-16 | Datadog APM | Confirm AI trace spans are emitted. | Generate ingest/query/summary traffic; search spans. | Spans `ai.ollama.embeddings` and `ai.ollama.completion` are present. | TODO |  |
| TC-17 | Datadog Metrics | Confirm custom metric emission. | Search Datadog metrics after test traffic. | Metrics include `open_defect_ingest.api.ollama.embedding.requests`, `open_defect_ingest.api.ollama.completion.requests`, `open_defect_ingest.ingestor.queue.processed`. | TODO |  |

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

