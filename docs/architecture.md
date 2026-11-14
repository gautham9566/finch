# Finch Architecture

## 1. Repeating Research Loop

```text
  [ DATA ] ──> clean Parquet + 16 point-in-time features (known_as_of <= 09:15 IST)
     │         + daily DataQualityReport (tradeable=true/false)
     ▼
[ TRAINING ] ─> expanding walk-forward splits (purge gap + untouched holdout)
     │          9 models + 3 specialist agents -> statutory-cost scorecard -> MLflow
     ▼
 [ SANDBOX ] ─> 08:45/09:20 snapshot -> Coordinator -> StubLLMReviewer -> RiskEngine -> PaperBroker
     │          logs all approved/vetoed candidates + counterfactual PnL to sandbox_journal
     ▼
[ FEEDBACK ] ─> exports sandbox outcomes back into DATA for continuous diagnostics
```

## 2. GCP Mumbai (`asia-south1`) Deployment Topology

- **Compute (`finch-vm`):** Single Ubuntu LTS `e2-standard-4` (4 vCPU, 16 GB RAM, 100 GB SSD) in `asia-south1-a` running Docker Compose.
- **Networking & Firewall:**
  - Public static IPv4 attached to `finch-vm`; firewall permits `tcp:80` and `tcp:443` only.
  - SSH (`tcp:22`) restricted to administrator CIDR and GCP Identity-Aware Proxy (`35.235.240.0/20`).
  - `postgres` (`5432`) and `mlflow` (`5000`) bound strictly to `127.0.0.1` inside the VM and private Compose network.
- **Storage (`gs://finch-514709-mumbai-lake`):** Versioned GCS bucket in `asia-south1` storing raw downloads, cleaned Parquet tables, feature stores, model binaries, journal exports, and nightly compressed Postgres dumps.
- **Secrets & IAM:** Least-privilege service account `finch-vm-sa`; zero credentials baked into Docker images or Git. External keys (future broker historical intraday feed) live in GCP Secret Manager (`finch-intraday-api-key`).

## 3. Web Security Model

1. **nginx (`1.27.3-alpine`)** rejects any HTTP verb other than `GET` and `HEAD` (`405 Method Not Allowed`), enforces rate limiting (`15 req/s`, burst `30`), and attaches CSP (`default-src 'self'`), `X-Frame-Options: DENY`, and `nosniff` headers.
2. **FastAPI backend** disables Swagger/OpenAPI routes on the public edge and enforces a second `GET`/`HEAD`-only HTTP middleware (`405`). No login exists because zero write paths and zero secret values are exposed.

## 4. Zero-Leakage Timestamp Contract

- **Price history:** Every equity feature (`prev_1d_return`, `prev_5d_return`, `volume_ratio_20d`, `atr_14_pct`, 20-day highs/lows, sector/Nifty relative strength) is computed exclusively from sessions `<= t - 1`, plus `opening_gap_pct` measured at `09:15 IST` on `t`.
- **Global cues & India VIX:** Merged via backward `pd.merge_asof` requiring `known_as_of <= decision_ts (09:20 IST)`.
- **News sentiment:** Averaged strictly over whitelisted RSS/corporate announcement timestamps in `[decision_ts - 24h, decision_ts]`.
- **Automated Enforcement:** `assert_no_leakage()` asserts `feature_known_as_of <= decision_ts` on every row and raises `LeakageError` if violated.
