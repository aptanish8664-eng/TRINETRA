# TRINETRA Simulation and Telemetry Audit

**Status:** source-inspected; historical live integration evidence exists in project documentation; current flight and combined live browser acceptance were not established.

## Data-path separation

The default simulation is deterministic, fixed-step, and abstract 2D. Abstract positions are not latitude/longitude. The simulator maintains truth-like positions separately from reported GPS; software faults alter reported values without changing the truth trajectory. The research detector consumes reported trajectory, route/destination, speed, and timestep rather than truth coordinates or attack labels.

PX4 is a separate, read-only MAVLink telemetry path. The decoder accepts supported MAVLink types, distinguishes PX4 system identities, and reports only received message fields with source message and receive timestamps. It includes position/GPS, attitude, velocity, raw IMU, battery, heartbeat/armed state, and mission-current state where reported. Missing values remain missing. The receiver can be source-IP/port filtered and exposes freshness states. It does not issue PX4 commands.

Gazebo is a separate read-only pose path. A bounded JSON decoder validates world/model names, numeric limits, quaternion norms, and model count. The UI displays local Gazebo XYZ/quaternion poses using X500 mesh assets. It does not stream native Gazebo world geometry, lighting, physics visualization, or video.

## Faults, detector, and response

The abstract simulator exposes clean and software-only GPS profiles: sudden/sustained offset, gradual drift, intermittent offset, one-tick jump, freeze, increased noise, degradation, and velocity inconsistency. The baseline heuristic is a trajectory-residual detector with a synthetic clean-noise threshold (512 samples, empirical 99.5th percentile); it is not trained on real flights. Consecutive anomaly persistence drives abstract degraded/isolation behavior; adaptive policy can reassign tasks using simulated readiness, communication, capability, payload, workload, and endurance constraints. These policies are not validated against live PX4.

The optional decision-tree inference pipeline operates on abstract dataset features and is not a PX4 threat estimator. PX4 telemetry explicitly reports threat analysis as not validated. There is no independent GPS/IMU navigation estimate integrated into the live PX4 path.

## Evidence and blockers

- Telemetry adapter and visualization source are present, including authenticated REST/WebSocket delivery and independent coordinate-frame labeling.
- Existing project records document prior observation of three disarmed PX4 SITL identities and three Gazebo models via authenticated service tests. The evidence says no completed flight or moving-vehicle acceptance, and no current process check could be completed because Windows CIM access was denied by sandbox policy.
- In the integrated browser, no active WebSocket was available to verify. No account credentials or state-mutating requests were used.
- D-01 identifies a likely viewer teardown/reinitialization issue: streamed state updates rerender the parent, recreate selection callbacks, and retrigger initialization effects in Cesium and Gazebo components.
- Phases requiring sensor consistency, one validated SITL flight sequence, then three- and five-vehicle adaptive evaluation remain incomplete or blocked. “Flight” here must remain within PX4 SITL/Gazebo; no physical vehicle/control workflow is in scope.

## Safe next evidence sequence

1. Without restarting unknown services, identify and document the active simulator/backend configuration through an owner-approved read-only check.
2. Fix and test viewer lifecycle stability before judging the live view.
3. Validate one X500 telemetry sequence in SITL/Gazebo with independently sourced truth and documented sensor frames, preserving existing PX4 configuration.
4. Only after single-system telemetry and sensors are validated, measure three-system stability, then five-system capacity and adaptive behavior, all inside the software simulator.
5. Do not label live PX4 packets as spoofed or apply abstract adaptive reassignments to PX4 until separate validation and authorization exist.
