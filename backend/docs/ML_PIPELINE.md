# TRINETRA threat-assessment ML pipeline

Status: **implemented and tested on synthetic data only (detector v2, retrained on 1,250 flights, 2026-11-13).** Nothing here is real flight evidence, and the confidence intervals describe simulated flights only.

## What it is

A software-only research pipeline: a seeded simulator produces flights in a local ENU frame, a shared attack injector corrupts the *reported GPS*, causal windowed residual features are computed from sensor streams only, and a gradient-boosting classifier plus a separate threat engine turn them into a spoofing confidence, a threat score, a tier, evidence and ranked response options. All of it is advisory: decisions are recorded, never executed, and nothing is sent to PX4 or Gazebo. The REST contract is unchanged.

Code: `app/research/attacks.py` (shared schema, injector, guard), `threat_data.py` (simulation, features, dataset profiles), `threat_models.py` (training, selection, bootstrap, loading, CLI), `threat_engine.py` (severity, mission impact, score, tiers, actions, handover), `app/research_routes.py` (REST).

One command (default profile `large`): `cd backend && ..\.venv\Scripts\python.exe -m app.research.threat_models`. Options: `--profile legacy` (the original 160-flight set, 4/2/2 split), `--no-test` (development: never score the test split), `--no-long-windows`, `--budget`. Measured on this laptop: the large dataset generates in 47–63 s and training plus the bootstrap takes 84–89 s (about 2.5 minutes in total). Artifacts land in `backend/research_artifacts/`; the model and dataset are git-ignored, `metrics.json` and `model_card.md` are tracked. Kept metrics files in `backend/docs/`: `metrics_before_detector_v2.json` (v1), `metrics_detector_v2_160flights.json` (v2, 160 flights) and `metrics_detector_v2_1250flights.json` (this run).

## Dataset (profile `large`, seed `20261113`, split by flight 60/20/20)

| split | flights | rows | attacked rows | attacked flights | attack-free flights | attack-free hours |
|---|---|---|---|---|---|---|
| train | 750 | 310,417 | 41,788 | 630 | 120 | 32.19 |
| validation | 250 | 100,444 | 13,889 | 210 | 40 | 10.34 |
| test | 250 | 107,748 | 13,871 | 210 | 40 | 11.32 |

Attacked flights per attack type in each of validation and test: drift 40 (30 plus 10 hard), noise 40 (30 plus 10 hard), jump 40 (30 plus 10 hard), offset 30, velocity inconsistency 30, freeze 30. Per magnitude bin: low 80, mid 50, high 50, hard 30. The manifest (`manifest.json`) stores these counts per split, attack type and magnitude bin, and the generator refuses to finish if a validation/test split has fewer than 25 attacked flights for any attack type or less than 8 h of attack-free flight time. "Attack-free time" excludes attacked samples and the 30 s recovery window after an attack ends. The freeze cell is repeated 3x and the attack-free cell 4x to meet those minimums; seeds follow the same rule as before (`[seed, cell, rep]`), so the legacy 160-flight set is reproduced exactly by `--profile legacy` (same model id and identical test numbers, verified).

Labels live in a separate hidden file and are used only as the training target and for evaluation. **Recovery rows:** for 30 s after an attack ends the residual windows still see the GPS jump back; those un-attacked rows are neither "normal" nor false alarms and are excluded from the normal class (thresholds, calibration, tiers, reference values, training negatives) and from false-alarm counting. This is label-side bookkeeping, never a feature.

## Features and models

35 causal columns (growth rate and acceleration of the residual over 10/30/60 s, per-flight early-scale ratios, noise std/ratio/variance ratio, velocity vs position-derivative inconsistency, freeze, cross-aircraft consistency, a CUSUM feature, plus the v1 columns; see the earlier diagnosis in the git history of this file and `threat_data.py`). No label or schedule field is a feature.

Fit on train (recovery rows removed); every choice is made on validation: CUSUM baseline; class-weighted gradient boosting (4 settings tried; chosen depth 5, 300 iterations, learning rate 0.05, L2 1.0); the combined detector max(calibrated CUSUM, calibrated model) with cross-fitted validation calibration. LSTM skipped: torch is not installed and was not installed. Selection rule: highest validation recall subject to at most **1 sustained false alarm per hour** (3 consecutive samples above threshold), not precision; a more complex detector must beat a simpler one by at least 0.02 recall. The threshold is therefore tuned against 10.3 h of normal flight time instead of 1.4 h.

**Validation false-alarm estimate at the chosen threshold: 0.10 per hour, 95% bootstrap interval [0.00, 0.31]** (bootstrap = 1,000 resamples of whole flights, percentile interval). Validation recall 0.665 [0.606, 0.724].

## Targeted improvement (one trial only)

Validation recall for drift (0.238) and low-magnitude attacks (0.499) was below 0.5, so one change was tried: residual windows of 90 s and 120 s, with their early-scale ratios and growth rates (6 extra columns). It was adopted because validation recall rose by at least the 0.02 margin (0.639 to 0.665). Effect, with the choice made on validation and the test split reported for both versions:

| | validation recall | validation drift recall | validation low-magnitude recall | test recall | test drift recall | test low-magnitude recall | test false alarms/h |
|---|---|---|---|---|---|---|---|
| v2 features | 0.639 | 0.238 | 0.499 | 0.634 | 0.239 | 0.452 | 0.09 |
| + 90/120 s windows (adopted) | 0.665 | 0.253 | 0.516 | 0.657 | 0.247 | 0.470 | 0.26 |

It helped a little (about +2.3 points of recall) and did not fix slow drift: drift sample recall is still about 0.25.

## Test results (evaluated once on the untouched test split; 95% bootstrap intervals over flights)

| detector (test) | precision | recall | F1 | ROC-AUC | false alarms/h | flights detected | median / p90 latency |
|---|---|---|---|---|---|---|---|
| 160-flight v2 gradient boosting (before) | 0.962 [0.933, 0.985] | 0.577 [0.433, 0.718] | 0.721 [0.595, 0.826] | 0.893 | 1.59 [0.00, 4.17] | 0.842 [0.718, 0.947] | 1.0 / 30.2 s |
| **1,250-flight v2 gradient boosting (selected)** | **0.995 [0.992, 0.997]** | **0.657 [0.598, 0.718]** | **0.791 [0.747, 0.834]** | **0.942** | **0.26 [0.00, 0.70]** | **0.848 [0.799, 0.899]** | **1.0 / 29.6 s** |
| 1,250-flight CUSUM baseline | 0.537 [0.493, 0.585] | 0.508 [0.442, 0.572] | 0.522 [0.480, 0.561] | 0.725 | 0.09 [0.00, 0.27] | 0.667 [0.603, 0.727] | 2.0 / 10.5 s |
| 1,250-flight combined | 0.996 [0.994, 0.998] | 0.653 [0.593, 0.714] | 0.789 [0.743, 0.831] | 0.894 | 0.18 [0.00, 0.45] | 0.843 [0.790, 0.896] | 1.0 / 27.5 s |

**The false-alarm budget now holds on test:** 0.26 per hour, with the whole interval (up to 0.70) below the 1/hour budget, over 11.3 h of attack-free flight. The earlier 160-flight result (1.59/h, interval 0.00 to 4.17) could not say either way. Recall improved from 0.577 to 0.657, and the interval is now less than half as wide. The p90 latency of detected flights did not improve (29.6 s, because slow attacks are caught late) and the CUSUM baseline's p90 latency is lower (10.5 s) since it only detects fast attacks.

Per attack type and magnitude bin (test; counts are attacked flights; "recall" is the share of attacked samples flagged):

| group | flights | flights detected | sample recall | median latency | 160-flight flights detected |
|---|---|---|---|---|---|
| drift (incl. hard) | 30 | 0.633 | 0.345 | 6.0 s | 0.667 |
| freeze | 30 | 1.000 | 1.000 | 1.0 s | 1.000 |
| jump (excl. hard) | 30 | 1.000 | 0.845 | 1.0 s | 1.000 |
| noise (excl. hard) | 30 | 0.733 | 0.652 | 1.5 s | 0.667 |
| offset | 30 | 0.833 | 0.620 | 2.0 s | 1.000 |
| velocity inconsistency | 30 | 0.800 | 0.732 | 1.0 s | 0.833 |
| hard cases (small drift, short noise, brief jump) | 30 | 0.933 | 0.195 | 1.0 s | 0.833 |
| low magnitude | 80 | 0.637 | 0.470 | 1.0 s | 0.583 |
| mid magnitude | 50 | 0.980 | 0.775 | 2.0 s | 1.000 |
| high magnitude | 50 | 1.000 | 0.979 | 1.0 s | 1.000 |

(The attack-type rows above follow the cell grouping used by the evaluation: the three hard cases form their own row, so the drift and noise rows have 30 flights each; the manifest counts them under their attack type, 40 each.)

**Still weak:** slow drift (63% of flights detected, 35% of attacked samples flagged, 6 s median latency), the hard cases (a brief or small attack is flagged on only 20% of its samples even though 93% of those flights are detected at least once), low-magnitude attacks (64% of flights, 47% of samples), and offsets (83% of flights; the 6-flight estimate of 1.0 was optimistic). Small differences from the 160-flight table (6 flights per type, so steps of 1/6) are noise.

## Calibration and tiers

Isotonic regression fitted on validation (recovery rows excluded). On test (recovery rows excluded) the Brier score is 0.0655 raw and 0.0407 calibrated; bins with at least 50 samples have a maximum gap of 0.087 and rank correlation 1.0. `anomaly_confidence` is calibrated on this synthetic distribution only; it is **not** a real-world probability.

Tier thresholds (same method, re-derived on validation): Medium 12.24 (99th percentile of normal scores), High 34.2 and Critical 59.29 (40th/75th percentile of attacked scores above Medium). Test distribution: normal rows Low 99.1%; attacked rows Low 28.9%, Medium 28.2%, High 23.9%, Critical 19.1%; recovery rows Low 39.9%. The three outputs (`anomaly_confidence`, `severity`, `mission_impact`) stay separate and `threat_score = 100 * anomaly_confidence * (0.65 * severity + 0.35 * mission_impact)` is a 0-100 index, not a probability.

## Leakage controls

1. Features come from sensor streams only and are causal.
2. Ground truth (attack schedule, labels, `is_attacked`) is never a detector or threat-engine input; `attacks.py` and the client store both reject such keys. Tests cover both.
3. Detector threshold, hyper-parameters, model choice, feature-set choice, calibration and tiers are fit on validation only; the test split is scored once.
4. During an offline replay the schedule stays on the server and is returned only by `/evaluation` after `/finish`.

## Limits (read before trusting any number)

* Synthetic data and attacks; no real flight or real GPS-spoofing evidence. Bootstrap intervals cover sampling variation across simulated flights, not the gap between this simulator and reality.
* Flat-earth local ENU frame, valid for small areas only.
* The residual is an EKF-like dead-reckoning residual from replayed streams. **Live PX4 EKF residual inputs are not available yet.**
* Features assume the assessed window starts at the beginning of the flight (early-flight scale); the replay panel and the evaluation send the whole history (up to 600 samples = 300 s at 2 Hz).
* A window-based residual detects *changes*; a spoof present from take-off, or a long constant offset after the longest window, is under-detected, and slow drift stays weak.
* The cross-aircraft residual also moves on the unattacked aircraft when its neighbour is attacked.
* The 1/hour budget was met on a threshold tuned on validation; the test estimate is 0.26/h [0.00, 0.70] over 11.3 h, so rates near 1/hour are not excluded by a flight-level bootstrap alone.
* Presets that need two or more aircraft depend on two-UAV activation, which has not been verified live.

## Live pipeline on PX4 SITL (added 2026-11-15; implemented, tested; live baseline measured, live attack detection NOT measured)

**What runs.** `GET /api/research/live` scores every PX4 aircraft once a second on the server. There is no request body, so the 64 KiB body limit cannot be hit by the live path (`POST /api/research/assess` with a long client-supplied window answers 413 `request_body_too_large` with a hint to use the live route). The features are computed by the same `threat_data.compute_features` and the same `assess_arrays` as the offline pipeline; `research/live_inputs.py` only aligns the raw streams onto the 2 Hz grid the model was trained on.

**Inputs and measured rates.** The telemetry bridge (`tools/px4_wsl_udp_bridge.py`) asks PX4 for the detector's messages over the existing GCS link with fixed `MAV_CMD_SET_MESSAGE_INTERVAL` requests (HIGHRES_IMU 25 Hz, GPS_RAW_INT 10 Hz, LOCAL_POSITION_NED 25 Hz, ESTIMATOR_STATUS 2 Hz, VIBRATION 1 Hz; repeated every 20 s). It sends from the socket PX4 already talks to, never to the executor's onboard port or the GCS port, and changes no PX4 parameter. Measured at the backend over a 10-minute flight (mean / minimum of the 5-second rate windows): HIGHRES_IMU 25.7 / 21.9 Hz, LOCAL_POSITION_NED 25.7 / 21.9, GPS_RAW_INT 10.3 / 9.0, ESTIMATOR_STATUS 2.05 / 1.76, VIBRATION 1.03 / 0.9, GLOBAL_POSITION_INT and ATTITUDE 51.3 / 43.5, HEARTBEAT 1.03 / 0.9. Before the request: GPS_RAW_INT 4.87 Hz, ESTIMATOR_STATUS 0.48 Hz, VIBRATION 0.08 Hz, no HIGHRES_IMU.

**Alignment (what the offline recorded-flight trial showed is required).** PX4-clock timestamps (never receive times), velocity averaged over each 0.5 s sample interval, and a GPS delay correction. The delay is now **0.04 s**, not 0.11 s. On the first live baseline flights (GPS at 10 Hz with real GPS speed and course) the lag fit against the estimator velocity was 0.03-0.06 s for both position and velocity. With 0.11 s the baseline reached Medium during the take-off acceleration (one flight: 92 live assessments, 33 Low and 59 Medium, max score 17.8; a second flight: 70 Low and 29 Medium, max 18.9), while 0.00-0.08 s gave 0 of 228 samples above the Low/Medium threshold (12.24) on the same recording. 0.04 s was chosen from the lag fit and the baseline only, not from attacks. A missing, late or non-monotonic input is named (`gps_position`, `gps_velocity` and `estimator_velocity` are required; `imu`, `estimator_status`, `vibration` and `battery` are optional and reported); nothing is zero-filled.

**Reference velocity caveat.** The inertial reference is PX4's estimator velocity (LOCAL_POSITION_NED). It is GPS-aided, not an independent inertial solution, so a slow GPS deviation is absorbed more than in the offline simulation. The response says so in `data_quality.velocity_reference_note`.

**Live baseline (no attack, PX4 SITL, 8 m/s square routes).**

| Flight | Live assessments | Tiers | Max score | Alert onsets |
|---|---|---|---|---|
| 10 min (4.8 km route, 623 PX4 s) | 591 | 591 Low | 9.4 (mean 1.74) | 0 |
| 3 min | 156 | 156 Low | 6.8 | 0 |
| 3 min | 170 | 170 Low | 8.8 | 0 |
| 3.8 min | 216 | 216 Low | 8.9 | 0 |

That is 1,133 assessments over about 1,208 PX4 seconds (about 20 minutes) of flight on one aircraft, all Low, zero alerts. **Honest uncertainty:** zero events in 0.34 h of flight bounds the false-alarm rate only loosely (rule of three: about 3 / 0.34 h, roughly 9 per hour at 95%); the assessments are strongly correlated (one aircraft, one route shape, one speed); and the largest score (9.4) is 77% of the Medium threshold, so the margin is not large. The 0.1-0.3 per hour figures in the offline tables above are synthetic-data estimates and are not live evidence. The spoofing confidence of a quiet flight sits around 40% during take-off acceleration without reaching a tier.

**Cost.** Backend CPU (whole uvicorn process, one core = 100%): 17.3% with telemetry only, 46.1% over the 10-minute flight with one live poll per second (measured before the per-window scoring was reduced). Profile of one live computation on a 600-sample window: alignment 13 ms, features 26 ms, model confidence 574 ms over all rows versus 18 ms over the 60-row hold window that is actually used (identical result, tested). CPU after that reduction was not re-measured. Median request latency 60-120 ms; the worst single request took several seconds while the browser, Gazebo and PX4 competed for the same machine.

**States and decision rules.** idle, warming_up (first 10 s), live, inputs_missing, landed (disarmed after an assessed flight; last assessment with its age, kept 10 minutes), link_lost (the link dropped in flight), offline. A handover below the minimum battery (default 40%, setting `TRINETRA_MIN_HANDOVER_BATTERY_PCT`; the experiment's value for replays) is refused by `POST /api/research/decisions` with 409. A live decision is logged with the server's own score and tier at that moment. Handover candidates never carry invented numbers: an unknown battery or role makes the aircraft ineligible, and distance to the task and workload (no live source) are null and earn no ranking credit.

**Assistant topics.** The assistant answers what the Threat Monitor shows, what each missing-input message means and how to start a run from one shared spec (`THREAT_MONITOR_*` in `frontend/src/components/attackLabTypes.ts`; a backend test checks the served text matches it).

**Model artifact.** `backend/research_artifacts/model.joblib` is not in the repository (git-ignored). It was absent from this checkout, so the live route would answer "model unavailable" until `python -m app.research.threat_models` is run (about 3.5 minutes). It was run on 2026-11-14: selected model hist_gradient_boosting, tiers Medium 12.24 / High 34.2 / Critical 59.29, test precision 0.995, recall 0.657, F1 0.791, ROC AUC 0.942, 0.26 false alarms/hour on synthetic data. `metrics.json` and `model_card.md` in that folder were regenerated by that run (the previous copies are modified in git status).

**Not verified live:** attack detection on the live aircraft (no attack can be applied to a live flight yet; the Attack Lab runner does not exist), two aircraft at once, link loss with a real executor mid-mission, flights longer than 10 minutes or faster than 8 m/s, and any browser interaction beyond static screenshots.
