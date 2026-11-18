# Detector loopholes (Part 7, step 1: the table and the plan)

Status: **step 1 done (this table, evidence from this run's live flights and from the code); the fixes, retraining and before/after validation are NOT done in this run.** Nothing here is real flight evidence: the model was trained on synthetic flights, and all live numbers are PX4 SITL flights with an injected simulated GPS fault.

## 1. What was measured in this run (live PX4 SITL, throwaway backend)

| Flight | Aircraft | Speed | Baseline (no attack) Medium-or-higher episodes | Source |
|---|---|---|---|---|
| Part 3, one experiment | px4-1 | 10 m/s survey | **2 episodes in 427 s** (Medium for about 40 s from 45 s, again 138-156 s) | run record (`live_exp_1.json`) |
| Part 3, two experiments | px4-1 | 10 m/s | 2 and 3 episodes in 436 s (the second count includes the other experiment's tail before the fix) | run records |
| Part 5, primary + decoy | px4-1, px4-2 | 10 m/s | px4-1 Medium for about 60 s (37-91 s); px4-2 Medium for about 20 s | live samples |
| Part 5, 3 aircraft | px4-1 | 10 m/s | Medium from 48 s | live samples |
| Part 5, 5 aircraft | all five | 10 m/s | Low throughout the 45 s observed | live samples |

Two to three episodes in about seven minutes is roughly **17-25 per hour**, against the budget of 1 per hour and the 0.26 per hour measured on the synthetic test set. The documented live baseline (docs `ML_PIPELINE.md`: 1,133 assessments, all Low) was flown at **8 m/s**; every flight above was at **10 m/s**. These are single flights of one machine, so they show that the problem exists, not its exact rate.

## 2. Loophole table

| # | Loophole | Evidence | Why it matters | Fix by data or features (not by a threshold) | Status |
|---|---|---|---|---|---|
| 1 | Speed range: the training flights use 3-10 m/s (`threat_data.py`: `uniform(3.0, 10.0)`); only 8 m/s was validated live | code; live baseline at 8 m/s only; today's false Mediums were at the 10 m/s edge | 12 and 16 m/s are outside the training range; the user reported false Medium at 16 m/s (not re-measured here) | generate training flights at 4, 8, 12 and 16 m/s and test each speed separately | not fixed |
| 2 | Flight phases are missing from training: only constant-speed legs along waypoint/lawnmower/square/perimeter paths. No take-off, landing, hold, return-to-launch, spiral or sharp turns | code (`_pattern`, `_along`) | the user reported false Medium in spiral turns and during RTL after Abort at 8 m/s; today's Medium episodes cluster after take-off/at turns | add those phases to the simulator, label them normal, retrain | not fixed |
| 3 | The window assumes it starts at the beginning of the flight (early-flight scale) | `metrics.json` limits; after Hold/Resume the live monitor restarts its window ("collecting data" for about 10 s) | a restarted window changes the scale the z-features use, so a pause can create or hide an alert | features that do not depend on window start (rolling reference), train with window restarts | not fixed |
| 4 | The inertial reference is PX4's GPS-aided estimator velocity, not an independent IMU solution | documented in `ML_PIPELINE.md`; `VELOCITY_REFERENCE_NOTE` | against real spoofing the EKF would follow the false GPS and the residual would shrink; slow drift is hardest | simulate an EKF that follows the spoofed GPS in training data; report detection for that case separately | not fixed |
| 5 | Monitor-level injection: the live runner (Part 3) corrupts only the monitor's copy of the GPS stream; PX4's estimator velocity stays truthful | `live_experiments.py` design | live detection of an injected fault is therefore easier than detection of a real spoof, so live latencies overstate performance | validate with spoofing injected into the simulator/estimator (Part 9 study) | known limit, documented |
| 6 | Synthetic-to-live gap in false alarms (0.26/h vs about 17-25/h live) | section 1 | the false-alarm claim only holds on synthetic data | train and validate on recorded live baseline flights as well (the run records and `recorded_dataset.py` already collect them) | not fixed |
| 7 | Alert hold: the strongest evidence of the last 30 s stays visible, so an alert outlasts the attack by up to 30 s and the run record counts the tail beyond the hold as false alarms (one tail of about 40 s was seen) | `HOLD_S = 30`; run record | inflates both latency tails and false-alarm counts | report episodes with and without the hold; do not change the hold | measured |
| 8 | Ground-truth columns sit next to the features in the training table (`battery_pct`, `role`, `remaining_fraction`, `position_error_m`) | `features.csv` | `position_error_m` is simulator truth: it must never reach the model (the guard excludes it from `feature_columns`, which list 41 columns) | move truth columns out of the feature file into the hidden labels file; keep the guard test | guard in place; layout not changed |
| 9 | Accelerometer clipping | `research_routes.py` lines 314-315: clipping is reported as a **data-quality flag** only, not scored | correct as is; the risk is a future change that feeds it to the model as evidence | keep it a flag; add a test that no clipping feature enters `feature_columns` | verified in code; test not added |
| 10 | Only single-aircraft live validation; peer features use other aircraft's residuals | `compute_live` | with several aircraft the peer comparison is untested against baselines | validate baseline false alarms with 2, 3 and 5 aircraft flying (today: one decoy Medium episode seen) | partly measured |
| 11 | Hard cases (brief or small attacks) flagged on only about 20 % of their samples; slow drift detected in 63 % of flights | `ML_PIPELINE.md` | the missed-attack side of the same problem | out of scope until the false alarms are fixed | documented |

## 3. Plan for steps 2 to 4 (not done)

1. **Data first.** Extend `threat_data.py` with the missing phases and speeds (items 1-3) and a "baseline recorded in SITL" set (item 6), keeping truth columns out of the feature file (item 8). Command (as in the pipeline doc): `cd backend && ..\.venv\Scripts\python.exe -m app.research.threat_models` after the simulator change; the large profile takes a long time and about 400 MB, so it needs its own planned run.
2. **Validate honestly.** Evaluate on an untouched test split plus fresh live baseline flights at 4, 8, 12 and 16 m/s; report false alarms per hour **with a confidence bound** (rule of three for zero events), before and after, per speed and per phase.
3. **Do not tune thresholds.** The tiers (12.24 / 34.2 / 59.29) are re-derived by the existing method from the new validation scores, not edited by hand.
4. **Documents to update after retraining:** `backend/research_artifacts/model_card.md`, `metrics.json`, `backend/docs/ML_PIPELINE.md`; rerun the Part 0 export (`backend/tools/export_dataset.py`).

Until then the detector is unchanged and its live false-alarm rate should be treated as unknown but clearly above the synthetic estimate.


---

# Job 2 results (2026-11-18): causes, live data, retraining, honest before/after

Software-only. Everything below is PX4 SITL, 5 aircraft flying at once in one Gazebo world, clean flights unless stated. Raw recordings are in `D:\TRINETRA_scratch` (not in Git): `live_flights\` (clean), `attack_flights\` (attacks), scoring scripts `eval_live.py`, `score_attacks.py`, `analyse_live.py`.

## 1. Cause of the live false alarms (evidence per explanation)

Data: the unchanged detector scored 1,944 recorded live assessments of 55 clean flights (1.51 aircraft-hours, 4-16 m/s, survey, spiral and patrol paths): 85 alert episodes = **56 per aircraft-hour**.

| Explanation | Test | Verdict |
|---|---|---|
| Flight phase never seen in training (acceleration and braking at path corners) | share of rows alerting by phase: cruise 6.4 %, turn/accelerating 10.7 %, climb 2.9 %, landing 2.7 %; median acceleration of alerting rows 2.62 m/s2 vs 0.23 quiet; the estimator-velocity residual (res_vel) 0.36 vs 0.07 m/s | **holds, the main cause**: the synthetic flights fly at constant speed along the path, so the model never saw the residual that a real aircraft produces while it accelerates |
| Speed outside training (3-10 m/s) | alert share by ground speed: <1 m/s 0.4 %, 1-5 6.4 %, 5-9 17 %, 9-11 6.9 %, 11-13 4.7 %, 13+ 18.9 % (few rows) | **partly**: the 5-9 m/s band is accelerating segments; 12 and 16 m/s flights were also outside training |
| Timing jitter from a loaded host | GPS rate 9.5 Hz and velocity rate 23.7 Hz in both alerting and quiet rows; no window restarted after a gap (0 of 1,944 rows) | **does not hold** at 5 aircraft flying with no browser open; an idle single-aircraft comparison was not flown |
| GPS-aided reference | the reference is PX4's own estimator velocity in every flight; it cannot explain why quiet segments are quiet | not the cause of the false alarms (it matters for detection against real spoofing) |
| GPS delay constant (0.04 s) | not varied | not tested |
| Accelerometer clipping | clipping is only a data-quality flag and is not a model input | not a cause |
| Synthetic flights do not look like simulator flights | the maneuver feature `accel_m_s2` separates alerting rows from quiet rows by a factor of 11 | **holds** (same as the first row) |

## 2. Data collected

11 batches x 5 aircraft = **55 clean flights, 1.51 aircraft-hours** (about 1,944 assessments, about one every 3 s on this busy machine): speeds 4, 8, 10, 12 and 16 m/s on survey (4, 8, 10, 12, 16), spiral (8, 10, 12) and perimeter patrol (4, 8, 12) paths. Whole batches go to one split, never rows: train 25 flights (0.686 h), validation 15 (0.398 h), **held-out test 15 (0.422 h: survey 10 m/s, patrol 8 m/s, spiral 12 m/s)**. A first collection attempt was discarded because the recorder lost every flight after the first of each aircraft (a bug: time going backwards was not recognised as a new flight); it was fixed and everything was flown again.

## 3. What was changed (data and features, not thresholds)

* Live clean flights added to training, weight 4 per row (rows resampled to the 0.5 s training grid by zero-order hold, at most 6 s old; no interpolation). A trial with weight 12 changed nothing on validation, so 4 was kept (validation only).
* Two causal maneuver features from the GPS velocity: turn rate (deg/s over 2 s) and acceleration (m/s2 over 2 s). Model inputs 35 -> 37.
* Synthetic speeds widened from 3-10 to 3-17 m/s.
* Tiers re-derived on validation only (Medium 12.7, High 37.03, Critical 62.14; were 12.24 / 34.2 / 59.29). Ground truth stays out of the features; live clean rows carry no simulator truth.
* Clipping and timing gaps remain a separate data-quality flag; no persistence rule was added to the live tier (the evaluation already uses 3 consecutive samples).

## 4. Before / after

**Held-out live clean flights** (15 flights, 0.422 aircraft-hours, never used to train or choose anything; scored once):

| | alert episodes | per aircraft-hour | 95 % upper bound |
|---|---|---|---|
| before (model 049e3cff124a) | 23 | **54.4** | - |
| after (model d9d7d3e7b267) | 0 | **0.0** | **7.1** per hour (0 events in 0.422 h: about 3 / 0.422) |

Per path (after): survey 0 in 0.177 h, patrol 0 in 0.131 h, spiral 0 in 0.115 h; per phase (after): accelerating 0 in 0.260 h (bound 11.5), cruise 0 in 0.239 h (bound 12.5), landing 0 in 0.091 h, climb 0 in 0.027 h. Before, the alerts sat in accelerating segments (19 of 23). **This does not show that the budget of 1 per hour is met**: demonstrating it needs about 3 aircraft-hours with no alert (7 times more than collected). On the validation flights (used to choose) the new model still had 4 episodes in 0.398 h (10 per hour), all in the 8 m/s spiral batch.

**Synthetic test set** (250 flights, scored once, includes the 15 live test flights as clean flights):

| | precision | recall | ROC-AUC | false alarms/h | flights detected | p90 latency |
|---|---|---|---|---|---|---|
| before | 0.995 | 0.657 | 0.942 | 0.26 [0.00, 0.70] | 0.848 [0.80, 0.90] | 29.6 s |
| after | 0.999 | 0.594 | 0.933 | 0.00 [0.00, 0.00] | 0.748 [0.69, 0.81] | 7.0 s |

The new detector is **less sensitive**: recall -0.06, flights detected -0.10, jump recall 0.85 -> 0.67, mid-magnitude 0.775 -> 0.654, drift 0.247 -> 0.21. That is the price for the live false alarms disappearing; I did not revert because the live false alarms were the problem being fixed, but this is a real regression in synthetic sensitivity and is reported as such.

**Live attacks** (Part 3 runner, monitor-level injection; 28 attack runs, 3 runs per type per speed (2 where the route was too short for a 40 s attack), both models scored on the identical recorded flights):

| | detected | median latency | highest tier |
|---|---|---|---|
| jump 150 m | 6 of 6 both | 0.2-0.6 s both | Critical both |
| offset 100 m | 5 of 5 both | 1.7-1.95 s both | Critical both |
| noise 15 m | 6 of 6 both | 1.3-1.7 s both | Critical both |
| drift 1 m/s | 6 of 6 both | **4.5-5.6 s before, 10.8-11.2 s after** | Critical both |
| velocity inconsistency 5 m/s | 5 of 5 both | 1.0-1.1 s both | **High before, Medium after** |

With 5-6 runs per type the 95 % Wilson interval of a 100 % rate is only about 0.44-1.0 (3 runs per cell). Large magnitudes were used, so this shows the monitor reacts, not how small an attack it can find.

## 5. What this does not prove

* The injection is at the monitor: PX4's estimator velocity stays truthful, so live detection is easier than against real spoofing (loophole 4-5). Real GPS spoofing feeding the EKF is the Part 9 study.
* 0.42 aircraft-hours is too little to claim the false-alarm budget; the 7.1/h bound is the honest statement.
* The test batches are three speed/path combinations; spiral at 8 m/s (validation) still alerts, so spirals are probably still under-represented; 16 m/s was only in training.
* All live flights are 5 aircraft on one loaded machine at 30 m altitude in the same area; other conditions are untested.
* The attack-side numbers use the new model after it was chosen; the attack flights were not used for training or choosing.

## 6. Loophole table status after Job 2

1 speed range: widened to 3-17 m/s in the synthetic data and live flights at 4-16 m/s added (**fixed in data**). 2 phases: live accelerating/climb/landing now in training (**partly**: no live hold or RTL flights were collected; spirals under-represented). 3 window start: not changed. 4 GPS-aided reference: not changed (study in `FUTURE_REALISM.md`). 5 monitor-level injection: unchanged, documented. 6 synthetic-to-live gap: **narrowed** (54.4 -> 0 per hour on held-out live) but not proven below budget. 7 hold tail: unchanged. 8 truth columns: live rows carry none. 9 clipping: unchanged (flag only). 10 multi-aircraft baselines: the live data are 5-aircraft flights (**partly**). 11 hard cases: slightly worse (0.195 -> 0.179).
