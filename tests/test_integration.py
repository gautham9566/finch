"""Integration tests for read-only web security and missing-data quality gate safety."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from pipelines.data import quality as q_mod
from web.backend.main import app


def test_web_api_blocks_mutating_http_methods() -> None:
    client = TestClient(app)
    for verb in ("post", "put", "patch", "delete"):
        resp = getattr(client, verb)("/api/health")
        assert resp.status_code == 405
        resp2 = getattr(client, verb)("/api/overview")
        assert resp2.status_code == 405


def test_missing_data_source_marks_day_untradeable_without_crashing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(q_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(q_mod, "record_intraday_status", lambda *_args, **_kwargs: "intraday: not configured")
    monkeypatch.setattr(q_mod, "_persist_report", lambda _report: None)

    report = q_mod.evaluate_data_quality(trade_date=date(2025, 3, 12))
    assert report.tradeable is False
    assert report.intraday_status == "intraday: not configured"
    assert any("CRITICAL" in r for r in report.reasons)
