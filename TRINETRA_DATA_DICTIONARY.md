# TRINETRA Data Dictionary

Current code is authoritative for this dictionary. Target PX4/sensor fields are not implemented and are marked proposed.

## Persistent tables

All ORM records are declared in `backend/app/persistence/database.py`; SQLite is the local default and PostgreSQL is supported through `DATABASE_URL` with Alembic-managed schema versions. Supabase itself is not yet configured or validated.

| Table | Purpose / key data |
|---|---|
| `runs` | Run ID, creation time, config JSON, status. |
| `snapshots` | Integer row ID, run foreign key, tick, full state JSON. |
| `events` | Integer row ID, run foreign key, event key, tick, event JSON. |
| `experiments` | Experiment ID, creation time, metadata JSON, results JSON. New records identify metadata/results schema versions, simulator/detector versions, runtime/platform, canonical per-policy config hashes, seed/attack settings, timestamps, trial IDs, outcomes, and unavailable external provenance as null. Older records without schema version remain valid historical entries. |
| `run_owners`, `experiment_owners` | Resource-to-user scope; nullable owner supports retained historical records. |
| `users` | Full name, unique username/email, Argon2 hash, requested/effective role, account status, approval and login timestamps. Never export hashes. |
| `sessions` | User FK, unique token digest, CSRF token, expiry/revocation timestamps. Do not expose token/CSRF values in logs. |
| `audit_logs` | Actor snapshot/FK, event/outcome/reason, target, request ID, metadata JSON, time. |
| `scenarios` | Owner, title, created/updated time. |
| `scenario_versions` | Scenario FK, unique version number, immutable config JSON/hash, creator, optional sandbox run, time. |
| `scenario_version_intent` | Optional advisory mission objectives, proposed task notes/assignments, UAV role suggestions, non-geographic area/environment notes, and constraints, keyed by immutable scenario version. Included in integrity hash when present. |
| `scenario_approval_requests` | Exact version, submitter/reviewer, constrained status/reason/timestamps. |
| `command_decisions` | Requester/reviewer, run, command, payload JSON, decision/reason/time. |
| `mission_proposals` | Operator, structured proposal/config/validation JSON, approval state, expiry, run link. Original prompt is not stored. |
| `ai_usage` | User, endpoint/model, time, input chars, output/reserved token counts, success. No API key. |
| `ai_conversations` | Owner, abstract simulator config, bounded redacted message history, proposal, expiry, transfer scenario link. |

The read-only SQLite database audit on 2026-11-07 found 34 runs, 615 snapshots, 352 events, 10 experiments, 2 users, 18 sessions, 0 scenarios, 0 scenario versions, and 153 audit logs. Integrity check was `ok`; foreign-key check had no violations. That snapshot predates `ai_conversations` and `scenario_version_intent`; SQLite startup creates missing tables without rewriting existing rows. No account identity was inspected. These figures are a dated snapshot, not a promised current count. PostgreSQL startup instead requires its Alembic revision to match the checked-in migration head and does not create schema automatically.

`scenario_versions.config_json` stores the executable abstract simulator settings. Optional advisory mission intent is kept in `scenario_version_intent`, keyed by version so editing intent creates a new immutable version. Legacy versions without an intent row are returned with an empty intent and retain their original configuration-only integrity hash. For new versions with intent, the integrity hash covers both settings and intent. An old client update that omits `intent` preserves the latest stored intent.

## Current API/state fields

New experiment records use metadata/results schema version 3 and include deterministic simulator and detector identifiers/mode, optional model version, Python/platform, effective configuration, baseline/adaptive SHA-256 configuration hashes, seed, attack settings, experiment and per-trial UTC timestamps, unique trial IDs, and paired mission/detection outcomes. Model identity is included in the policy hashes when configured. Scenario/version, PX4/Gazebo, dataset, and source revision are explicit `null` until those sources are linked or available. Old JSON records lacking these version keys are historical and remain readable.

`SimulationRequest`: `uav_count`, `task_count`, `seed`, `duration`, `spoofing_type`, `target_uav`, `spoof_start`, `spoof_duration`, `spoof_magnitude`, `response_policy`, optional `detector_threshold`.

State: `status`, `tick`, `time`, `seed`, `coordinate_system`, `config`, `detector` (`mode`, optional `model_id`), `uavs`, `tasks`, `progress`, recent `events`, detector `calibration`.

Each current UAV record includes `id`, abstract `position` (software truth-like x/y), reported `gps` (x/y), `gps_velocity` (reported/derived x/y), `heading`, `speed`, `task_id`, threat `status`, `readiness`, workload and workload capacity, abstract `communications_available`, capability tags, payload capacity, battery percentage/reserve, endurance remaining/reserve ticks, `anomaly_score`, `threat_level`, alert state, model-local `velocity`, residual, and optional `ml_detection` (attack probability, uncalibrated confidence, classification, severity/level, model ID). These resource values are deterministic abstract simulation parameters, not measured aircraft telemetry. Internal anomaly/clear streak counters are omitted from public views. Task records include id, abstract position, priority, status, assigned UAV, required abstract capability, required payload mass, creation tick, and completion tick. Reassignment considers task priority, workload, capability, payload, communication availability, and estimated battery/endurance reserve; no-recipient outcomes stay explicitly unassigned with reasons.

All coordinate positions are abstract simulator units; `time` is simulation seconds, and `tick` is the fixed one-second step. Current frames/units do not identify a geographic datum or real flight coordinate system.

## Generated research artifact (partial Phase 8)

The generator `backend/app/research/dataset.py` writes versioned `samples.csv` and `manifest.json` under a new output directory. V2 rows contain seed, split, abstract scenario type, tick, UAV ID, software-fault-active label, and six reported-GPS/velocity/residual-derived features. It covers nine profiles including clean. The manifest records schema/generator/simulator/detector versions, runtime/platform, exact seed groups, scenario parameters, class counts, row count, and content hash. All scenarios for one seed remain in a single split. V1 manifests remain loadable. These are generated files, not database tables or stored telemetry; do not present them as flight data.

The trainer writes a separate JSON decision-tree artifact under `backend/research-data/models/`. Its metadata links the dataset ID, feature schema, train/validation/test seeds, threshold, and metrics. V1 and v2 model schemas load without pickle and are validated by content hash before inference. `TRINETRA_ML_MODEL_PATH` opts into the configured runtime model; unset remains heuristic. Model files are research artifacts, not ORM rows.

## Proposed sensor envelope (not implemented)

Every future telemetry envelope needs `uav_id`, UTC/source timestamp, receive timestamp, source, monotonically increasing sequence, coordinate frame, units, validity/freshness, and mission/run ID. Keep these sibling objects separate:

- `simulator_truth`: position, velocity, acceleration, quaternion attitude/angular rate, altitude, motor/battery/health where simulator exposes them.
- `raw_sensors`: GPS position/velocity/validity/age and IMU accel/gyro/timestamps/validity/noise metadata.
- `px4_estimate`: PX4 estimator state and uncertainty.
- `trinetra_estimate`: independent estimate, covariance/uncertainty, GPS residuals, and detector outputs.

Never add `simulator_truth` or hidden attack labels to model detector input. Never persist high-frequency raw samples as one PostgreSQL row per sample without a measured retention/storage design.
