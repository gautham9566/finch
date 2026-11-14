"""Read-only PostgreSQL query helpers for the Finch monitoring API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from libs.common.config import (
    DATA_DIR,
    get_costs_config,
    get_effective_risk_config,
    get_risk_config,
    get_schedule_config,
    load_all_model_configs,
)
from libs.common.db import get_conn, init_schema
from libs.common.logging import get_logger
from pipelines.data.features import FEATURE_COLUMNS, FEATURE_DESCRIPTIONS
from pipelines.data.intraday import BrokerIntradayAdapter

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.web.db_queries")


def _serialize(obj: Any) -> Any:
    """Convert dates, datetimes, and Decimals into JSON-safe primitives."""
    if isinstance(obj, datetime):
        return obj.astimezone(IST).isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    return obj


def _rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        init_schema()
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            return [_serialize(dict(r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Database query skipped (offline/unavailable): {exc}")
        return []


def _one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = _rows(query, params)
    return rows[0] if rows else None


def get_health_payload() -> dict[str, Any]:
    db_ok = False
    latest_quality: dict[str, Any] | None = None
    latest_job: dict[str, Any] | None = None
    kill_switch = bool(get_risk_config().get("kill_switch", False))

    try:
        one = _one("SELECT pg_database_size(current_database()) AS db_bytes")
        db_ok = one is not None
        latest_quality = _one(
            """
            SELECT trade_date, tradeable, checked_at_ist, intraday_status
            FROM data_quality_reports
            ORDER BY trade_date DESC
            LIMIT 1
            """
        )
        latest_job = _one(
            """
            SELECT job_name, status, started_at_ist, finished_at_ist, duration_sec
            FROM job_runs
            ORDER BY id DESC
            LIMIT 1
            """
        )
        ks_row = _one("SELECT value FROM system_state WHERE key = 'kill_switch'")
        if ks_row is not None:
            kill_switch = str(ks_row["value"]).lower() in {"1", "true", "on", "yes"}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Health query warning: {exc}")

    return {
        "status": "ok" if db_ok else "degraded",
        "version": "0.2.0",
        "mode": "read_only_paper",
        "db_connected": db_ok,
        "kill_switch": kill_switch,
        "latest_quality": latest_quality,
        "latest_job": latest_job,
        "checked_at_ist": datetime.now(IST).isoformat(),
    }


def get_overview_payload() -> dict[str, Any]:
    health = get_health_payload()
    risk_cfg = get_risk_config()

    champion = _one(
        """
        SELECT r.model_name, r.model_type, r.agent_name, r.status, r.dataset_version,
               r.trained_at_ist, s.train_auc, s.val_auc, s.test_auc, s.ece,
               s.net_return_pct, s.max_drawdown_pct, s.win_rate, s.profit_factor,
               s.promotion_status
        FROM model_registry r
        LEFT JOIN LATERAL (
            SELECT * FROM model_scorecards ms
            WHERE ms.model_name = r.model_name
            ORDER BY ms.evaluated_at_ist DESC, ms.id DESC
            LIMIT 1
        ) s ON TRUE
        WHERE LOWER(COALESCE(s.promotion_status, r.status)) = 'champion'
        ORDER BY COALESCE(s.test_auc, 0) DESC
        LIMIT 1
        """
    )

    counts = (
        _one(
            """
        SELECT
            (SELECT COUNT(*) FROM model_registry) AS model_count,
            (SELECT COUNT(*) FROM data_sources_status) AS sources_count,
            (SELECT COUNT(*) FROM data_quality_reports) AS quality_days,
            (SELECT COUNT(*) FROM sandbox_journal) AS total_journal_rows,
            (SELECT COUNT(*) FROM sandbox_journal WHERE quantity > 0) AS executed_trades,
            (SELECT COUNT(*) FROM job_runs) AS total_job_runs
        """
        )
        or {}
    )

    champion_equity_curve = _rows(
        """
        SELECT trade_date, model_name, tradeable, candidates_count, executed_count,
               rejected_count, gross_pnl_inr, total_cost_inr, total_slippage_inr,
               net_pnl_inr, equity_inr
        FROM sandbox_daily_summary
        WHERE model_name = %s
        ORDER BY trade_date ASC
        """,
        (champion["model_name"],) if champion else ("__none__",),
    )

    if not champion_equity_curve:
        # Fall back to best-available summary series if champion name isn't set yet
        top_model_row = _one(
            """
            SELECT model_name
            FROM sandbox_daily_summary
            GROUP BY model_name
            ORDER BY COUNT(*) DESC, MAX(equity_inr) DESC
            LIMIT 1
            """
        )
        if top_model_row:
            champion_equity_curve = _rows(
                """
                SELECT trade_date, model_name, tradeable, candidates_count, executed_count,
                       rejected_count, gross_pnl_inr, total_cost_inr, total_slippage_inr,
                       net_pnl_inr, equity_inr
                FROM sandbox_daily_summary
                WHERE model_name = %s
                ORDER BY trade_date ASC
                """,
                (top_model_row["model_name"],),
            )

    recent_quality = _rows(
        """
        SELECT trade_date, checked_at_ist, universe_count, daily_rows, missing_symbols,
               bad_ohlc_rows, illiquid_dropped, global_cues_ok, intraday_status,
               tradeable, reasons_json
        FROM data_quality_reports
        ORDER BY trade_date DESC
        LIMIT 10
        """
    )

    recent_trades = _rows(
        """
        SELECT trade_date, snapshot_slot, model_name, symbol, sector, direction,
               probability, entry_price, target_price, stop_price,
               coordinator_decision, llm_decision, risk_decision, decision_reason,
               is_champion_trade, quantity, fill_price, exit_price, exit_reason,
               slippage_inr, total_cost_inr, gross_pnl_inr, net_pnl_inr,
               counterfactual_pnl_inr, known_as_of
        FROM sandbox_journal
        ORDER BY trade_date DESC, id DESC
        LIMIT 12
        """
    )

    latest_equity = (
        champion_equity_curve[-1]["equity_inr"]
        if champion_equity_curve
        else float(risk_cfg.get("capital_inr", 1000000.0))
    )
    cumulative_net_pnl = round(sum(float(r["net_pnl_inr"]) for r in champion_equity_curve), 2)

    return {
        "health": health,
        "champion": champion,
        "counts": counts,
        "sandbox_kpis": {
            "starting_capital_inr": float(risk_cfg.get("capital_inr", 1000000.0)),
            "latest_equity_inr": latest_equity,
            "cumulative_net_pnl_inr": cumulative_net_pnl,
            "replay_days": len(champion_equity_curve),
        },
        "champion_equity_curve": champion_equity_curve,
        "recent_quality": recent_quality,
        "recent_trades": recent_trades,
    }


def get_data_payload() -> dict[str, Any]:
    sources = _rows(
        """
        SELECT source_name, status, coverage_start, coverage_end,
               row_count, last_updated_ist, notes
        FROM data_sources_status
        ORDER BY source_name ASC
        """
    )
    datasets = _rows(
        """
        SELECT version_id, created_at_ist, row_count, symbol_count,
               min_date, max_date, feature_columns_json, parquet_path
        FROM dataset_versions
        ORDER BY created_at_ist DESC
        LIMIT 15
        """
    )
    quality_reports = _rows(
        """
        SELECT trade_date, checked_at_ist, universe_count, daily_rows, missing_symbols,
               bad_ohlc_rows, illiquid_dropped, global_cues_ok, intraday_status,
               tradeable, reasons_json
        FROM data_quality_reports
        ORDER BY trade_date DESC
        LIMIT 45
        """
    )
    adapter = BrokerIntradayAdapter()
    return {
        "sources": sources,
        "datasets": datasets,
        "quality_reports": quality_reports,
        "intraday_configured": adapter.is_configured(),
        "features": [{"name": name, "description": FEATURE_DESCRIPTIONS.get(name, "")} for name in FEATURE_COLUMNS],
    }


def get_models_payload() -> dict[str, Any]:
    registered = _rows(
        """
        SELECT r.model_name, r.model_type, r.agent_name, r.status, r.dataset_version,
               r.mlflow_run_id, r.git_commit, r.config_json, r.artifact_path, r.trained_at_ist,
               s.evaluated_at_ist, s.train_auc, s.val_auc, s.test_auc, s.train_val_gap,
               s.overfit_flag, s.underfit_flag, s.ece, s.net_return_pct,
               s.max_drawdown_pct, s.win_rate, s.profit_factor, s.beats_baseline,
               s.promotion_status, s.regime_breakdown_json, s.reliability_bins_json
        FROM model_registry r
        LEFT JOIN LATERAL (
            SELECT * FROM model_scorecards ms
            WHERE ms.model_name = r.model_name
            ORDER BY ms.evaluated_at_ist DESC, ms.id DESC
            LIMIT 1
        ) s ON TRUE
        ORDER BY COALESCE(s.net_return_pct, -999) DESC, COALESCE(s.test_auc, 0) DESC, r.model_name ASC
        """
    )

    configured = [
        {
            "model_name": c.get("name"),
            "model_type": c.get("model_type"),
            "agent_name": c.get("agent"),
            "description": f"{c.get('model_type')} ({c.get('agent')})",
            "is_baseline": bool(c.get("is_baseline", False)),
        }
        for c in load_all_model_configs()
    ]

    date_bounds: dict[str, str | None] = {"min_date": None, "max_date": None}
    feat_path = DATA_DIR / "features" / "features_daily.parquet"
    if feat_path.exists():
        try:
            date_df = pd.read_parquet(feat_path, columns=["trade_date"])
            if not date_df.empty and "trade_date" in date_df.columns:
                parsed_dates = pd.to_datetime(date_df["trade_date"], errors="coerce").dropna().dt.date
                if not parsed_dates.empty:
                    date_bounds = {
                        "min_date": parsed_dates.min().isoformat(),
                        "max_date": parsed_dates.max().isoformat(),
                    }
        except Exception as exc:  # noqa: BLE001
            log.warning(f"Failed to read date bounds from features_daily.parquet: {exc}")

    if date_bounds["min_date"] is None or date_bounds["max_date"] is None:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT MIN(date_start) AS min_date, MAX(date_end) AS max_date FROM dataset_versions"
                )
                row = cur.fetchone()
                if row:
                    date_bounds = {
                        "min_date": _serialize(row.get("min_date")),
                        "max_date": _serialize(row.get("max_date")),
                    }
        except Exception as exc:  # noqa: BLE001
            log.warning(f"Failed to read date bounds from dataset_versions: {exc}")

    return {
        "models": registered,
        "configured_models": configured,
        "date_bounds": date_bounds,
    }


def get_rankings_payload() -> dict[str, Any]:
    ladder = _rows(
        """
        WITH latest AS (
            SELECT DISTINCT ON (model_name)
                model_name, dataset_version, evaluated_at_ist, train_auc, val_auc, test_auc,
                train_val_gap, overfit_flag, underfit_flag, ece, net_return_pct,
                max_drawdown_pct, win_rate, profit_factor, beats_baseline, promotion_status
            FROM model_scorecards
            ORDER BY model_name, evaluated_at_ist DESC, id DESC
        )
        SELECT ROW_NUMBER() OVER (
            ORDER BY
                CASE LOWER(promotion_status)
                    WHEN 'champion' THEN 1
                    WHEN 'shadow' THEN 2
                    WHEN 'candidate' THEN 3
                    WHEN 'retired' THEN 4
                    ELSE 5
                END,
                net_return_pct DESC,
                COALESCE(test_auc, 0) DESC
        ) AS rank,
        latest.*,
        r.model_type, r.agent_name, r.mlflow_run_id
        FROM latest
        LEFT JOIN model_registry r ON r.model_name = latest.model_name
        ORDER BY rank ASC
        """
    )

    history = _rows(
        """
        SELECT evaluated_at_ist, dataset_version, model_name, test_auc,
               net_return_pct, win_rate, max_drawdown_pct, promotion_status,
               RANK() OVER (
                   PARTITION BY dataset_version
                   ORDER BY net_return_pct DESC, COALESCE(test_auc, 0) DESC
               ) AS rank
        FROM model_scorecards
        ORDER BY evaluated_at_ist ASC, rank ASC
        """
    )

    return {
        "ladder": ladder,
        "history": history,
    }


def get_sandbox_payload(
    limit: int = 100,
    model_name: str | None = None,
    trade_date: str | None = None,
    only_champion: bool = False,
) -> dict[str, Any]:
    capped_limit = max(1, min(limit, 500))

    daily_summaries = _rows(
        """
        SELECT trade_date, model_name, tradeable, candidates_count, executed_count,
               rejected_count, gross_pnl_inr, total_cost_inr, total_slippage_inr,
               net_pnl_inr, equity_inr
        FROM sandbox_daily_summary
        ORDER BY trade_date ASC, model_name ASC
        """
    )

    clauses = ["1=1"]
    params: list[Any] = []
    if model_name:
        clauses.append("model_name = %s")
        params.append(model_name)
    if trade_date:
        clauses.append("trade_date = %s")
        params.append(trade_date)
    if only_champion:
        clauses.append("is_champion_trade = TRUE")

    where_sql = " AND ".join(clauses)
    params.append(capped_limit)

    journal = _rows(
        f"""
        SELECT id, trade_date, snapshot_slot, model_name, model_version, dataset_version,
               isin, symbol, sector, direction, probability, entry_price, target_price,
               stop_price, coordinator_decision, llm_decision, risk_decision, decision_reason,
               is_champion_trade, quantity, fill_price, exit_price, exit_reason,
               slippage_inr, total_cost_inr, gross_pnl_inr, net_pnl_inr,
               counterfactual_pnl_inr, counterfactual_exit_reason, known_as_of
        FROM sandbox_journal
        WHERE {where_sql}
        ORDER BY trade_date DESC, id DESC
        LIMIT %s
        """,
        tuple(params),
    )

    model_totals = _rows(
        """
        SELECT model_name,
               COUNT(*) AS days,
               SUM(candidates_count) AS total_candidates,
               SUM(executed_count) AS total_executed,
               SUM(rejected_count) AS total_rejected,
               ROUND(SUM(gross_pnl_inr)::numeric, 2) AS total_gross_pnl_inr,
               ROUND(SUM(total_cost_inr)::numeric, 2) AS total_cost_inr,
               ROUND(SUM(total_slippage_inr)::numeric, 2) AS total_slippage_inr,
               ROUND(SUM(net_pnl_inr)::numeric, 2) AS total_net_pnl_inr,
               MAX(equity_inr) FILTER (
                   WHERE trade_date = (SELECT MAX(trade_date) FROM sandbox_daily_summary)
               ) AS latest_equity_inr
        FROM sandbox_daily_summary
        GROUP BY model_name
        ORDER BY total_net_pnl_inr DESC
        """
    )

    health = get_health_payload()
    risk_cfg = get_effective_risk_config()
    costs_cfg = get_costs_config()
    available_models = [
        str(c["name"]) for c in load_all_model_configs() if c.get("name") != "regime_classifier"
    ]
    available_dates: list[str] = []
    feat_path = DATA_DIR / "features" / "features_daily.parquet"
    if feat_path.exists():
        try:
            fdf = pd.read_parquet(feat_path, columns=["trade_date"])
            available_dates = sorted(
                {pd.to_datetime(dt).date().isoformat() for dt in fdf["trade_date"].dropna()},
                reverse=True,
            )[:90]
        except Exception as exc:  # noqa: BLE001
            log.warning(f"Failed to read available_dates: {exc}")
    if not available_dates:
        available_dates = [
            str(r["trade_date"])
            for r in _rows("SELECT trade_date FROM data_quality_reports ORDER BY trade_date DESC LIMIT 90")
        ]

    return {
        "risk_config": {
            "capital_inr": risk_cfg.get("capital_inr"),
            "risk_per_trade_pct": risk_cfg.get("risk_per_trade_pct"),
            "max_position_pct": risk_cfg.get("max_position_pct"),
            "max_sector_pct": risk_cfg.get("max_sector_pct"),
            "max_trades_per_day": risk_cfg.get("max_trades_per_day"),
            "max_daily_loss_pct": risk_cfg.get("max_daily_loss_pct"),
            "max_opening_gap_pct": risk_cfg.get("max_opening_gap_pct"),
            "entry_cutoff_ist": risk_cfg.get("entry_cutoff_ist"),
            "square_off_ist": risk_cfg.get("square_off_ist"),
            "min_probability": risk_cfg.get("min_probability"),
            "direction_filter": risk_cfg.get("direction_filter", "BOTH"),
            "preferred_champion": risk_cfg.get("preferred_champion", ""),
            "kill_switch": bool(health["kill_switch"] or risk_cfg.get("kill_switch", False)),
        },
        "costs_config": costs_cfg,
        "available_models": available_models,
        "available_dates": available_dates,
        "model_totals": model_totals,
        "daily_summaries": daily_summaries,
        "journal": journal,
    }


def get_jobs_payload(limit: int = 80) -> dict[str, Any]:
    capped = max(1, min(limit, 300))
    schedule_cfg = get_schedule_config()
    runs = _rows(
        """
        SELECT id, job_name, started_at_ist, finished_at_ist, duration_sec, status, summary
        FROM job_runs
        ORDER BY id DESC
        LIMIT %s
        """,
        (capped,),
    )
    return {
        "timezone": schedule_cfg.get("timezone", "Asia/Kolkata"),
        "schedules": schedule_cfg.get("jobs", []),
        "runs": runs,
    }
