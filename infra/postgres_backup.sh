#!/usr/bin/env bash
set -euo pipefail

TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_DIR:-/var/lib/finch/backups}"
OUT_FILE="${BACKUP_DIR}/finch_pg_${TS}.sql.gz"
mkdir -p "$BACKUP_DIR"

PGPASSWORD="${POSTGRES_PASSWORD:-finch_pass}" pg_dump \
  -h "${POSTGRES_HOST:-postgres}" \
  -U "${POSTGRES_USER:-finch}" \
  -d "${POSTGRES_DB:-finch}" \
  --clean --if-exists | gzip > "$OUT_FILE"

echo "Local backup written: $OUT_FILE ($(stat -c%s "$OUT_FILE") bytes)"

if [ -n "${FINCH_GCS_BUCKET:-}" ]; then
  python3 -c "
from google.cloud import storage
client = storage.Client()
bucket = client.bucket('${FINCH_GCS_BUCKET}')
blob = bucket.blob('backups/finch_pg_${TS}.sql.gz')
blob.upload_from_filename('${OUT_FILE}')
print('Uploaded to gs://${FINCH_GCS_BUCKET}/backups/finch_pg_${TS}.sql.gz')
"
fi
