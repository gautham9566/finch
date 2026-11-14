"""Model artifact registry, MLflow logger, and Postgres persistence."""

from __future__ import annotations

import json
import pickle
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import mlflow

from libs.common.config import DATA_DIR, REPO_ROOT, get_gcp_settings
from libs.common.db import get_conn
from libs.common.logging import get_logger
from libs.common.storage import upload_file_to_gcs

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.training.registry")


def get_git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def save_model_artifact(model_name: str, bundle: dict[str, Any]) -> Path:
    models_dir = DATA_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"{model_name}.pkl"
    with path.open("wb") as f:
        pickle.dump(bundle, f)
    upload_file_to_gcs(path, f"models/{model_name}.pkl")
    return path


def load_model_artifact(model_name: str) -> dict[str, Any] | None:
    path = DATA_DIR / "models" / f"{model_name}.pkl"
    if not path.exists():
        return None
    with path.open("rb") as f:
        return pickle.load(f)


def log_to_mlflow(
    model_name: str,
    model_cfg: dict[str, Any],
    dataset_version: str,
    git_commit: str,
    scorecard: dict[str, Any],
    artifact_path: Path,
) -> str:
    """Log params, metrics, dataset version, git commit, and artifact to MLflow."""
    tracking_uri = get_gcp_settings()["mlflow_uri"]
    try:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment("finch-nse-intraday")
        with mlflow.start_run(run_name=model_name) as run:
            mlflow.log_params(
                {
                    "model_name": model_name,
                    "model_type": model_cfg["model_type"],
                    "agent": model_cfg["agent"],
                    "dataset_version": dataset_version,
                    "git_commit": git_commit,
                    **{f"p_{k}": v for k, v in model_cfg.get("params", {}).items()},
                }
            )
            mlflow.log_metrics(
                {
                    "train_auc": float(scorecard["train_auc"]),
                    "val_auc": float(scorecard["val_auc"]),
                    "test_auc": float(scorecard["test_auc"]),
                    "ece": float(scorecard["ece"]),
                    "net_return_pct": float(scorecard["net_return_pct"]),
                    "max_drawdown_pct": float(scorecard["max_drawdown_pct"]),
                    "win_rate": float(scorecard["win_rate"]),
                    "profit_factor": float(scorecard["profit_factor"]),
                }
            )
            mlflow.log_artifact(str(artifact_path))
            return str(run.info.run_id)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"MLflow unavailable for {model_name}, recording local run id: {exc}")
        return f"local-{model_name}-{dataset_version}"


def persist_registry_and_scorecard(
    model_cfg: dict[str, Any],
    dataset_version: str,
    mlflow_run_id: str,
    git_commit: str,
    artifact_path: Path,
    scorecard: dict[str, Any],
) -> None:
    now_ist = datetime.now(IST)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry (
                    model_name, model_type, agent_name, status, dataset_version,
                    mlflow_run_id, git_commit, config_json, artifact_path, trained_at_ist
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (model_name) DO UPDATE SET
                    model_type = EXCLUDED.model_type,
                    agent_name = EXCLUDED.agent_name,
                    status = EXCLUDED.status,
                    dataset_version = EXCLUDED.dataset_version,
                    mlflow_run_id = EXCLUDED.mlflow_run_id,
                    git_commit = EXCLUDED.git_commit,
                    config_json = EXCLUDED.config_json,
                    artifact_path = EXCLUDED.artifact_path,
                    trained_at_ist = EXCLUDED.trained_at_ist
                """,
                (
                    model_cfg["name"],
                    model_cfg["model_type"],
                    model_cfg["agent"],
                    scorecard["promotion_status"],
                    dataset_version,
                    mlflow_run_id,
                    git_commit,
                    json.dumps(model_cfg),
                    str(artifact_path),
                    now_ist,
                ),
            )
            cur.execute(
                """
                INSERT INTO model_scorecards (
                    model_name, dataset_version, evaluated_at_ist, train_auc, val_auc, test_auc,
                    train_val_gap, overfit_flag, underfit_flag, ece, net_return_pct, max_drawdown_pct,
                    win_rate, profit_factor, beats_baseline, promotion_status,
                    regime_breakdown_json, reliability_bins_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (model_name, dataset_version) DO UPDATE SET
                    evaluated_at_ist = EXCLUDED.evaluated_at_ist,
                    train_auc = EXCLUDED.train_auc,
                    val_auc = EXCLUDED.val_auc,
                    test_auc = EXCLUDED.test_auc,
                    train_val_gap = EXCLUDED.train_val_gap,
                    overfit_flag = EXCLUDED.overfit_flag,
                    underfit_flag = EXCLUDED.underfit_flag,
                    ece = EXCLUDED.ece,
                    net_return_pct = EXCLUDED.net_return_pct,
                    max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                    win_rate = EXCLUDED.win_rate,
                    profit_factor = EXCLUDED.profit_factor,
                    beats_baseline = EXCLUDED.beats_baseline,
                    promotion_status = EXCLUDED.promotion_status,
                    regime_breakdown_json = EXCLUDED.regime_breakdown_json,
                    reliability_bins_json = EXCLUDED.reliability_bins_json
                """,
                (
                    model_cfg["name"],
                    dataset_version,
                    now_ist,
                    scorecard["train_auc"],
                    scorecard["val_auc"],
                    scorecard["test_auc"],
                    scorecard["train_val_gap"],
                    scorecard["overfit_flag"],
                    scorecard["underfit_flag"],
                    scorecard["ece"],
                    scorecard["net_return_pct"],
                    scorecard["max_drawdown_pct"],
                    scorecard["win_rate"],
                    scorecard["profit_factor"],
                    scorecard["beats_baseline"],
                    scorecard["promotion_status"],
                    json.dumps(scorecard.get("regime_breakdown", {})),
                    json.dumps(scorecard.get("reliability_bins", [])),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB persist skipped for model {model_cfg['name']}: {exc}")
