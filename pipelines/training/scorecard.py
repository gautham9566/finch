"""Model evaluation scorecard with real statutory costs, slippage, and promotion rules."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from libs.common.config import get_costs_config, get_risk_config, get_scorecard_config
from libs.common.costs import apply_pessimistic_fill, calculate_roundtrip_costs
from pipelines.training.calibration import compute_calibration


def _safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return 0.5
    return round(float(roc_auc_score(y_true, y_prob)), 5)


def evaluate_portfolio_after_costs(
    df_eval: pd.DataFrame,
    probs: np.ndarray,
    model_name: str,
    top_k_per_day: int = 5,
    min_prob: float = 0.52,
) -> dict[str, Any]:
    """Simulate daily top-k intraday picks with real Indian brokerage/taxes and slippage."""
    if df_eval.empty:
        return {
            "net_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "trades": 0,
            "regime_breakdown": {},
        }

    costs_cfg = get_costs_config()
    risk_cfg = get_risk_config()
    capital = float(risk_cfg.get("capital_inr", 1_000_000.0))
    risk_per_trade = capital * float(risk_cfg.get("risk_per_trade_pct", 0.005))
    max_pos_val = capital * float(risk_cfg.get("max_position_pct", 0.10))

    work = df_eval.copy()
    work["pred_prob"] = probs
    equity = capital
    peak = capital
    max_dd = 0.0
    trade_pnls: list[float] = []
    regime_pnls: dict[str, list[float]] = {"low_vix": [], "normal_vix": [], "high_vix": []}

    is_buy_hold = model_name == "baseline_buy_hold"

    for _dt, day_df in work.groupby("trade_date", sort=True):
        if is_buy_hold:
            picks = day_df.head(5)
        else:
            picks = (
                day_df[day_df["pred_prob"] >= min_prob].sort_values("pred_prob", ascending=False).head(top_k_per_day)
            )

        if picks.empty:
            continue

        day_net = 0.0
        for _, row in picks.iterrows():
            entry_raw = float(row["open"])
            atr = max(float(row.get("atr_14", entry_raw * 0.01)), entry_raw * 0.005)
            qty = max(1, min(int(risk_per_trade / atr), int(max_pos_val / entry_raw)))
            # Raw simulated exit from label move
            gross_ret_frac = float(row.get("gross_return_bps", 0.0)) / 10000.0
            exit_raw = entry_raw * (1.0 + gross_ret_frac)

            fill_in = apply_pessimistic_fill(entry_raw, "BUY", cfg=costs_cfg)
            fill_out = apply_pessimistic_fill(exit_raw, "SELL", cfg=costs_cfg)
            costs = calculate_roundtrip_costs(
                entry_price=fill_in,
                exit_price=fill_out,
                quantity=qty,
                direction="LONG",
                gap_pct=float(row.get("opening_gap_pct", 0.0)),
                cfg=costs_cfg,
            )
            gross_pnl = (exit_raw - entry_raw) * qty
            net_pnl = gross_pnl - costs.total_cost_inr
            day_net += net_pnl
            trade_pnls.append(net_pnl)

            vix = float(row.get("india_vix_level", 15.0))
            reg = "high_vix" if vix >= 20.0 else ("low_vix" if vix < 14.0 else "normal_vix")
            regime_pnls[reg].append(net_pnl)

        equity += day_net
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    net_ret_pct = ((equity - capital) / capital) * 100.0
    wins = [p for p in trade_pnls if p > 0]
    losses = [abs(p) for p in trade_pnls if p < 0]
    win_rate = (len(wins) / len(trade_pnls)) if trade_pnls else 0.0
    profit_factor = (sum(wins) / sum(losses)) if losses else (2.0 if wins else 0.0)

    reg_summary = {
        k: {
            "trades": len(v),
            "net_pnl_inr": round(sum(v), 2),
            "win_rate": round(sum(1 for x in v if x > 0) / len(v), 4) if v else 0.0,
        }
        for k, v in regime_pnls.items()
    }
    return {
        "net_return_pct": round(net_ret_pct, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(min(profit_factor, 10.0), 4),
        "trades": len(trade_pnls),
        "regime_breakdown": reg_summary,
    }


def build_model_scorecard(
    model_name: str,
    y_train: np.ndarray,
    p_train: np.ndarray,
    y_val: np.ndarray,
    p_val: np.ndarray,
    df_test: pd.DataFrame,
    p_test: np.ndarray,
    is_baseline: bool,
) -> dict[str, Any]:
    """Assemble full diagnostics and portfolio metrics for one model."""
    rules = get_scorecard_config()["promotion_rules"]
    train_auc = _safe_auc(y_train, p_train)
    val_auc = _safe_auc(y_val, p_val)
    y_test = df_test["target_hit"].to_numpy()
    test_auc = _safe_auc(y_test, p_test)
    gap = round(abs(train_auc - val_auc), 5)

    overfit_flag = bool((train_auc - val_auc) > float(rules.get("max_train_val_auc_gap", 0.15)))
    underfit_flag = bool((not is_baseline) and (train_auc < float(rules.get("min_train_auc", 0.505))))

    ece, rel_bins = compute_calibration(y_test, p_test, n_bins=10)
    port = evaluate_portfolio_after_costs(df_test, p_test, model_name=model_name)

    return {
        "model_name": model_name,
        "is_baseline": is_baseline,
        "train_auc": train_auc,
        "val_auc": val_auc,
        "test_auc": test_auc,
        "train_val_gap": gap,
        "overfit_flag": overfit_flag,
        "underfit_flag": underfit_flag,
        "ece": ece,
        "reliability_bins": rel_bins,
        **port,
    }


def rank_and_assign_statuses(scorecards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank models and assign candidate/shadow/champion/retired using pre-committed scorecard rules."""
    cfg = get_scorecard_config()
    rules = cfg["promotion_rules"]
    weights = cfg["weights"]

    by_name = {s["model_name"]: s for s in scorecards}
    must_beat = [by_name[b]["net_return_pct"] for b in rules.get("must_beat_baselines", []) if b in by_name]
    baseline_floor = max(must_beat) if must_beat else -999.0

    for sc in scorecards:
        beats = sc["net_return_pct"] > baseline_floor
        eligible = (
            (not sc["is_baseline"])
            and beats
            and sc["net_return_pct"] >= float(rules.get("min_net_return_pct", 0.0))
            and sc["profit_factor"] >= float(rules.get("min_profit_factor", 1.02))
            and (sc["max_drawdown_pct"] / 100.0) <= float(rules.get("max_drawdown_pct", 0.12))
            and sc["ece"] <= float(rules.get("max_calibration_ece", 0.15))
            and not sc["overfit_flag"]
        )
        sc["beats_baseline"] = bool(beats)
        sc["composite_score"] = round(
            float(weights["net_return_pct"]) * sc["net_return_pct"]
            + float(weights["profit_factor"]) * sc["profit_factor"] * 5.0
            + float(weights["win_rate"]) * sc["win_rate"] * 10.0
            - float(weights["neg_drawdown"]) * sc["max_drawdown_pct"]
            - float(weights["neg_ece"]) * sc["ece"] * 20.0,
            4,
        )
        sc["eligible"] = eligible

    ordered = sorted(scorecards, key=lambda s: (s["eligible"], s["composite_score"]), reverse=True)

    champion_assigned = False
    for sc in ordered:
        if sc["is_baseline"]:
            sc["promotion_status"] = "shadow"
        elif sc["eligible"] and not champion_assigned:
            sc["promotion_status"] = "champion"
            champion_assigned = True
        elif sc["beats_baseline"] or not sc["overfit_flag"]:
            sc["promotion_status"] = "shadow"
        else:
            sc["promotion_status"] = "candidate"
    return ordered
