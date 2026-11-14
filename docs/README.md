# Finch Documentation

**Finch** is an end-to-end Indian equity (NSE Nifty 200) intraday stock-selection research and paper-trading platform deployed on Google Cloud Platform (`asia-south1`, Mumbai).

> **Scope & Safety:** Paper trading only. There are no broker order-placement integrations and no automated LLM trading execution in this phase (`StubLLMReviewer` returns `APPROVE`).

## Repository Layout

```text
/config             markets.yaml, costs.yaml, risk.yaml, scorecard.yaml, schedule.yaml, models/*.yaml
/libs/common        schemas, YAML config loader, IST JSON logging, ISIN instruments, statutory costs, Postgres & GCS helpers
/pipelines/data     NSE/Yahoo equity & cue ingestion, IntradayDataSource interface, clean Parquet, point-in-time features, quality gate
/pipelines/training walk-forward splitters, calibration, 9 models, 3 specialist agents + coordinator, scorecard ranking, MLflow registry
/pipelines/sandbox  LLM reviewer stub, plain-code RiskEngine, PaperBroker, 08:45/09:20 snapshots, replay, APScheduler IST daemon
/web                Read-only FastAPI backend (GET/HEAD only) + 8-page institutional monitoring frontend
/infra              Terraform GCP resources, non-root Dockerfiles, Docker Compose stack, nginx reverse proxy, Postgres GCS backup
/tests              Unit, leakage, train/serve parity, cost, risk, simulator, and integration tests
/docs               Platform documentation, architecture, runbook, scorecard rules, and model cards
```

## Quickstart Commands

All standard development and pipeline commands are wrapped in the root `Makefile`:

```bash
make lint        # ruff check libs pipelines web tests
make typecheck   # mypy libs pipelines web
make test        # pytest -v
make up          # Start full Docker Compose monitoring + scheduler stack
make data        # Run Pipeline 1 daily EOD ingest, clean, feature build, quality gate
make train       # Run Pipeline 2 walk-forward training and ranking for all 9 models
make replay      # Run Pipeline 3 sandbox replay for DATE=YYYY-MM-DD
make backup      # Compressed pg_dump uploaded to gs://<bucket>/backups/postgres/
```

## Further Reading

- [Architecture & Security Boundaries](architecture.md)
- [Operations Runbook](runbook.md)
- [Scorecard & Promotion Rules](scorecard_rules.md)
- [Model & Agent Cards](model_cards.md)
