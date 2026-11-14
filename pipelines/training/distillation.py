"""Student distillation job (remains disabled until journal accumulates sufficient paper days)."""

from __future__ import annotations

from libs.common.config import get_scorecard_config
from libs.common.db import get_conn
from libs.common.logging import get_logger

log = get_logger("finch.training.distillation")


def run_distillation_if_ready() -> dict[str, object]:
    """Check paper-trading history threshold before training a distilled student model."""
    rules = get_scorecard_config()["promotion_rules"]
    enabled = bool(rules.get("distillation_enabled", False))
    min_days = int(rules.get("distillation_min_paper_days", 60))

    paper_days = 0
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT trade_date) AS n FROM sandbox_daily_summary")
            row = cur.fetchone()
            paper_days = int(row["n"]) if row else 0
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Distillation DB check skipped: {exc}")

    if not enabled or paper_days < min_days:
        msg = f"disabled (enabled={enabled}, paper_days={paper_days}/{min_days})"
        log.info(f"Distillation skipped: {msg}")
        return {"status": "disabled", "paper_days": paper_days, "required_days": min_days}

    return {"status": "ready", "paper_days": paper_days, "required_days": min_days}
