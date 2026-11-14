# Research methods and current limitations

## Simulation

Each run initializes a Python `random.Random` generator with the configured seed and advances a fixed one-second simulation step. The initial fleet positions and task locations are deterministic for that seed. Ground-truth position is updated by the software movement model. Reported GPS is separate and can receive software-only offsets, one-tick jumps, freeze, higher/degrading noise, or velocity inconsistency; faults do not alter the ground-truth trajectory. Each aircraft has independent sensor RNG streams. The abstract coordinate plane spans 0 to 10 simulation units; these values are not geographic coordinates.

Tasks are owned by one UAV at a time. A pending task is completed once when the assigned UAV reaches its location. Each aircraft has a task-assignment capacity derived from fleet size and task count. On isolation, pending tasks are considered in descending priority order and assigned to the nearest active or degraded UAV with an available task slot. Unserviceable tasks are explicitly left unassigned.

## Detection baseline

The detector uses a dead-reckoned trajectory prediction. It starts from the first reported position after a route change, predicts movement toward the assigned destination using reported-coordinate history, assigned destination, configured speed, and fixed timestep, and calculates Euclidean prediction residual. It receives neither ground-truth coordinates nor the spoof scenario label.

The residual threshold is estimated from 512 synthetic clean samples generated with a fixed calibration seed and independent Gaussian coordinate noise (sigma 0.0125 units per axis). The configured threshold is the empirical 99.5th percentile of the resulting 2D residual magnitudes. This is a synthetic calibration distribution, not training against recorded clean flights. The model assumes a known destination and a simple movement law. A route change resets the dead-reckoning baseline.

Scenario configuration may override the calibrated threshold with any positive abstract-coordinate value. The selected threshold is included in persisted snapshots and experiment metadata.

## Threat and response

Two consecutive anomalous steps produce one elevated detection event and a degraded status. Under the adaptive policy, a third consecutive anomalous step isolates that UAV and triggers task reassignment. The baseline does not isolate; monitor mode reports the same detection but does not isolate. Four clear steps can return a degraded (not isolated) UAV to active status. Current isolation is one-way during the run.

## Experiment metrics

Baseline and adaptive comparisons instantiate separate simulations with identical configurations and seeds, including independent per-aircraft sensor-noise streams so isolation does not shift another aircraft's noise sequence. Completion rate is completed tasks divided by configured tasks. Continuity score currently equals completion rate and continuity loss is its complement. Completion time is reported only when all tasks complete; mean task completion tick averages the recorded completion tick of completed tasks. Detection delay is the difference in simulation ticks between the configured spoof start and first persistent anomaly event. The current precision, recall, and F1 are run-level event-presence estimates: one detection event counts as a positive run when spoofing is configured, while an event in a clean run counts as a false positive. The current false-positive rate is likewise a run-level indicator, not a per-timestep rate. Unnecessary isolation rate is the fraction of isolation events involving a non-target UAV, or any isolation in a clean run. These metrics do not summarize a distribution over seeds and do not provide confidence intervals.

The output is a demonstration of this specific seeded model, not evidence that adaptive response generally improves mission outcomes. A future evaluation should label per-timestep attack windows, compute confusion matrices on independent simulation seeds, report detection delay distributions, and include sensitivity analysis of detector calibration and response thresholds.

## Recorded sensor data and offline GPS/IMU research

`backend/app/research/recorded_dataset.py` provides a CSV import boundary for externally recorded data. The required normalized inputs are separate GPS and IMU streams with a `session_id` and monotonic source `timestamp_s`; GPS uses latitude/longitude degrees and altitude metres, while IMU uses acceleration in m/s² and angular rate in rad/s. Source name, source URL and license are mandatory. The loader hashes source files, rejects duplicate/out-of-order/non-finite rows and invalid coordinates, and joins IMU to GPS epochs using interpolation only when both bracketing IMU samples are within the configured limit. It does not extrapolate. Optional labels remain a separate list and must point to an existing GPS epoch. `recorded_features.py` converts each session to a local ENU origin and derives GPS velocity, acceleration, and IMU vector magnitudes; labels are emitted as a separate target list and are never read in feature generation. `split_sessions()` assigns whole sessions to disjoint groups and requires at least three sessions. A single session cannot support a leakage-safe held-out model evaluation.

The importer is a generic normalized-data interface; no public external dataset is bundled or automatically downloaded. Candidate source records reviewed for this implementation:

| Dataset | Source and license | Available observations | Limits for this project |
| --- | --- | --- | --- |
| A DATASET for GPS Spoofing Detection on Unmanned Aerial System, v3 | [Mendeley Data record](https://data.mendeley.com/datasets/z7dj3yyzt8/3), CC BY 4.0 | Eight-channel GPS receiver data from a vehicle emulating UAS, three static rooftop positions, 13 GPS features, authentic signals and three simulated attack types | Not actual UAV flight; record does not document synchronized IMU, sampling rate or a paired inertial stream, so it cannot alone validate fusion. |
| MUN-FRL aerial visual-inertial-LiDAR dataset | [Official dataset documentation](https://mun-frl-vil-dataset.readthedocs.io/en/latest/), CC BY 4.0 | DJI M600 aerial sequences; IMU at 400 Hz and RTK-GNSS position at 5 Hz are hardware time-synchronized; PPK position ground truth at 5 Hz | No spoof labels; ROS bags are large (smallest documented sequence is about 27 GB). It was not downloaded or imported in this work. |

Consequently, the current trained/evaluated research results remain generated from the explicitly synthetic ENU simulator dataset described above. The recorded importer has deterministic fixture tests but has not yet been exercised against either external dataset. Record each real dataset's original license, source and checksums in the importer manifest before training.

### Evidence-based GPS/IMU consistency detector

`backend/app/research/spoofing_detector.py` exposes `SensorObservation` and `GpsImuConsistencyDetector`. Inputs are timestamped local ENU GPS position/optional velocity and world ENU acceleration, with optional GPS course, IMU heading, reported accuracy, satellite count and fix quality. It reports position discontinuity, reported-velocity versus position-derived velocity, GPS-velocity acceleration versus IMU acceleration, and course versus heading residuals. Poor accuracy, low satellite count, invalid fix, missing GPS and long sample gaps remain alternative degradation explanations. The result includes timestamped measurement context, structured value/threshold/unit evidence, persistence count, detector version and limitations. Confidence is deliberately null and the score is an evidence count, not a probability or threat percentage. Status `LIKELY_GNSS_SPOOFING` is a conservative research label requiring persistent independent inconsistencies; this detector is not calibrated or connected to live control.

If a future operator workflow uses a calibrated detector, false positives can trigger unnecessary vehicle degradation/isolation and task reassignment, while false negatives can leave corrupted navigation observations trusted. Any such action must remain a separate reviewed policy decision; this offline detector currently has no control effect.

### Coordinate and asynchronous fusion interfaces

`geodetic_to_enu()` converts WGS84 latitude/longitude/altitude to local tangent ENU metres around an explicit origin, using an ECEF delta and local rotation. Existing `SimulationSensorFusion` remains synthetic-data-specific. `DelayedGpsReplayFusion` is a separate ENU navigation EKF wrapper: callers provide monotonic IMU source epochs with world-frame acceleration and GPS source epochs (matching an IMU epoch) with their later arrival time. GPS delayed within its configured lag is inserted and the bounded time history is replayed deterministically; too-old, duplicate, future, or unaligned fixes are rejected with a reason. The replay history has a maximum-sample guard and requires starting a new segment when full. It does not itself transform body acceleration, estimate attitude, synchronize arbitrary PX4 clocks, or replace the PX4 telemetry adapter. Its tests establish software behavior only, not flight estimator accuracy.

For future read-only live integration, first map PX4 message source epochs to one monotonic clock and convert GPS to ENU using a recorded origin; transform body acceleration with a validated attitude estimate and remove gravity consistently. Pass immutable normalized samples to these research interfaces. Do not treat receive time as measurement time, mix PPK ground truth into observable features, or expose this uncalibrated output as production threat status.

## Reproducible abstract dataset (Phase 8 partial)

Run from `D:\TRINETRA\backend`:

```powershell
..\.venv\Scripts\python.exe -m app.research.cli --seeds 20261101,20261102,20261103,20261104,20261105,20261106,20261107,20261108,20261109,20261110,20261111,20261112,20261113,20261114,20261115,20261116,20261117,20261118,20261119,20261120,20261121,20261122,20261123,20261124,20261125,20261126,20261127,20261128,20261129,20261130 --output-root research-data\datasets --duration 90 --uav-count 3 --task-count 40
```

The command generates a new content-addressed directory with `samples.csv` and `manifest.json`, and refuses to overwrite an existing dataset directory. Earlier v2 data artifacts remain available as historical versions. Current simulator v2 dataset `84a505efbbc9c4a6e710cdd632fcac6044432527e389ba6f51383476693495b1` has 72,900 rows over 30 seeds and 18/6/6 train/validation/test seed groups. Simulator v2 includes synthetic resource accounting, so these samples supersede the simulator-v1 dataset for current experiments. It covers clean, offset, drift, intermittent, jump, freeze, noise, degradation, and velocity-inconsistency profiles. Fault labels describe the actual software effect active on each UAV/tick. Six features use reported-GPS derivatives, trajectory residual/score, and reported-velocity disagreement; truth coordinates and labels are never features. This is synthetic abstract-simulator research data, not PX4/field data. V1 dataset validation remains supported.

## Abstract ML model training and optional runtime inference (partial Phase 9)

Train/evaluate with a validated generated dataset, from `D:\TRINETRA\backend`:

```powershell
..\.venv\Scripts\python.exe -m app.research.train --dataset-dir research-data\datasets\84a505efbbc9c4a6e710cdd632fcac6044432527e389ba6f51383476693495b1 --output-root research-data\models
```

The command fits a scikit-learn `DecisionTreeClassifier` on train seeds only, chooses a threshold using validation seeds, and scores held-out test seeds. Current model v2 ID `f2956c53b02514a77aebe38f4d202e87d37bbcccf79b86acc8811fb1d7cea471` links to the simulator-v2 30-seed dataset above. Test split (six seeds) has 14,580 samples / 1,386 active labels. Aggregate metrics: accuracy 0.9770, precision 0.8567, recall 0.9098, F1 0.8824, ROC AUC 0.9837; confusion matrix (true rows 0/1, predicted columns 0/1) `[[12983,211],[125,1261]]`; validation-selected threshold 0.7219304. Per-seed F1: 20261103 .8989, 20261118 .8742, 20261122 .8723, 20261123 .8079, 20261125 .9072, 20261129 .9412. Mean seed F1 .8836; deterministic 20,000-resample percentile bootstrap over these six seed values gives 95% interval [.8503,.9133]. This small seed-level interval does not represent flight variability.

Held-out profile F1: degradation .8571, freeze .8618, gradual .8864, intermittent .7658, jump .7059, noise .9145, sudden .8605, velocity inconsistency .9976. Each profile has 1,620 test rows; jump has only six positive rows and its result is highly uncertain. The `none` profile has no positives. These scores remain abstract-only; no field or timestep-level classifier claim is supported. The hash-checked JSON tree avoids executable pickle loading. `predict()` returns probability, explicitly uncalibrated confidence, class, threat severity 0?100, and band. Optional `TRINETRA_ML_MODEL_PATH` integrates model inference into backend/API and Mission Control; unset retains the heuristic. Older v1 dataset/model artifacts remain loadable.

## Boundaries

All GPS anomalies exist only in software. The project has no radio, RF, SDR, operational navigation, real GPS coordinate, or physical aircraft interface. The 3D view is an abstract locally generated terrain-like coordinate scene and does not consume geographic terrain imagery.
