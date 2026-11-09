# TRINETRA Simulation Specification

Status: current implementation is an abstract deterministic software simulator. PX4/Gazebo is the target and has not been installed or exercised in this checkout.

## Current simulator

Implementation: `backend/app/simulation/engine.py`, `detector.py`, and `response.py`. Current simulator identity: `deterministic-abstract-2d-v2`, which adds deterministic abstract resource accounting and constrained reassignment. Resource values are synthetic model inputs, not physical vehicle measurements.

- `SimulationConfig` defaults to 4 UAVs, 10 tasks, seed 21, 180 ticks, one-second `dt`, sudden GPS offset on `UAV-02` beginning at tick 15 for 35 ticks with magnitude 3 abstract units, and adaptive response. Validation allows 2–12 UAVs, 1–40 tasks, 1–3600 ticks; software fault profiles are none/sudden/gradual/intermittent/jump/freeze/noise/degradation/velocity_inconsistency; response is adaptive/monitor/baseline.
- Seeded initial UAV and task positions lie on an abstract 0–10 plane. The software movement model moves directly toward each assigned pending task at a fixed nominal speed. Task ownership and per-UAV capacity are explicit.
- Ground-truth-like `position` and noisy/attacked reported `gps` are separate fields in the in-memory state. Per-UAV random streams keep one UAV's isolation from changing another's sensor noise sequence.
- Fault effects exist only in generated software telemetry, and the simulated truth trajectory is not modified by them. Sudden offsets persist for the configured window; gradual offsets ramp; intermittent offsets alternate five-tick periods; jump is a one-tick position jump; freeze holds the first attacked GPS fix; noise raises GPS noise sigma to 0.12; degradation ramps sigma from 0.01 to 0.25; velocity_inconsistency leaves GPS position intact and adds an offset to reported GPS velocity. Fault labels describe the actual applied window, including intermittent/jump timing. These are abstract software models, not receiver-level PX4 faults. No real geographic coordinates, RF, receiver, or hardware interfaces exist.
- Each UAV has separate software `velocity` and derived/attacked `gps_velocity` fields. The latter is derived from successive reported positions except during velocity_inconsistency. The new model feature compares reported GPS velocity with the velocity implied by successive reported GPS positions; ground truth and hidden labels remain excluded.
- Detector initializes/reinitializes its prediction from reported GPS around route changes. It dead-reckons toward the known destination at configured speed and computes Euclidean position residual. It does not consume `position` or attack labels.
- The default threshold is generated from 512 fixed-seed synthetic 2D Gaussian noise samples (sigma 0.0125 per axis), at the empirical 99.5th percentile. This is calibration of a heuristic, not ML training.
- Two consecutive anomaly ticks degrade and raise one event. Adaptive mode isolates at three consecutive anomalies and attempts to reassign pending tasks in descending task priority to the nearest eligible vehicle. Eligibility checks status/readiness, communications, required capability, payload, workload capacity, and estimated trip battery/endurance reserve. Rejection reasons are included when no vehicle can accept a task. Four clear ticks can clear degraded state; isolation is one-way in this run. An abstract vehicle that reaches its configured endurance reserve stops accepting work, and its pending tasks are reassigned or explicitly unassigned.
- `backend/app/main.py` manages in-memory runs, 250 ms async runner interval, persistence snapshots/events, owner-scoped REST APIs, and authenticated WebSocket state.

## Native PX4-to-Mission-Control acceptance (pending actual SITL telemetry)

The final geographic visualization must use CesiumJS inside the existing React Mission Control page. PX4/MAVSDK telemetry must flow through FastAPI and its existing authenticated WebSocket architecture. The same page must show multiple actual simulated vehicles, position/orientation/altitude/speed and trails, per-UAV selection and details, fleet health/battery/communications, mission progress, detector alerts/severity, and reassignment/coordination events. Do not build a separate dashboard or use synthetic/random telemetry after live simulator connection. This integration is gated on a verified single-X500 flight and validated telemetry contract; three/five-vehicle acceptance follows in dependency order.

## Paired experiment provenance and repeatability

`POST /api/experiments/compare` stores metadata/results JSON with schema version 3. New records include the effective deterministic Python simulator version, detector version/mode, optional model ID, Python/platform, full effective config, policy-specific SHA-256 config hashes, seed, full attack configuration, UTC experiment and per-trial start/end times, trial IDs, and baseline/adaptive outcomes. Model ID is included in the hashes and metadata when the opt-in model is configured. Unavailable provenance is explicit: scenario/version, PX4/Gazebo, dataset version, and source revision are `null` because this endpoint currently runs an abstract config directly and this checkout has no usable Git revision. Legacy experiment records without schema-version keys remain readable. Repeated results are regression-tested for exact equality under the same implementation/runtime/configuration/seed after excluding per-execution IDs and wall-clock timestamps; cross-Python/OS tolerances and flight simulator reproducibility are not characterized. Detector precision/recall/F1 fields remain run-level event-presence indicators, not timestep classifier metrics.

## Abstract dataset generation (partial)

`backend/app/research/dataset.py` generates tick-level rows from the deterministic software simulator for clean, sudden-offset, gradual-drift, intermittent-offset, jump, freeze, noise, degradation, and velocity-inconsistency scenarios. The v2 feature schema contains reported GPS step/speed/turn features, trajectory residual/score, and reported-velocity disagreement derived without truth. Labels come from the simulator's actual active software fault schedule. Splits group all scenarios for a seed into exactly one of train, validation, or test; the manifest records simulator/detector/generator versions, parameters, split membership, label counts, runtime, schema, and a content SHA-256. The export is explicitly abstract simulation data, not PX4, field, or sensor hardware data. The loader continues to validate v1 datasets.

`backend/app/research/model.py` trains a scikit-learn decision tree on train seeds, chooses a binary threshold on validation seeds, and reports held-out test metrics. It exports a hash-validated JSON artifact; inference returns attack probability, explicitly uncalibrated class-probability confidence, binary classification, 0–100 severity, and the specified LOW/MODERATE/HIGH/CRITICAL band. The v2 model uses the v2 feature schema; v1 models remain loadable. Set `TRINETRA_ML_MODEL_PATH` to a validated project-local JSON artifact to opt into runtime/API/UI inference; otherwise the heuristic remains active. Model evaluation is abstract-only and not validated against PX4 data.

## Scenario Lab mission intent

Scenario versions may also store advisory mission intent: objectives, proposed task descriptions/references/priorities, UAV role suggestions, area/environment notes, and constraints. It is versioned separately in `scenario_version_intent` and is included in the version integrity hash. The simulator consumes only `SimulationConfig`; mission intent does not set task placement, UAV roles, or vehicle behavior. Area notes are narrative only. Geographic coordinates and routes are rejected until a validated geographic frame and origin exist.

## Limitations of current research output

- No physical dynamics, wind, inertia, battery chemistry/power draw, altitude, collision, waypoint navigation, or PX4 estimator. Battery/endurance are abstract budget variables only; they do not represent physical battery performance.
- No raw IMU/GPS measurement object, sensor age/timestamps, frame/unit metadata, uncertainty, quaternion, bias/noise model beyond the toy GPS perturbation, or independent estimate.
- Threat score is normalized residual; no trained classifier. Existing precision/recall/F1 are run-level event-presence indicators, not time-sample classification, field evaluation, or multi-seed confidence intervals.
- The resource fields and checks are deterministic abstract variables. They do not model battery chemistry, true power draw, payload aerodynamics, communications links, return-to-home, flight dynamics, or operational safety guarantees. Fault injection and telemetry remain software-only.
- Current visuals show abstract backend state. `ThreeEnvironment.tsx` is illustrative and must not be represented as Gazebo or Cesium output.

## Target PX4/Gazebo requirements

The chosen first target is Ubuntu 24.04.5 under WSL2, PX4 v1.17.0 stable, Gazebo Harmonic, and the official X500 model. Ubuntu/PX4 setup is blocked pending the official dependency installer and has not been attempted. Setup/verification commands and their sources are in `docs/PX4_SETUP.md`.

The future telemetry contract must identify each vehicle and carry timestamp, source, sequence, frame, units, validity, ground truth, raw GPS+IMU, PX4 estimate, and independent TRINETRA estimate in distinct structures. WSL networking, UDP ports, stale/disconnected behavior, bounded queues, reconnection, and safe shutdown remain to be designed and tested against actual SITL before Cesium integration.
