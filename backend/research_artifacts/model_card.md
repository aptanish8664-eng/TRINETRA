# GPS inconsistency detector - model card

Model id `049e3cff124a`, trained 2026-11-13T04:20:08.910813+00:00. **Synthetic data only; not validated on real attacks.**
Dataset profile `large` (train: 750 flights, 32.19 h attack-free, val: 250 flights, 10.34 h attack-free, test: 250 flights, 11.32 h attack-free). Selected detector: **hist_gradient_boosting** on feature set `v2_long_windows` - highest validation recall subject to <= 1 sustained false alarm(s) per hour; a more complex detector must beat a simpler one by >= 0.02 recall; not precision.

| model (test split; 95% bootstrap interval over flights) | precision | recall | F1 | ROC-AUC | false alarms/h | flights detected | median / p90 latency s |
|---|---|---|---|---|---|---|---|
| CUSUM baseline | 0.5373 [0.493, 0.585] | 0.5079 [0.442, 0.572] | 0.5222 [0.48, 0.561] | 0.7248 | 0.09 [0.0, 0.273] | 0.667 [0.603, 0.727] | 2.0 / 10.5 |
| Gradient boosting (class-weighted) | 0.9947 [0.992, 0.997] | 0.6568 [0.598, 0.718] | 0.7912 [0.747, 0.834] | 0.9419 | 0.26 [0.0, 0.702] | 0.848 [0.799, 0.899] | 1.0 / 29.6 |
| Combined (max of calibrated CUSUM and model) | 0.9958 [0.994, 0.998] | 0.6526 [0.593, 0.714] | 0.7885 [0.743, 0.831] | 0.8935 | 0.18 [0.0, 0.447] | 0.843 [0.79, 0.896] | 1.0 / 27.5 |

Sequence model: skipped (torch is not installed and was not installed automatically).

Calibration: isotonic regression fit on the validation split (recovery rows excluded); test Brier 0.0655 -> 0.04069.
Tier thresholds (validation): {'medium': 12.24, 'high': 34.2, 'critical': 59.29}.

Known limits: synthetic data, not real attacks; flat-earth ENU frame; EKF-like residual, not a live PX4 EKF residual; confidence intervals are bootstrap over simulated flights and do not cover simulator-vs-real differences; features assume the assessed window starts at the beginning of the flight (early-flight scale).
