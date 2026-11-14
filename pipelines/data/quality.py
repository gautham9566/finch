"""Daily data-quality gate: writes report to Postgres and blocks sandbox trading on failure."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from libs.common.config import DATA_DIR
from libs.common.db import get_conn
from libs.common.logging import get_logger
from libs.common.schemas import DataQualityReport
from pipelines.data.intraday import BrokerIntradayAdapter, record_intraday_status

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.data.quality")


def evaluate_data_quality(
    trade_date: date | None = None,
    min_symbols: int = 100,
    required_sources: tuple[str, ...] = ("daily_prices", "global_cues"),
) -> DataQualityReport:
    """Check completeness and consistency; a missing critical source sets tradeable=False without crashing."""
    now_ist = datetime.now(IST)
    reasons: list[str] = []

    eq_clean_path = DATA_DIR / "clean" / "equities_daily_clean.parquet"
    cues_clean_path = DATA_DIR / "clean" / "global_cues_clean.parquet"
    univ_path = DATA_DIR / "clean" / "universe_nifty200.parquet"

    universe_count = len(pd.read_parquet(univ_path)) if univ_path.exists() else 0
    intraday_status = record_intraday_status(BrokerIntradayAdapter())

    if "daily_prices" in required_sources and not eq_clean_path.exists():
        reasons.append("CRITICAL: clean daily equity prices missing")
    if "global_cues" in required_sources and not cues_clean_path.exists():
        reasons.append("CRITICAL: clean global cues missing")

    if reasons:
        report = DataQualityReport(
            trade_date=trade_date or now_ist.date(),
            checked_at_ist=now_ist,
            universe_count=universe_count,
            daily_rows=0,
            missing_symbols=universe_count,
            bad_ohlc_rows=0,
            illiquid_dropped=0,
            global_cues_ok=cues_clean_path.exists(),
            intraday_status=intraday_status,
            tradeable=False,
            reasons=reasons,
        )
        _persist_report(report)
        return report

    eq = pd.read_parquet(eq_clean_path)
    cues = pd.read_parquet(cues_clean_path)
    eq["trade_date"] = pd.to_datetime(eq["trade_date"]).dt.date
    target_day = trade_date or eq["trade_date"].max()

    day_df = eq[eq["trade_date"] == target_day]
    day_rows = len(day_df)
    bad_ohlc = (
        int(((day_df["open"] <= 0) | (day_df["close"] <= 0) | (day_df["high"] < day_df["low"])).sum())
        if day_rows
        else 0
    )
    missing_syms = max(0, universe_count - day_rows)

    if day_rows < min_symbols:
        reasons.append(f"LOW_COVERAGE: {day_rows} symbols on {target_day} (< {min_symbols})")
    if bad_ohlc > 0:
        reasons.append(f"BAD_OHLC: {bad_ohlc} invalid bars on {target_day}")

    cues_ok = not cues.empty and cues["symbol"].nunique() >= 6
    if not cues_ok:
        reasons.append("GLOBAL_CUES_MISSING: fewer than 6 cue series present")

    tradeable = len(reasons) == 0
    report = DataQualityReport(
        trade_date=target_day,
        checked_at_ist=now_ist,
        universe_count=universe_count,
        daily_rows=day_rows,
        missing_symbols=missing_syms,
        bad_ohlc_rows=bad_ohlc,
        illiquid_dropped=0,
        global_cues_ok=cues_ok,
        intraday_status=intraday_status,
        tradeable=tradeable,
        reasons=reasons,
    )
    _persist_report(report)
    return report


def _persist_report(report: DataQualityReport) -> None:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_quality_reports (
                    trade_date, checked_at_ist, universe_count, daily_rows, missing_symbols,
                    bad_ohlc_rows, illiquid_dropped, global_cues_ok, intraday_status, tradeable, reasons_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (trade_date) DO UPDATE SET
                    checked_at_ist = EXCLUDED.checked_at_ist,
                    universe_count = EXCLUDED.universe_count,
                    daily_rows = EXCLUDED.daily_rows,
                    missing_symbols = EXCLUDED.missing_symbols,
                    bad_ohlc_rows = EXCLUDED.bad_ohlc_rows,
                    global_cues_ok = EXCLUDED.global_cues_ok,
                    intraday_status = EXCLUDED.intraday_status,
                    tradeable = EXCLUDED.tradeable,
                    reasons_json = EXCLUDED.reasons_json
                """,
                (
                    report.trade_date,
                    report.checked_at_ist,
                    report.universe_count,
                    report.daily_rows,
                    report.missing_symbols,
                    report.bad_ohlc_rows,
                    report.illiquid_dropped,
                    report.global_cues_ok,
                    report.intraday_status,
                    report.tradeable,
                    json.dumps(report.reasons),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB persist skipped for data_quality_reports: {exc}")


def is_day_tradeable(trade_date: date) -> tuple[bool, list[str]]:
    """Read stored data-quality decision for trade_date; default to re-evaluating if missing."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT tradeable, reasons_json FROM data_quality_reports WHERE trade_date = %s", (trade_date,))
            row = cur.fetchone()
            if row is not None:
                return bool(row["tradeable"]), list(row["reasons_json"] or [])
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB lookup fallback for quality gate: {exc}")
    rep = evaluate_data_quality(trade_date=trade_date)
    return rep.tradeable, rep.reasons
