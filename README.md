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

## Quick Start

### 1 — Prerequisites

- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/)

### 2 — Start all services

```bash
cp .env.example .env   # optional — override defaults
docker compose up -d --build
```

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
GET  /queue/stats         — RabbitMQ queue statistics
POST /defects/ingest      — Publish a defect to the queue
POST /defects/query       — Semantic similarity search
GET  /defects/summary     — AI-generated summary (via Ollama)
GET  /defects/list        — Paginated list of all defects
```

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

- Symptom: Ingestor logs show `Failed to process defect: '_type'`.
- Cause: Chroma server/client version mismatch (for example, server `1.x` with Python client `0.5.23`).
- Fix: Keep Chroma server and Python client on the same compatible line. This project pins:
  - Docker image: `chromadb/chroma:0.5.23`
  - Python package: `chromadb==0.5.23`

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
```
