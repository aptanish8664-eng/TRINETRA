# TRINETRA AI and ML Audit

**Status:** source-inspected; research workflow is abstract-simulator-only; live provider and independent real telemetry validation are unverified.

The initial observations below include historical abstract-GPS artifacts. A separate synthetic sensor-fusion research run and its held-out analysis are recorded in the 2026-11-09 supplemental section.

## AI mission-planning provider

The AI module isolates an optional OpenAI Responses provider behind a protocol. The API uses strict structured schemas, validates proposals against operator-selected simulator configuration, redacts common direct identifier/secret patterns, rejects geographic-coordinate text, limits input/output/time/usage, persists owner-scoped conversations, and records usage/audit outcomes. Proposal output is advisory; it does not change simulator settings, approve a scenario, or execute a run. General review and explicit user transfer to Scenario Lab remain separate.

The API provider is unavailable when no backend key is configured; manual planning remains available. The project docs state that live OpenAI calls were not performed. Any AI-generated task notes and scenario intent are descriptive/advisory, not validated navigation or flight instructions. Deterministic contradiction checks cover a limited set of explicit statements; broader natural-language semantics remain model-mediated.

## Dataset and classifier

The dataset pipeline creates content-addressed, versioned samples from deterministic abstract runs, separates train/validation/test by seed, checks schema/label consistency and hashes, and excludes truth coordinates/attack labels from features. The optional scikit-learn decision-tree pipeline trains on train seeds, selects a threshold on validation data, tests on held-out seeds, serializes a validated JSON tree, and checks artifact integrity. Runtime model loading is optional; heuristic detection remains the default.

Project research documentation reports 72,900 rows over 30 abstract seeds, nine profiles, and an 18/6/6 seed split. It reports aggregate test F1 around 0.882 and explicitly notes the six-positive-sample jump profile, small seed count, uncalibrated confidence, and absence of field/PX4 validation. These are repository-reported historical artifact metrics, not recomputed in this audit and not operational performance claims.

The UI and experiment outputs disclose important limitations: precision/recall/F1 are run-level event-presence indicators rather than per-timestep metrics; there are no confidence intervals in a single run; model confidence is uncalibrated. A high abstract test score cannot establish robustness to real sensor noise, dynamics, environments, adversarial behavior, or distribution shift.

## Findings and recommendations

- Dataset/model tooling is implemented but not independently reproduced during this audit. The current backend research tests were run in a selected batch and the result is in the test report.
- Live OpenAI provider acceptance is blocked by unconfigured/unavailable credentials and was not attempted.
- Add broader seed groups, per-profile uncertainty, calibration, robustness/sensitivity tests, and an independent held-out simulator configuration before increasing research claims.
- Keep any future PX4 data evaluation separately sourced, permissioned, and explicitly SITL. Do not mix synthetic labels or hidden truth into operational inference inputs.
- Ensure optional AI keys remain backend-only; no key was inspected or written during this audit.

## Supplemental synthetic sensor research (2026-11-09)

A separate pipeline under `backend/app/research/` now implements a quaternion complementary attitude filter, a 3D local-ENU position/velocity/accelerometer-bias EKF, deterministic faulted sensor generation, grouped train/validation/test evaluation, and offline explainable assessments. It remains separate from the abstract runtime DecisionTree and from PX4/live threat and response code.

The generated dataset contains 26,130 rows from ten seeds and 13 scripted profiles (normal, GPS jump/drift/outage, accelerometer and gyro bias, IMU noise/spikes/missing/delayed samples, GPS/IMU disagreement, combined faults, and recovery). Seed groups are disjoint (6 train / 2 validation / 2 test); truth and labels are excluded from model features. Dataset ID: `f982e0fd018d6eb5dc67b4c509ac49ab9c5c3ba9a59a932debf9d0e1bfa84c88`.

Held-out Random Forest metrics were precision 0.905, recall 0.639, F1 0.749, false-positive rate 0.048, and ROC-AUC 0.899; threshold baseline F1 was 0.504, Isolation Forest F1 0.291, and Gradient Boosting F1 0.717. Threshold selection used validation seeds with a 5% false-positive-rate cap. EKF held-out 3D position RMSE was 2.4627 m versus raw GPS 3.1503 m; attitude yaw RMSE was 0.0652 rad. The report documents much weaker per-profile results for drift, outage, jumps, and recovery, among other limitations. The generated critical analysis, confusion metrics, per-profile results, correlation outputs, and time-series plot are under the ignored local `backend/research-data/sensor-fusion/integrated-v1/` directory.

These are deterministic same-generator software results, not independent environmental generalization or flight evidence. Synthetic measurements are synchronized by construction; live PX4 stream timestamps use different clock fields and do not yet have a validated common frame. Delayed GPS is rejected rather than fixed-lag-replayed. Confidence/severity mapping is uncalibrated. No model or estimator output is loaded by the runtime API or used for vehicle commands.

The four new research test modules contributed **19 focused passing tests**; the latest complete backend suite passed **140 tests**, and the frontend passed **31 tests**. See [TRINETRA_TEST_COVERAGE_AUDIT.md](./TRINETRA_TEST_COVERAGE_AUDIT.md) for the latest commands and exact scope.
