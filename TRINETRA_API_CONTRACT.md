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
- AI provider behavior is mock-tested only; no live OpenAI request was made. `backend/.env` is prepared with an empty key and verified `gpt-4.1-mini` model. Provider rate-limit failures are surfaced as HTTP 429, insufficient project credits/billing as HTTP 402, and rejected credentials as sanitized HTTP 503; raw provider bodies are never returned. Browser map rendering and populated-database operation remain unverified.


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

## Managed SITL fleet and Mission Control deployment handoff

| Route | Access | Contract |
|---|---|---|
| `GET /api/fleet/status` | Authenticated mission read | Reports only fixed-instance PX4 process, matching telemetry, and matching `x500_N` pose checks. |
| `POST /api/fleet/activate` | Scenario-create permission plus CSRF | Accepts `required_uav_count` from 1–5, invokes the fixed allowlisted WSL fleet helper, and returns success only after every requested process, PX4 position, and Gazebo model is verified. |
| `POST /api/fleet/stop-managed` | Scenario-create permission plus CSRF | Stops only exact PX4 processes registered as manager-owned; it sends no vehicle flight commands. |
| `POST /api/scenarios/{scenario_id}/versions/{version_id}/deploy` | Scenario-create permission plus CSRF | Persists a deployment only for the latest immutable, complete mission version and only when selected PX4 processes, telemetry, and Gazebo poses are live. Operators need approval of that exact version; General accounts do not. |
| `GET /api/mission-control/deployments/latest` | Authenticated mission read | Returns the caller-scoped deployment record and full versioned mission snapshot. |

Deployment hands the validated mission to the existing PX4 Mission Control page. It does not upload a PX4 mission, arm, take off, or execute a route. Scenario completeness (sections A-C: mission, individual UAV configuration, review) is checked server-side, and General accounts cannot submit directly for review. The latest runtime was idle at the 2026-11-11 implementation checkpoint; previous live SITL observations in this document are historical and do not prove current fleet activation or movement.

`geographic_mission.mission_duration_minutes` is optional and accepts 1-1440 minutes. It is stored in the immutable Scenario Lab intent/version hash. Scenario Lab's current contextual assistant applies only allowlisted deterministic form commands and invokes fleet changes through `POST /api/fleet/activate`; general generative chat is not connected yet.

`GET /api/health` also reports `components.default_sitl_startup`. Automatic startup is asynchronous, uses the allowlisted WSL manager only when both configured UDP receivers are available, and reports READY only after PX4 process, telemetry position and Gazebo model verification.


## Mission plan, schema version 2 (route planning) - implemented, unit/API tested, not browser/flight tested

- **Schema versioning.** `ScenarioIntent.schema_version` is `1` (default; legacy `geographic_mission`) or `2` (`mission_plan`). Version 2 forbids `geographic_mission`; version 1 forbids `mission_plan`. Stored legacy versions are never rewritten: reads add `schema_version: 1` and `mission_plan: null` defaults only, and the UI shows them read-only. Intents are stored as JSON, so **no database migration is required or pending**.
- **`mission_plan`** (`backend/app/mission_planning/schemas.py`): `objectives` (incl. `relief_supply_delivery`), optional `description`/`notes`, structured `operating_area` (circle `centre`+`radius_m`, or polygon `vertices`), `required_uav_count`, optional `max_duration_minutes`, enforced `constraints.max_distance_from_start_m` and `max_altitude_m`, `allow_multiple_primary`, and per aircraft: `role` (primary/decoy/standby), `priority` 1-3, `reassignable`, `task_type`, per-type `parameters`, `start_location`, `altitude_m`, `speed_mps`, `loiter_duration_s`, `return_mode`, the generated `route` and `confirmed`. Removed fields: individual objective, data requirements, completion condition, expected result, return completion condition, per-aircraft free-text constraints and the role/task duplication.
- **Task types:** `expanding_square_search`, `sector_search`, `coverage_survey`, `perimeter_patrol`, `point_inspection`, `waypoint_route`, `loiter_hold`, `payload_dropoff` (simulated relief supply drop-off; `payload_category` is the fixed list `relief_supplies|medical_kit|flotation_device`). Objectives only suggest defaults; Target Tracking and Custom Mission allow `waypoint_route` only (no tracking capability exists); `payload_dropoff` needs the Relief Supply Delivery, Search and Rescue or Disaster Assessment objective.
- `GET /api/mission-plans/limits` - planning limits (config `TRINETRA_MP_*`) and the task/payload lists.
- `POST /api/mission-plans/preview-route` (`scenario.create` + CSRF), body `{plan, uav_id}` - generates and validates one aircraft's route; 200 `{route, summary}` or 422 `{detail: {code: "route_invalid", message, issues: [{code, message, uav_id, field}]}}`. Nothing is saved or sent anywhere.
- **Authoritative validation** (`mission_planning/validation.py`) runs on scenario create/update (drafts verify any stored route; `faults`/`review` stages require a complete plan), on submit-for-review and on deploy: missing fields/parameters, speed and altitude limits, mission altitude and distance limits for every generated waypoint, start-to-area distance (SITL sanity limit), waypoint cap, `max_duration_minutes`, points inside the operating area, drop point inside the area and within the distance limit, drop altitude and hover ranges, exactly one primary (unless enabled), sequential confirmation, and the stored route must equal the deterministic regeneration (`route_mismatch`). Failures return 422 `{code: "mission_plan_invalid", message, issues[]}`.
- **Generation.** Pure functions in `mission_planning/generators.py` work in local ENU around the aircraft start (`geometry.py` reuses `research.sensor_fusion.geodetic_to_enu`; the inverse is exact ECEF). Flat-earth limit: the tangent plane drops about d^2/2R (0.08 m at 1 km, 7.8 m at 10 km); intended for areas of a few kilometres. Routes have `takeoff`, pattern, optional loiter, then return/land waypoints, with estimated `length_m` and `duration_s` (3-D distance / speed plus hovers). `payload_dropoff` adds a `payload_released` event `{aircraft_id, waypoint_seq, planned_time_s, payload_category, simulated: true}`: a logged software event only; no actuator command exists and none is sent to PX4 or Gazebo.
- **Deploy** still only hands off to Mission Control; the stored `intent.mission_plan` (routes, roles, priorities, events) is carried in the deployment payload and shown in the Mission Control deployment panel. It is not uploaded to PX4.
- **Sections (2026-11-14).** Scenario Lab has three sections: A Mission, B Individual UAV configuration, C Review and validation. The earlier Section C (fault/attack panel) was removed; GPS attack experiments are designed in the Attack Lab after saving.

## Attack Lab and threat assessment (added 2026-11-13; implemented and tested, simulated/software-only)

All routes need a session; mutating routes need CSRF and an allowed origin. Experiments are owned by their creator.

- `GET /api/attack-lab/limits` - ranges, attack types, presets (backend is the authority; the UI mirrors them).
- `GET|POST /api/attack-lab/experiments`, `PUT|DELETE /api/attack-lab/experiments/{id}` - experiments pinned to an exact schema-2 scenario version; `POST /api/attack-lab/validate` returns structured issues; `POST /api/attack-lab/presets/{id}/apply` builds a preset from a plan. Validation failures are 422 `{detail:{code,message,issues[]}}`. Experiments never execute (`executed: false`).
- `GET /api/research/model-info` - model id, tiers, formula, `live_residual_inputs_available`.
- `POST /api/research/assess` - sensor-stream windows only; ground-truth keys are rejected with 422. Returns `anomaly_confidence`, `severity`, `mission_impact`, `threat_score` (0-100, `threat_score_is_probability: false`), `tier`, `evidence`, ranked `actions`, `handover_candidates`.
- `POST|GET /api/research/decisions` - records an operator decision (`executed: false`); nothing is sent to an aircraft.
- `POST /api/research/replays`, `POST .../{id}/finish`, `GET .../{id}/evaluation` - offline replay; the attack schedule is withheld until finish.
- Scenario Lab no longer edits fault profiles; `uav_fault_assignments` is still accepted and saved as `[]` by the UI. Not applied to the real DB: migration `20261113_attack_lab_decisions`.

## Deploy flow, simulation home and Attack Lab changes (2026-11-14; implemented, unit/API tested, live-checked on PX4 SITL through the API; not browser-verified)

**Route-order fix.** `GET /api/scenarios/my-approval-requests` is now declared before `GET /api/scenarios/{scenario_id}`. Before, it was captured as a scenario id and always returned 404 `Scenario not found.`; the Scenario Lab refresh after a save failed on it, so the saved scenario and its deploy button never appeared. A regression test checks that no static path is shadowed by an earlier parameterised route.

**Deploy** (`POST /api/scenarios/{scenario_id}/versions/{version_id}/deploy`; rules unchanged, errors now structured):

| Status | `detail.code` | Meaning |
|---|---|---|
| 409 | `version_superseded` | Not the latest immutable version. Carries `latest_version_id` and `latest_version_number`. |
| 409 | `approval_required` | An Operator deployed a version that is not approved. |
| 503 | `fleet_not_verified` | A selected PX4 process, telemetry source or Gazebo pose is not verified. Carries `unverified`, `process_missing`, `manager_error`. |
| 422 | `mission_plan_invalid` | Unchanged. |

Deploying a version that is already deployed keeps the same record id and makes it the current deployment again. The deploy response and `GET /api/mission-control/deployments/latest` add, computed on read: `scenario_version_number`, `latest_version_number`, `schema_version`, `legacy` (true for a schema-1 deployment, which has no routes), `superseded` (a newer version of the scenario exists) and `mission_start`.

**Mission start** (`backend/app/mission_planning/start.py`, the single definition): the take-off point of the primary aircraft, which is its `start_location` and the first generated waypoint. The "max distance from start" validation measures each aircraft from its own take-off point through the same helper. The home altitude is a fixed 488 m AMSL (the stock PX4 world elevation) because the plan stores no terrain elevation.

| Endpoint | Access | Behaviour |
|---|---|---|
| `GET /api/fleet/simulation-location?scenario_id=&scenario_version_id=` | Authenticated mission read | Read-only. Returns `mission_start`, per-aircraft position and `distance_to_start_m` / `distance_to_area_m`, `relocation_recommended` (more than 1 km from the mission start), `far_from_mission_area` (more than 50 km from the operating area), a plain `message` and the `thresholds`. 404 `version_not_found`, 422 `version_not_schema_2`. |
| `POST /api/fleet/relocate` | Scenario-create permission plus CSRF | Body `{scenario_id, scenario_version_id, confirm: true}`. Stops the managed PX4 instances and this checkout's Gazebo, restarts them with the mission start as home, waits up to 90 s for telemetry plus a Gazebo pose and for every aircraft to report a position within 50 m of the mission start. Sends no arm, take-off or mission command. 200 `{relocated, mission_start, checks, max_distance_to_start_m, commands_sent_to_aircraft: false, ...}`. |
| `POST /api/fleet/activate` | unchanged | Optional `scenario_id` + `scenario_version_id`: instances this call has to start use that version's mission start as home. A running instance is never restarted; a different home is reported as `checks.simulation_home.status = "differs"`. |
| `GET /api/fleet/status` | unchanged | Adds `simulation_home`, `simulation_home_source` (`px4_home_env`, `px4_default_world`, `unknown`) and per instance `home` and `managed`. |

Relocate failures are structured (`detail.code`, `message`, `checks`, `simulation_stopped`, `recovery`): 422 `confirmation_required`, 422 `mission_start_unavailable`, 503 `receivers_unavailable`, 409 `aircraft_armed` (nothing changed), 409 `instance_not_managed` (the instance was not started by the fleet manager; nothing stopped), 502 `stop_failed`, 502 `home_not_applied`, 504 `verification_timeout`. Nothing in the application calls relocate on its own; the UI calls it only after an in-page confirmation.

How the home is applied: the WSL manager sets `PX4_HOME_LAT`, `PX4_HOME_LON` and `PX4_HOME_ALT` (range-checked numbers, fixed decimal format) for an instance it starts. PX4's own `px4-rc.gzsim` then calls Gazebo's `/world/default/set_spherical_coordinates`. The PX4 source tree and the world file are not edited. Manager CLI: `activate --count N [--home-lat= --home-lon= --home-alt=]`, `relocate --count N (--home-* | --default-home)`.

Live check (2026-11-14, PX4 SITL `px4_sitl_default`, Gazebo `default` world, throwaway backend and database): position before 47.397971, 8.546164; after relocate to a plan starting at 12.971600, 77.594600 the reported position was 12.971600, 77.594600 (0.03 m away), Gazebo pose receiving, fleet verified 1, relocate request 17 s. `relocate --default-home` returned the position to 47.397971, 8.546164.

**Attack Lab.** Presets define the attack only; nothing scripts a response or a role change.

- Preset ids: `baseline`, `slow_drift`, `rapid_jump`, `strong_offset`, `noise_burst`, and `paired_drift` (attacks two aircraft, `min_aircraft: 2`). Earlier ids are still accepted and returned under the new ids: `mild_drift` -> `slow_drift`, `rapid_spoof` -> `rapid_jump`, `role_handover` -> `strong_offset`, `isolation_fallback` -> `slow_drift`. Stored rows are not rewritten.
- `config.response_policy` is always `operator_decides`; any other stored or submitted value is read as that. `benchmark_flagged` and `limits.response_policies` were removed. `config.handover.min_battery_pct` (default 40) stays as an eligibility setting for the operator's later handover choice.
- `GET /api/attack-lab/limits` adds per preset `does`, `useful`, `min_aircraft`; `field_help` (start, duration, magnitude, direction, seed); `notes.hidden_schedule`, `notes.software_only`; `response`; `handover.label`, `handover.note`.
- `GET /api/attack-lab/missions` (authenticated mission read): saved schema-2 scenarios and their versions with `latest`, `superseded`, `deployed`, `deployed_at`, `in_mission_control`, `aircraft` (id, role, reassignable, route duration), `estimated_duration_s`, `area_summary`, and `presets` (`presets` offered, `hidden` with the `reason`). It carries no experiment or attack data.
- A preset whose `min_aircraft` exceeds the plan's aircraft count is refused by apply and by validation with issue `preset_needs_two_aircraft`. The issues `preset_needs_standby` and `benchmark_needs_preset` no longer exist.

No database schema changed, so no migration was written for this work.

## Aircraft endurance limit (2026-11-14; implemented, unit/API tested)

Why a 255 km, 224 min coverage route was not flagged: `max_duration_minutes` is optional and the only duration check (`backend/app/mission_planning/validation.py`, the `duration_over_limit` rule) ran only when it was set; no other limit on flight time or distance existed, and the 200-waypoint cap passed at 173 waypoints.

Now one shared definition applies to every route check (route preview, save, review, submit, deploy), with or without `max_duration_minutes`:

- Config `TRINETRA_MP_MAX_FLIGHT_MINUTES` (default 20) and `TRINETRA_MP_MAX_FLIGHT_DISTANCE_M` (default 15000), read into `PlanningLimits.max_flight_s` / `max_flight_distance_m`. They describe the simulated X500 for planning; they are not a measured battery model.
- A route over the time limit gets issue `endurance_time_exceeded` ("Route takes about 224 min; the aircraft limit is 20 min. Reduce the area, widen track spacing, or raise speed."); over the distance limit `endurance_distance_exceeded` ("Route is about 255.0 km long; the aircraft limit is 15 km. ..."). `duration_over_limit` remains for a `max_duration_minutes` that is stricter than the aircraft limit.
- `POST /api/mission-plans/preview-route` returns 422 `route_invalid` with these issues, and on success `summary.limit_duration_s` and `summary.limit_length_m`. `GET /api/mission-plans/limits` now includes `max_flight_s` and `max_flight_distance_m`.
- The frontend mirror (`enduranceIssues` in `missionPlan.ts`) uses the limits served by the backend and the same wording; the backend is authoritative.
