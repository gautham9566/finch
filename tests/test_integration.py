"""Integration tests for read-only web security and missing-data quality gate safety."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from pipelines.data import quality as q_mod
from pipelines.sandbox.replay import _serialize_signal_record
from pipelines.training.cli import filter_training_dataframe
from web.backend import actions as actions_mod
from web.backend.main import app


def test_web_api_blocks_mutating_http_methods() -> None:
    client = TestClient(app)
    for verb in ("post", "put", "patch", "delete"):
        resp = getattr(client, verb)("/api/health")
        assert resp.status_code == 405
        resp2 = getattr(client, verb)("/api/overview")
        assert resp2.status_code == 405

    for verb in ("put", "patch", "delete"):
        resp3 = getattr(client, verb)("/api/actions/paper-options")
        assert resp3.status_code == 405


def test_web_action_endpoints_validation_and_handlers(monkeypatch, tmp_path: Path) -> None:
    client = TestClient(app)

    opts_resp = client.get("/api/actions/paper-options")
    assert opts_resp.status_code == 200
    opts_body = opts_resp.json()
    assert "options" in opts_body and "defaults" in opts_body
    assert "available_models" in opts_body and isinstance(opts_body["available_models"], list)
    assert len(opts_body["available_models"]) >= 1

    status_resp = client.get("/api/actions/train/status")
    assert status_resp.status_code == 200
    assert "status" in status_resp.json()

    bad_dir = client.post("/api/actions/paper-options", json={"direction_filter": "INVALID"})
    assert bad_dir.status_code == 400

    bad_model = client.post("/api/actions/train", json={"model_name": "nonexistent_model_xyz"})
    assert bad_model.status_code == 400

    bad_range = client.post(
        "/api/actions/train",
        json={"model_name": "all", "start_date": "2026-06-10", "end_date": "2026-06-01"},
    )
    assert bad_range.status_code == 400

    bad_date_fmt = client.post(
        "/api/actions/train",
        json={"model_name": "all", "start_date": "2026/06/01", "end_date": "2026-06-10"},
    )
    assert bad_date_fmt.status_code == 400

    unpaired_date = client.post(
        "/api/actions/train",
        json={"model_name": "all", "start_date": "2026-06-01"},
    )
    assert unpaired_date.status_code == 400

    monkeypatch.setattr(
        actions_mod,
        "reset_sandbox_trades",
        lambda: {"deleted_journal_rows": 12, "deleted_summary_rows": 4, "capital_inr": 1_000_000.0},
    )
    reset_resp = client.post("/api/actions/reset-trades")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["deleted_journal_rows"] == 12

    monkeypatch.setattr(actions_mod, "DATA_DIR", tmp_path)
    missing_sim = client.post("/api/actions/simulate", json={"mode": "live"})
    assert missing_sim.status_code == 400


def test_missing_data_source_marks_day_untradeable_without_crashing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(q_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(q_mod, "record_intraday_status", lambda *_args, **_kwargs: "intraday: not configured")
    monkeypatch.setattr(q_mod, "_persist_report", lambda _report: None)

    report = q_mod.evaluate_data_quality(trade_date=date(2025, 3, 12))
    assert report.tradeable is False
    assert report.intraday_status == "intraday: not configured"
    assert any("CRITICAL" in r for r in report.reasons)


def test_filter_training_dataframe_and_signal_serialization() -> None:
    base = date(2026, 1, 1)
    rows = [{"trade_date": base + timedelta(days=i), "dataset_version": "v1"} for i in range(30)]
    df = pd.DataFrame(rows)

    sliced, ver = filter_training_dataframe(df, start_date=date(2026, 1, 5), end_date=date(2026, 1, 28))
    assert len(sliced) == 24
    assert ver == "v1_2026-01-05_2026-01-28"

    with pytest.raises(ValueError, match="at least 20"):
        filter_training_dataframe(df, start_date=date(2026, 1, 1), end_date=date(2026, 1, 10))

    rec = {
        "trade_date": date(2026, 1, 15),
        "snapshot_slot": "09:20",
        "model_name": "lightgbm_v1",
        "isin": "INE002A01018",
        "symbol": "RELIANCE",
        "sector": "Energy",
        "direction": "LONG",
        "probability": 0.72,
        "entry_price": 1000.0,
        "target_price": 1020.0,
        "stop_price": 990.0,
        "coordinator_decision": "BUY",
        "llm_decision": "APPROVE",
        "risk_decision": "REJECTED",
        "decision_reason": "max trades",
        "is_champion_trade": False,
        "quantity": 0,
        "fill_price": None,
        "exit_price": None,
        "exit_reason": "REJECTED",
        "slippage_inr": 0.0,
        "total_cost_inr": 0.0,
        "gross_pnl_inr": 0.0,
        "net_pnl_inr": 0.0,
        "counterfactual_pnl_inr": 350.0,
    }
    bar = {"open": 1000.0, "high": 1025.0, "low": 995.0, "close": 1015.0, "target_hit": 1}
    serialized = _serialize_signal_record(rec, {"INE002A01018": bar})
    assert serialized["predicted_return_pct"] == 2.0
    assert serialized["actual_open"] == 1000.0
    assert serialized["actual_close"] == 1015.0
    assert serialized["actual_return_pct"] == 1.5
    assert serialized["actual_target_hit"] == 1
    assert serialized["effective_pnl_inr"] == 350.0
