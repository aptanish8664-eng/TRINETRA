# TRINETRA Complete Architecture (Current Audit)

Audit date: 2026-11-09. This describes the inspected `D:\TRINETRA` checkout, not the final target architecture. Implementation states are labeled explicitly; historical smoke checks are distinguished from current runtime evidence.

## System context

TRINETRA currently runs as a Windows-hosted React/Vite frontend and a Python FastAPI backend. The backend owns deterministic simulator state, authentication, scenario approval, AI proposals, persistence, and REST/WebSocket delivery. SQLite is the default; an optional PostgreSQL/Alembic path passed isolated local PostgreSQL tests. Optional abstract ML inference is connected through backend simulation/API and Mission Control; heuristic remains default. A read-only PX4 MAVLink receiver, Cesium Mission Control layer, and a separate read-only Gazebo pose receiver/mirror are implemented. A new sensor-fusion, dataset, and model-comparison pipeline is implemented only for a deterministic synthetic ENU research simulator; it is deliberately not connected to live PX4 telemetry or adaptive vehicle response. MAVSDK control, native Gazebo world/video rendering, synchronized PX4 sensor fusion/threat validation, and PX4-linked reassignment remain unimplemented.

```text
Browser (React + Vite)
  | REST + cookie session + CSRF on authenticated writes
  | WebSocket (authenticated cookie, origin allowlist, run ownership)
FastAPI (backend/app/main.py + auth/scenario/AI routers)
  | deterministic in-memory run state
  | SQLAlchemy persistence (SQLite default or DATABASE_URL PostgreSQL)
SQLite (backend/trinetra.db) / PostgreSQL (Alembic-managed)

AI proposal/assessment routes -- optional OpenAI Responses API (server-side key)

WSL PX4 MAVLink UDP relay -> source-filtered FastAPI decoder/store -> authenticated REST/WebSocket -> Mission Control Cesium map
WSL Gazebo pose topic subscriber -> source-filtered FastAPI pose store -> authenticated REST/WebSocket -> Mission Control X500 mesh pose mirror
```

Earlier smoke checks observed three disarmed X500 instances with separate MAVLink identities and valid GPS positions over authenticated REST/WebSocket; a separate pose-bridge smoke reported three Gazebo models. These historical checks used separate backends and did not verify combined authenticated browser operation, in-flight movement, or current process availability. At the latest check, the browser rendered the access portal and `GET /api/health` returned ready with SQLite healthy and one managed abstract simulation (none running); PX4 and Gazebo reported `not_integrated`. WSL had no running distributions. The backend health payload does not disclose its selected SQLite path, so this does not prove it uses the canonical file. No PX4/Gazebo process was started, stopped, or modified. The Gazebo panel renders live XYZ/quaternion updates with X500 mesh assets when connected; it does not stream Gazebo's world, lighting, camera/video, or physics overlays. PX4 threat detection and adaptive reassignment are not linked to actual PX4 telemetry. Keep these limits explicit and do not generate fake live telemetry. PostgreSQL URL support and local Alembic migrations were validated on an isolated local PostgreSQL cluster; no Supabase connection is configured. Phase 15 preparation includes Netlify SPA configuration, a separate FastAPI container definition, and deployment guidance, but neither image nor hosted deployment has been exercised.

## Frontend

- `frontend/src/main.tsx` mounts `App`; `frontend/src/App.tsx` implements history-based paths and views: Overview, Mission Control, Mission Planner, Scenario Lab, Fleet, Analytics, History, and Research. Authentication/General administration is in `AccessPortal.tsx`.
- `frontend/src/services/api.ts` owns REST requests, cookie credentials, CSRF refresh/retry, and WebSocket construction; `services/auth.ts` holds client types/session state.
- `Map2D.tsx` uses an SVG abstract 0–10 coordinate plane and backend state. `ThreeEnvironment.tsx` is a lazy-loaded illustrative Three.js view, not a geographic map or simulator bridge.
- `ScenarioWorkspace.tsx` lists, selects/loads, creates, versions, edits advisory mission intent, sandboxes, submits, and executes approved versions. Mission Planner and Scenario Lab share the selected scenario. AI transfer to an existing owner-owned scenario appends an immutable version while retaining its config and existing geographic/fault intent. Per-aircraft fault profiles remain advisory; combinations outside the deterministic simulator's single-active-fault capability are blocked by both UI and backend checks.
- `GeographicMissionPlanner.tsx` is the Mission Planner surface for WGS84 map selection, multiple survey polygons, draggable/editable vertices and markers, per-PX4 start/destination/waypoint/zone assignments, validation/preview, and versioned save/approval. It supports a per-user browser-session AI conversation capped at eight operator turns; restored dialogue is bounded and validated, incomplete recommendations cannot be applied, and applying advice preserves manually mapped aircraft geography. The geographic dialogue is browser-session state, not server-persisted conversation history. `MissionPlannerMap.tsx` reuses the installed Cesium assets and offers public Esri street, satellite, hybrid-label, and ArcGIS elevation providers plus explicit Photon place search. Attribution is provider-supplied for Esri and visible for Photon/OpenStreetMap; current endpoints required no credentials in endpoint checks. Dashed connectors are illustrative only. Geographic drafts are blocked from abstract sandbox/execution. Provider responses were verified, but a live-browser screenshot still showed a black globe after two rendering attempts; visible imagery is blocked pending a new Cesium render/layer diagnostic.
- `AccessPortal.tsx` General review queue displays the submitted version's exact simulator config and advisory intent before approval/rejection/change requests.
- `MissionAssistant.tsx` remains available in the Mission Planner's separate abstract-simulator section with its resumable owner-scoped conversation and Scenario Lab transfer. The geographic planner uses a structured endpoint through the same provider/quota/audit adapter; exact map coordinates are not sent to AI. Both surfaces operate on the shared selected scenario but keep abstract IDs and geographic PX4 IDs separate.
- Styling is in `frontend/src/styles.css` and component CSS. Dependencies/scripts are in `frontend/package.json`; Vite uses Tailwind v4 and manual chunks.

## Backend

- `backend/app/main.py`: FastAPI lifecycle, request IDs/body limit, active-dialect database readiness probe, simulation creation/state/control, command review, alerts/isolation/reassignment, authenticated WebSocket with a bounded per-client sender queue and timeout, history/replay, paired experiments, and CSV exports.
- `backend/app/simulation/engine.py`: versioned seeded 2D movement; task generation/completion; software-only GPS fault profiles; separate truth-like `position`, reported `gps`, model `velocity`, and reported `gps_velocity`; explicitly abstract battery/endurance, payload, capability, communications, and workload resources.
- `backend/app/simulation/detector.py`: synthetic clean-noise-calibrated trajectory residual detector. Inputs are reported coordinates, assigned destination, speed, and timestep; hidden truth and attack labels are not passed.
- `backend/app/simulation/response.py`: persistence thresholds, degraded/isolation state, and nearest eligible abstract task reassignment checked against workload, payload, capability, communication availability, and battery/endurance reserve; unassignable work and rejection reasons are recorded.
- `backend/app/research`: the existing deterministic abstract-GPS dataset/JSON DecisionTree pipeline remains connected optionally to the abstract simulator. Separate `sensor_fusion.py`, `sensor_dataset.py`, `sensor_models.py`, and `sensor_threat.py` provide a synthetic-only quaternion complementary filter, 3D ENU accelerometer-bias EKF, truth-labelled multi-fault datasets, grouped model comparisons, EDA, and offline explainable assessments. The sensor research joblib models are not loaded by the runtime API and are not PX4/field models.
- `backend/app/auth_routes.py`, `security.py`, `authorization.py`: pending registration, Argon2id, `GENERAL`/`OPERATOR`, interactive first-General bootstrap, revocable server sessions, CSRF/origin validation, permissions, ownership, account decisions, and audit history.
- `backend/app/scenario_routes.py`: owned drafts, immutable simulator-config and advisory-intent versions, geographic WGS84/polygon/connected-PX4-assignment validation, combined integrity hashing, exact-version review, and an explicit block preventing geographic intent from entering the abstract sandbox/execution path.
- `backend/app/ai`: OpenAI Responses SDK adapter, strict abstract and geographic structured schemas, proposal validation, redaction, quota reservations, persisted owner-scoped conversations, independent General proposal approval, and telemetry-bounded assessment. The geographic planning endpoint receives named zones/current valid PX4 IDs but no coordinates. `OPENAI_API_KEY` is not configured; live provider behavior remains unverified.
- `backend/app/config.py` loads the repository-root `.env` and environment settings. `backend/app/persistence/database.py` supports SQLite by default and PostgreSQL through `DATABASE_URL`; SQLite uses `create_all` after legacy-schema checks, while PostgreSQL startup requires the Alembic head and uses a transaction-scoped advisory lock for read-then-write operations. Migration procedures are in `docs/DATABASE_MIGRATIONS.md`.

## Data and trust boundaries

The deterministic simulator's `position` is its ground-truth-like software state and `gps` is reported/noisy/attacked telemetry. The synthetic sensor research pipeline adds generated accelerometer/gyro/magnetometer streams, quaternion estimates, covariance, and truth comparisons, but it does not implement synchronized live PX4 sensor fusion. The current PX4 API exposes raw latest-value telemetry and PX4-provided attitude; real timestamp alignment, frame transforms, estimator output, and PX4 threat validation remain absent. Geographic mission input is WGS84 lat/lon degrees stored in advisory version JSON; it is separate from simulator coordinates and is not transformed to PX4/Gazebo local frames. Dashed route lines are visual planning sketches, not feasibility evidence.

SQLite tables include runs, snapshots, events, experiments, ownership, users, sessions, audit logs, scenarios/versions/versioned intent/approval requests, command decisions, AI proposals/usage, and `ai_conversations`. The active database was read-only inspected on 2026-11-07 before the conversation and scenario-intent tables were introduced: 34 runs, 615 snapshots, 352 events, 10 experiments, 2 users, 18 sessions, 0 scenarios, 0 scenario versions, and 153 audit logs. `PRAGMA integrity_check` returned `ok`; `foreign_key_check` returned no rows. Startup `create_all` adds the two empty tables without rewriting existing rows. Legacy scenario versions without intent keep their config-only integrity hashes; hashes for versions with intent cover both objects. No user identity or secret fields were inspected. Preserve this database and its records.

## Feature classification at audit time

| Feature | Classification | Evidence / boundary |
|---|---|---|
| Authentication, roles, CSRF, ownership, audit | FULLY IMPLEMENTED for the current local app | Backend policy, session/auth routes, and API regression tests. Multi-worker shared rate limits and MFA are absent. |
| SQLite runs, events, replay, experiments | FULLY IMPLEMENTED for current software simulator | SQLAlchemy models/routes and backend tests; local active DB integrity verified. |
| Abstract deterministic multi-UAV simulation | PARTIALLY IMPLEMENTED | Seeded 2D movement and task states, but no flight dynamics or SITL. |
| Software-only GPS fault injection | PARTIALLY IMPLEMENTED | Offset/drift/intermittent, jump, freeze, noise, degradation and velocity inconsistency in reported software telemetry; no PX4/Gazebo fault injection. |
| Independent GPS consistency detector | PARTIALLY IMPLEMENTED | Telemetry-only trajectory residual baseline, no raw IMU or PX4 estimate and not field-validated. |
| Adaptive response | PARTIALLY IMPLEMENTED | Abstract workload, payload, capability, communications, and endurance/battery-reserve checks with explicit no-recipient behavior; no vehicle measurements or flight constraints are represented. |
| Mission Control 2D | FULLY IMPLEMENTED for abstract state | Connected REST/WebSocket state and replay; not geographic. |
| Three.js 3D view | UI ONLY | Illustrative terrain/vehicle scene, not a simulator connection. |
| Cesium geographic map | PARTIALLY IMPLEMENTED | Cesium is inside the existing Mission Control PX4 view and consumes valid MAVLink WGS84 positions. Combined authenticated browser acceptance, vehicle movement, and the remaining telemetry details require a live session. |
| Mission Planner imagery/search | PARTIAL; VISUAL RENDER BLOCKED | Street/satellite/label/elevation providers, style selector, Photon search, and entity-preservation tests are implemented. Multiple live tile endpoints returned valid images, but the attached browser showed a black globe. Do not claim visible map acceptance until the layer/render issue is resolved and screenshot-verified. |
| PX4 SITL and MAVLink telemetry | PARTIALLY IMPLEMENTED | `pymavlink` decoding, source-filtered one-way WSL UDP relay, authenticated FastAPI REST/WS, stale handling, and per-system identities are implemented. Three disarmed X500 systems were observed with valid positions. No arm/takeoff/waypoint/landing sequence is verified; MAVSDK Server is absent. |
| Gazebo pose visualization | PARTIALLY IMPLEMENTED | WSL subscribes read-only to `/world/<world>/pose/info`; backend validates source/model/pose data and provides authenticated REST/WS; Mission Control displays PX4 X500 mesh poses and trails. Three models were received in a separate scratch-backend smoke. Native Gazebo rendering/world/video/physics overlays are not streamed. |
| PX4 threat detection/adaptive reassignment | NOT IMPLEMENTED for live telemetry | Existing detector/reassignment apply only to the deterministic abstract simulator. Do not infer PX4 spoofing or issue reassignment from unvalidated live data. |
| Dataset generation | PARTIALLY IMPLEMENTED | Reproducible abstract-simulator samples/labels, seed-group splits, quality checks, and content-addressed export exist. No PX4/field telemetry data pipeline or trained model yet. |
| Trained ML detection | PARTIALLY IMPLEMENTED | A real classifier is trained/evaluated on seed-disjoint abstract samples and can score features from a hash-validated JSON tree artifact. Optional runtime/API/UI integration exists; PX4/field validation remains absent. |
| Scenario Lab | PARTIALLY IMPLEMENTED | Save/list/load/edit/version/review/sandbox/approved execution exist, including advisory mission objectives, task/role suggestions, and notes; geographic route and flight capability fields are absent. |
| Conversational AI planning | PARTIALLY IMPLEMENTED | Durable owner-scoped turns, structured proposal validation, bounded deterministic setting-conflict checks, and idempotent transfer of config plus advisory intent are implemented and mock-tested. Live provider and broad semantic extraction remain unverified/incomplete. |
| PostgreSQL adapter and local migrations | PARTIALLY IMPLEMENTED | Isolated local PostgreSQL 18 migration and all backend tests pass; Supabase/TLS/cloud backup and load validation are absent. |
| Deployment | PARTIAL PREPARATION | `netlify.toml`, `backend/Dockerfile`, `.dockerignore`, and `docs/DEPLOYMENT.md` define the build/runtime boundary; no image build, hosted frontend/backend, cloud database, or remote simulator has been tested. |
| Health / observability | PARTIALLY IMPLEMENTED | HTTP health probes the database and reports in-process abstract simulation/WebSocket counts, detector mode/model, AI key configuration, and explicit PX4/telemetry `not_integrated` states. HTTP requests emit JSON logs correlated with server-generated response request IDs, excluding query strings, headers and bodies. It does not check external PX4 processes, provider reachability, per-UAV freshness, or stale telemetry; end-to-end audit/log correlation is not fully verified. |

## Security model

The inspected code resolves the user's role/status from the configured database on requests; the browser role is not authoritative. Protected reads enforce permission and resource ownership. Mutations use the session CSRF token and configured origin check. The WebSocket authenticates the cookie, checks origin and run ownership before accepting. Each client has a bounded four-state queue; overflow coalesces stale snapshots, and a send timeout closes a slow stream. Session cookies are HttpOnly/SameSite Strict and Secure by default. Rate limits for login/registration are in-process; multi-worker shared limiting and MFA are not implemented.

## Target architecture, not yet implemented

```text
PX4 SITL X500 + Gazebo (WSL2 Ubuntu 24.04)
  -> MAVLink/MAVSDK per-UAV adapters with bounded queues and health/freshness
  -> FastAPI validated telemetry envelope
  -> authenticated WebSocket -> React CesiumJS geographic view
  -> separated simulator truth / raw GPS+IMU / PX4 estimate / independent estimate
  -> validated software-only fault injection, labeled datasets, trained/evaluated model
  -> threat scoring and evidence-based mission response
  -> SQLite-compatible local persistence, then tested PostgreSQL/Supabase adapter/migrations
  -> separately hosted FastAPI, Netlify frontend, secured PostgreSQL; simulation host distinct
```

No part of this target pipeline should be called complete until its simulator path has been run and evidenced. Detailed dependencies/status are in `TRINETRA_IMPLEMENTATION_ROADMAP.md` and `TRINETRA_IMPLEMENTATION_PROGRESS.md`.
