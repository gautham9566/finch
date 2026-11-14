"""CLI entrypoint for Pipeline 1 (Data)."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from zoneinfo import ZoneInfo

from libs.common.db import get_conn, init_schema
from libs.common.instruments import load_or_fetch_universe
from libs.common.logging import get_logger
from libs.common.storage import sync_data_dir_to_gcs
from pipelines.data.clean import clean_daily_prices, clean_global_cues
from pipelines.data.features import build_and_save_feature_store
from pipelines.data.flows_events import ingest_flows_and_events
from pipelines.data.global_cues import ingest_global_cues
from pipelines.data.intraday import save_forward_intraday
from pipelines.data.news import ingest_news
from pipelines.data.quality import evaluate_data_quality
from pipelines.data.sources import ingest_indian_daily

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.data.cli")


def _record_job(job_name: str, started: datetime, status: str, summary: str) -> None:
    finished = datetime.now(IST)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_runs (job_name, started_at_ist, finished_at_ist, duration_sec, status, summary)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (job_name, started, finished, round((finished - started).total_seconds(), 2), status, summary),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Job run record skipped: {exc}")


def run_eod() -> None:
    started = datetime.now(IST)
    init_schema()
    try:
        ingest_indian_daily(refresh_universe=True)
        ingest_global_cues()
        ingest_flows_and_events()
        universe = load_or_fetch_universe()
        ingest_news(universe)
        save_forward_intraday(date.today(), universe)
        clean_daily_prices(universe_df=universe)
        clean_global_cues()
        _, version_id = build_and_save_feature_store()
        report = evaluate_data_quality()
        uploaded = sync_data_dir_to_gcs()
        summary = (
            f"version={version_id}, tradeable={report.tradeable}, rows={report.daily_rows}, "
            f"intraday={report.intraday_status}, gcs_synced={uploaded}"
        )
        _record_job("data_eod", started, "success", summary)
        log.info(f"EOD pipeline complete: {summary}")
    except Exception as exc:  # noqa: BLE001
        _record_job("data_eod", started, "failed", str(exc))
        raise


def run_premarket() -> None:
    started = datetime.now(IST)
    init_schema()
    ingest_global_cues()
    clean_global_cues()
    universe = load_or_fetch_universe()
    ingest_news(universe)
    report = evaluate_data_quality()
    sync_data_dir_to_gcs()
    _record_job("data_premarket", started, "success", f"tradeable={report.tradeable}")


def run_news_only() -> None:
    started = datetime.now(IST)
    init_schema()
    universe = load_or_fetch_universe()
    ingest_news(universe)
    _record_job("data_news", started, "success", "RSS news refreshed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Finch Pipeline 1 (Data)")
    parser.add_argument("command", choices=["eod", "premarket", "news", "quality", "status"])
    args = parser.parse_args()
    if args.command == "eod":
        run_eod()
    elif args.command == "premarket":
        run_premarket()
    elif args.command == "news":
        run_news_only()
    elif args.command == "quality":
        init_schema()
        rep = evaluate_data_quality()
        print(rep.model_dump_json(indent=2))
    elif args.command == "status":
        print("Finch Data Pipeline healthy")


if __name__ == "__main__":
    main()
