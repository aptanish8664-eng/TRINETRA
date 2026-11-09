# TRINETRA Remediation and Acceptance Roadmap

**Disposition:** prioritized follow-up recommendations only. This audit did not implement fixes.

## P0 — Preserve data and current runtime

- Keep `backend/trinetra.db` canonical unless an owner-approved configuration explicitly selects another target.
- Do not reset accounts, run bootstrap, rebuild the schema, rewrite historical audit entries, or migrate production data as part of defect remediation.
- Before any future schema or runtime-path change, obtain and validate a reviewed backup and non-destructive migration plan.
- Do not stop or restart unknown PX4/Gazebo/backend services. Identify service ownership and environment safely before any lifecycle change.

## P1 — Stabilize and verify live-view lifecycle

1. Stabilize callback identity or separate viewer setup from selection handling in the PX4 parent/Cesium/Gazebo component boundary.
2. Add a focused regression that delivers repeated telemetry updates and asserts the Cesium/Three viewer is initialized once and disposed only on component unmount or deliberate mode change.
3. Validate with an authorized browser session and read-only SITL/Gazebo streams. Confirm no viewer churn, asset reload loop, stale-state recovery, and cleanup.
4. Keep telemetry authority in backend REST/WebSocket data; do not add frontend fake telemetry.

## P1 — Make audit exports complete

1. Define and document CSV behavior for large histories.
2. Implement all-row streamed export or explicit pagination with a visible row count/truncation notice.
3. Test filter parity between on-screen history and exported rows with more than 200 matching events, including oldest/newest ordering.
4. Preserve historical `actor_role` as unknown; add role metadata only for new events where the actor’s role is available.

## P2 — Complete account workflow in isolation

Using a separate temporary/test database and owner-authorized controlled accounts, verify Operator registration, pending status, General approval and rejection, role-scoped access, refresh/navigation, CSRF, logout/revocation, 401 vs 403, REST and WebSocket denial, and audit event correlation. Do not exercise these mutation paths against the canonical DB without explicit authorization and safeguards.

## P2 — Complete current repository test evidence

- The selected audit backend run was stopped after more than seven minutes without a final result. Do not count it as a pass; run focused isolated backend tests only when the environment can complete them normally.
- Run full backend suite only if targeted results justify it; ensure pytest continues to use the isolated temporary DB.
- Preserve the passing frontend 29-test and build results as current evidence. Add tests for D-01 and D-02 when code remediation is authorized.

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
