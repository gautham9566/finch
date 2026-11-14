# Scorecard & Promotion Rules

All thresholds and ranking weights are declared in `/config/scorecard.yaml` and `/config/costs.yaml`, never hardcoded in feature or model logic.

## 1. Walk-Forward Split Geometry

- **Chronological only:** Dates are strictly ordered (`train_end < val_start`, `val_end < test_start`). Random shuffles across dates are prohibited.
- **Purge gap (`purge_days: 2`):** Separates every train/validation boundary and validation/test boundary to prevent multi-day momentum or rolling volatility leakage.
- **Untouched holdout (`test_holdout_days`):** Used solely once per run to measure out-of-sample AUC, Expected Calibration Error (ECE), net return after costs, drawdown, win rate, and profit factor.

## 2. Statutory Cost & Slippage Assumptions (`config/costs.yaml`)

| Cost Component | Rule / Rate |
| --- | --- |
| Brokerage | `0.03%` per leg capped at `INR 20` per order |
| STT (Intraday Equities) | `0.025%` on sell turnover |
| NSE Exchange Transaction | `0.00297%` on total turnover |
| SEBI Turnover Fee | `0.0001%` on total turnover |
| Stamp Duty | `0.003%` on buy turnover |
| GST | `18%` on `(brokerage + exchange_txn + SEBI)` |
| Base Slippage | `5.0 bps` adverse per leg (`1.5x` multiplier when `|opening_gap_pct| > 1.5%`) |

Same-bar execution ambiguity resolves pessimistically: if a single daily bar intersects both `stop_price` and `target_price`, `PaperBroker` always exits at `STOP`.

## 3. Diagnostic Flags

- `overfit_flag = true` if `train_auc - val_auc > 0.15` (`max_train_val_auc_gap`).
- `underfit_flag = true` if non-baseline model has `train_auc < 0.505` (`min_train_auc`).
- **Expected Calibration Error (ECE):** Computed across 10 probability bins on out-of-sample predictions (`max_calibration_ece: 0.15`).

## 4. Promotion Ladder & Composite Score

To be eligible for **`champion`**, a non-baseline model must satisfy every hard gate:
1. Net return strictly exceeds all `must_beat_baselines` (`baseline_logreg` and `baseline_gap_and_go`).
2. `net_return_pct >= 0.0%` after all statutory costs and slippage.
3. `profit_factor >= 1.02` and `max_drawdown_pct <= 12%`.
4. `ece <= 0.15` and `overfit_flag == false`.

Eligible candidates are ranked by composite score:

```text
score = 0.40 * net_return_pct
      + 0.25 * (profit_factor * 5.0)
      + 0.15 * (win_rate * 10.0)
      - 0.10 * max_drawdown_pct
      - 0.10 * (ece * 20.0)
```

- Highest-scoring eligible model -> **`champion`**
- Remaining strong / non-overfit models & baselines -> **`shadow`**
- Underperforming / flagged models -> **`candidate`** (or **`retired`** when superseded).
