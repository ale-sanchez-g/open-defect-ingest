# Migration Baseline

This document records pre-migration performance and the reproducible command used for comparison after each phase.

## Benchmark Command
Run from repository root:

```bash
bash migration/benchmark.sh > migration/baseline-latest.csv
```

Optional overrides:

```bash
API_BASE=http://localhost:8080 RUNS=5 QUERY_TEXT="login failure" bash migration/benchmark.sh > migration/baseline-latest.csv
```

## Initial Baseline Snapshot (2026-03-20)
Single-run spot checks collected during architecture review:

- health: ~0.222s
- ingest: ~0.138s
- query: ~0.323s
- list: ~0.329s
- summary: ~120.124s (timeout/500 observed)

## Streaming Endpoint Baseline (2026-03-24)
Run from repository root:

```bash
python3 migration/benchmark_stream.py > /tmp/benchmark_stream.txt && cat /tmp/benchmark_stream.txt
```

Results:

```
stream,200,94.822,190,4463
first_results_time,0.345
```

- stream: HTTP 200, total time 94.8s, 190 tokens, 4463 bytes streamed
- first_results_time: 0.345s (time to first results event)

This is now the baseline for `/defects/query/stream` performance after migration.

## Notes
- Summary timeout is the highest-priority migration driver.
- Re-run benchmark script after each completed phase and append deltas here.
- Keep at least one baseline CSV artifact in migration/ for trend comparison.
