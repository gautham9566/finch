.PHONY: lint typecheck test data train replay up down backup

lint:
	python -m ruff check libs pipelines web tests

typecheck:
	python -m mypy libs pipelines web

test:
	python -m pytest -q

data:
	python -m pipelines.data.cli eod

train:
	python -m pipelines.training.cli train-all

replay:
ifndef DATE
	$(error Usage: make replay DATE=YYYY-MM-DD)
endif
	python -m pipelines.sandbox.cli replay --date $(DATE)

up:
	docker compose -f infra/docker-compose.yml up -d --build

down:
	docker compose -f infra/docker-compose.yml down

backup:
	bash infra/postgres_backup.sh
