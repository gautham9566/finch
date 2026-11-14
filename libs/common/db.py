"""PostgreSQL connection helper and schema initialization."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import DictRow, dict_row

from libs.common.config import get_postgres_dsn

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS data_sources_status (
    source_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    coverage_start TEXT,
    coverage_end TEXT,
    row_count BIGINT DEFAULT 0,
    last_updated_ist TIMESTAMPTZ NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_reports (
    trade_date DATE PRIMARY KEY,
    checked_at_ist TIMESTAMPTZ NOT NULL,
    universe_count INTEGER NOT NULL,
    daily_rows INTEGER NOT NULL,
    missing_symbols INTEGER NOT NULL,
    bad_ohlc_rows INTEGER NOT NULL,
    illiquid_dropped INTEGER NOT NULL,
    global_cues_ok BOOLEAN NOT NULL,
    intraday_status TEXT NOT NULL,
    tradeable BOOLEAN NOT NULL,
    reasons_json JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    version_id TEXT PRIMARY KEY,
    created_at_ist TIMESTAMPTZ NOT NULL,
    row_count BIGINT NOT NULL,
    symbol_count INTEGER NOT NULL,
    min_date DATE NOT NULL,
    max_date DATE NOT NULL,
    feature_columns_json JSONB NOT NULL,
    parquet_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_registry (
    model_name TEXT PRIMARY KEY,
    model_type TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    mlflow_run_id TEXT,
    git_commit TEXT,
    config_json JSONB NOT NULL,
    artifact_path TEXT,
    trained_at_ist TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS model_scorecards (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    evaluated_at_ist TIMESTAMPTZ NOT NULL,
    train_auc DOUBLE PRECISION,
    val_auc DOUBLE PRECISION,
    test_auc DOUBLE PRECISION,
    train_val_gap DOUBLE PRECISION,
    overfit_flag BOOLEAN DEFAULT FALSE,
    underfit_flag BOOLEAN DEFAULT FALSE,
    ece DOUBLE PRECISION,
    net_return_pct DOUBLE PRECISION NOT NULL,
    max_drawdown_pct DOUBLE PRECISION NOT NULL,
    win_rate DOUBLE PRECISION NOT NULL,
    profit_factor DOUBLE PRECISION NOT NULL,
    beats_baseline BOOLEAN NOT NULL,
    promotion_status TEXT NOT NULL,
    regime_breakdown_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    reliability_bins_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (model_name, dataset_version)
);

CREATE TABLE IF NOT EXISTS sandbox_journal (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    snapshot_slot TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    isin TEXT NOT NULL,
    symbol TEXT NOT NULL,
    sector TEXT NOT NULL,
    direction TEXT NOT NULL,
    probability DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    target_price DOUBLE PRECISION NOT NULL,
    stop_price DOUBLE PRECISION NOT NULL,
    coordinator_decision TEXT NOT NULL,
    llm_decision TEXT NOT NULL,
    risk_decision TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    is_champion_trade BOOLEAN NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    fill_price DOUBLE PRECISION,
    exit_price DOUBLE PRECISION,
    exit_reason TEXT,
    slippage_inr DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    total_cost_inr DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    gross_pnl_inr DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    net_pnl_inr DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    counterfactual_pnl_inr DOUBLE PRECISION,
    counterfactual_exit_reason TEXT,
    known_as_of TIMESTAMPTZ NOT NULL,
    UNIQUE (trade_date, snapshot_slot, model_name, symbol)
);

CREATE TABLE IF NOT EXISTS sandbox_daily_summary (
    trade_date DATE NOT NULL,
    model_name TEXT NOT NULL,
    tradeable BOOLEAN NOT NULL,
    candidates_count INTEGER NOT NULL,
    executed_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    gross_pnl_inr DOUBLE PRECISION NOT NULL,
    total_cost_inr DOUBLE PRECISION NOT NULL,
    total_slippage_inr DOUBLE PRECISION NOT NULL,
    net_pnl_inr DOUBLE PRECISION NOT NULL,
    equity_inr DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (trade_date, model_name)
);

CREATE TABLE IF NOT EXISTS job_runs (
    id BIGSERIAL PRIMARY KEY,
    job_name TEXT NOT NULL,
    started_at_ist TIMESTAMPTZ NOT NULL,
    finished_at_ist TIMESTAMPTZ,
    duration_sec DOUBLE PRECISION,
    status TEXT NOT NULL,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at_ist TIMESTAMPTZ NOT NULL
);
"""


@contextmanager
def get_conn() -> Iterator[psycopg.Connection[DictRow]]:
    conn = psycopg.connect(get_postgres_dsn(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
