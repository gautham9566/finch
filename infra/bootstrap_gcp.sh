#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:-finch-514709}"
BILLING_ID="${2:-01DEBA-ADE9E4-429DE9}"
REGION="${3:-us-central1}"
BUCKET="gs://${PROJECT_ID}-lake"

gcloud config set project "$PROJECT_ID"
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ID" || true

gcloud services enable \
  compute.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com

gsutil mb -p "$PROJECT_ID" -l "$REGION" -b on "$BUCKET" || true

for prefix in raw clean features snapshots mlflow; do
  echo "" | gsutil cp - "${BUCKET}/${prefix}/.keep"
done

echo "Done. Finch GCS Data Lake ready at ${BUCKET}"
