# Finch local research

Finch trains and evaluates Indian cash-equity research models using public data, without a paid API or broker account. It runs locally with Docker PostgreSQL, a durable worker, and a browser dashboard.

The study is capped at **28 May 2026**. That date was an NSE holiday; the final equity session is **27 May**. Daily context reaches back to January 2000 where sources and listings permit. Genuine minute and hourly histories are shorter and their coverage is reported separately.

## Start locally

From this project directory in PowerShell, with Docker/Rancher Desktop running:

```powershell
.\scripts\start-local.ps1
```

Open **http://127.0.0.1:8800**. PostgreSQL listens on `127.0.0.1:55432`; the dashboard listens on `127.0.0.1:8800`. The script creates a random database password in the ignored `.env.research.local`. Preserve that file with the database volume. Existing legacy Finch containers on port 5432 are independent.

```powershell
.\scripts\stop-local.ps1     # Retains models, data and database
.\scripts\backup-local.ps1   # Registry/jobs backup; separately back up data_local/research
```

## Use a date

- **Historical replay:** choose a held-out date between July 2025 and May 2026, then **View date**. Predictions come from a model frozen before that test period. Dates used to fit that model are rejected.
- **Later forecast:** select the hourly strategy and a date after the training cutoff, then **Prepare forecast**. The worker collects public observations and applies the final frozen model. After the job finishes, use **View date**. Later observations never retrain the model.
- Hourly decisions occur at **10:15 IST**, after the opening hour, with entry permitted at **11:15**. Minute decisions occur at **09:30**, with entry permitted at **09:31**. A date implies these fixed decision times, not a premarket forecast.
- Outputs contain a reference price, direction, conditional target/stop, outcome probabilities, expected edge after modeled costs, and a research/no-trade decision. Missing data and market-closed dates return an empty result.

Public quote availability and latency are not guaranteed. Training completion does not establish profitable trading. Results remain gated by performance, data coverage, corporate-action verification and historical-universe completeness. There is no order-placement integration.

## Research workflow

Two daily context agents use expanding history from 2000 onward. Their development predictions are chronological; final test context uses a teacher frozen through June 2025. Strategies compare logistic regression, LightGBM, XGBoost and Extra Trees, then a diversity ensemble and a distilled student. No model is silently substituted for another.

Pilot → expanded → full experiments increase stocks and training history. A full experiment compares 28 base configurations plus two derived candidates, tests three finalists across chronological folds, freezes its selection, measures the final holdout, and separately refits a model for later inference. Validation chooses the model and threshold. Completed trials and measured holdouts survive interruptions.

Costs include brokerage and modeled Indian cash-equity charges, adverse slippage, and a doubled-slippage stress test. Capital, planned risk and liquidity constrain quantities. Unknown same-bar target/stop ordering is scored as stop-first. These are research approximations, not reconstructed historical broker bills.

## Details

- [Local research runbook and data sources](docs/local_research.md)
- [API reference](http://127.0.0.1:8800/docs)
- Configuration: `config/research.yaml`, `config/research_hourly.yaml`
- Artifacts: `data_local/research/`, with hourly results in `hourly/`
- Later observations: `data_local/research/forward/`; excluded from training
- [Legacy cloud README](docs/legacy/cloud-readme.md): the older training and paper-trading modules remain for reference and are not the local research execution path.

Run `python -m pytest -q` in the existing virtual environment for regression checks. Container dependencies are pinned in `requirements-local.txt`; cloud integrations require an explicit `FINCH_ENV=cloud` opt-in.
