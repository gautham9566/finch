"""Deterministic end-to-end replay & live simulation runner for Pipeline 3."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from libs.common.config import DATA_DIR, get_effective_risk_config, load_all_model_configs
from libs.common.db import get_conn, init_schema
from libs.common.logging import get_logger
from pipelines.data.quality import is_day_tradeable
from pipelines.sandbox.broker import PaperBroker
from pipelines.sandbox.journal import save_daily_summaries, save_journal_records
from pipelines.sandbox.llm_reviewer import StubLLMReviewer
from pipelines.sandbox.risk import DailyRiskState, RiskEngine
from pipelines.sandbox.snapshot import freeze_snapshot_for_date
from pipelines.training.agents.coordinator import coordinate_signals, get_agent_for_name
from pipelines.training.registry import load_model_artifact

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.sandbox.replay")


def _load_active_models(preferred_champion: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Load champion + shadow models from registry/artifacts, honoring preferred champion override."""
    champion = ""
    statuses: dict[str, str] = {}
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT model_name, status FROM model_registry")
            for row in cur.fetchall():
                statuses[str(row["model_name"])] = str(row["status"])
                if row["status"] == "champion":
                    champion = str(row["model_name"])
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Registry lookup fallback to local configs: {exc}")

    pref = (preferred_champion or "").strip()
    bundles: list[dict[str, Any]] = []
    for cfg in load_all_model_configs():
        name = cfg["name"]
        if name == "regime_classifier":
            continue
        status = statuses.get(name, "shadow")
        if status not in ("champion", "shadow") and name != pref:
            continue
        art = load_model_artifact(name)
        if art is not None:
            art["status"] = "champion" if name == pref else status
            bundles.append(art)

    available_names = {b["model_name"] for b in bundles}
    if pref and pref in available_names:
        champion = pref
    elif not champion and bundles:
        champion = bundles[0]["model_name"]
    return champion, bundles


def _run_model_inference(bundle: dict[str, Any], snap_df: pd.DataFrame, min_prob: float):
    agent = get_agent_for_name(str(bundle.get("agent", "momentum")))
    return agent.predict_signals(
        snapshot_df=snap_df,
        model=bundle["model"],
        model_name=bundle["model_name"],
        model_version=str(bundle.get("dataset_version", "v1")),
        features=bundle["features"],
        min_prob=min_prob,
    )


def replay_date(
    trade_date: date,
    slot: str = "09:20",
    max_candidates_per_model: int = 12,
    risk_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically simulate one trading day across champion + shadow models."""
    init_schema()
    started = datetime.now(IST)
    tradeable, reasons = is_day_tradeable(trade_date)

    risk_cfg = get_effective_risk_config()
    if risk_overrides:
        risk_cfg.update({k: v for k, v in risk_overrides.items() if v is not None})

    snap_path = freeze_snapshot_for_date(trade_date, slot=slot)
    snap_df = pd.read_parquet(snap_path)
    bar_by_isin = {str(r["isin"]): r for _, r in snap_df.iterrows()}
    dataset_version = str(snap_df["dataset_version"].iloc[0]) if "dataset_version" in snap_df.columns else "unknown"

    preferred_champ = str(risk_cfg.get("preferred_champion") or "")
    champion_name, bundles = _load_active_models(preferred_champion=preferred_champ)
    reviewer = StubLLMReviewer()
    broker = PaperBroker()
    min_prob = float(risk_cfg.get("min_probability", 0.52))

    # Run all champion & shadow models in parallel on the frozen snapshot
    signals_by_model: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {b["model_name"]: pool.submit(_run_model_inference, b, snap_df, min_prob) for b in bundles}
        for name, fut in futs.items():
            signals_by_model[name] = fut.result()

    # Coordinator combines picks across all active models
    all_signals = [s for sigs in signals_by_model.values() for s in sigs]
    coordinated = coordinate_signals(all_signals, min_prob=min_prob)
    coord_by_isin = {c.signal.isin: c for c in coordinated}

    records: list[dict[str, Any]] = []
    decision_time = dtime(8, 45) if slot == "08:45" else dtime(9, 20)

    for bundle in bundles:
        mname = bundle["model_name"]
        is_champ = mname == champion_name
        sigs = sorted(signals_by_model[mname], key=lambda s: s.probability, reverse=True)[:max_candidates_per_model]

        risk_engine = RiskEngine(risk_cfg)
        risk_state = DailyRiskState()

        for sig in sigs:
            bar = bar_by_isin[sig.isin]
            coord_pick = coord_by_isin.get(sig.isin)
            coord_dec = (
                coord_pick.decision
                if (is_champ and coord_pick)
                else ("SHADOW_DIRECT" if not is_champ else "APPROVE_SINGLE")
            )

            verdict = reviewer.review(sig)
            if is_champ and coord_dec == "CONFLICT_SKIP":
                r_dec_approved = False
                r_qty = 0
                r_reason = coord_pick.reason if coord_pick else "CONFLICT_SKIP"
            elif verdict.decision != "APPROVE":
                r_dec_approved = False
                r_qty = 0
                r_reason = verdict.reason
            else:
                r_dec = risk_engine.evaluate(
                    signal=sig,
                    state=risk_state,
                    decision_time_ist=decision_time,
                    day_tradeable=tradeable,
                )
                r_dec_approved = r_dec.approved
                r_qty = r_dec.quantity
                r_reason = r_dec.reason

            if r_dec_approved and r_qty > 0:
                fill = broker.simulate_intraday_trade(
                    signal=sig,
                    quantity=r_qty,
                    bar_open=float(bar["open"]),
                    bar_high=float(bar["high"]),
                    bar_low=float(bar["low"]),
                    bar_close=float(bar["close"]),
                    bar_volume=int(bar["volume"]),
                )
                risk_state.realized_pnl_inr += fill.net_pnl_inr
                records.append(
                    {
                        "trade_date": trade_date,
                        "snapshot_slot": slot,
                        "model_name": mname,
                        "model_version": sig.model_version,
                        "dataset_version": dataset_version,
                        "isin": sig.isin,
                        "symbol": sig.symbol,
                        "sector": sig.sector,
                        "direction": sig.direction,
                        "probability": sig.probability,
                        "entry_price": sig.entry,
                        "target_price": sig.target,
                        "stop_price": sig.stop,
                        "coordinator_decision": coord_dec,
                        "llm_decision": verdict.decision,
                        "risk_decision": "APPROVED",
                        "decision_reason": r_reason,
                        "is_champion_trade": is_champ,
                        "quantity": fill.filled_qty,
                        "fill_price": fill.fill_price,
                        "exit_price": fill.exit_price,
                        "exit_reason": fill.exit_reason,
                        "slippage_inr": fill.costs.slippage_inr,
                        "total_cost_inr": fill.costs.total_cost_inr,
                        "gross_pnl_inr": fill.gross_pnl_inr,
                        "net_pnl_inr": fill.net_pnl_inr,
                        "counterfactual_pnl_inr": None,
                        "counterfactual_exit_reason": None,
                        "known_as_of": sig.known_as_of,
                    }
                )
            else:
                # Counterfactual simulation for rejected / untraded candidate (1-unit risk-budget qty)
                cf_qty = max(
                    1,
                    int(
                        (float(risk_cfg["capital_inr"]) * float(risk_cfg["risk_per_trade_pct"]))
                        / max(abs(sig.entry - sig.stop), 0.01)
                    ),
                )
                cf_sig = sig if sig.direction != "FLAT" else sig.model_copy(update={"direction": "LONG"})
                cf_fill = broker.simulate_intraday_trade(
                    signal=cf_sig,
                    quantity=cf_qty,
                    bar_open=float(bar["open"]),
                    bar_high=float(bar["high"]),
                    bar_low=float(bar["low"]),
                    bar_close=float(bar["close"]),
                    bar_volume=int(bar["volume"]),
                )
                records.append(
                    {
                        "trade_date": trade_date,
                        "snapshot_slot": slot,
                        "model_name": mname,
                        "model_version": sig.model_version,
                        "dataset_version": dataset_version,
                        "isin": sig.isin,
                        "symbol": sig.symbol,
                        "sector": sig.sector,
                        "direction": sig.direction,
                        "probability": sig.probability,
                        "entry_price": sig.entry,
                        "target_price": sig.target,
                        "stop_price": sig.stop,
                        "coordinator_decision": coord_dec,
                        "llm_decision": verdict.decision,
                        "risk_decision": "REJECTED",
                        "decision_reason": r_reason if tradeable else f"UNTRADEABLE_DAY: {'; '.join(reasons)}",
                        "is_champion_trade": is_champ,
                        "quantity": 0,
                        "fill_price": None,
                        "exit_price": None,
                        "exit_reason": "REJECTED",
                        "slippage_inr": 0.0,
                        "total_cost_inr": 0.0,
                        "gross_pnl_inr": 0.0,
                        "net_pnl_inr": 0.0,
                        "counterfactual_pnl_inr": cf_fill.net_pnl_inr,
                        "counterfactual_exit_reason": cf_fill.exit_reason,
                        "known_as_of": sig.known_as_of,
                    }
                )

    save_journal_records(records, trade_date)
    save_daily_summaries(
        records,
        trade_date,
        tradeable=tradeable,
        capital_inr=float(risk_cfg.get("capital_inr", 1_000_000.0)),
    )

    finished = datetime.now(IST)
    champ_recs = [r for r in records if r["is_champion_trade"]]
    champ_exec = sum(1 for r in champ_recs if r["quantity"] > 0)
    champ_pnl = round(sum(r["net_pnl_inr"] for r in champ_recs), 2)
    summary = (
        f"date={trade_date}, slot={slot}, champion={champion_name}, exec={champ_exec}, "
        f"net_pnl_inr={champ_pnl}, total_records={len(records)}"
    )
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_runs (job_name, started_at_ist, finished_at_ist, duration_sec, status, summary)
                VALUES ('sandbox_replay', %s, %s, %s, 'success', %s)
                """,
                (started, finished, round((finished - started).total_seconds(), 2), summary),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Job run record skipped: {exc}")

    log.info(f"Replay complete: {summary}")
    display_recs = sorted(
        champ_recs if champ_recs else records,
        key=lambda r: (-int(r["quantity"] > 0), -float(r["probability"])),
    )
    signals_out = [
        {
            "trade_date": str(r["trade_date"]),
            "snapshot_slot": r["snapshot_slot"],
            "model_name": r["model_name"],
            "symbol": r["symbol"],
            "sector": r["sector"],
            "direction": r["direction"],
            "probability": round(float(r["probability"]), 4),
            "entry_price": round(float(r["entry_price"]), 2),
            "target_price": round(float(r["target_price"]), 2),
            "stop_price": round(float(r["stop_price"]), 2),
            "coordinator_decision": r["coordinator_decision"],
            "llm_decision": r["llm_decision"],
            "risk_decision": r["risk_decision"],
            "decision_reason": r["decision_reason"],
            "is_champion_trade": bool(r["is_champion_trade"]),
            "quantity": int(r["quantity"]),
            "fill_price": round(float(r["fill_price"]), 2) if r["fill_price"] is not None else None,
            "exit_price": round(float(r["exit_price"]), 2) if r["exit_price"] is not None else None,
            "exit_reason": r["exit_reason"],
            "slippage_inr": round(float(r["slippage_inr"]), 2),
            "total_cost_inr": round(float(r["total_cost_inr"]), 2),
            "gross_pnl_inr": round(float(r["gross_pnl_inr"]), 2),
            "net_pnl_inr": round(float(r["net_pnl_inr"]), 2),
            "counterfactual_pnl_inr": (
                round(float(r["counterfactual_pnl_inr"]), 2)
                if r["counterfactual_pnl_inr"] is not None
                else None
            ),
        }
        for r in display_recs[:24]
    ]
    return {
        "trade_date": trade_date.isoformat(),
        "slot": slot,
        "tradeable": tradeable,
        "reasons": reasons,
        "champion": champion_name,
        "executed_champion_trades": champ_exec,
        "champion_net_pnl_inr": champ_pnl,
        "total_journal_records": len(records),
        "signals": signals_out,
    }


def replay_last_n_days(days: int = 25) -> list[dict[str, Any]]:
    """Replay the last `days` trading sessions available in the feature store chronologically."""
    df = pd.read_parquet(DATA_DIR / "features" / "features_daily.parquet", columns=["trade_date"])
    unique_dates = sorted(pd.to_datetime(df["trade_date"]).dt.date.unique())[-days:]
    results = []
    for dt in unique_dates:
        results.append(replay_date(dt))
    return results
