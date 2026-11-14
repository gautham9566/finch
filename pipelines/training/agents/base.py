"""Standard interface for trading agents outputting calibrated AgentSignal candidates."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from libs.common.config import get_markets_config
from libs.common.schemas import AgentSignal

IST = ZoneInfo("Asia/Kolkata")


class BaseAgent:
    """Wraps a fitted picker model into standardized (instrument, direction, prob, entry, target, stop) picks."""

    agent_name: str = "base"

    def predict_signals(
        self,
        snapshot_df: pd.DataFrame,
        model: Any,
        model_name: str,
        model_version: str,
        features: list[str],
        min_prob: float = 0.50,
    ) -> list[AgentSignal]:
        if snapshot_df.empty:
            return []
        cfg = get_markets_config()
        target_mult = float(cfg.get("target_atr_mult", 1.5))
        stop_mult = float(cfg.get("stop_atr_mult", 1.0))

        probs = model.predict_proba(snapshot_df[features])[:, 1]
        signals: list[AgentSignal] = []

        for idx, (_, row) in enumerate(snapshot_df.iterrows()):
            p = float(np.clip(probs[idx], 0.0, 1.0))
            entry = float(row["open"])
            atr = max(float(row["atr_14"]), entry * 0.005)
            target = round(entry + target_mult * atr, 2)
            stop = round(entry - stop_mult * atr, 2)
            known_ts = pd.Timestamp(row["feature_known_as_of"]).to_pydatetime()
            if known_ts.tzinfo is None:
                known_ts = known_ts.replace(tzinfo=IST)
            signals.append(
                AgentSignal(
                    model_name=model_name,
                    model_version=model_version,
                    agent_name=self.agent_name,
                    isin=str(row["isin"]),
                    symbol=str(row["symbol"]),
                    sector=str(row["sector"]),
                    direction="LONG" if p >= min_prob else "FLAT",
                    probability=round(p, 5),
                    entry=round(entry, 2),
                    target=target,
                    stop=stop,
                    atr=round(atr, 2),
                    opening_gap_pct=float(row.get("opening_gap_pct", 0.0)),
                    known_as_of=known_ts if isinstance(known_ts, datetime) else datetime.now(IST),
                )
            )
        return signals
