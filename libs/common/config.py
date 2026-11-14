"""Central configuration loader for Finch YAML configs and environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(os.getenv("FINCH_REPO_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = Path(os.getenv("FINCH_DATA_DIR", str(REPO_ROOT / "data_local")))


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}, got {type(data).__name__}")
    return data


@lru_cache(maxsize=1)
def get_markets_config() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "markets.yaml")


@lru_cache(maxsize=1)
def get_costs_config() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "costs.yaml")


@lru_cache(maxsize=1)
def get_risk_config() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "risk.yaml")


@lru_cache(maxsize=1)
def get_scorecard_config() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "scorecard.yaml")


@lru_cache(maxsize=1)
def get_schedule_config() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "schedule.yaml")


def load_all_model_configs() -> list[dict[str, Any]]:
    models_dir = CONFIG_DIR / "models"
    return [load_yaml(p) for p in sorted(models_dir.glob("*.yaml"))]


def get_postgres_dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "finch")
    password = os.getenv("POSTGRES_PASSWORD", "finch_pass")
    db = os.getenv("POSTGRES_DB", "finch")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def get_gcp_settings() -> dict[str, Any]:
    return {
        "project_id": os.getenv("FINCH_PROJECT_ID", "finch-514709"),
        "region": os.getenv("FINCH_REGION", "asia-south1"),
        "vm_name": os.getenv("FINCH_VM_NAME", "finch-vm"),
        "vm_zone": os.getenv("FINCH_VM_ZONE", "asia-south1-a"),
        "gcs_bucket": os.getenv("FINCH_GCS_BUCKET", "finch-514709-mumbai-lake"),
        "budget_inr": float(os.getenv("FINCH_BUDGET_INR", "12500")),
        "mlflow_uri": os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"),
    }
