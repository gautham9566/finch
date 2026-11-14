"""Deterministic sandbox journal and daily summary persistence (Postgres + GCS Parquet)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from libs.common.config import DATA_DIR, get_effective_risk_config
from libs.common.db import get_conn, init_schema
from libs.common.logging import get_logger
from libs.common.storage import upload_file_to_gcs

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.sandbox.journal")


def save_journal_records(records: list[dict[str, Any]], trade_date: date) -> Path:
    """Persist every candidate (approved + rejected + untraded) deterministically."""
    journal_dir = DATA_DIR / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    out_path = journal_dir / f"journal_{trade_date.isoformat()}.parquet"
    df = pd.DataFrame(records)
    df.to_parquet(out_path, index=False)
    upload_file_to_gcs(out_path, f"journal/journal_{trade_date.isoformat()}.parquet")

    if not records:
        return out_path

    try:
        with get_conn() as conn, conn.cursor() as cur:
            for r in records:
                cur.execute(
                    """
                    INSERT INTO sandbox_journal (
                        trade_date, snapshot_slot, model_name, model_version, dataset_version,
                        isin, symbol, sector, direction, probability, entry_price, target_price,
                        stop_price, coordinator_decision, llm_decision, risk_decision, decision_reason,
                        is_champion_trade, quantity, fill_price, exit_price, exit_reason,
                        slippage_inr, total_cost_inr, gross_pnl_inr, net_pnl_inr,
                        counterfactual_pnl_inr, counterfactual_exit_reason, known_as_of
                    )
                    VALUES (
                        %(trade_date)s, %(snapshot_slot)s, %(model_name)s, %(model_version)s,
                        %(dataset_version)s, %(isin)s, %(symbol)s, %(sector)s, %(direction)s,
                        %(probability)s, %(entry_price)s, %(target_price)s, %(stop_price)s,
                        %(coordinator_decision)s, %(llm_decision)s, %(risk_decision)s, %(decision_reason)s,
                        %(is_champion_trade)s, %(quantity)s, %(fill_price)s, %(exit_price)s, %(exit_reason)s,
                        %(slippage_inr)s, %(total_cost_inr)s, %(gross_pnl_inr)s, %(net_pnl_inr)s,
                        %(counterfactual_pnl_inr)s, %(counterfactual_exit_reason)s, %(known_as_of)s
                    )
                    ON CONFLICT (trade_date, snapshot_slot, model_name, symbol) DO UPDATE SET
                        model_version = EXCLUDED.model_version,
                        dataset_version = EXCLUDED.dataset_version,
                        direction = EXCLUDED.direction,
                        probability = EXCLUDED.probability,
                        entry_price = EXCLUDED.entry_price,
                        target_price = EXCLUDED.target_price,
                        stop_price = EXCLUDED.stop_price,
                        coordinator_decision = EXCLUDED.coordinator_decision,
                        llm_decision = EXCLUDED.llm_decision,
                        risk_decision = EXCLUDED.risk_decision,
                        decision_reason = EXCLUDED.decision_reason,
                        is_champion_trade = EXCLUDED.is_champion_trade,
                        quantity = EXCLUDED.quantity,
                        fill_price = EXCLUDED.fill_price,
                        exit_price = EXCLUDED.exit_price,
                        exit_reason = EXCLUDED.exit_reason,
                        slippage_inr = EXCLUDED.slippage_inr,
                        total_cost_inr = EXCLUDED.total_cost_inr,
                        gross_pnl_inr = EXCLUDED.gross_pnl_inr,
                        net_pnl_inr = EXCLUDED.net_pnl_inr,
                        counterfactual_pnl_inr = EXCLUDED.counterfactual_pnl_inr,
                        counterfactual_exit_reason = EXCLUDED.counterfactual_exit_reason,
                        known_as_of = EXCLUDED.known_as_of
                    """,
                    r,
                )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB persist skipped for sandbox_journal: {exc}")
    return out_path


def save_daily_summaries(
    records: list[dict[str, Any]],
    trade_date: date,
    tradeable: bool,
    capital_inr: float | None = None,
) -> None:
    """Aggregate per-model daily P/L and equity curve deterministically."""
    if not records:
        return
    capital = (
        float(capital_inr)
        if capital_inr is not None
        else float(get_effective_risk_config().get("capital_inr", 1_000_000.0))
    )
    df = pd.DataFrame(records)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            for model_name, grp in df.groupby("model_name", sort=True):
                cur.execute(
                    """
                    SELECT equity_inr FROM sandbox_daily_summary
                    WHERE model_name = %s AND trade_date < %s
                    ORDER BY trade_date DESC LIMIT 1
                    """,
                    (str(model_name), trade_date),
                )
                prev_row = cur.fetchone()
                prev_eq = float(prev_row["equity_inr"]) if prev_row else capital

                exec_mask = grp["quantity"] > 0
                net_pnl = float(grp["net_pnl_inr"].sum())
                gross_pnl = float(grp["gross_pnl_inr"].sum())
                tot_cost = float(grp["total_cost_inr"].sum())
                tot_slip = float(grp["slippage_inr"].sum())
                new_eq = round(prev_eq + net_pnl, 2)

                cur.execute(
                    """
                    INSERT INTO sandbox_daily_summary (
                        trade_date, model_name, tradeable, candidates_count, executed_count,
                        rejected_count, gross_pnl_inr, total_cost_inr, total_slippage_inr,
                        net_pnl_inr, equity_inr
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (trade_date, model_name) DO UPDATE SET
                        tradeable = EXCLUDED.tradeable,
                        candidates_count = EXCLUDED.candidates_count,
                        executed_count = EXCLUDED.executed_count,
                        rejected_count = EXCLUDED.rejected_count,
                        gross_pnl_inr = EXCLUDED.gross_pnl_inr,
                        total_cost_inr = EXCLUDED.total_cost_inr,
                        total_slippage_inr = EXCLUDED.total_slippage_inr,
                        net_pnl_inr = EXCLUDED.net_pnl_inr,
                        equity_inr = EXCLUDED.equity_inr
                    """,
                    (
                        trade_date,
                        str(model_name),
                        tradeable,
                        len(grp),
                        int(exec_mask.sum()),
                        int((~exec_mask).sum()),
                        round(gross_pnl, 2),
                        round(tot_cost, 2),
                        round(tot_slip, 2),
                        round(net_pnl, 2),
                        new_eq,
                    ),
                )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB persist skipped for sandbox_daily_summary: {exc}")


def reset_sandbox_trades() -> dict[str, Any]:
    """Delete all simulated paper-trading journal & summary rows and reset equity state."""
    init_schema()
    started = datetime.now(IST)
    capital = float(get_effective_risk_config().get("capital_inr", 1_000_000.0))
    deleted_journal = 0
    deleted_summary = 0

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sandbox_journal")
        deleted_journal = max(0, int(cur.rowcount or 0))
        cur.execute("DELETE FROM sandbox_daily_summary")
        deleted_summary = max(0, int(cur.rowcount or 0))

    deleted_files = 0
    journal_dir = DATA_DIR / "journal"
    if journal_dir.exists():
        for p in journal_dir.glob("journal_*.parquet"):
            try:
                p.unlink()
                deleted_files += 1
            except OSError as exc:  # noqa: BLE001
                log.warning(f"Could not delete journal file {p}: {exc}")

    finished = datetime.now(IST)
    summary = (
        f"reset_sandbox: deleted_journal={deleted_journal}, "
        f"deleted_summary={deleted_summary}, deleted_files={deleted_files}, capital_inr={capital:.2f}"
    )
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_runs (job_name, started_at_ist, finished_at_ist, duration_sec, status, summary)
                VALUES ('sandbox_reset', %s, %s, %s, 'success', %s)
                """,
                (started, finished, round((finished - started).total_seconds(), 2), summary),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Job run record skipped for sandbox_reset: {exc}")

    log.info(summary)
    return {
        "deleted_journal_rows": deleted_journal,
        "deleted_summary_rows": deleted_summary,
        "deleted_parquet_files": deleted_files,
        "capital_inr": capital,
        "summary": summary,
    }
