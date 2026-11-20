.PHONY: lint typecheck test data train replay up down backup legacy-data legacy-replay

lint:
	python -m ruff check libs pipelines web tests

typecheck:
	python -m mypy libs pipelines web

test:
	python -m pytest -q

data:
	python -m pipelines.research.sources stocks
	python -m pipelines.research.sources broad
	python -m pipelines.research.sources macro
	python -m pipelines.research.sources events

train:
	python -m pipelines.research.runner full --profile hourly --finalize

replay:
ifndef DATE
	$(error Usage: make replay DATE=YYYY-MM-DD)
endif
	powershell -NoProfile -File scripts/replay-local.ps1 -Date $(DATE)

legacy-data:
	python -m pipelines.data.cli eod

legacy-replay:
ifndef DATE
	$(error Usage: make legacy-replay DATE=YYYY-MM-DD)
endif
	python -m pipelines.sandbox.cli replay --date $(DATE)

up:
	powershell -NoProfile -File scripts/start-local.ps1

down:
	powershell -NoProfile -File scripts/stop-local.ps1

backup:
	powershell -NoProfile -File scripts/backup-local.ps1
