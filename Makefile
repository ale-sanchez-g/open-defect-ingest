.PHONY: up down restart logs pull-models test-ingestor test-api benchmark

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

## Pull required Ollama models (run once after `make up`)
pull-models:
	docker compose exec ollama ollama pull nomic-embed-text
	docker compose exec ollama ollama pull llama3.2

## Run Python unit tests locally (requires pip-installed deps)
test-ingestor:
	cd ingestor && pip install -q -r requirements.txt && python -m pytest tests/ -v

test-api:
	cd api && pip install -q -r requirements.txt && python -m pytest tests/ -v

test: test-ingestor test-api

benchmark:
	bash migration/benchmark.sh > migration/baseline-latest.csv
	@echo "Wrote migration/baseline-latest.csv"
