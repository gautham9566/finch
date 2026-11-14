"""CLI entrypoint for Pipeline 2 (Training)."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from libs.common.config import DATA_DIR, load_all_model_configs, load_yaml
from libs.common.db import get_conn, init_schema
from libs.common.logging import get_logger
from pipelines.training.distillation import run_distillation_if_ready
from pipelines.training.models import build_and_fit_model
from pipelines.training.registry import (
    get_git_commit,
    log_to_mlflow,
    persist_registry_and_scorecard,
    save_model_artifact,
)
from pipelines.training.scorecard import build_model_scorecard, rank_and_assign_statuses
from pipelines.training.walk_forward import make_walk_forward_splits

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.training.cli")


def _in_nse_market_hours(now_ist: datetime) -> bool:
    if now_ist.weekday() >= 5:
        return False
    t = now_ist.time()
    return dtime(9, 0) <= t <= dtime(15, 30)


def train_single_model(
    model_cfg: dict[str, Any], df: pd.DataFrame, dataset_version: str, git_commit: str
) -> dict[str, Any]:
    """Train one configured model across walk-forward folds and evaluate on untouched test period."""
    name = model_cfg["name"]
    features = model_cfg["features"]
    wf = model_cfg.get("walk_forward", {})

    folds, test_idx = make_walk_forward_splits(
        df,
        n_folds=int(wf.get("n_folds", 4)),
        min_train_days=int(wf.get("min_train_days", 250)),
        val_days=int(wf.get("val_days", 60)),
        test_holdout_days=int(wf.get("test_holdout_days", 90)),
        purge_days=int(wf.get("purge_days", 2)),
    )

    last_model = None
    last_tr_y = np.array([])
    last_tr_p = np.array([])
    val_y_all: list[np.ndarray] = []
    val_p_all: list[np.ndarray] = []

    for fold in folds:
        X_tr = df.loc[fold.train_idx, features]
        y_tr = df.loc[fold.train_idx, "target_hit"].to_numpy()
        X_va = df.loc[fold.val_idx, features]
        y_va = df.loc[fold.val_idx, "target_hit"].to_numpy()

        model = build_and_fit_model(model_cfg, X_tr, y_tr, X_va, y_va)
        p_va = model.predict_proba(X_va)[:, 1]
        val_y_all.append(y_va)
        val_p_all.append(p_va)

        last_model = model
        last_tr_y = y_tr
        last_tr_p = model.predict_proba(X_tr)[:, 1]

    assert last_model is not None
    df_test = df.loc[test_idx].copy()
    p_test = last_model.predict_proba(df_test[features])[:, 1]
    y_val_cat = np.concatenate(val_y_all) if val_y_all else last_tr_y
    p_val_cat = np.concatenate(val_p_all) if val_p_all else last_tr_p

    sc = build_model_scorecard(
        model_name=name,
        y_train=last_tr_y,
        p_train=last_tr_p,
        y_val=y_val_cat,
        p_val=p_val_cat,
        df_test=df_test,
        p_test=p_test,
        is_baseline=bool(model_cfg.get("is_baseline", False)),
    )

    bundle = {
        "model_name": name,
        "model_type": model_cfg["model_type"],
        "agent": model_cfg["agent"],
        "features": features,
        "dataset_version": dataset_version,
        "model": last_model,
    }
    artifact_path = save_model_artifact(name, bundle)
    run_id = log_to_mlflow(name, model_cfg, dataset_version, git_commit, sc, artifact_path)
    return {
        "config": model_cfg,
        "scorecard": sc,
        "artifact_path": artifact_path,
        "mlflow_run_id": run_id,
    }


def train_all(allow_market_hours: bool = False) -> list[dict[str, Any]]:
    started = datetime.now(IST)
    if _in_nse_market_hours(started) and not allow_market_hours:
        raise RuntimeError(
            "Training blocked during NSE market hours (09:00-15:30 IST). Pass --allow-market-hours to override."
        )

    init_schema()
    feat_path = DATA_DIR / "features" / "features_daily.parquet"
    df = pd.read_parquet(feat_path)
    dataset_version = str(df["dataset_version"].iloc[0]) if "dataset_version" in df.columns else "unknown"
    git_commit = get_git_commit()

    configs = [c for c in load_all_model_configs() if c.get("enabled", True)]
    log.info(f"Training {len(configs)} models on dataset {dataset_version} (commit={git_commit})")

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(train_single_model, cfg, df, dataset_version, git_commit) for cfg in configs]
        for fut in futures:
            results.append(fut.result())

    ranked = rank_and_assign_statuses([r["scorecard"] for r in results])
    by_name = {r["config"]["name"]: r for r in results}
    for sc in ranked:
        r = by_name[sc["model_name"]]
        persist_registry_and_scorecard(
            model_cfg=r["config"],
            dataset_version=dataset_version,
            mlflow_run_id=r["mlflow_run_id"],
            git_commit=git_commit,
            artifact_path=r["artifact_path"],
            scorecard=sc,
        )

    distill = run_distillation_if_ready()
    finished = datetime.now(IST)
    champ = next((s["model_name"] for s in ranked if s["promotion_status"] == "champion"), "none")
    summary = f"trained={len(ranked)}, champion={champ}, dataset={dataset_version}, distillation={distill['status']}"
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_runs (job_name, started_at_ist, finished_at_ist, duration_sec, status, summary)
                VALUES ('training_all', %s, %s, %s, 'success', %s)
                """,
                (started, finished, round((finished - started).total_seconds(), 2), summary),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Job run record skipped: {exc}")
    log.info(f"Training complete: {summary}")
    return ranked


def main() -> None:
    parser = argparse.ArgumentParser(description="Finch Pipeline 2 (Training)")
    parser.add_argument("command", choices=["train-all", "train-one", "status"])
    parser.add_argument("--config", type=str, help="Path to a model YAML config for train-one")
    parser.add_argument("--allow-market-hours", action="store_true", help="Override market-hours guard")
    args = parser.parse_args()

    if args.command == "train-all":
        ranked = train_all(allow_market_hours=args.allow_market_hours)
        for s in ranked:
            print(
                f"{s['model_name']:22s} status={s['promotion_status']:9s} "
                f"ret={s['net_return_pct']:+7.3f}% dd={s['max_drawdown_pct']:6.3f}% "
                f"pf={s['profit_factor']:5.2f} ece={s['ece']:6.4f} "
                f"auc(tr/va/te)={s['train_auc']:.3f}/{s['val_auc']:.3f}/{s['test_auc']:.3f}"
            )
    elif args.command == "train-one":
        if not args.config:
            raise SystemExit("--config is required for train-one")
        init_schema()
        df = pd.read_parquet(DATA_DIR / "features" / "features_daily.parquet")
        dv = str(df["dataset_version"].iloc[0])
        res = train_single_model(load_yaml(Path(args.config)), df, dv, get_git_commit())
        print(res["scorecard"])
    elif args.command == "status":
        print("Finch Training Pipeline healthy")


if __name__ == "__main__":
    main()
