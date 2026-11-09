# TRINETRA Project Status

## Current PX4/Gazebo integration status (2026-11-08)

- The existing Mission Control route contains an authenticated PX4 telemetry view with a CesiumJS map fed by actual MAVLink position reports, plus a read-only Gazebo pose mirror fed by the WSL `/world/default/pose/info` bridge. Neither feature is a separate dashboard, and the frontend has no mock/random live-telemetry fallback.
- An authenticated combined backend on an isolated scratch database receives PX4 IDs 1/2/3 and Gazebo `x500_0/1/2` concurrently. Current sampled vehicles are disarmed, GPS-valid, have near-zero reported speed, and report battery/mission messages; Gazebo positions remained static over the sample. This is live telemetry reception, not flight or moving-vehicle evidence. The populated `backend/trinetra.db` was not used.
- An authenticated browser view previously showed the existing Mission Control page with both telemetry panels and all three PX4/model rows. The Cesium renderer then failed to decode its bundled imagery because the copied asset paths were nested incorrectly. The path mapping is corrected, and direct development/production asset checks now return valid XML, JPEG, worker JavaScript, and X500 texture resources. A post-fix browser-render check is still pending. The pose mirror uses the PX4 X500 mesh but does not stream native Gazebo world geometry, lighting, camera/video, or physics overlays. Gazebo local XYZ and PX4 WGS84 positions remain separate.
- PX4 spoofing/threat scoring, live adaptive reassignment, raw sensor consistency estimation, MAVSDK command/control, a completed X500 flight, and three-aircraft independent motion remain incomplete. Do not change the working PX4 route/settings or claim flight/deployment.
- The latest completed UI increment pairs Gazebo selection with matching PX4 system details and treats MAVLink `MISSION_CURRENT` count sentinels as unknown/no mission rather than waypoint totals. See the appended progress checkpoint for current test counts and build results; jsdom cannot verify WebGL rendering. The Cesium bundle still produces a large-chunk warning.
- Current full verification after the sentinel fix: backend **117 passed in 254.96 s**; frontend **10 files / 27 tests passed in 62.97 s**; `npm.cmd run build` passed (4,215 modules, 342 static assets, 1m27s). The build retains the Cesium 5,011.16 kB minified chunk warning. Backend tests used isolated test DBs; the live scratch DB and the populated application DB were not test targets.

## Architecture

- `backend/app/simulation`: seeded fixed-timestep simulator, telemetry residual detector, threat persistence, isolation, and capacity-aware task reassignment.
- `backend/app/research`: versioned abstract-simulator dataset generation, seed-group splits, sample/manifest integrity checks, DecisionTreeClassifier training, held-out evaluation, and hash-checked JSON inference. Optional model path connects the classifier to authoritative backend run and Mission Control state; heuristic remains the default.
- `backend/app/main.py`: FastAPI controls, WebSocket state, replay, experiments, exports, authenticated ownership checks, and restricted command review.
- `backend/app/persistence/database.py`: SQLAlchemy records on SQLite by default or PostgreSQL via `DATABASE_URL`; PostgreSQL schema is Alembic-managed and startup enforces the migration head. Includes simulation records, users/sessions/audit, ownership, scenarios, and AI workflows.
- `backend/app/security.py`, `backend/app/authorization.py`, and `backend/app/auth_routes.py`: Argon2id credentials, the `GENERAL`/`OPERATOR` role model, revocable sessions, CSRF enforcement, General account management, audit, and ownership.
- `backend/app/scenario_routes.py`: scenario drafts, immutable configuration versions, sandbox runs, General review, and backend-verified approved-version execution.
- `backend/app/ai`: OpenAI Responses adapter, strict structured proposals, deterministic checks, quotas, and telemetry-grounded assessment.
- `frontend/src`: React and strict TypeScript application with registration/sign-in, General Control Center, mission views, scenario workflow, and AI assistant.

## Historical audit snapshot (2026-11-07; database counts remain the latest read-only DB inspection)

The active database `backend\trinetra.db` was opened read-only. It contains 34 runs, 615 snapshots, 352 events, 10 experiments, 2 users, 18 sessions, 0 scenarios, 0 scenario versions, and 153 audit logs. SQLite `integrity_check` returned `ok`; `foreign_key_check` returned no violations. No usernames or credential values were inspected. Preserve all existing rows.

The older migration row counts and account-empty statements below are historical and do not describe the current database. Do not rerun the legacy rebuild or first-General provisioning against the populated database.

The read-only database count snapshot above predates `ai_conversations` and `scenario_version_intent`. The active database was not written during the 2026-11-07 implementation; SQLAlchemy `create_all` adds these new empty tables at the next backend startup without altering existing scenario versions or data rows. That startup path has been exercised against isolated test databases, not against the populated file.

PostgreSQL support was implemented after that SQLite snapshot. An isolated local PostgreSQL 18 cluster on loopback port 55432 accepted Alembic revision `17ea6f9d666a`; `alembic check` reported no schema drift and all 60 backend tests passed against it. The separate isolated SQLite suite also passed 60 tests. This did not access the existing populated SQLite file or the machine's separately installed PostgreSQL service. Supabase connectivity, TLS, cloud backup/restore, deployment, and load have not been tested. See [database migration notes](../docs/DATABASE_MIGRATIONS.md).

Deployment preparation now includes a Netlify Vite/SPA build definition, a non-root FastAPI Docker image, Docker build-context exclusions, a frontend API-origin example, and host/cookie/WebSocket/migration/rollback guidance. Docker and Netlify CLI are unavailable in the current environment, so no image build or hosted deployment has been verified. See [deployment preparation](../docs/DEPLOYMENT.md).

## Historical database rebuild status (not a current inventory)

The configured local database is SQLite at `backend\trinetra.db`. Before rebuilding, the inspected schema contained one legacy account, six legacy sessions, 22 runs, 279 snapshots, 160 events, seven experiments, and 47 audit events. The backup made immediately before the active rebuild is `backend\backups\trinetra-pre-two-role-rebuild-20261026-143429.db`; an earlier verified backup is also retained. Both contain sensitive legacy authentication data and must remain access-controlled.

The active database has been rebuilt using the explicit backup-first migration. It removed `operators`, `operator_sessions`, `access_requests`, and the old `audit_events` table; the 47 retained audit events are now in `audit_logs` with legacy user IDs and login identifiers removed. It preserved all 22 runs, 279 snapshots, 160 events, and seven experiments; all 22 run and seven experiment ownership rows remain unassigned. The new `users` and `sessions` tables are empty pending secure General provisioning. Integrity and foreign-key checks pass, and no legacy authentication tables remain.

## Implemented functionality

- Deterministic simulation, snapshots, events, run history, replay, paired experiments, metrics, and CSV export.
- Phase 7 abstract software fault profiles: sudden, gradual, intermittent, single-tick jump, GPS freeze, increased noise, degradation and velocity inconsistency. Faults change reported GPS/velocity only; software truth trajectory remains unchanged. These are not PX4 faults.
- Phase 8 dataset v2 supports nine profiles including clean, six reported-GPS/velocity/residual features, active-fault labels, seed-grouped splits, quality/hash validation, and non-overwriting export. Earlier artifacts are superseded by simulator-v2 dataset `84a505efbbc9c4a6e710cdd632fcac6044432527e389ba6f51383476693495b1` (72,900 abstract-only rows; 18/6/6 train/validation/test seeds). V1 dataset loading remains supported.
- Phase 9 model v2: current simulator-v2 artifact is model `f2956c53b02514a77aebe38f4d202e87d37bbcccf79b86acc8811fb1d7cea471`, trained on train seeds, threshold-selected on validation seeds, and held out on six abstract test seeds. Earlier model IDs are superseded. The JSON tree supports hash-validated inference, probability, explicitly uncalibrated confidence, binary classification, severity and bands. Set `TRINETRA_ML_MODEL_PATH` to a project-local model JSON artifact to enable backend runs, paired experiments and Mission Control display; default remains heuristic. No PX4/field validation; v1 models remain loadable.
- Paired deterministic experiments now persist schema-versioned runtime/config/attack/timestamp provenance and policy-specific config hashes. Repeated results under the same runtime/config/seed have an API regression test; Scenario Lab linkage, PX4/Gazebo provenance, generated-dataset/model linkage, and usable Git source revision remain explicitly unavailable.
- Dataset CLI example from `D:\TRINETRA\backend`: `..\.venv\Scripts\python.exe -m app.research.cli --seeds 20261101,20261102,20261103,20261104,20261105,20261106,20261107,20261108,20261109,20261110 --output-root research-data\datasets --duration 90 --uav-count 3 --task-count 40`. Earlier generated artifact `80fe45ec332ecf34b3f2135ca7ace4d63da2130ce11022a7c1f1fa97306777fd` is superseded. Current simulator-v2 artifact `84a505efbbc9c4a6e710cdd632fcac6044432527e389ba6f51383476693495b1` has 72,900 rows (18/6/6 seed split) and contains no PX4/field telemetry.
- One registration workflow for both roles. New accounts remain pending until a General approves and assigns the effective role.
- Backend-only, interactive first-General provisioning. There is no seeded account, default password, or public bootstrap route.
- General user review, hold/reject/approve, role change, activation/deactivation, and last-General protection.
- Operator-owned scenarios, sandbox execution, immutable simulator-config and advisory mission-intent versions, exact-version General approval, and integrity-checked execution. Legacy versions remain readable; omitted intent on old-client updates is preserved.
- A single persistent audit log with search, event/outcome/date/user filters, pagination, and CSV export.
- AI proposals remain advisory; a different General must review them, and approval creates a ready simulation rather than starting it.
- Multi-turn AI conversations persist owner-scoped redacted turns, validate structured proposals, and detect explicitly stated fleet/task/duration/spoof-type/spoof-target/response-policy/start-tick/duration-tick/magnitude conflicts with selected settings. Planner UI state resets on account/role changes. The planner transfers objectives/tasks/roles/constraints into an editable Scenario Lab draft without starting a run. Live provider use is not configured; area notes remain non-geographic narrative, not routes or operating boundaries.

## Latest verification completed (2026-11-07)

- After adding offline Phase 9 trainer, inference, JSON artifact loading, and tests: from `D:\TRINETRA\backend`, `..\.venv\Scripts\python.exe -m pytest -q tests` — **70 passed** in 30.10 s, exit code 0.
- After adding an explicit marker that tree probability confidence is uncalibrated: focused model tests **5 passed** in 6.27 s; final full backend `..\.venv\Scripts\python.exe -m pytest -q tests` — **70 passed** in 41.18 s, exit code 0. This supersedes the preceding 70-test timing.
- Model artifact `b092ce5ed5f43839d6c80015076a97ea52ec7fddbbd39c39aa1dcb45017f9424` was trained from dataset `80fe45ec332ecf34b3f2135ca7ace4d63da2130ce11022a7c1f1fa97306777fd`. Held-out test seeds 20261108 and 20261103: 2,160 samples / 180 active-fault rows; accuracy 0.9796, precision 0.8617, recall 0.9000, F1 0.8804, ROC AUC 0.9669, confusion matrix `[[1954,26],[18,162]]`; validation-selected threshold 0.8461538462. These results are specific to the abstract generator; features include the trajectory heuristic residual, and two test seeds do not establish confidence intervals or field performance.
- Model load/inference smoke verified hash-checked JSON loading and returned nominal, probability 0.0, severity 0/LOW on a generated clean sample. Optional model inference is integrated into the API and Mission Control; default runtime remains heuristic.
- Runtime/API follow-up: `TRINETRA_ML_MODEL_PATH` opt-in and shared-feature inference are covered by the model-enabled API test. Focused API/dataset/model/simulation suite: **44 passed** in 26.91 s. Full backend suite after integration: `..\.venv\Scripts\python.exe -m pytest -q tests` — **71 passed** in 44.10 s, exit code 0. This does not test ML-enabled paired experiments or frontend rendering.
- 2026-11-08 expanded software-only faults, v2 velocity feature, and model: focused backend `tests/test_simulation.py tests/test_research_dataset.py tests/test_research_model.py tests/test_api.py::test_configured_ml_model_drives_authoritative_simulation_state tests/test_api.py::test_model_enabled_experiment_records_detector_provenance_and_repeatability tests/test_ai_routes.py::test_conversation_recognizes_velocity_inconsistency_fault_profile -q` — **34 passed** in 24.65 s. Additional fault/API/AI regression batch: **75 passed** in 62.69 s.
- Generated dataset v2 `17fca081ff00ad1c2399cf15fc31752e37e38ced77f0ff93706330addc8c2cb8` (24,300 rows; nine profiles; split 6/2/2). Trained model v2 `687ac3912ef2210cad97ae5c5bd12e28a950f2483700513e7a30e83a04075a0f`; held-out metrics: accuracy .9844, precision .9106, recall .9264, F1 .9185, ROC AUC .9832, confusion matrix `[[4356,42],[34,428]]`, threshold .6556351. Held-out data has two seeds. Per-profile jump has only two active held-out samples and F1 .40; not a robust performance estimate. These metrics remain abstract-only.
- Model/schema compatibility smoke loaded old dataset schema 1, new dataset schema 2, old model schema 1 and new model schema 2; both model versions successfully inferred on v2 feature state.
- After model-enabled experiment provenance and Mission Control rendering: backend full suite **72 passed** in 47.16 s; frontend **7 files / 19 tests passed** in 7.32 s; `npm.cmd run build` passed (TypeScript and Vite). The later fault expansion and dataset/model v2 changes passed the full backend, frontend, and production-build checks recorded below.
- 2026-11-08 post-fault-model validation: focused simulator/dataset/model/API/AI suites passed 75 tests in 62.69 s; an additional focused model/runtime/new AI profile set passed 34 tests in 24.65 s. Full backend: **79 passed** in 55.52 s; frontend: **7 files / 19 tests passed** in 8.11 s; production build passed (Vite 6.29 s).
- After adding exported-dataset loading and content-tamper checks: `..\.venv\Scripts\python.exe -m pytest -q tests/test_research_dataset.py` — **5 passed** in 2.77 s; full backend rerun — **65 passed** in 30.27 s, exit code 0. These supersede the earlier 64-test result above.
- Focused dataset plus Phase 16 repeatability tests: `..\.venv\Scripts\python.exe -m pytest -q tests/test_research_dataset.py tests/test_api.py -k 'research_dataset or validation_and_experiment_comparison'` — **5 passed, 17 deselected** in 3.12 s, exit code 0.
- PostgreSQL test revalidation after the final hash change is blocked in this environment: PostgreSQL 18 denied binding the isolated project test cluster to `127.0.0.1:55432` and shut down. No PostgreSQL tests ran in that attempt. The last PostgreSQL suite pass remains 60 passed before the hash refinement.

- Backend on isolated PostgreSQL 18 after the Phase 16 experiment metadata change, before the final detector-version hash-input refinement: with `TRINETRA_TEST_DATABASE_URL=postgresql+psycopg://trinetra_test@127.0.0.1:55432/trinetra_test`, `..\.venv\Scripts\python.exe -m pytest -q tests` — **60 passed** in 34.57 s.
- Backend on default isolated SQLite after the final detector-version hash-input refinement: from `D:\TRINETRA\backend`, `..\.venv\Scripts\python.exe -m pytest -q tests` — **60 passed** in 34.03 s, exit code 0. Pytest uses its isolated temporary SQLite test DB; it does not use `backend/trinetra.db`.
- Focused deterministic experiment/repeatability test: **1 passed, 17 deselected** in 1.79 s; verifies provenance, persistence/export, config hashes, and repeated output equality.
- After adding detector version to the canonical per-policy hash, the focused test passed twice: **1 passed, 17 deselected** in 1.54 s and, on continuation, **1 passed, 17 deselected** in 1.48 s.
- An isolated PostgreSQL rerun after the hash refinement was attempted using the retained project-owned cluster and exact `trinetra_test` database. Windows denied binding `127.0.0.1:55432`; PostgreSQL shut down without accepting connections. `pg_ctl status` confirmed no server, and the port had no listener. No PostgreSQL tests ran in that attempt; the prior 60-test pass predates the hash refinement. No elevated retry was made.
- PostgreSQL guard check: a URL ending in `/not_test` was rejected during pytest collection with the expected `RuntimeError`; no connection was attempted.
- PostgreSQL migrations: `..\.venv\Scripts\python.exe -m alembic current` — `17ea6f9d666a (head)`; `..\.venv\Scripts\python.exe -m alembic check` — `No new upgrade operations detected.`
- PostgreSQL-focused bootstrap, registration, and scenario tests: **5 passed**, 13 deselected in 5.30 s.
- PostgreSQL test fixture now rejects databases unless the explicitly supplied database name is exactly `trinetra_test`.

- Revalidated after account-isolation and planner-setting changes: backend `..\.venv\Scripts\python.exe -m pytest -q tests` â€” **57 passed** in 33.64 s using isolated test SQLite.
- Frontend `npm.cmd test` â€” **6 test files / 16 tests passed** in 6.76 s; three existing jsdom `scrollTo()` warnings.
- Frontend `npm.cmd run build` â€” passed in 8.42 s (`tsc -b` and Vite); app 525.82 kB, CSS 76.97 kB, Three.js 516.51 kB minified.

The historical backend/frontend/build counts immediately below are from earlier milestones and are superseded by the current results above.

- Backend: from `backend`, `..\.venv\Scripts\python.exe -m pytest -q tests` — initial baseline 47 passed; after health/WebSocket changes 50 passed; after conversational planning and versioned mission intent, **56 passed** using isolated test SQLite.
- Frontend: `npm.cmd test` — 5 test files, 13 tests passed; jsdom printed three `scrollTo()` not implemented warnings.
- Frontend: `npm.cmd run build` — TypeScript check and production build passed. Latest app, CSS, and Three.js chunks are 525.71 kB, 76.97 kB, and 516.51 kB minified.
- Active database was checked read-only; integrity passed and no FK violations were found.
- `python -m pytest -q tests` using system Python 3.9 failed collection because that interpreter lacks `openai` and `argon2`; use the project-local venv above.
- A local HTTP smoke passed: backend `/api/health` returned ready/healthy on an isolated database; Vite `/` and `/src/main.tsx` returned HTTP 200. Both services were stopped. No browser automation, real OpenAI, PX4/Gazebo, PostgreSQL, load, or deployment test was performed.
- WebSocket regression tests verify bounded queue overflow/coalescing and a timed-out client close; no real load test was run.

- OpenAI adapter, multi-turn routes, mission-intent validation, immutable version hashes, transfer, and explicit planner setting conflicts were tested with mocks; no live API key is configured. Geographic notes remain descriptive and do not create executable routes. Broad semantic extraction remains incomplete.

## Historical verification (superseded)

- Backend: `python -m pytest -q tests` — 45 passed using an isolated temporary SQLite database.
- Frontend: `npm test` — 3 passed.
- Frontend: `npm run build` — TypeScript check and production build passed.
- Migration: both a copy and the configured database passed `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and record-count checks after rebuild.
- Active database smoke test: the legacy login identity fails authentication, the removed `/api/admin` route returns 404, and `/api/health` reports `ready`.
- Migrated-copy workflow test: a backend-only bootstrap General signed in and could read all 22 preserved runs.

## First account provisioning (historical instructions; database is populated)

The interactive backend-only provisioning command is documented in [AUTHENTICATION.md](./AUTHENTICATION.md). It refuses if any user already exists. The current database has two user rows, so do not attempt first-General provisioning; use only an already authorized account. No credential values were inspected.

## Known limitations

- Login and registration rate limits are in-process; multi-worker deployments require a shared rate-limit store. MFA is not implemented.
- The simulator uses abstract local coordinate units; there is no verified geographic waypoint registry. The 3D scene is illustrative, not geographic.
- The detector is a research baseline calibrated from synthetic noise, not validated against recorded flight telemetry. Precision/recall/F1 are run-level indicators rather than timestep metrics.
- No browser automation or deployed multi-worker validation has been run.
- PX4 MAVLink REST/WebSocket and the Cesium Mission Control layer are implemented; the Gazebo pose bridge/mirror is partial and does not stream the native Gazebo scene. PX4 threat detection, MAVSDK command integration and raw IMU estimator remain unimplemented. Phase 8 dataset generation and Phase 9 model runtime/UI are abstract-simulator-only. PostgreSQL support is local-test validated; Supabase and cloud operations are not verified.


## Prior abstract fault and model evaluation snapshot (2026-11-08; simulator v1, superseded)

The prior simulator-v1 dataset v2 `2abfd7fb09de1330fd512f58ce916a8ac67c8c525ec6031b13a3d195d0b2a638` contains 72,900 rows over 30 seeds (18/6/6 train/validation/test). Model v2 `075bda40b765336fc6c6ea214d304835880bb0219dbdaeaed1f909235f2f3825` was evaluated on six held-out seeds / 14,580 rows: accuracy .9735, precision .8297, recall .9069, F1 .8666, ROC AUC .9826; mean per-seed F1 .8688 and deterministic six-seed bootstrap 95% interval [.8336,.8995]. Jump profile has only six positive held-out rows; this is abstract-simulator evidence only and confidence is uncalibrated. Latest full verification after the expanded fault profiles: backend **79 passed**, frontend **19 passed**, and production build passed. See [research methods](./RESEARCH_METHODS.md) and the root progress checkpoint for profile metrics, exact commands and limitations.


## Simulator v2 abstract resources and updated dataset/model (2026-11-08)

Simulator `deterministic-abstract-2d-v2` adds synthetic battery/endurance budget, workload, payload, capability, and communication checks to task reassignment. These values are not measured telemetry. The current dataset `84a505efbbc9c4a6e710cdd632fcac6044432527e389ba6f51383476693495b1` contains 72,900 rows over 30 seeds (18/6/6). Model `f2956c53b02514a77aebe38f4d202e87d37bbcccf79b86acc8811fb1d7cea471` test results over six held-out seeds: accuracy .9770, precision .8567, recall .9098, F1 .8824, ROC AUC .9837; mean seed F1 .8836, bootstrap 95% [.8503,.9133]. Jump has only six positive test samples. At the simulator-v2 milestone, backend passed 86 tests; a subsequent health-observability milestone passed 87. Frontend passed 19 tests and production build passed. PX4 SITL has launched externally, but GCS connection and X500 flight remain unverified; live telemetry, Supabase, Docker and hosting remain unverified.

## Health component observability (2026-11-08)

`GET /api/health` retains its read-only database probe and legacy fields, and now reports in-process abstract simulator/WebSocket counts, detector/model configuration, AI key configuration, and explicit PX4/telemetry not-integrated states. It does not claim external PX4 process health, provider reachability, or per-UAV freshness. The subsequent full backend suite passed 87 tests; frontend passed 19 tests and production build succeeded. PX4/Gazebo is running externally in WSL, but the QGC link remains localhost-only and no flight is verified. See [PX4 setup notes](./PX4_SETUP.md) and the root progress checkpoint.
