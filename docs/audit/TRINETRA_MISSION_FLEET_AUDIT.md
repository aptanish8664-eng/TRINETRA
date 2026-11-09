# TRINETRA Mission and Fleet Audit

**Status:** abstract mission/fleet workflows are implemented; live PX4 mission control and adaptive fleet response are not validated.

## Abstract mission lifecycle

The simulation API creates a run for an authenticated owner, then supports read, start/pause/resume/step/stop/reset, alerts, isolation, and pending-task reassignment. Run ownership is stored separately and checked on reads/controls. Snapshots and events support history/replay. General has all-run reads; Operators are limited to owned runs. Stop/reset and high-impact response use a General approval path for Operator requests.

Paired baseline/adaptive experiments use the same seed/config and persist results/metadata. Metrics include task completion, continuity, completion time, detection delay, isolation, reassignment, and run-level precision/recall/F1. Current continuity is derived from task completion and current classification indicators are not timestep confusion metrics. Per-run results do not establish that the adaptive policy generally improves outcomes.

## Fleet and response semantics

The abstract simulator supports 2–12 UAVs and 1–40 task markers, with deterministic seeded placements. Reassignment orders pending tasks by priority and checks modeled readiness/status, communications, capabilities, payload capacity, workload, and endurance/battery reserves before selecting the nearest eligible abstract vehicle. Resource values are synthetic parameters, not real aircraft measurements or guarantees.

The PX4 page displays received telemetry and maps system IDs to X500 models for selection. It explicitly states PX4 task reassignment is not linked to live autopilot missions; no PX4 control commands are implemented in the inspected route. Gazebo mirror selection is visualization only.

## Scenario Lab and planner linkage

Scenario Lab persists editable executable simulator configuration as immutable versions and can attach advisory intent (objectives, task notes, role suggestions, non-geographic notes/constraints). General review binds to the version/hash; execution checks current approval and integrity. Sandbox runs and approved execution remain abstract simulation runs.

There is no validated geographic origin/frame, waypoint registry, actual geographic route execution, modeled weather, physical payload/performance model, or live PX4 mission linkage. Narrative area/environment notes do not drive simulator execution. This is an explicit product boundary, not a hidden capability.

## Acceptance evidence and gaps

- Source contains the abstract mission/fleet workflow and resource-aware reassignment.
- Frontend tests passed for app routing, command-decision UI, and other page modules; they use mocked API responses.
- No authenticated live browser mission was created during this audit. The canonical DB was kept read-only.
- Historical docs report three disarmed SITL systems and telemetry identities, but no complete flight, verified independent movement, or three/five vehicle resource acceptance.
- Adaptive response is not applied to actual PX4. Do not equate abstract UAV rows or a Gazebo mesh with live coordinated vehicles.

## Follow-up

Validate task assignment and lifecycle on isolated abstract runs; then establish a documented, safe SITL-only single-vehicle sensor/telemetry gate before any multi-vehicle adaptive study. Preserve the no-real-UAV boundary.
