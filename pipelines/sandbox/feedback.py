"""Nightly feedback loop: exports sandbox journal outcomes back into Pipeline 1/2 dataset."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from libs.common.config import DATA_DIR
from libs.common.db import get_conn
from libs.common.logging import get_logger
from libs.common.storage import upload_file_to_gcs

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.sandbox.feedback")


def export_journal_feedback() -> Path:
    """Export Postgres sandbox_journal to point-in-time Parquet for future model retraining."""
    out_dir = DATA_DIR / "features"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "journal_feedback.parquet"

    rows: list[dict] = []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT trade_date, snapshot_slot, model_name, model_version, dataset_version,
                   isin, symbol, sector, direction, probability, entry_price, target_price,
                   stop_price, risk_decision, is_champion_trade, quantity, fill_price,
                   exit_price, exit_reason, slippage_inr, total_cost_inr, gross_pnl_inr,
                   net_pnl_inr, counterfactual_pnl_inr, counterfactual_exit_reason, known_as_of
            FROM sandbox_journal
            ORDER BY trade_date, model_name, symbol
            """
        )
        rows = list(cur.fetchall())

    df = pd.DataFrame(rows)
    if not df.empty:
        # Feedback outcomes are only known after 16:00 IST close reconciliation
        df["feedback_known_as_of"] = pd.to_datetime(df["trade_date"].astype(str) + " 16:00:00+05:30")
    df.to_parquet(out_path, index=False)
    upload_file_to_gcs(out_path, "features/journal_feedback.parquet")

    now_ist = datetime.now(IST)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_runs (job_name, started_at_ist, finished_at_ist, duration_sec, status, summary)
                VALUES ('sandbox_feedback', %s, %s, 0.1, 'success', %s)
                """,
                (now_ist, now_ist, f"Exported {len(df)} journal rows to {out_path.name}"),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Job log skip: {exc}")

    log.info(f"Feedback export complete: {len(df)} rows -> {out_path}")
    return out_path
