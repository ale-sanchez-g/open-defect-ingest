# Open Defect Ingest Migration Plan

## Objective
Migrate AI workflows from the current imperative implementation to a LangGraph + LangChain architecture, improving latency, reliability, observability, and scalability while preserving existing functionality.

## Scope
- In scope:
  - API AI paths: query and summary
  - Worker throughput and embedding flow
  - Runtime configuration and feature flags
  - Tests, benchmarks, and rollout controls
- Out of scope for initial migration:
  - Full UI redesign
  - Replacing RabbitMQ, ChromaDB, or Ollama

## Success Criteria
- Summary workflow no longer blocks request threads with long-running generation.
- Query workflow is routed through LangGraph/LangChain with equivalent or better relevance.
- Measurable performance improvements against baseline:
  - Query p95 latency reduced or stable under load.
  - Summary timeout rate near 0% in normal conditions.
  - Queue drain rate improved under ingestion bursts.
- Existing endpoints remain backward compatible or have explicit migration notes.

## SLO Targets (Phase 0 Baseline Contract)
These targets are used as migration gates from Phase 1 onward.

| Metric | Baseline signal | Target SLO |
|---|---|---|
| Query latency p95 (/defects/query) | ~0.66s max seen in quick baseline sample | <= 1.0s in local docker benchmark, no regression > 20% vs baseline median |
| Summary timeout rate (/defects/summary or replacement job flow) | Timeouts observed (500 at 120s) | <= 1% timeouts across 100 requests in benchmark/load run |
| Summary completion time p95 (new async workflow end-to-end) | Blocking call often > 70s | <= 30s p95 for 50-defect context on local docker profile |
| API availability (health/list/query routes) | Healthy | >= 99% successful responses during benchmark window |
| Ingest queue drain efficiency | Single-consumer bottleneck | >= 2x improvement in messages/min drained under burst test |

## Baseline (Already Observed)
- /defects/summary can time out at ~120s due to blocking generation.
- API process model is single-worker and blocking-heavy for AI calls.
- Ingestor processes one message at a time with prefetch_count=1.

## Migration Phases

### Phase 0 - Setup and Guardrails
1. Create migration branch and feature flags.
2. Add benchmark script(s) for baseline + post-change comparison.
3. Define performance SLO targets in this file.
4. Add rollback toggles for old vs new AI paths.

Acceptance criteria:
- Feature flags exist for summary and query workflows.
- Benchmark command is documented and repeatable.
- Rollback path is one env change + deploy.

### Phase 1 - LangGraph Summary Workflow (Highest ROI)
1. Add LangChain and LangGraph dependencies.
2. Introduce summary graph state and nodes:
   - document loader
   - chunk or selection node
   - parallel analyzers (patterns, severity, impacted areas)
   - synthesizer node
3. Execute summary asynchronously:
   - start summary job endpoint
   - job status endpoint
   - job result endpoint
4. Keep old summary endpoint behind fallback flag until validated.
5. Add tests for job lifecycle and graph execution errors/timeouts.

Acceptance criteria:
- New summary endpoints function end-to-end.
- No request hangs waiting for full generation.
- Old summary path can be re-enabled via flag.

### Phase 2 - LangGraph Query Workflow
1. Add query graph state and routing node.
2. Replace direct query logic with LangChain retriever abstraction over Chroma.
3. Add synthesis step for final response formatting.
4. Add optional specialist branches (if routed) similar to langgraph-demo pattern.
5. Keep current query implementation as fallback flag.

Acceptance criteria:
- Query path uses LangGraph/LangChain when enabled.
- Relevance is equal or better for representative test set.
- Fallback to legacy query path is available.

### Phase 3 - Ingest Throughput and Stability
1. Improve ingestor concurrency strategy:
   - configurable prefetch_count
   - multi-replica worker support
2. Evaluate embedding batching or controlled parallelism.
3. Add dead-letter handling policy for poison messages.
4. Add worker metrics and structured logging for throughput visibility.

Acceptance criteria:
- Queue backlog drains faster under load.
- Failed messages are visible and recoverable.
- Worker behavior is configurable without code changes.

### Phase 4 - Observability and Prompt Management
1. Add workflow-level tracing around graph nodes.
2. Add per-node timing + error telemetry.
3. Add prompt version control pattern (local prompt files first).
4. Optional: integrate Open Prompt Manager with fallback behavior.

Acceptance criteria:
- Node-level latency and error visibility exists.
- Prompt sources are explicit and versioned.

### Phase 5 - Rollout and Cutover
1. Run side-by-side validation with feature flags:
   - legacy vs new summary
   - legacy vs new query
2. Execute load tests and compare to baseline.
3. Enable new paths in staging, then production incrementally.
4. Remove deprecated legacy code after stabilization window.

Acceptance criteria:
- Performance and reliability targets are met.
- No critical regressions in production.
- Legacy paths removed only after safe window.

## Task Tracker
Use this table to track execution state. Update after every completed step.

| ID | Phase | Task | Owner | Status | Started | Completed | Notes |
|---|---|---|---|---|---|---|---|
| P0-1 | 0 | Create migration branch + feature flags | Copilot | DONE | 2026-03-20T04:11:23Z | 2026-03-20T04:11:23Z | Branch context confirmed on copilot/add-ui-for-defect-traffic. Added USE_LANGGRAPH_QUERY and USE_LANGGRAPH_SUMMARY feature flags and runtime config endpoint. Files: api/main.py, docker-compose.yml, .env.example. Validation: docker run --rm -v "$PWD/api:/work" -w /work python:3.12-slim sh -lc "pip install -q -r requirements.txt && pytest tests/test_api.py -q" -> 11 passed. Rollback: set both flags to false. |
| P0-2 | 0 | Add benchmark script and baseline doc | Copilot | DONE | 2026-03-20T04:11:23Z | 2026-03-20T04:19:16Z | Added reproducible benchmark script and baseline documentation. Files: migration/benchmark.sh, migration/baseline.md, Makefile. Validation: benchmark script executed successfully and emitted CSV timings including summary timeout behavior. Rollback: remove benchmark artifacts only; no runtime path changes. |
| P0-3 | 0 | Define SLO targets | Copilot | DONE | 2026-03-20T04:19:16Z | 2026-03-20T04:19:36Z | Added explicit SLO target table for query latency, summary timeouts, summary completion p95, API availability, and ingest drain efficiency. File: migration/plan.md. Validation: targets align with captured baseline in migration/baseline.md. Rollback: adjust thresholds in this plan if hardware profile changes. |
| P0-4 | 0 | Add rollback toggles and docs | Copilot | DONE | 2026-03-20T04:19:36Z | 2026-03-20T04:21:44Z | Documented rollback flags and runtime verification steps in README. Added /config endpoint and wired env flags via compose and .env.example. Files: README.md, api/main.py, docker-compose.yml, .env.example. Validation: docker compose up -d --build api and curl http://localhost:8080/config returned both flags as false. Rollback: keep both flags false. |
| P1-1 | 1 | Add LangGraph/LangChain dependencies | Copilot | DONE | 2026-03-20T04:21:44Z | 2026-03-20T04:23:33Z | Added API dependencies for LangGraph/LangChain migration: langgraph, langchain, langchain-community, langchain-ollama. File: api/requirements.txt. Validation: docker compose build api completed successfully. Rollback: remove added dependencies and rebuild api image. |
| P1-2 | 1 | Implement summary graph nodes | Copilot | IN_PROGRESS | 2026-03-20T04:23:33Z |  |  |
| P1-3 | 1 | Add async summary job endpoints | TBD | TODO |  |  |  |
| P1-4 | 1 | Add summary fallback flag | TBD | TODO |  |  |  |
| P1-5 | 1 | Add summary tests | TBD | TODO |  |  |  |
| P2-1 | 2 | Implement query graph and routing | TBD | TODO |  |  |  |
| P2-2 | 2 | Integrate Chroma retriever via LangChain | TBD | TODO |  |  |  |
| P2-3 | 2 | Add query synthesis node | TBD | TODO |  |  |  |
| P2-4 | 2 | Add query fallback flag | TBD | TODO |  |  |  |
| P2-5 | 2 | Add query tests and relevance checks | TBD | TODO |  |  |  |
| P3-1 | 3 | Make prefetch_count configurable | TBD | TODO |  |  |  |
| P3-2 | 3 | Enable worker horizontal scaling | TBD | TODO |  |  |  |
| P3-3 | 3 | Add DLQ/error policy | TBD | TODO |  |  |  |
| P3-4 | 3 | Add throughput metrics/logging | TBD | TODO |  |  |  |
| P4-1 | 4 | Add graph tracing and node timings | TBD | TODO |  |  |  |
| P4-2 | 4 | Add prompt version strategy | TBD | TODO |  |  |  |
| P4-3 | 4 | Optional OPM integration | TBD | TODO |  |  |  |
| P5-1 | 5 | Run side-by-side validation | TBD | TODO |  |  |  |
| P5-2 | 5 | Execute load tests and compare metrics | TBD | TODO |  |  |  |
| P5-3 | 5 | Production cutover by flag | TBD | TODO |  |  |  |
| P5-4 | 5 | Remove legacy code after stability period | TBD | TODO |  |  |  |

Status values:
- TODO
- IN_PROGRESS
- BLOCKED
- DONE

## Mandatory Plan Update Protocol (After Every Step)
After completing each task, this file must be updated immediately before starting the next task.

Required updates per completed step:
1. Set task Status to DONE.
2. Fill Completed date/time.
3. Add concise Notes:
   - what changed
   - key files touched
   - test/benchmark evidence
   - rollback notes (if applicable)
4. Mark next task as IN_PROGRESS.
5. If blocked, set Status to BLOCKED and add unblock action.

## Change Log
Record meaningful plan-level decisions.

- 2026-03-20: Initial migration runbook created from architecture review and performance findings.
- 2026-03-20: Phase 0 started. Added migration feature flags for query and summary paths with runtime config visibility.
- 2026-03-20: Added benchmark tooling and baseline documentation to enforce repeatable before/after comparisons.
- 2026-03-20: Added quantitative SLO targets to gate migration phases with objective pass/fail criteria.
- 2026-03-20: Added rollback docs and runtime config validation for migration feature flags.
- 2026-03-20: Phase 1 started with LangGraph/LangChain dependencies added and build-validated.

## Working Rules
- Do not remove fallback paths until post-cutover stability window is complete.
- Do not merge major phase changes without passing relevant tests.
- Keep each phase deliverable deployable and reversible.
- Update this plan after every completed task without exception.
