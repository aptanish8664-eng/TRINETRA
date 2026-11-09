# TRINETRA Complete Architecture (Current Audit)

Audit date: 2026-11-08. This describes the inspected `D:\TRINETRA` checkout, not the final target architecture. Implementation states are labeled explicitly.

## System context

TRINETRA currently runs as a Windows-hosted React/Vite frontend and a Python FastAPI backend. The backend owns deterministic simulator state, authentication, scenario approval, AI proposals, persistence, and REST/WebSocket delivery. SQLite is the default; an optional PostgreSQL/Alembic path passed isolated local PostgreSQL tests. Optional abstract ML inference is connected through backend simulation/API and Mission Control; heuristic remains default. A read-only PX4 MAVLink receiver, Cesium Mission Control layer, and a separate read-only Gazebo pose receiver/mirror are now implemented and live-smoke tested. MAVSDK control, native Gazebo world/video rendering, IMU estimator/threat validation, and PX4-linked reassignment remain unimplemented.

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

Three disarmed X500 instances with separate MAVLink system identities and valid GPS positions were observed through authenticated REST/WebSocket. A separate pose-bridge smoke forwarded Gazebo `/world/default/pose/info` and the isolated backend reported three X500 models. The read-only routes and both visual components are integrated in the existing Mission Control code. The two runtime smoke checks used separate backend processes; a combined authenticated browser demo and in-flight movement are not verified. The Gazebo panel renders live XYZ/quaternion updates with official PX4 X500 mesh assets; it does not stream Gazebo's world, lighting, camera/video, or physics overlays. PX4 threat detection and adaptive reassignment are not linked to actual PX4 telemetry. Keep these limits explicit and do not generate fake live telemetry. PostgreSQL URL support and local Alembic migrations were validated on an isolated local PostgreSQL cluster; no Supabase connection is configured. Phase 15 preparation includes Netlify SPA configuration, a separate FastAPI container definition, and deployment guidance, but neither image nor hosted deployment has been exercised.

## Frontend

- `frontend/src/main.tsx` mounts `App`; `frontend/src/App.tsx` implements history-based paths and views: Overview, Mission Control, Mission Planner, Scenario Lab, Fleet, Analytics, History, and Research. Authentication/General administration is in `AccessPortal.tsx`.
- `frontend/src/services/api.ts` owns REST requests, cookie credentials, CSRF refresh/retry, and WebSocket construction; `services/auth.ts` holds client types/session state.
- `Map2D.tsx` uses an SVG abstract 0–10 coordinate plane and backend state. `ThreeEnvironment.tsx` is a lazy-loaded illustrative Three.js view, not a geographic map or simulator bridge.
- `ScenarioWorkspace.tsx` lists, selects/loads, creates, versions, edits advisory mission intent, sandboxes, submits, and executes approved versions. Approval and integrity enforcement are backend-owned.
- `AccessPortal.tsx` General review queue displays the submitted version's exact simulator config and advisory intent before approval/rejection/change requests.
- `MissionAssistant.tsx` supports a resumable multi-turn proposal conversation, displays persisted user/assistant turns and the structured review, restores the current user's conversation from a session-scoped ID, and transfers the reviewed simulator config plus objectives/tasks/roles/constraints to Scenario Lab.
- Styling is in `frontend/src/styles.css` and component CSS. Dependencies/scripts are in `frontend/package.json`; Vite uses Tailwind v4 and manual chunks.

## Backend

- `backend/app/main.py`: FastAPI lifecycle, request IDs/body limit, active-dialect database readiness probe, simulation creation/state/control, command review, alerts/isolation/reassignment, authenticated WebSocket with a bounded per-client sender queue and timeout, history/replay, paired experiments, and CSV exports.
- `backend/app/simulation/engine.py`: versioned seeded 2D movement; task generation/completion; software-only GPS fault profiles; separate truth-like `position`, reported `gps`, model `velocity`, and reported `gps_velocity`; explicitly abstract battery/endurance, payload, capability, communications, and workload resources.
- `backend/app/simulation/detector.py`: synthetic clean-noise-calibrated trajectory residual detector. Inputs are reported coordinates, assigned destination, speed, and timestep; hidden truth and attack labels are not passed.
- `backend/app/simulation/response.py`: persistence thresholds, degraded/isolation state, and nearest eligible abstract task reassignment checked against workload, payload, capability, communication availability, and battery/endurance reserve; unassignable work and rejection reasons are recorded.
- `backend/app/research`: deterministic, versioned tick-level abstract GPS dataset generation, seed-group train/validation/test splits, integrity checks, scikit-learn DecisionTreeClassifier training, validation-threshold selection, held-out evaluation, and JSON-only tree artifact inference. Optional model inference is connected to runtime simulation/API/UI; this is not a PX4/field model.
- `backend/app/auth_routes.py`, `security.py`, `authorization.py`: pending registration, Argon2id, `GENERAL`/`OPERATOR`, interactive first-General bootstrap, revocable server sessions, CSRF/origin validation, permissions, ownership, account decisions, and audit history.
- `backend/app/scenario_routes.py`: owned drafts, immutable simulator-config and advisory-intent versions, count/reference/coordinate validation, combined integrity hashing, sandbox run, exact-version review, and approved execution. Only simulator config is passed to the engine.
- `backend/app/ai`: OpenAI Responses SDK adapter, strict structured schemas, proposal validation, redaction, quota reservations, persisted owner-scoped conversation turns with expiry/length limits, idempotent Scenario Lab draft transfer, independent General proposal approval, and telemetry-bounded assessment. `OPENAI_API_KEY` is not configured; live provider behavior remains unverified.
- `backend/app/config.py` loads the repository-root `.env` and environment settings. `backend/app/persistence/database.py` supports SQLite by default and PostgreSQL through `DATABASE_URL`; SQLite uses `create_all` after legacy-schema checks, while PostgreSQL startup requires the Alembic head and uses a transaction-scoped advisory lock for read-then-write operations. Migration procedures are in `docs/DATABASE_MIGRATIONS.md`.

## Data and trust boundaries

The deterministic simulator's `position` is its ground-truth-like software state and `gps` is reported/noisy/attacked telemetry. This is adequate to demonstrate a toy residual baseline, but it is not the requested three-stream flight/sensor architecture. No accelerometer, gyroscope, PX4 estimator, quaternion state, uncertainty, real sensor timestamps, or coordinate-frame transform exists. The current API and UI must label coordinates as abstract simulation units.

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
