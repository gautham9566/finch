# Finch — Indian Equity Intraday Research & Paper-Trading Platform

Finch is an automated research and **paper-trading** platform for NSE Nifty 200 equities running on GCP (`asia-south1`, Mumbai). It implements a strictly leakage-free, chronological walk-forward loop (`DATA -> TRAINING -> SANDBOX -> FEEDBACK`) alongside a public, strictly read-only monitoring dashboard.

> **Important:** Paper trading only. There are no broker order-placement integrations and no active LLM reviewer in this phase (`StubLLMReviewer` always returns `APPROVE`).

## Documentation

- [Documentation Index](docs/README.md)
- [Architecture & Security Boundaries](docs/architecture.md)
- [Operations Runbook](docs/runbook.md)
- [Scorecard & Promotion Rules](docs/scorecard_rules.md)
- [Model & Agent Cards](docs/model_cards.md)

## Quick Commands

```bash
make lint        # Check formatting and lint rules with ruff
make typecheck   # Static type check with mypy
make test        # Run unit, leakage, parity, cost, risk, simulator, and integration tests
make up          # Start Postgres, MLflow, APScheduler, Read-Only Web API, and nginx
```
