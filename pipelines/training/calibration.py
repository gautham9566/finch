"""Probability calibration diagnostics: Expected Calibration Error (ECE) and reliability table."""

from __future__ import annotations

from typing import Any

import numpy as np


def compute_calibration(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> tuple[float, list[dict[str, Any]]]:
    """Compute ECE and per-bin reliability statistics."""
    if len(y_true) == 0:
        return 0.0, []
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.clip(np.asarray(y_prob, dtype=float), 0.0, 1.0)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    bins: list[dict[str, Any]] = []
    n = len(y_true)

    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (y_prob >= lo) & (y_prob <= hi if i == n_bins - 1 else y_prob < hi)
        cnt = int(mask.sum())
        if cnt == 0:
            continue
        conf = float(y_prob[mask].mean())
        acc = float(y_true[mask].mean())
        ece += (cnt / n) * abs(acc - conf)
        bins.append(
            {
                "bin": f"{lo:.1f}-{hi:.1f}",
                "count": cnt,
                "mean_pred": round(conf, 4),
                "empirical_rate": round(acc, 4),
            }
        )
    return round(float(ece), 5), bins
