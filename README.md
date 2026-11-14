# Finch

Intraday NSE stock-selection and ML platform. Runs entirely on GCP (`finch-514709`) with no manual steps.

## How it runs
| What | Where | Trigger |
|---|---|---|
| Lint + tests | GitHub Actions | every push / PR |
| Build image -> Artifact Registry, deploy Cloud Run Jobs | GitHub Actions (Workload Identity, no keys) | push to `main` |
| `bootstrap-etl`: Kaggle 1-min -> `raw/` -> split-adjusted `clean/` + validation report | Cloud Run Job (8 vCPU / 32 GiB) | one time: Actions -> "Run workflow" -> `bootstrap-etl` |
| `daily-eod`: NSE bhavcopy + delivery, FII/DII, global macro | Cloud Run Job (1 vCPU / 2 GiB) | Cloud Scheduler, 18:30 IST Mon-Fri |

Secrets (Kaggle token) live in Secret Manager. Data lives in `gs://finch-514709-lake` (mounted at `/lake` in jobs).

## Lake layout
```
raw/nse_750_1min/          Kaggle source files (unadjusted)
raw/nse_meta/splits.parquet
raw/nse_bhavcopy/date=YYYY-MM-DD/
raw/nse_fii_dii/date=YYYY-MM-DD/
raw/macro/date=YYYY-MM-DD/
clean/equities_1min_adj/year=YYYY/   split-adjusted 1-min bars
reports/                   validation + daily job reports
```

## One-time setup (already done)
`bash infra/setup_automation.sh` - APIs, service accounts, Workload Identity Federation, Secret Manager, Artifact Registry.

## Rotating the Kaggle token
`printf '%s' "NEW_TOKEN" | gcloud secrets versions add kaggle-api-token --data-file=-`

## Local dev
```
pip install ".[dev]"
ruff check src tests && pytest -q
```
