# TRINETRA Simulation and Telemetry Audit

**Status:** source-inspected; earlier live smoke evidence exists in project documentation, but no WSL distribution is currently running. Current health reports PX4/Gazebo not integrated; current flight and combined live browser acceptance are not established.

## Data-path separation

The default simulation is deterministic, fixed-step, and abstract 2D. Abstract positions are not latitude/longitude. The simulator maintains truth-like positions separately from reported GPS; software faults alter reported values without changing the truth trajectory. The research detector consumes reported trajectory, route/destination, speed, and timestep rather than truth coordinates or attack labels.

PX4 is a separate, read-only MAVLink telemetry path. The decoder accepts supported MAVLink types, distinguishes PX4 system identities, and reports only received message fields with source message and receive timestamps. It includes position/GPS, attitude, velocity, raw IMU, battery, heartbeat/armed state, and mission-current state where reported. Missing values remain missing. The receiver can be source-IP/port filtered and exposes freshness states. It does not issue PX4 commands.

The `HIGHRES_IMU.fields_updated` mask is honored per accelerometer, gyro, magnetometer, and temperature field; fields not updated by that message are reported as `null`. The raw IMU/GPS/attitude message families still have different source timestamp fields, and the adapter does not synchronize them, define a validated cross-sensor frame transform, estimate IMU bias, or provide an independent TRINETRA navigation estimate. `ATTITUDE` is PX4-provided state, not independent TRINETRA orientation. No sensor-fusion, detector, or flight-validation claim follows from raw decoding.

Gazebo is a separate read-only pose path. A bounded JSON decoder validates world/model names, numeric limits, quaternion norms, and model count. The UI displays local Gazebo XYZ/quaternion poses using X500 mesh assets. It does not stream native Gazebo world geometry, lighting, physics visualization, or video.

## Faults, detector, and response

The abstract simulator exposes clean and software-only GPS profiles: sudden/sustained offset, gradual drift, intermittent offset, one-tick jump, freeze, increased noise, degradation, and velocity inconsistency. The baseline heuristic is a trajectory-residual detector with a synthetic clean-noise threshold (512 samples, empirical 99.5th percentile); it is not trained on real flights. Consecutive anomaly persistence drives abstract degraded/isolation behavior; adaptive policy can reassign tasks using simulated readiness, communication, capability, payload, workload, and endurance constraints. These policies are not validated against live PX4.

The optional decision-tree inference pipeline operates on abstract dataset features and is not a PX4 threat estimator. PX4 telemetry explicitly reports threat analysis as not validated. There is no independent GPS/IMU navigation estimate integrated into the live PX4 path.

## Evidence and blockers

- Telemetry adapter and visualization source are present, including authenticated REST/WebSocket delivery and independent coordinate-frame labeling.
- Existing project records document prior observation of three disarmed PX4 SITL identities and three Gazebo models via authenticated service tests. The evidence says no completed flight or moving-vehicle acceptance; treat these as historical observations, not current process state.
- During the initial non-mutating browser inspection, no active telemetry WebSocket was available. Controlled abstract simulation WebSocket acceptance was performed separately against a temporary database; no PX4/Gazebo action was taken.
- D-01 callback stability was corrected using stable parent callbacks and refs in the Cesium/Gazebo viewers. Component regression tests exercise repeated snapshots and stable callback identity; live WebGL construction/disposal and mesh-load behavior remain unverified.
- Phases requiring sensor consistency, one validated SITL flight sequence, then three- and five-vehicle adaptive evaluation remain incomplete or blocked. “Flight” here must remain within PX4 SITL/Gazebo; no physical vehicle/control workflow is in scope.

## Supplemental sensor research and current runtime (2026-11-09)

The synthetic sensor research path is now implemented and tested independently of the MAVLink path. It provides quaternion complementary attitude estimation, a 3D local-ENU accelerometer-bias EKF with covariance/innovation gating and outage reacquisition, deterministic sensor-fault generation, and held-out model/estimator analysis. Dataset ID `f982e0fd018d6eb5dc67b4c509ac49ab9c5c3ba9a59a932debf9d0e1bfa84c88`; details and limitations are in [TRINETRA_AI_ML_AUDIT.md](./TRINETRA_AI_ML_AUDIT.md) and its generated critical analysis. The estimator and model outputs are not connected to PX4, the authenticated runtime threat API, or adaptive response.

The live browser at `http://127.0.0.1:5173/login` rendered and showed the backend health status. `GET /api/health` returned ready with SQLite healthy and an abstract simulator; PX4 and Gazebo reported `not_integrated`. WSL reported no running distributions. No PX4/Gazebo process was started, stopped, or modified. The health payload did not disclose which SQLite path the backend uses; do not infer that the running service selected the canonical database.

## Safe next evidence sequence

1. Without restarting unknown services, identify and document the active simulator/backend configuration through an owner-approved read-only check.
2. Fix and test viewer lifecycle stability before judging the live view.
3. Validate one X500 telemetry sequence in SITL/Gazebo with independently sourced truth and documented sensor frames, preserving existing PX4 configuration.
4. Only after single-system telemetry and sensors are validated, measure three-system stability, then five-system capacity and adaptive behavior, all inside the software simulator.
5. Do not label live PX4 packets as spoofed or apply abstract adaptive reassignments to PX4 until separate validation and authorization exist.
