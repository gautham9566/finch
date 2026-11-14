# Finch

Centralized GCP + Colab intraday stock selection & ML training platform for NSE (GCP Project: `finch-514709`).

## Architecture
- **Data Lake (GCS):** `gs://finch-514709-lake` (`raw/` -> `clean/` -> `features/` -> `snapshots/`)
- **Core VM (GCP `e2-small` / `e2-micro`):** Docker Compose (`Postgres`, `Redis`, `MLflow`, `Scheduler`)
- **Heavy ETL & GPU Training:** Google Colab (reads/writes `gs://finch-514709-lake`, logs to MLflow)
- **CI/CD:** GitHub Actions (`.github/workflows/ci.yml`)

## Quick Start
1. Run `bash infra/bootstrap_gcp.sh` in Google Cloud Shell.
2. Run `notebooks/01_kaggle_to_gcs_etl.ipynb` (or `src/data/etl_kaggle_to_gcs.py`) in Google Colab to ingest & clean the 7 GB Kaggle 1-min dataset into GCS.
3. Deploy `infra/docker-compose.yml` on your GCP VM.
