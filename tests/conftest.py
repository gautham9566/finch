"""Shared synthetic market fixtures for Finch unit, leakage, parity, and simulation tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from libs.common.schemas import AgentSignal

IST = ZoneInfo("Asia/Kolkata")


def _trading_days(n_days: int = 80, start: date = date(2025, 1, 1)) -> list[date]:
    days: list[date] = []
    cur = start
    while len(days) < n_days:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


@pytest.fixture
def sample_market_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate deterministic multi-stock OHLCV, Nifty benchmark, global cues, and news DataFrames."""
    rng = np.random.default_rng(42)
    days = _trading_days(80)
    symbols = [
        ("INE002A01018", "RELIANCE", "Energy"),
        ("INE040A01034", "HDFCBANK", "Financials"),
        ("INE009A01021", "INFY", "IT"),
        ("INE467B01029", "TCS", "IT"),
        ("INE090A01021", "ICICIBANK", "Financials"),
    ]

    eq_rows: list[dict] = []
    for s_idx, (isin, sym, sector) in enumerate(symbols):
        px = 1000.0 + s_idx * 350.0
        for d in days:
            gap = rng.normal(0.001, 0.004)
            op = round(px * (1.0 + gap), 2)
            ret = rng.normal(0.0008, 0.012)
            cl = round(op * (1.0 + ret), 2)
            hi = round(max(op, cl) * (1.0 + abs(rng.normal(0.004, 0.003))), 2)
            lo = round(min(op, cl) * (1.0 - abs(rng.normal(0.004, 0.003))), 2)
            vol = int(rng.integers(500_000, 3_000_000))
            eq_rows.append(
                {
                    "isin": isin,
                    "symbol": sym,
                    "sector": sector,
                    "trade_date": d,
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": cl,
                    "volume": vol,
                    "traded_value_inr": cl * vol,
                    "known_as_of": datetime.combine(d, datetime.min.time(), tzinfo=IST).replace(hour=15, minute=35),
                }
            )
            px = cl

    bench_rows: list[dict] = []
    n_px = 24000.0
    for d in days:
        op = n_px
        cl = round(op * (1.0 + rng.normal(0.0004, 0.006)), 2)
        bench_rows.append(
            {
                "symbol": "NIFTY50",
                "trade_date": d,
                "open": op,
                "high": max(op, cl) * 1.003,
                "low": min(op, cl) * 0.997,
                "close": cl,
                "volume": 0,
            }
        )
        n_px = cl

    cue_syms = ["^INDIAVIX", "^GSPC", "^IXIC", "^N225", "^HSI", "BZ=F", "INR=X", "^TNX"]
    cue_rows: list[dict] = []
    for sym in cue_syms:
        val = 15.0 if sym == "^INDIAVIX" else 100.0
        for d in days:
            r1 = float(rng.normal(0.0, 0.01))
            val = round(val * (1.0 + r1), 4)
            known = datetime.combine(d, datetime.min.time(), tzinfo=IST).replace(hour=8, minute=0)
            cue_rows.append(
                {
                    "symbol": sym,
                    "trade_date": d,
                    "close": val,
                    "ret_1d": r1,
                    "known_as_of": known,
                }
            )

    news_rows = [
        {
            "isin": "INE002A01018",
            "symbol": "RELIANCE",
            "sentiment_score": 0.45,
            "known_as_of": datetime.combine(days[40], datetime.min.time(), tzinfo=IST).replace(hour=8, minute=15),
        },
        {
            "isin": "INE040A01034",
            "symbol": "HDFCBANK",
            "sentiment_score": -0.25,
            "known_as_of": datetime.combine(days[50], datetime.min.time(), tzinfo=IST).replace(hour=8, minute=30),
        },
    ]

    return (
        pd.DataFrame(eq_rows),
        pd.DataFrame(bench_rows),
        pd.DataFrame(cue_rows),
        pd.DataFrame(news_rows),
    )


@pytest.fixture
def sample_signal() -> AgentSignal:
    return AgentSignal(
        model_name="lgbm_picker",
        model_version="vtest",
        agent_name="agent_a_momentum",
        isin="INE002A01018",
        symbol="RELIANCE",
        sector="Energy",
        direction="LONG",
        probability=0.62,
        entry=2500.0,
        target=2545.0,
        stop=2470.0,
        atr=30.0,
        opening_gap_pct=0.008,
        known_as_of=datetime(2025, 3, 10, 9, 20, tzinfo=IST),
    )
