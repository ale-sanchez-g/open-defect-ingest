# open-defect-ingest

A smart defect ingestor that captures bugs from all your projects and builds a centralised knowledge database using ChromaDB vector embeddings, with a local LLM for natural-language querying.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Compose                           │
│                                                                 │
│  External        ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  Systems ──────► │ RabbitMQ │───►│ Ingestor │───►│ ChromaDB │  │
│                  └──────────┘    └────┬─────┘    └────▲─────┘  │
│                       ▲              │ embed           │        │
│  Browser  ──────► ┌───┴───┐          ▼           ┌────┴─────┐  │
│                   │  UI   │◄────► ┌─────┐        │  Ollama  │  │
│                   │(React)│       │ API │────────►│ (Local   │  │
│                   └───────┘       └─────┘  query  │   LLM)   │  │
│                                                   └──────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

| Service | Port | Description |
|---------|------|-------------|
| **RabbitMQ** | 5672 / 15672 | Message broker + management UI |
| **ChromaDB** | 8000 | Vector database |
| **Ollama** | 11434 | Local LLM (embeddings + generation) |
| **Ingestor** | — | Worker that consumes from RabbitMQ and stores embeddings |
| **API** | 8080 | FastAPI REST service |
| **UI** | 3000 | React dashboard |
| **Datadog Agent** | 8126 / 8125 UDP | Collects logs, APM traces, runtime metrics, and custom DogStatsD metrics |

## Quick Start

### 1 — Prerequisites

- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/)

### 2 — Start all services

```bash
cp .env.example .env   # optional — override defaults
docker compose up -d --build
```

### 2.1 — Configure Datadog (required for observability data export)

Set at least these variables in `.env`:

```dotenv
DD_API_KEY=<your_datadog_api_key>
DD_SITE=datadoghq.com
DD_ENV=local
DD_TRACE_LANGCHAIN_ENABLED=false
```

The stack includes a `datadog-agent` service and both Python services (`api`, `ingestor`) run under `ddtrace-run`.

Telemetry covered by default:

- Logs: container logs are collected by the agent.
- Traces: FastAPI, requests/pika instrumentation, and custom spans.
- Metrics: DogStatsD counters/timings from API and ingestor.
- AI telemetry: explicit spans and metrics around Ollama embedding/completion calls.

### 3 — Pull LLM models (first run only)

The `ollama-setup` container will attempt this automatically, but you can also run it manually:

```bash
docker compose exec ollama ollama pull nomic-embed-text   # embedding model (~274 MB)
docker compose exec ollama ollama pull llama3.2           # generation model (~2 GB)
```

### 4 — Open the UI

Navigate to **http://localhost:3000**

## UI Features

| Tab | Description |
|-----|-------------|
| **Queue Dashboard** | Live RabbitMQ queue stats (auto-refreshes every 5 s) + link to RabbitMQ Management Console |
| **Ingest Defect** | Submit a new defect via the API → queued in RabbitMQ → embedded by the ingestor |
| **Query** | Semantic search — find similar defects using natural language |
| **AI Summary** | Generate an AI analysis of all ingested defects using the local LLM |
| **Defects** | Paginated list of all defects stored in ChromaDB |

## API Endpoints

```
GET  /health              — Health check
GET  /config              — Runtime feature flags and migration config
GET  /queue/stats         — RabbitMQ queue statistics
POST /defects/ingest      — Publish a defect to the queue
POST /defects/query       — Semantic similarity search
GET  /defects/summary     — AI-generated summary (via Ollama)
POST /defects/summary/jobs             — Start async summary job
GET  /defects/summary/jobs/{job_id}    — Summary job status
GET  /defects/summary/jobs/{job_id}/result — Summary job result
GET  /defects/list        — Paginated list of all defects
```

When `USE_LANGGRAPH_QUERY=true`, `/defects/query` includes additional fields:

- `route` — query workflow route selected by graph router
- `analysis` — concise synthesized guidance from retrieved results
- `trace_id` — workflow trace identifier
- `node_timings_ms` — per-node execution timings

When graph tracing is enabled, API logs include node timing lines for query and summary workflows.

## Defect Schema

```json
{
  "id":          "BUG-1234",        // optional — auto-generated if omitted
  "title":       "Login fails",
  "description": "Users get HTTP 500 on POST /auth/login",
  "project":     "auth-service",
  "severity":    "high",            // critical | high | medium | low
  "status":      "open"             // open | in-progress | resolved | closed
}
```

## Development

### Run tests

```bash
# Python unit tests (mocked — no services required)
make test-ingestor
make test-api
```

## Troubleshooting

### Datadog verification checklist

After starting the stack and generating traffic (ingest defects, run query + summary):

```bash
docker compose ps datadog-agent
docker compose logs datadog-agent | tail -n 50
docker compose logs api | tail -n 50
docker compose logs ingestor | tail -n 50
```

In Datadog UI, verify:

- APM services include `open-defect-api` and `open-defect-ingestor`.
- Logs include entries from `api` and `ingestor` containers.
- Metrics include:
  - `open_defect_ingest.api.ollama.embedding.requests`
  - `open_defect_ingest.api.ollama.completion.requests`
  - `open_defect_ingest.ingestor.queue.processed`
- AI traces include spans named:
  - `ai.ollama.embeddings`
  - `ai.ollama.completion`

### Ollama runner startup timeout mitigation

If you see logs like:

```text
timed out waiting for llama runner to start: context canceled
```

apply these runtime controls (already wired in compose/env):

- `OLLAMA_NUM_PARALLEL=1`
- `OLLAMA_MAX_LOADED_MODELS=1`
- `OLLAMA_KEEP_ALIVE=30m`

These settings reduce model load contention and keep runners warm between requests.

After changing values, restart impacted services:

```bash
docker compose up -d ollama api ingestor
```

Note on Datadog log status: Ollama logs can include an `error="..."` field even when `level=INFO`. In Datadog pipelines, map status from parsed `level` instead of any generic `error` field to avoid false error classification.

- Symptom: Ingestor logs show `Failed to process defect: '_type'`.
- Cause: Chroma server/client version mismatch (for example, server `1.x` with Python client `0.5.23`).
- Fix: Keep Chroma server and Python client on the same compatible line. This project pins:
  - Docker image: `chromadb/chroma:0.5.23`
  - Python package: `chromadb==0.5.23`

### Migration flags and rollback

Phase 0 introduces runtime flags for progressive LangGraph rollout:

- `USE_LANGGRAPH_QUERY` (default: `false`)
- `USE_LANGGRAPH_SUMMARY` (default: `false`)
- `GRAPH_TRACE_ENABLED` (default: `true`)
- `PROMPT_STRATEGY` (default: `local_versioned`)
- `PROMPTS_DIR` (default: `/app/prompts`)

Current behavior remains legacy while these are `false`.

Rollback procedure:

1. Set both flags to `false` in `.env`.
2. Restart API service:

```bash
docker compose up -d api
```

Verify active flag state:

```bash
curl http://localhost:8080/config
```

### Prompt version strategy

Prompt templates are versioned as local files under [api/prompts](api/prompts):

- [api/prompts/prompt-metadata.json](api/prompts/prompt-metadata.json) stores active prompt version and file mapping.
- Prompt text files are loaded at startup when `PROMPT_STRATEGY=local_versioned`.
- If prompt files are missing or invalid, API falls back to safe inline defaults.

Runtime prompt strategy/version are exposed in `/config` under `prompts`.

### Horizontal scaling for ingestor workers

Scale ingest workers horizontally to increase queue drain throughput:

```bash
make scale-ingestor REPLICAS=2
```

You can scale up or down at runtime without changing application code.

Verify active worker replicas:

```bash
docker compose ps ingestor
```

The ingestor emits periodic throughput logs with:

- processed/success/failure totals
- average and max processing latency
- window message rate (messages/sec)

Tune log cadence with:

```dotenv
METRICS_LOG_INTERVAL_SECONDS=30
```

### Local UI development

```bash
cd ui
npm install
npm run dev      # starts Vite dev server on http://localhost:3000
                 # /api/* is proxied to http://localhost:8080
```

## Make Targets

```
make up            — Build and start all services
make down          — Stop all services
make pull-models   — Pull Ollama models into a running stack
make logs          — Follow logs for all services
make test          — Run all Python unit tests
make benchmark     — Run migration benchmark and write migration/baseline-latest.csv
make scale-ingestor REPLICAS=2 — Scale ingestor workers horizontally
```
