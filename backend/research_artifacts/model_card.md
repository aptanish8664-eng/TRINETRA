# GPS inconsistency detector - model card

Model id `d9d7d3e7b267`, trained 2026-11-18T04:22:13.215313+00:00. **Synthetic data only; not validated on real attacks.**
Dataset profile `large` (train: 750 flights, 32.19 h attack-free, val: 250 flights, 10.34 h attack-free, test: 250 flights, 11.32 h attack-free). Selected detector: **hist_gradient_boosting** on feature set `v2` - highest validation recall subject to <= 1 sustained false alarm(s) per hour; a more complex detector must beat a simpler one by >= 0.02 recall; not precision.

| model (test split; 95% bootstrap interval over flights) | precision | recall | F1 | ROC-AUC | false alarms/h | flights detected | median / p90 latency s |
|---|---|---|---|---|---|---|---|
| CUSUM baseline | 0.5017 [0.457, 0.551] | 0.4334 [0.366, 0.497] | 0.4651 [0.419, 0.51] | 0.7357 | 0.43 [0.0, 1.073] | 0.567 [0.502, 0.63] | 3.0 / 8.6 |
| Gradient boosting (class-weighted) | 0.9988 [0.997, 1.0] | 0.5938 [0.529, 0.652] | 0.7448 [0.692, 0.79] | 0.9326 | 0.0 [0.0, 0.0] | 0.748 [0.69, 0.806] | 1.0 / 7.0 |
| Combined (max of calibrated CUSUM and model) | 0.999 [0.998, 1.0] | 0.5912 [0.527, 0.65] | 0.7428 [0.69, 0.788] | 0.888 | 0.0 [0.0, 0.0] | 0.748 [0.69, 0.806] | 1.0 / 7.0 |

Sequence model: skipped (torch is not installed and was not installed automatically).

Calibration: isotonic regression fit on the validation split (recovery rows excluded); test Brier 0.07327 -> 0.04438.
Tier thresholds (validation): {'medium': 12.7, 'high': 37.03, 'critical': 62.14}.

Known limits: synthetic data, not real attacks; flat-earth ENU frame; EKF-like residual, not a live PX4 EKF residual; confidence intervals are bootstrap over simulated flights and do not cover simulator-vs-real differences; features assume the assessed window starts at the beginning of the flight (early-flight scale).


## Live addendum (2026-11-18)

Trained on 1,250 synthetic flights plus 25 live clean SITL flights (weight 4). Live held-out clean flights: 0 alert episodes in 0.422 aircraft-hours (95 % upper bound 7.1 per hour); the budget of 1 per hour is not demonstrated. Synthetic test sensitivity is lower than the previous model (recall 0.594 vs 0.657). Live attacks were injected into the monitor's copy of the GPS stream only. See docs/DETECTOR_LOOPHOLES.md.
