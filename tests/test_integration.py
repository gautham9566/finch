"""Integration tests for read-only web security and missing-data quality gate safety."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from pipelines.data import quality as q_mod
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
