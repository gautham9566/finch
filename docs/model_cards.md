# Model & Agent Cards

Finch evaluates 9 YAML-configured models under `/config/models/*.yaml`, grouped under specialist intraday agents and a deterministic coordinator.

## 1. Models

| Model Name | Type | Agent | Baseline | Primary Role |
| --- | --- | --- | --- | --- |
| `baseline_logreg` | Logistic Regression | `momentum` | Yes | L2-regularized linear probability baseline (`C=0.25`, balanced weights) across all 16 features |
| `baseline_gap_and_go` | Deterministic Rule | `momentum` | Yes | Simple opening gap (`0.5% - 3.0%`) + volume surge (`>= 1.15x 20d avg`) momentum rule |
| `baseline_buy_hold` | Deterministic Rule | `benchmark` | Yes | Equal-weighted top-symbol proxy for Nifty 50 daily session exposure |
| `lgbm_picker` | LightGBM Classifier | `momentum` | No | Shallow gradient-boosted trees (`max_depth=4`, `num_leaves=15`, `min_child_samples=80`) across all 16 features |
| `xgb_picker` | XGBoost Classifier | `momentum` | No | Regularized XGBoost ensemble (`max_depth=4`, `min_child_weight=50`, L1/L2 penalties) across all 16 features |
| `regime_classifier` | Gradient Boosted Classifier | `regime` | No | Global cues + VIX macro regime model gating risk-on vs risk-off exposure (`vix_skip_threshold=28.0`) |
| `gru_sequence` | Compact Sequence Model | `momentum` | No | 10-day sliding sequence encoder (`hidden_size=16`, CPU-trained) over price, volume, ATR, and VIX changes |
| `mean_reversion` | LightGBM Classifier | `mean_reversion` | No | Shallow LightGBM (`max_depth=3`, `num_leaves=10`) specialized in 20d range extremes and overnight fades |
| `sector_news` | XGBoost Classifier | `sector_news` | No | Shallow XGBoost (`max_depth=3`) emphasizing sector relative strength (`rel_strength_sector_5d`) and trailing 24h RSS/NSE sentiment |

## 2. Specialist Agents & Coordinator

- **`agent_a_momentum`** (`MomentumAgent`): Tracks breakouts, opening gaps, relative strength vs Nifty 50, and volume surges.
- **`agent_b_mean_reversion`** (`MeanReversionAgent`): Tracks stretched deviations from 20-day highs/lows under low-to-moderate VIX.
- **`agent_c_sector_news`** (`SectorNewsAgent`): Weights sector-level relative strength alongside whitelisted trailing 24h news sentiment.
- **`Coordinator`** (`pipelines/training/agents/coordinator.py`): Combines candidate specialist signals using deterministic weighting and disagreement resolution (`APPROVE` / `REJECT`) before forwarding candidates to `StubLLMReviewer` and plain-code `RiskEngine`.
- **Policy Distillation Hook (`pipelines/training/distillation.py`):** Scaffolding ready (`distillation_enabled: false` until `>= 60` paper-trading days accumulate).
