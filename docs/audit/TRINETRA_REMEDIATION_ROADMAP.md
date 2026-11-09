# TRINETRA Remediation and Acceptance Roadmap

**Disposition:** prioritized follow-up recommendations with remediation status updated after the 2026-11-09 regression milestone.

## P0 — Preserve data and current runtime

- Keep `backend/trinetra.db` canonical unless an owner-approved configuration explicitly selects another target.
- Do not reset accounts, run bootstrap, rebuild the schema, rewrite historical audit entries, or migrate production data as part of defect remediation.
- Before any future schema or runtime-path change, obtain and validate a reviewed backup and non-destructive migration plan.
- Do not stop or restart unknown PX4/Gazebo/backend services. Identify service ownership and environment safely before any lifecycle change.

## P1 — Stabilize and verify live-view lifecycle

1. **Implemented:** stabilize parent callback identity and use callback refs so Cesium/Three setup is independent of callback changes.
2. **Partially verified:** repeated telemetry updates preserve selection callback identity and PX4/Gazebo selection works in the component regression. The test does not count actual WebGL viewer construction/disposal.
3. Validate with an authorized browser session and read-only SITL/Gazebo streams. Confirm no viewer churn, asset reload loop, stale-state recovery, and cleanup.
4. Keep telemetry authority in backend REST/WebSocket data; do not add frontend fake telemetry.

## P1 — Make audit exports complete

1. **Implemented:** General audit CSV streams all matching records in 200-row keyset batches, ordered by timestamp and ID descending.
2. **Verified:** isolated API regression exports 450 filtered records, including rows sharing timestamps and correctly escaped comma, quotes, and newline content.
3. Filter parameters are shared with audit history; Operator export remains denied with 403.
4. Preserve historical `actor_role` as unknown; add role metadata only for new events where the actor’s role is available.

## P2 — Complete account workflow in isolation

- **Controlled workflow verified:** disposable Operator registration/pending state, General approval and rejection, role-scoped navigation/API 403, refresh, authenticated abstract WebSocket, rejection/login denial, audit correlation, and logout/session 401 were exercised against a separate temporary database. The prompt-driven reject button was not directly invoked because the browser harness lacks `window.prompt()`; its authenticated REST path was exercised.
- Backend tests cover unauthenticated REST and WebSocket denial for telemetry, and the complete 140-test isolated suite passed. Frontend tests cover the 401-versus-403 client behavior.
- Original General credentials, the canonical database, and canonical user/audit rows were not used. Original-account acceptance and confirmation of the running service's SQLite file path remain owner/runtime-only gaps; do not alter canonical data to close them.

## P2 — Complete current repository test evidence

- The prior selected audit backend run was inconclusive; it is superseded by the completed isolated full backend suite recorded in `TRINETRA_IMPLEMENTATION_PROGRESS.md`.
- The 2026-11-09 regression milestone passed the full backend and frontend suites and the frontend production build. Pytest uses the temporary test DB configured by `backend/tests/conftest.py`; the canonical database was not used.
- Remaining acceptance gaps are original-account live browser acceptance, actual Cesium/Three initialization counters, and authorized read-only live SITL/Gazebo browser validation.

## P3 — Validate software telemetry in dependency order

1. Confirm current backend and existing SITL/Gazebo state without restart or configuration changes.
2. Complete the documented single-X500 SITL telemetry validation gate with movement, stale/reconnect, and data-frame checks. Keep all activity inside PX4 SITL/Gazebo.
3. Validate explicit truth/raw/estimate separation and sensor timing/frame contracts.
4. Only after the single-system gate, evaluate three then five SITL vehicles, resource limits, queues, and adaptive reassignment. Do not connect real aircraft or add physical control.
5. Keep PX4 threat scoring and live task reassignment disabled/unclaimed until independently validated.

## P3 — Research and integration integrity

- Expand seed groups/profile counts and report uncertainty; calibrate confidence and avoid treating run-level metrics as timestep classification.
- Test live AI only when a backend key is intentionally configured; keep prompts/secrets out of logs.
- Validate PostgreSQL migration and backup/restore only on a dedicated isolated environment; Supabase and production remain separate gates.
- Exercise Docker/hosted deployment, HTTPS cookie/CSRF, WebSocket upgrade, persistence, observability, and rollback on a non-production environment.
- Measure Cesium startup and runtime performance before setting a bundle budget.

## Exit gate

Close each item only with its direct evidence, exact command/browser path, observed outcome, environment/data boundary, and regression coverage. Keep the product labeled a software-only research demonstrator until the relevant staged SITL and research validation gates pass. No completion claim should be based solely on source presence or historical test results.
