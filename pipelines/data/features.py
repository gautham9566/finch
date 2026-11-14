"""Shared point-in-time feature & label module used identically by Training and Sandbox."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from libs.common.config import DATA_DIR, get_markets_config
from libs.common.db import get_conn
from libs.common.logging import get_logger

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.data.features")

FEATURE_COLUMNS: list[str] = [
    "opening_gap_pct",
    "prev_1d_return",
    "prev_5d_return",
    "prev_day_range_pct",
    "volume_ratio_20d",
    "atr_14_pct",
    "dist_20d_high_pct",
    "dist_20d_low_pct",
    "rel_strength_nifty_5d",
    "rel_strength_sector_5d",
    "india_vix_level",
    "india_vix_change_1d",
    "us_overnight_return",
    "asia_morning_return",
    "crude_usdinr_yield_composite",
    "news_sentiment_score",
]

FEATURE_DESCRIPTIONS: dict[str, str] = {
    "opening_gap_pct": "(open_t - close_{t-1}) / close_{t-1} known at 09:15 IST",
    "prev_1d_return": "Prior completed session close-to-close return",
    "prev_5d_return": "Prior 5-session cumulative return ending at t-1 close",
    "prev_day_range_pct": "(high_{t-1} - low_{t-1}) / close_{t-1}",
    "volume_ratio_20d": "volume_{t-1} / 20d rolling average volume ending t-1",
    "atr_14_pct": "14-day Wilder ATR as of t-1 close divided by close_{t-1}",
    "dist_20d_high_pct": "(close_{t-1} - max_high_{t-20..t-1}) / max_high",
    "dist_20d_low_pct": "(close_{t-1} - min_low_{t-20..t-1}) / min_low",
    "rel_strength_nifty_5d": "Stock 5d return minus Nifty 50 5d return as of t-1",
    "rel_strength_sector_5d": "Stock 5d return minus equal-weight sector 5d return as of t-1",
    "india_vix_level": "India VIX close strictly known before decision_ts",
    "india_vix_change_1d": "Latest 1-day India VIX percentage change known before decision_ts",
    "us_overnight_return": "Average overnight S&P 500 & Nasdaq return known before 09:20 IST",
    "asia_morning_return": "Latest Nikkei & Hang Seng return known before 09:20 IST",
    "crude_usdinr_yield_composite": "Mean standardized 1d change across Brent, USD/INR, US10Y known before 09:20 IST",
    "news_sentiment_score": "Trailing 24h whitelisted news lexicon polarity for the stock before 09:20 IST",
}


class LeakageError(ValueError):
    """Raised when any feature carries a known_as_of timestamp after the decision timestamp."""


def assert_no_leakage(
    df: pd.DataFrame,
    decision_col: str = "decision_ts",
    known_col: str = "feature_known_as_of",
) -> None:
    """Fail immediately if any row's feature timestamp exceeds its decision timestamp."""
    if df.empty:
        return
    dec = pd.to_datetime(df[decision_col], utc=True)
    known = pd.to_datetime(df[known_col], utc=True)
    bad = df[known > dec]
    if not bad.empty:
        sample = bad[[c for c in ["symbol", "trade_date", decision_col, known_col] if c in bad.columns]].head(3)
        raise LeakageError(f"Future-dated feature detected in {len(bad)} rows:\n{sample.to_string()}")


def _merge_cue_asof(
    decision_dates: pd.DataFrame,
    cues_df: pd.DataFrame,
    symbol: str,
    col_name: str,
    value_col: str = "ret_1d",
) -> pd.Series:
    """Point-in-time merge_asof on known_as_of <= decision_ts (strict zero-leakage)."""
    sub = cues_df[cues_df["symbol"] == symbol][["known_as_of", value_col]].dropna().sort_values("known_as_of")
    if sub.empty:
        return pd.Series(0.0, index=decision_dates.index)
    sub = sub.rename(columns={"known_as_of": "cue_known_as_of", value_col: col_name})
    merged = pd.merge_asof(
        decision_dates.sort_values("decision_ts"),
        sub,
        left_on="decision_ts",
        right_on="cue_known_as_of",
        direction="backward",
        allow_exact_matches=True,
    )
    # Restore original index alignment
    merged = merged.set_index(decision_dates.sort_values("decision_ts").index).reindex(decision_dates.index)
    return merged[col_name].fillna(0.0)


def build_features(
    equities_df: pd.DataFrame,
    benchmarks_df: pd.DataFrame,
    cues_df: pd.DataFrame,
    news_df: pd.DataFrame | None = None,
    include_labels: bool = True,
) -> pd.DataFrame:
    """Compute exact 09:20 IST point-in-time features (and optional intraday outcome labels)."""
    cfg = get_markets_config()
    target_mult = float(cfg.get("target_atr_mult", 1.5))
    stop_mult = float(cfg.get("stop_atr_mult", 1.0))

    eq = equities_df.sort_values(["isin", "trade_date"]).copy().reset_index(drop=True)
    g = eq.groupby("isin", group_keys=False)

    # All historical price indicators strictly shifted by 1 session so t uses <= t-1 close + t open only
    prev_close = g["close"].shift(1)
    prev_high = g["high"].shift(1)
    prev_low = g["low"].shift(1)
    prev_vol = g["volume"].shift(1)

    tr_prev = pd.concat(
        [
            prev_high - prev_low,
            (prev_high - g["close"].shift(2)).abs(),
            (prev_low - g["close"].shift(2)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_14 = tr_prev.groupby(eq["isin"]).transform(lambda s: s.rolling(14, min_periods=5).mean())

    eq["opening_gap_pct"] = (eq["open"] - prev_close) / prev_close
    eq["prev_1d_return"] = g["close"].pct_change(1).shift(1)
    eq["prev_5d_return"] = g["close"].pct_change(5).shift(1)
    eq["prev_day_range_pct"] = (prev_high - prev_low) / prev_close
    vol_ma20 = prev_vol.groupby(eq["isin"]).transform(lambda s: s.rolling(20, min_periods=5).mean())
    eq["volume_ratio_20d"] = (prev_vol / vol_ma20.replace(0, np.nan)).clip(0, 10.0)
    eq["atr_14"] = atr_14
    eq["atr_14_pct"] = atr_14 / prev_close

    high_20 = prev_high.groupby(eq["isin"]).transform(lambda s: s.rolling(20, min_periods=5).max())
    low_20 = prev_low.groupby(eq["isin"]).transform(lambda s: s.rolling(20, min_periods=5).min())
    eq["dist_20d_high_pct"] = (prev_close - high_20) / high_20
    eq["dist_20d_low_pct"] = (prev_close - low_20) / low_20

    # Benchmark 5d return shifted by 1 session
    nifty = benchmarks_df[benchmarks_df["symbol"] == "NIFTY50"].sort_values("trade_date").copy()
    nifty["nifty_5d_prev"] = nifty["close"].pct_change(5).shift(1)
    eq = eq.merge(nifty[["trade_date", "nifty_5d_prev"]], on="trade_date", how="left")
    eq["rel_strength_nifty_5d"] = eq["prev_5d_return"] - eq["nifty_5d_prev"].fillna(0.0)

    # Sector 5d return as of t-1 (already lagged because prev_5d_return uses t-1..t-6)
    sector_5d = eq.groupby(["trade_date", "sector"])["prev_5d_return"].transform("mean")
    eq["rel_strength_sector_5d"] = eq["prev_5d_return"] - sector_5d.fillna(0.0)

    # Decision timestamp is 09:20 IST on trade_date (after 09:15 NSE open)
    eq["decision_ts"] = pd.to_datetime(eq["trade_date"].astype(str) + " 09:20:00+05:30", utc=False)
    eq["decision_ts"] = pd.to_datetime(eq["decision_ts"], utc=True).dt.tz_convert(IST)

    # Global cues merged strictly with known_as_of <= decision_ts on unique trade dates
    dates_df = pd.DataFrame({"trade_date": sorted(eq["trade_date"].unique())})
    dates_df["decision_ts"] = pd.to_datetime(dates_df["trade_date"].astype(str) + " 09:20:00+05:30")
    dates_df["decision_ts"] = pd.to_datetime(dates_df["decision_ts"], utc=True).dt.tz_convert(IST)

    cues_clean = cues_df.copy()
    cues_clean["known_as_of"] = pd.to_datetime(cues_clean["known_as_of"], utc=True).dt.tz_convert(IST)

    dates_df["india_vix_level"] = _merge_cue_asof(dates_df, cues_clean, "^INDIAVIX", "vix_lvl", "close")
    dates_df["india_vix_change_1d"] = _merge_cue_asof(dates_df, cues_clean, "^INDIAVIX", "vix_chg", "ret_1d")
    spx = _merge_cue_asof(dates_df, cues_clean, "^GSPC", "spx", "ret_1d")
    ndx = _merge_cue_asof(dates_df, cues_clean, "^IXIC", "ndx", "ret_1d")
    dates_df["us_overnight_return"] = 0.5 * (spx + ndx)
    n225 = _merge_cue_asof(dates_df, cues_clean, "^N225", "n225", "ret_1d")
    hsi = _merge_cue_asof(dates_df, cues_clean, "^HSI", "hsi", "ret_1d")
    dates_df["asia_morning_return"] = 0.5 * (n225 + hsi)
    brent = _merge_cue_asof(dates_df, cues_clean, "BZ=F", "brent", "ret_1d")
    usdinr = _merge_cue_asof(dates_df, cues_clean, "INR=X", "usdinr", "ret_1d")
    us10y = _merge_cue_asof(dates_df, cues_clean, "^TNX", "us10y", "ret_1d")
    dates_df["crude_usdinr_yield_composite"] = (brent + usdinr + us10y) / 3.0

    eq = eq.merge(
        dates_df[
            [
                "trade_date",
                "india_vix_level",
                "india_vix_change_1d",
                "us_overnight_return",
                "asia_morning_return",
                "crude_usdinr_yield_composite",
            ]
        ],
        on="trade_date",
        how="left",
    )

    # Trailing 24h whitelisted news sentiment known before 09:20 IST
    eq["news_sentiment_score"] = 0.0
    if news_df is not None and not news_df.empty:
        nd = news_df.copy()
        nd["known_as_of"] = pd.to_datetime(nd["known_as_of"], utc=True).dt.tz_convert(IST)
        for isin, grp_idx in eq.groupby("isin").groups.items():
            sub_news = nd[nd["isin"] == isin]
            if sub_news.empty:
                continue
            for idx in grp_idx:
                dec_t = eq.at[idx, "decision_ts"]
                win = sub_news[
                    (sub_news["known_as_of"] <= dec_t) & (sub_news["known_as_of"] >= dec_t - pd.Timedelta(hours=24))
                ]
                if not win.empty:
                    eq.at[idx, "news_sentiment_score"] = float(win["sentiment_score"].mean())

    # Exact feature availability timestamp (09:15 open price + t-1 EOD + pre-market cues <= 09:15)
    eq["feature_known_as_of"] = pd.to_datetime(eq["trade_date"].astype(str) + " 09:15:00+05:30")
    eq["feature_known_as_of"] = pd.to_datetime(eq["feature_known_as_of"], utc=True).dt.tz_convert(IST)

    # Drop warmup rows without ATR or previous close
    eq = eq.dropna(subset=FEATURE_COLUMNS + ["atr_14"]).copy()
    for col in FEATURE_COLUMNS:
        eq[col] = eq[col].replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)

    assert_no_leakage(eq)

    if include_labels:
        target_px = eq["open"] + target_mult * eq["atr_14"]
        stop_px = eq["open"] - stop_mult * eq["atr_14"]
        hit_target = eq["high"] >= target_px
        hit_stop = eq["low"] <= stop_px

        # Pessimistic rule: if both target and stop sit inside the day's high/low range, assume stop hit first
        exit_px = np.where(
            hit_stop,
            stop_px,
            np.where(hit_target, target_px, eq["close"]),
        )
        eq["exit_reason"] = np.where(hit_stop, "STOP", np.where(hit_target, "TARGET", "EOD_SQUARE_OFF"))
        eq["target_hit"] = np.where(hit_target & (~hit_stop), 1, 0)
        eq["move_size_atr"] = (exit_px - eq["open"]) / eq["atr_14"].replace(0, np.nan)
        eq["gross_return_bps"] = ((exit_px / eq["open"]) - 1.0) * 10000.0
        eq["label_known_as_of"] = pd.to_datetime(eq["trade_date"].astype(str) + " 15:30:00+05:30")
        eq["label_known_as_of"] = pd.to_datetime(eq["label_known_as_of"], utc=True).dt.tz_convert(IST)

    return eq.reset_index(drop=True)


def compute_dataset_version(df: pd.DataFrame) -> str:
    """Content-addressable SHA-256 dataset version string."""
    h = hashlib.sha256()
    h.update(",".join(FEATURE_COLUMNS).encode("utf-8"))
    h.update(str(len(df)).encode("utf-8"))
    if not df.empty:
        h.update(str(df["trade_date"].min()).encode("utf-8"))
        h.update(str(df["trade_date"].max()).encode("utf-8"))
        sample = df[["trade_date", "isin", "close", *FEATURE_COLUMNS]].head(500).to_csv(index=False)
        h.update(sample.encode("utf-8"))
    return f"ds_{h.hexdigest()[:12]}"


def build_and_save_feature_store() -> tuple[Path, str]:
    """Generate versioned features Parquet from clean layers and register dataset version."""
    eq = pd.read_parquet(DATA_DIR / "clean" / "equities_daily_clean.parquet")
    bench = pd.read_parquet(DATA_DIR / "raw" / "daily_prices" / "nse_benchmarks_daily.parquet")
    cues = pd.read_parquet(DATA_DIR / "clean" / "global_cues_clean.parquet")
    news_path = DATA_DIR / "raw" / "news" / "news_sentiment.parquet"
    news = pd.read_parquet(news_path) if news_path.exists() else None

    feat_df = build_features(eq, bench, cues, news, include_labels=True)
    version_id = compute_dataset_version(feat_df)
    feat_df["dataset_version"] = version_id

    feat_dir = DATA_DIR / "features"
    feat_dir.mkdir(parents=True, exist_ok=True)
    out_path = feat_dir / "features_daily.parquet"
    feat_df.to_parquet(out_path, index=False)

    now_ist = datetime.now(IST)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dataset_versions (
                    version_id, created_at_ist, row_count, symbol_count,
                    min_date, max_date, feature_columns_json, parquet_path
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (version_id) DO NOTHING
                """,
                (
                    version_id,
                    now_ist,
                    len(feat_df),
                    int(feat_df["isin"].nunique()),
                    feat_df["trade_date"].min(),
                    feat_df["trade_date"].max(),
                    json.dumps(FEATURE_COLUMNS),
                    str(out_path),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB skip for dataset_version {version_id}: {exc}")

    log.info(f"Built feature store {version_id}: {len(feat_df)} rows across {feat_df['isin'].nunique()} symbols")
    return out_path, version_id
