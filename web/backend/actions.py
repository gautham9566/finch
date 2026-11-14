"""Interactive action handlers for Paper Trading options, simulation, reset, and model training."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import HTTPException

from libs.common.config import (
    DATA_DIR,
    get_effective_risk_config,
    get_risk_config,
    load_all_model_configs,
)
from libs.common.db import get_conn, init_schema
from libs.common.logging import get_logger
from pipelines.sandbox.journal import reset_sandbox_trades
from pipelines.sandbox.replay import replay_date
from pipelines.training.cli import filter_training_dataframe, train_all, train_single_model
from pipelines.training.registry import get_git_commit, persist_registry_and_scorecard

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.web.actions")

_TRAIN_LOCK = threading.Lock()
_TRAIN_STATE: dict[str, Any] = {
    "status": "idle",
    "model_name": None,
    "started_at_ist": None,
    "finished_at_ist": None,
    "duration_sec": None,
    "summary": None,
    "error": None,
    "results": [],
}

_VALID_DIRECTIONS = {"BOTH", "LONG", "SHORT"}


def list_available_models() -> list[str]:
    """Return configured trainable / paper-tradable model names."""
    return [str(c["name"]) for c in load_all_model_configs() if c.get("name") != "regime_classifier"]


def list_available_dates(limit: int = 90) -> list[str]:
    """Return available trading dates (descending ISO strings) from feature store or DB."""
    feat_path = DATA_DIR / "features" / "features_daily.parquet"
    if feat_path.exists():
        try:
            df = pd.read_parquet(feat_path, columns=["trade_date"])
            dates = sorted(
                {pd.to_datetime(d).date().isoformat() for d in df["trade_date"].dropna()},
                reverse=True,
            )
            return dates[:limit]
        except Exception as exc:  # noqa: BLE001
            log.warning(f"Could not read dates from {feat_path}: {exc}")

    try:
        init_schema()
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date FROM data_quality_reports ORDER BY trade_date DESC LIMIT %s",
                (limit,),
            )
            return [r["trade_date"].isoformat() for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        return []


def get_paper_options_payload() -> dict[str, Any]:
    """Return effective paper-trading options alongside YAML defaults and selectable models/dates."""
    return {
        "options": get_effective_risk_config(),
        "defaults": get_risk_config(),
        "available_models": list_available_models(),
        "available_dates": list_available_dates(),
    }


def _clamp_float(val: Any, lo: float, hi: float, field: str) -> float:
    try:
        num = float(val)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid numeric value for '{field}': {val}") from exc
    return max(lo, min(hi, num))


def _clamp_int(val: Any, lo: int, hi: int, field: str) -> int:
    try:
        num = int(val)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid integer value for '{field}': {val}") from exc
    return max(lo, min(hi, num))


def update_paper_options(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist runtime paper-trading options into Postgres system_state."""
    now_ist = datetime.now(IST)
    available_models = set(list_available_models())

    if bool(payload.get("reset_defaults")):
        init_schema()
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM system_state WHERE key = 'paper_trading_options'")
            cur.execute(
                """
                INSERT INTO system_state (key, value, updated_at_ist)
                VALUES ('kill_switch', 'false', %s)
                ON CONFLICT (key) DO UPDATE SET value = 'false', updated_at_ist = EXCLUDED.updated_at_ist
                """,
                (now_ist,),
            )
            cur.execute(
                """
                INSERT INTO job_runs (job_name, started_at_ist, finished_at_ist, duration_sec, status, summary)
                VALUES (
                    'paper_options_reset', %s, %s, 0.0, 'success',
                    'Reset paper trading options to config/risk.yaml defaults'
                )
                """,
                (now_ist, now_ist),
            )
        return {"status": "ok", "reset": True, **get_paper_options_payload()}

    current = get_effective_risk_config()

    def _fopt(key: str, default: float, lo: float, hi: float, digits: int = 4) -> float:
        return round(_clamp_float(payload.get(key, current.get(key, default)), lo, hi, key), digits)

    updated: dict[str, Any] = {
        "capital_inr": _fopt("capital_inr", 1_000_000.0, 10_000.0, 100_000_000.0, 2),
        "risk_per_trade_pct": _fopt("risk_per_trade_pct", 0.005, 0.0005, 0.05, 6),
        "max_position_pct": _fopt("max_position_pct", 0.10, 0.01, 0.50, 4),
        "max_sector_pct": _fopt("max_sector_pct", 0.25, 0.05, 1.0, 4),
        "max_trades_per_day": _clamp_int(
            payload.get("max_trades_per_day", current.get("max_trades_per_day", 6)),
            1,
            50,
            "max_trades_per_day",
        ),
        "max_daily_loss_pct": _fopt("max_daily_loss_pct", 0.02, 0.002, 0.20, 4),
        "max_opening_gap_pct": _fopt("max_opening_gap_pct", 0.035, 0.005, 0.15, 4),
        "min_probability": _fopt("min_probability", 0.52, 0.50, 0.90, 4),
        "direction_filter": str(
            payload.get("direction_filter", current.get("direction_filter", "BOTH"))
        ).upper(),
        "preferred_champion": str(
            payload.get("preferred_champion", current.get("preferred_champion", "")) or ""
        ).strip(),
        "kill_switch": bool(payload.get("kill_switch", current.get("kill_switch", False))),
    }

    if updated["direction_filter"] not in _VALID_DIRECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"direction_filter must be one of {sorted(_VALID_DIRECTIONS)}",
        )
    if updated["preferred_champion"] and updated["preferred_champion"] not in available_models:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown preferred_champion '{updated['preferred_champion']}'. "
                f"Available: {sorted(available_models)}"
            ),
        )

    kill_val = "true" if updated["kill_switch"] else "false"
    summary = (
        f"capital={updated['capital_inr']:.0f}, risk={updated['risk_per_trade_pct']*100:.2f}%, "
        f"max_trades={updated['max_trades_per_day']}, min_prob={updated['min_probability']:.2f}, "
        f"dir={updated['direction_filter']}, champion={updated['preferred_champion'] or 'auto'}, kill={kill_val}"
    )

    init_schema()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO system_state (key, value, updated_at_ist)
            VALUES ('paper_trading_options', %s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at_ist = EXCLUDED.updated_at_ist
            """,
            (json.dumps(updated), now_ist),
        )
        cur.execute(
            """
            INSERT INTO system_state (key, value, updated_at_ist)
            VALUES ('kill_switch', %s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at_ist = EXCLUDED.updated_at_ist
            """,
            (kill_val, now_ist),
        )
        cur.execute(
            """
            INSERT INTO job_runs (job_name, started_at_ist, finished_at_ist, duration_sec, status, summary)
            VALUES ('paper_options_update', %s, %s, 0.0, 'success', %s)
            """,
            (now_ist, now_ist, summary),
        )

    return {"status": "ok", "summary": summary, **get_paper_options_payload()}


def trigger_reset_trades() -> dict[str, Any]:
    """Clear simulated sandbox journal and summary tables."""
    try:
        res = reset_sandbox_trades()
        return {"status": "ok", **res}
    except Exception as exc:
        log.exception("Failed to reset sandbox trades")
        raise HTTPException(status_code=500, detail=f"Failed to reset trades: {exc}") from exc


def trigger_simulation(payload: dict[str, Any]) -> dict[str, Any]:
    """Run live / selected-date or multi-day paper-trading signal simulation."""
    feat_path = DATA_DIR / "features" / "features_daily.parquet"
    if not feat_path.exists():
        raise HTTPException(
            status_code=400,
            detail="Feature store (features_daily.parquet) not found. Run Pipeline 1 (data eod) first.",
        )

    mode = str(payload.get("mode", "single_date")).lower()
    slot = str(payload.get("slot", "09:20"))
    if slot not in {"09:20", "08:45"}:
        raise HTTPException(status_code=400, detail="slot must be '09:20' or '08:45'")

    raw_overrides = payload.get("options")
    risk_overrides: dict[str, Any] = dict(raw_overrides) if isinstance(raw_overrides, dict) else {}
    if payload.get("model_name"):
        risk_overrides["preferred_champion"] = str(payload["model_name"]).strip()

    available_dates = list_available_dates(limit=500)
    if not available_dates:
        raise HTTPException(status_code=400, detail="No trading dates available in features_daily.parquet")

    if mode == "replay_range":
        days = _clamp_int(payload.get("days", 5), 1, 60, "days")
        chrono_dates = sorted(available_dates[:days])
        results: list[dict[str, Any]] = []
        for iso in chrono_dates:
            results.append(replay_date(date.fromisoformat(iso), slot=slot, risk_overrides=risk_overrides or None))
        total_exec = sum(int(r.get("executed_champion_trades", 0)) for r in results)
        total_pnl = round(sum(float(r.get("champion_net_pnl_inr", 0.0)) for r in results), 2)
        latest = results[-1] if results else {}
        return {
            "status": "ok",
            "mode": mode,
            "slot": slot,
            "days_simulated": len(results),
            "start_date": chrono_dates[0] if chrono_dates else None,
            "end_date": chrono_dates[-1] if chrono_dates else None,
            "executed_champion_trades": total_exec,
            "champion_net_pnl_inr": total_pnl,
            "champion": latest.get("champion"),
            "trade_date": latest.get("trade_date"),
            "tradeable": latest.get("tradeable", True),
            "signals": latest.get("signals", []),
            "daily_results": [
                {k: v for k, v in r.items() if k != "signals"} for r in results
            ],
        }

    req_date = str(payload.get("trade_date") or "").strip()
    if mode == "live" or not req_date or req_date.lower() == "latest":
        target_date = date.fromisoformat(available_dates[0])
    else:
        try:
            target_date = date.fromisoformat(req_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid trade_date '{req_date}'. Use YYYY-MM-DD.") from exc
        if target_date.isoformat() not in set(available_dates):
            raise HTTPException(
                status_code=400,
                detail=f"No feature rows found for {target_date.isoformat()}. Latest available: {available_dates[0]}.",
            )

    res = replay_date(target_date, slot=slot, risk_overrides=risk_overrides or None)
    return {
        "status": "ok",
        "mode": mode,
        **res,
    }


def _compact_scorecard(sc: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_name": str(sc.get("model_name", "")),
        "promotion_status": str(sc.get("promotion_status", "candidate")),
        "train_auc": round(float(sc.get("train_auc", 0.0)), 4),
        "val_auc": round(float(sc.get("val_auc", 0.0)), 4),
        "test_auc": round(float(sc.get("test_auc", 0.0)), 4),
        "ece": round(float(sc.get("ece", 0.0)), 4),
        "net_return_pct": round(float(sc.get("net_return_pct", 0.0)), 4),
        "max_drawdown_pct": round(float(sc.get("max_drawdown_pct", 0.0)), 4),
        "win_rate": round(float(sc.get("win_rate", 0.0)), 4),
        "profit_factor": round(float(sc.get("profit_factor", 0.0)), 4),
        "overfit_flag": bool(sc.get("overfit_flag", False)),
        "underfit_flag": bool(sc.get("underfit_flag", False)),
    }


def _parse_training_date_range(payload: dict[str, Any]) -> tuple[date | None, date | None]:
    """Validate optional start_date/end_date ISO date strings for training."""
    raw_start = str(payload.get("start_date") or "").strip()
    raw_end = str(payload.get("end_date") or "").strip()
    if not raw_start and not raw_end:
        return None, None
    if not raw_start or not raw_end:
        raise HTTPException(
            status_code=400,
            detail="Both start_date and end_date must be provided together (YYYY-MM-DD).",
        )
    try:
        start_d = date.fromisoformat(raw_start)
        end_d = date.fromisoformat(raw_end)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid training date format. Expected YYYY-MM-DD: {exc}",
        ) from exc
    if start_d > end_d:
        raise HTTPException(
            status_code=400,
            detail=f"start_date ({raw_start}) must be on or before end_date ({raw_end}).",
        )
    return start_d, end_d


def _execute_training(
    target_model: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    started = datetime.now(IST)
    feat_path = DATA_DIR / "features" / "features_daily.parquet"
    if not feat_path.exists():
        raise FileNotFoundError("Feature dataset not found. Run Pipeline 1 (data eod) first.")

    start_iso = start_date.isoformat() if start_date else None
    end_iso = end_date.isoformat() if end_date else None

    if target_model == "all":
        ranked = train_all(
            allow_market_hours=True,
            start_date=start_date,
            end_date=end_date,
        )
        champ = next((s["model_name"] for s in ranked if s.get("promotion_status") == "champion"), "none")
        finished = datetime.now(IST)
        window_note = f" [{start_iso}..{end_iso}]" if start_iso and end_iso else ""
        summary = f"Trained all {len(ranked)} models{window_note}; champion={champ}"
        return {
            "status": "completed",
            "model_name": "all",
            "start_date": start_iso,
            "end_date": end_iso,
            "started_at_ist": started.isoformat(),
            "finished_at_ist": finished.isoformat(),
            "duration_sec": round((finished - started).total_seconds(), 2),
            "summary": summary,
            "error": None,
            "results": [_compact_scorecard(s) for s in ranked],
        }

    configs = {str(c["name"]): c for c in load_all_model_configs() if c.get("enabled", True)}
    if target_model not in configs:
        raise ValueError(f"Unknown or disabled model '{target_model}'. Available: {sorted(configs.keys())}")

    init_schema()
    df = pd.read_parquet(feat_path)
    df, dataset_version = filter_training_dataframe(df, start_date=start_date, end_date=end_date)
    git_commit = get_git_commit()
    res = train_single_model(configs[target_model], df, dataset_version, git_commit)
    sc = res["scorecard"]

    prev_status = "candidate"
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM model_registry WHERE model_name = %s", (target_model,))
            row = cur.fetchone()
            if row and row.get("status"):
                prev_status = str(row["status"])
    except Exception:  # noqa: BLE001
        pass

    sc["promotion_status"] = prev_status
    persist_registry_and_scorecard(
        model_cfg=res["config"],
        dataset_version=dataset_version,
        mlflow_run_id=res["mlflow_run_id"],
        git_commit=git_commit,
        artifact_path=res["artifact_path"],
        scorecard=sc,
    )

    finished = datetime.now(IST)
    duration = round((finished - started).total_seconds(), 2)
    window_note = f" [{start_iso}..{end_iso}]" if start_iso and end_iso else ""
    summary = (
        f"model={target_model}{window_note}, status={prev_status}, test_auc={sc['test_auc']:.3f}, "
        f"net_ret={sc['net_return_pct']:+.2f}%"
    )
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_runs (job_name, started_at_ist, finished_at_ist, duration_sec, status, summary)
                VALUES ('training_one', %s, %s, %s, 'success', %s)
                """,
                (started, finished, duration, summary),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Job run record skipped for training_one: {exc}")

    return {
        "status": "completed",
        "model_name": target_model,
        "start_date": start_iso,
        "end_date": end_iso,
        "started_at_ist": started.isoformat(),
        "finished_at_ist": finished.isoformat(),
        "duration_sec": duration,
        "summary": summary,
        "error": None,
        "results": [_compact_scorecard(sc)],
    }


def get_training_status() -> dict[str, Any]:
    """Return latest UI-triggered training execution state."""
    return dict(_TRAIN_STATE)


def trigger_model_training(payload: dict[str, Any]) -> dict[str, Any]:
    """Trigger walk-forward model training for all models or a selected model."""
    global _TRAIN_STATE
    target_model = str(payload.get("model_name") or "all").strip()
    async_mode = bool(payload.get("async_mode", False))
    start_date, end_date = _parse_training_date_range(payload)

    if target_model != "all" and target_model not in set(list_available_models()):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model_name '{target_model}'. Expected 'all' or one of {list_available_models()}.",
        )

    if not _TRAIN_LOCK.acquire(blocking=False):
        return {
            **_TRAIN_STATE,
            "already_running": True,
        }

    start_iso = start_date.isoformat() if start_date else None
    end_iso = end_date.isoformat() if end_date else None

    started = datetime.now(IST)
    _TRAIN_STATE = {
        "status": "running",
        "model_name": target_model,
        "start_date": start_iso,
        "end_date": end_iso,
        "started_at_ist": started.isoformat(),
        "finished_at_ist": None,
        "duration_sec": None,
        "summary": f"Training {target_model} in progress...",
        "error": None,
        "results": [],
    }

    def _run_and_release() -> dict[str, Any]:
        global _TRAIN_STATE
        try:
            outcome = _execute_training(target_model, start_date=start_date, end_date=end_date)
            _TRAIN_STATE = outcome
            return outcome
        except Exception as exc:  # noqa: BLE001
            finished = datetime.now(IST)
            log.exception(f"UI training failed for {target_model}")
            _TRAIN_STATE = {
                "status": "failed",
                "model_name": target_model,
                "start_date": start_iso,
                "end_date": end_iso,
                "started_at_ist": started.isoformat(),
                "finished_at_ist": finished.isoformat(),
                "duration_sec": round((finished - started).total_seconds(), 2),
                "summary": f"Training failed for {target_model}: {exc}",
                "error": str(exc),
                "results": [],
            }
            raise
        finally:
            _TRAIN_LOCK.release()

    def _bg_worker() -> None:
        try:
            _run_and_release()
        except Exception:  # noqa: BLE001
            pass

    if async_mode:
        t = threading.Thread(target=_bg_worker, daemon=True)
        t.start()
        return dict(_TRAIN_STATE)

    try:
        return _run_and_release()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Training failed: {exc}") from exc
