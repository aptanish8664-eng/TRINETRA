# TRINETRA API Contract (Inspected Routes)

This is a route inventory of the current FastAPI application, not an OpenAPI dump. Protected writes generally require the authenticated session cookie and `X-CSRF-Token`; backend permissions and ownership remain authoritative. Exact request models are in `backend/app/schemas.py` and `backend/app/ai/schemas.py`. Every HTTP response receives a server-generated `X-Request-ID`; the backend emits a JSON request log with that ID, method, path (without query), status and elapsed milliseconds. Request bodies, headers and query values are excluded from that log.

## Public/session and General administration

| Method and path | Purpose / access |
|---|---|
| `GET /api/health` | Public; executes a read-only `SELECT 1` probe and returns legacy readiness fields plus in-process API/database, abstract simulator, WebSocket, detector/model, AI configuration, PX4 receiver, PX4 telemetry freshness, and optional Gazebo receiver/model counts. Database failure returns sanitized `503/degraded`. PX4/Gazebo component state reflects data received by this backend process; it does not verify external process identity, completed flight, or independent vehicle movement. |
| `POST /api/auth/login` | Public login; creates HttpOnly server session and returns CSRF token. |
| `POST /api/auth/register` | Public pending registration, rate-limited and input-validated. |
| `GET /api/auth/registration/{application_id}` | Registration status lookup. |
| `GET /api/auth/session` | Current authenticated session/permissions/CSRF token. |
| `POST /api/auth/logout` | Authenticated + CSRF; revokes session. |
| `GET /api/general/users` | General-only account list. |
| `POST /api/general/users/{user_id}/decision` | General approval/rejection/hold/resume. |
| `PUT /api/general/users/{user_id}/role` | General role update with last-General safeguards. |
| `POST /api/general/users/{user_id}/{action}` | General account activation/deactivation action. |
| `GET /api/general/audit`, `GET /api/general/audit.csv` | General-only filtered audit listing/export. |

## Simulation, commands, replay, experiments

| Method and path | Purpose / access |
|---|---|
| `POST /api/simulations` | Authenticated mission create permission + CSRF; creates ready deterministic software run. |
| `GET /api/simulations/{run_id}` | Authenticated mission read + owner/General scope. |
| `POST /api/simulations/{run_id}/{action}` | Authenticated + CSRF lifecycle action (`start`, `pause`, `resume`, `step`, `stop`, `reset`). |
| `POST /api/simulations/{run_id}/alerts/{uav_id}/acknowledge` | Authenticated + CSRF alert acknowledgement. |
| `POST /api/simulations/{run_id}/uavs/{uav_id}/isolate` | Restricted operator command workflow/General execution policy. |
| `POST /api/simulations/{run_id}/tasks/{task_id}/reassign` | Restricted task reassignment workflow. |
| `POST /api/command-decisions` | Create pending restricted command request. |
| `GET /api/command-decisions` | Permission-filtered review queue. |
| `POST /api/command-decisions/{decision_id}/decision` | Independent General decision; validates and executes an approved command. |
| `WS /api/simulations/{run_id}/stream` | Authenticated cookie; checks run ownership and configured origin before accepting; sends authoritative state through a per-client bounded queue (4 states). On queue overflow it drops the oldest replaceable snapshot; a send blocked longer than 5 seconds is closed with code 1013. |
| `GET /api/history`, `GET /api/replay/{run_id}` | Authenticated own/all-scoped history and snapshots/events. |
| `POST /api/experiments/compare` | Authenticated deterministic baseline/adaptive paired experiment. Schema-v3 metadata records engine/detector versions and mode, optional model ID, Python/platform, effective config and per-policy SHA-256 hashes, seed/attack settings, UTC start/end times, trial IDs, and explicit `null` for unavailable Scenario/PX4/Gazebo/dataset/source revision. Detection indicators remain run-level event-presence measures. |
| `GET /api/experiments/{experiment_id}.csv`, `GET /api/experiments/{experiment_id}`, `GET /api/analytics/{run_id}.csv` | Authenticated owner/General scoped exports/results. |

## Scenario and AI

| Method and path | Purpose / access |
|---|---|
| `GET/POST /api/scenarios` | List owned scenarios / create immutable version 1 with optional validated advisory intent. Geographic mission coordinates, zones, and PX4 assignments are stored in the separate `geographic_mission` intent object and included in the version hash. |
| `GET/PUT/DELETE /api/scenarios/{scenario_id}` | Read/update by creating a new immutable version; clients omitting intent preserve the current intent. Geographic assignments are checked against currently connected PX4 telemetry. Geographic drafts cannot use the abstract sandbox or execution endpoints. Delete eligible draft. |
| `POST /api/scenarios/{scenario_id}/versions/{version_id}/submit` | Submit exact version for review. |
| `GET /api/scenarios/my-approval-requests` | Own review requests. |
| `GET /api/general/scenario-approvals`, `POST /api/general/scenario-approvals/{request_id}/decision` | General review queue/decision. |
| `POST /api/scenarios/{scenario_id}/versions/{version_id}/sandbox` | Run draft sandbox without approval. |
| `POST /api/scenarios/{scenario_id}/execute` | Execute backend-verified approved version; starts ready, not running. |
| `GET /api/ai/status`, `POST /api/ai/proposals` | Authenticated provider/quota status and structured advisory proposal. |
| `POST /api/ai/geographic-mission-plan` | Authenticated + CSRF; returns an advisory structured plan using the configured provider, current connected PX4 IDs, objective, and zone names. Exact map coordinates are omitted from the AI request; suggestions are checked against known IDs/zones and are never saved or executed by this endpoint. |
| `POST /api/ai/conversations` | Authenticated + CSRF; starts a persisted, bounded multi-turn planning conversation with an operator-selected simulator config. |
| `POST /api/ai/conversations/{conversation_id}/turn`, `GET /api/ai/conversations/{conversation_id}` | Owner-scoped turn submission and resume; conversations expire after seven days, allow at most eight user turns and configured input limits. |
| `POST /api/ai/conversations/{conversation_id}/scenario` | Owner + scenario-create permission + CSRF; transfers a completed, validated simulator config plus advisory objectives/tasks/roles/constraints into an idempotent Scenario Lab draft. |
| `GET /api/ai/proposals/pending`, `GET /api/ai/proposals/{proposal_id}` | General review queue or authorized proposal read. |
| `POST /api/ai/proposals/{proposal_id}/approve`, `.../reject` | Independent General decision; approval creates a ready run only. |
| `POST /api/ai/assessments/{run_id}` | Authenticated, CSRF-protected, run-scoped telemetry-bounded AI explanation. |

## Simulation response state

Simulation state returns abstract simulator coordinates and, when the optional model is configured, backend-owned detector output. UAVs also expose generated abstract readiness, workload, communications, capability, payload, and battery/endurance budget fields; tasks expose abstract required capability/payload. Adaptive and manual task reassignment enforce workload, capability, payload, communication, and estimated reserve eligibility. These are software model fields, not real telemetry or configurable vehicle capabilities.

## Known contract gaps

- Paired experiment records use metadata/results schema version 3 with explicit simulator/runtime/config/seed/attack/time provenance and policy-specific configuration hashes. Detector mode and model ID are included in hashes and metadata when optional `TRINETRA_ML_MODEL_PATH` is configured; without it, the heuristic remains the default and model provenance is `null`. Scenario/PX4/Gazebo/dataset/source-revision provenance is `null` when unavailable; experiments are not yet linked to a persisted Scenario Lab version. Exact repeatability is covered only for the deterministic engine under the same Python runtime and config.

- The API has no PX4 process lifecycle or MAVSDK control. The optional read-only MAVLink receiver includes raw GPS and HIGHRES_IMU fields only when those messages are published; no independent navigation estimate, validated PX4 threat score, or telemetry persistence is implemented. It has no standalone dataset/training/inference routes; optional model inference is reflected in authoritative abstract simulation state and paired experiment records.
- `GET /api/health` probes the database and reports in-process component/configuration and receiver state. `websocket.status=available` means the application route is present; `active_clients` is an in-process aggregate. `ai_provider.status=configured` only means a server-side key is present, not that the provider is reachable. PX4/Gazebo status and per-vehicle freshness describe received data, not process supervision, flight, or movement acceptance.
- PostgreSQL is supported through `DATABASE_URL` and Alembic migrations; Supabase credentials/connectivity and cloud operations are not configured or validated. Schema upgrades are an operator-run CLI step, not an API route.
- Current simulation request schema remains abstract (uav count, task count, seed, duration, spoofing type/target/window/magnitude, response policy, optional threshold). Geographic mission coordinates use WGS84 latitude/longitude degrees and remain separate from local simulation coordinates. No geographic-to-PX4/Gazebo frame transform, altitude model, route feasibility, or flight execution is implemented.
- `scenario_version_intent.geographic_mission` is advisory geographic mission data bound into the immutable version hash. PX4 IDs are validated against currently connected telemetry with valid positions. General approval does not start a flight, and geographic drafts are explicitly rejected by abstract sandbox/execution routes.
- AI provider behavior is mock-tested only. Live provider use remains blocked until a server-side `OPENAI_API_KEY` is configured; browser map interaction and populated-database operation were not verified in this milestone.


## PX4 and Gazebo telemetry endpoints (implemented; browser acceptance partial)

| Route | Access | Source / contract |
|---|---|---|
| `GET /api/telemetry/px4` | Existing mission-read permission; session required | Validated actual MAVLink vehicle states from the source-filtered UDP receiver. |
| `WS /api/telemetry/px4/stream` | Existing authenticated WebSocket policy | Actual MAVLink state, per-system freshness and transport status. Optional `trail_mode=delta-v1&since=<generated_at>` sends only new trail points after the supplied snapshot cursor. |
| `GET /api/telemetry/gazebo` | Existing mission-read permission; session required | Validated X500 poses decoded from Gazebo `/world/<world>/pose/info` and relayed read-only from WSL. |
| `WS /api/telemetry/gazebo/stream` | Existing authenticated WebSocket policy | Actual Gazebo poses and per-model freshness. Optional `trail_mode=delta-v1&since=<generated_at>` sends only new pose-trail points after the supplied snapshot cursor. |

The `delta-v1` frame carries the same current vehicle/model fields, omits the repeated full `flight_trail` array, and adds `flight_trail_delta`. If `since` is omitted or invalid, the first frame contains a full trail. Clients without `trail_mode=delta-v1` keep the previous full-snapshot behavior. The React Mission Control client supplies the REST snapshot's `generated_at`, merges only backend-reported trail points, and reconnects from its latest cursor. REST endpoints continue returning full bounded trails.

Both receivers are disabled by default and must be enabled with the documented server-side environment variables. Their UI is inside the existing Mission Control page. PX4 states feed the Cesium map; Gazebo world XYZ/quaternion poses feed a separate X500 mesh pose mirror. Do not combine the coordinate frames or describe the mirror as a native Gazebo render. Three disarmed SITL systems and three Gazebo models were observed in live smoke checks; a combined authenticated live backend has now been verified, while the browser login and vehicle movement acceptance remain pending. PX4 threat scoring and task reassignment are not validated or linked to these routes. No mock/random source may represent live vehicles.

`MISSION_CURRENT.total_sequence` is returned as `null` when MAVLink reports `0` (count unsupported) or `UINT16_MAX` (no mission loaded); neither sentinel is a waypoint count. `mission_state=1` means no mission is present. Mission sequence remains read-only autopilot state and is not linked to TRINETRA task assignment.

Three disarmed systems (IDs 1/2/3) and Gazebo models (`x500_0/1/2`) have been received together through the authenticated backend. An authenticated Mission Control browser view previously showed both live data panels and the three corresponding rows, but its Cesium renderer reported an image decode error. Cesium asset URLs have since been corrected and direct dev/build asset responses verified; a fresh post-fix visual render and flight movement are still unverified. PX4 threat scoring and task reassignment are not validated or linked to these routes. No mock/random source may represent live vehicles.
