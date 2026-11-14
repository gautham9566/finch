#!/usr/bin/env bash
# ONE-TIME GCP setup for Finch automation. Idempotent: safe to re-run, but you never need to.
# After this, every deploy/run happens via GitHub Actions + Cloud Scheduler.
#
# Usage (Cloud Shell or any shell with gcloud logged in as project owner):
#   bash infra/setup_automation.sh
# Optional: KAGGLE_API_TOKEN=... bash infra/setup_automation.sh   (otherwise prompts, input hidden)
set -euo pipefail

PROJECT_ID="finch-514709"
REGION="us-central1"
GITHUB_REPO="gautham9566/finch"
BUCKET="finch-514709-lake"
AR_REPO="finch"
JOBS_SA="finch-jobs"
DEPLOY_SA="finch-deployer"
POOL="github"
PROVIDER="github-oidc"
SECRET="kaggle-api-token"

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
JOBS_EMAIL="${JOBS_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_EMAIL="${DEPLOY_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

echo ">> Enabling APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  cloudscheduler.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  storage.googleapis.com

echo ">> Artifact Registry"
gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location="$REGION"

echo ">> Service accounts"
for sa in "$JOBS_SA" "$DEPLOY_SA"; do
  gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$sa" --display-name="$sa"
done

echo ">> Jobs SA: bucket read/write + can be invoked by Scheduler"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${JOBS_EMAIL}" --role="roles/storage.objectAdmin" >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${JOBS_EMAIL}" --role="roles/run.invoker" --condition=None >/dev/null

echo ">> Secret Manager: Kaggle token"
if ! gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  TOKEN="${KAGGLE_API_TOKEN:-}"
  if [ -z "$TOKEN" ]; then read -rsp "Paste Kaggle API token (hidden): " TOKEN; echo; fi
  printf '%s' "$TOKEN" | gcloud secrets create "$SECRET" --data-file=- --replication-policy=automatic
fi
gcloud secrets add-iam-policy-binding "$SECRET" \
  --member="serviceAccount:${JOBS_EMAIL}" --role="roles/secretmanager.secretAccessor" >/dev/null

echo ">> Deployer SA roles (used by GitHub Actions)"
for role in roles/run.admin roles/artifactregistry.writer roles/cloudscheduler.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOY_EMAIL}" --role="$role" --condition=None >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$JOBS_EMAIL" \
  --member="serviceAccount:${DEPLOY_EMAIL}" --role="roles/iam.serviceAccountUser" >/dev/null

echo ">> Workload Identity Federation (GitHub -> GCP, no keys)"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers describe "$PROVIDER" --location=global \
  --workload-identity-pool="$POOL" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global \
    --workload-identity-pool="$POOL" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='${GITHUB_REPO}'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" \
  >/dev/null

echo
echo "Setup complete."
echo "WIF provider: projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "Deployer SA : ${DEPLOY_EMAIL}"
