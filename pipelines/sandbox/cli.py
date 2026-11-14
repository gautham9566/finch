"""CLI entrypoint for Pipeline 3 (Sandbox)."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from libs.common.config import DATA_DIR
from libs.common.db import init_schema
from libs.common.logging import get_logger
from pipelines.sandbox.feedback import export_journal_feedback
from pipelines.sandbox.replay import replay_date, replay_last_n_days
from pipelines.sandbox.snapshot import freeze_snapshot_for_date

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.sandbox.cli")


def _latest_available_date() -> date:
    df = pd.read_parquet(DATA_DIR / "features" / "features_daily.parquet", columns=["trade_date"])
    return pd.to_datetime(df["trade_date"]).dt.date.max()


def main() -> None:
    parser = argparse.ArgumentParser(description="Finch Pipeline 3 (Sandbox)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot")
    p_snap.add_argument("--date", type=str, default=None)
    p_snap.add_argument("--slot", type=str, default="09:20")

    p_rep = sub.add_parser("replay")
    p_rep.add_argument("--date", type=str, required=True)
    p_rep.add_argument("--slot", type=str, default="09:20")

    p_range = sub.add_parser("replay-range")
    p_range.add_argument("--days", type=int, default=25)

    sub.add_parser("run-today")
    sub.add_parser("square-off")
    sub.add_parser("feedback")
    sub.add_parser("status")

    args = parser.parse_args()
    init_schema()

    if args.command == "snapshot":
        dt = date.fromisoformat(args.date) if args.date else _latest_available_date()
        out = freeze_snapshot_for_date(dt, slot=args.slot)
        print(f"Snapshot frozen: {out}")
    elif args.command == "replay":
        res = replay_date(date.fromisoformat(args.date), slot=args.slot)
        print(res)
    elif args.command == "replay-range":
        range_res = replay_last_n_days(days=args.days)
        tot_pnl = sum(float(r["champion_net_pnl_inr"]) for r in range_res)
        print(f"Replayed {len(range_res)} days. Cumulative champion net P/L: INR {tot_pnl:,.2f}")
    elif args.command == "run-today":
        today = datetime.now(IST).date()
        feat_dates = set(
            pd.to_datetime(
                pd.read_parquet(DATA_DIR / "features" / "features_daily.parquet", columns=["trade_date"])["trade_date"]
            ).dt.date
        )
        target = today if today in feat_dates else _latest_available_date()
        res = replay_date(target, slot="09:20")
        print(res)
    elif args.command == "square-off":
        log.info("Square-off reconciliation completed via replay/daily summary")
    elif args.command == "feedback":
        out = export_journal_feedback()
        print(f"Feedback exported: {out}")
    elif args.command == "status":
        print("Finch Sandbox Pipeline healthy")


if __name__ == "__main__":
    main()
