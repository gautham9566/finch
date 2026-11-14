"""GCS bucket sync and Secret Manager helpers (read-only on secrets)."""

from __future__ import annotations

import os
from pathlib import Path

from google.cloud import secretmanager, storage

from libs.common.config import DATA_DIR, get_gcp_settings
from libs.common.logging import get_logger

log = get_logger("finch.storage")


def get_secret(secret_id: str) -> str | None:
    """Read latest secret version from GCP Secret Manager; return None if unconfigured."""
    project_id = get_gcp_settings()["project_id"]
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name}, timeout=5.0)
        val = response.payload.data.decode("utf-8").strip()
        return val or None
    except Exception as exc:  # noqa: BLE001
        log.info(f"Secret '{secret_id}' unavailable or has no active version: {exc}")
        return None


def upload_file_to_gcs(local_path: Path, gcs_relative_path: str) -> str | None:
    """Upload a local artifact/parquet file to the configured GCS data lake."""
    bucket_name = get_gcp_settings()["gcs_bucket"]
    if not local_path.exists():
        return None
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(gcs_relative_path.lstrip("/"))
        blob.upload_from_filename(str(local_path))
        uri = f"gs://{bucket_name}/{gcs_relative_path.lstrip('/')}"
        log.info(f"Uploaded {local_path.name} -> {uri}")
        return uri
    except Exception as exc:  # noqa: BLE001
        log.warning(f"GCS upload skipped for {gcs_relative_path}: {exc}")
        return None


def upload_dir_to_gcs(local_dir: Path, gcs_prefix: str) -> int:
    """Upload all files under local_dir to GCS under gcs_prefix."""
    if not local_dir.exists():
        return 0
    count = 0
    for fpath in local_dir.rglob("*"):
        if fpath.is_file():
            rel = fpath.relative_to(local_dir).as_posix()
            if upload_file_to_gcs(fpath, f"{gcs_prefix.rstrip('/')}/{rel}"):
                count += 1
    return count


def get_bucket_stats() -> dict[str, int]:
    """Return total object count and bytes in the GCS bucket (or local fallback stats)."""
    bucket_name = get_gcp_settings()["gcs_bucket"]
    try:
        client = storage.Client()
        blobs = list(client.list_blobs(bucket_name, max_results=2000))
        return {
            "object_count": len(blobs),
            "total_bytes": sum(int(b.size or 0) for b in blobs),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Bucket stats fallback to local dir: {exc}")
        files = [p for p in DATA_DIR.rglob("*") if p.is_file()]
        return {
            "object_count": len(files),
            "total_bytes": sum(p.stat().st_size for p in files),
        }


def sync_data_dir_to_gcs() -> int:
    """Mirror local clean/features/models/journal parquet artifacts to GCS."""
    if os.getenv("FINCH_DISABLE_GCS_SYNC") == "1":
        return 0
    total = 0
    for sub in ["raw", "clean", "features", "models", "journal"]:
        total += upload_dir_to_gcs(DATA_DIR / sub, sub)
    return total
