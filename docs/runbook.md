# Finch Operations Runbook

## 1. Stopping and Starting the VM (Cost Control)

Stopping `finch-vm` halts compute billing while preserving the attached 100 GB boot disk, Postgres volumes, static IP, and GCS lake.

```bash
# Stop VM
gcloud compute instances stop finch-vm --zone=asia-south1-a --project=finch-514709

# Start VM (Docker Compose services with restart: unless-stopped come back automatically)
gcloud compute instances start finch-vm --zone=asia-south1-a --project=finch-514709
```

## 2. Inspecting Stack & Logs on the VM

```bash
gcloud compute ssh finch-vm --zone=asia-south1-a --project=finch-514709
cd /opt/finch
sudo docker compose -f infra/docker-compose.yml ps
sudo docker compose -f infra/docker-compose.yml logs --tail=100 scheduler web nginx
```

## 3. Emergency Kill Switch

`RiskEngine` halts all sandbox entries immediately when either `config/risk.yaml` (`kill_switch: true`) or Postgres `system_state` (`key='kill_switch'`) is enabled:

```bash
# Enable kill switch via CLI inside sandbox container
sudo docker compose -f infra/docker-compose.yml run --rm sandbox kill-switch --on

# Disable kill switch
sudo docker compose -f infra/docker-compose.yml run --rm sandbox kill-switch --off
```

## 4. Running Pipelines Manually

```bash
cd /opt/finch
# 1. Fetch & clean market data + build features + evaluate quality gate
sudo docker compose -f infra/docker-compose.yml run --rm data eod

# 2. Train all 9 models on walk-forward splits (off-hours default; pass --allow-market-hours to override)
sudo docker compose -f infra/docker-compose.yml run --rm training train-all --allow-market-hours

# 3. Replay historical trading days through paper sandbox & export feedback
sudo docker compose -f infra/docker-compose.yml run --rm sandbox replay-range --days 25
sudo docker compose -f infra/docker-compose.yml run --rm sandbox feedback
```

## 5. Enabling Optional Integrations Later

1. **Historical Intraday Broker Data (`finch-intraday-api-key`):**
   - Add a secret version with your broker API token (`<CLIENT_ID>:<ACCESS_TOKEN>`):
     ```bash
     printf "%s" "<YOUR_BROKER_TOKEN>" | gcloud secrets versions add finch-intraday-api-key --data-file=- --project=finch-514709
     ```
   - The `BrokerIntradayAdapter` automatically detects the secret on the next EOD run and begins saving 5-minute candles to GCS.
2. **Live Cloud Billing Ledger in UI:**
   - In **GCP Console -> Billing -> Billing export -> BigQuery export**, link dataset `finch-514709.billing_export` (region `asia-south1`).
   - Within ~24 hours of export tables populating, `/api/cloud/billing` automatically switches from the labeled Mumbai pricing `ESTIMATE` to live BigQuery invoice rows.

## 6. Database Backup & Restore

```bash
# Trigger immediate backup to GCS
cd /opt/finch
sudo docker compose -f infra/docker-compose.yml exec -T postgres sh -c 'pg_dump -U finch finch' | gzip > finch_backup.sql.gz

# Automated backup + GCS upload script
/bin/bash /opt/finch/infra/postgres_backup.sh
```

To restore into a fresh Postgres instance:

```bash
gunzip -c finch_backup.sql.gz | docker exec -i <target_pg_container> psql -U finch -d finch
```
