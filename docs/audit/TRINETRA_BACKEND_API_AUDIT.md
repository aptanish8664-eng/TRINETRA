# TRINETRA Backend and API Audit

**Status:** source-inspected; endpoint inventory extracted statically; the latest full backend suite passed 140 tests. Original-account and canonical-database API acceptance were not performed.

## Architecture

FastAPI is the application API, with SQLAlchemy persistence, Pydantic request/response validation, REST endpoints, and WebSockets. The backend owns authentication, authorization, simulation state, telemetry stores, and database writes. The frontend consumes those API and stream values; no client-side random telemetry source was found in the inspected paths.

### Static route inventory

AST inspection found **54 route operations**: 51 HTTP operations and 3 WebSockets. The routes span health, authentication and registration, General user/audit functions, AI proposals/conversations/assessments, scenarios and approvals, abstract simulations and controls, command approvals, PX4/Gazebo telemetry, history/replay, and paired experiments/exports. This count is from route decorators in `main.py`, `auth_routes.py`, `scenario_routes.py`, and `ai/routes.py`; it does not count middleware or OpenAPI documentation routes.

The three WebSockets are the owner-scoped abstract run stream and authenticated PX4 and Gazebo telemetry streams. Source checks authenticate WebSocket clients, enforce role/ownership or read permission, validate browser Origin when supplied, and return explicit close codes for missing authentication, authorization denial, missing runs, and unavailable telemetry.

## Positive implementation evidence

- HTTP middleware assigns request IDs, exposes them in responses, and logs structured method/path/status/duration fields without query strings.
- Request bodies have a configured content-length limit; invalid or oversized declared lengths are rejected.
- `GET /api/health` probes the configured database and reports API, database, abstract simulation, in-process WebSocket, detector/model, AI key configuration, PX4, telemetry, and Gazebo status. It does not probe every external process/provider or prove per-vehicle freshness when no receiver is running.
- Backend authorization uses a permission map and server-side dependencies. Protected mutations use CSRF and trusted-Origin checks. Resource reads enforce ownership or General’s all-run permission.
- Abstract run state, replay snapshots, events, experiment records, and owner links persist through SQLAlchemy.
- WebSocket state queues are bounded and apply backpressure/coalescing. A sender timeout closes slow clients. Load and real-browser reconnection behavior were not measured in this audit.
- Fixed simulation/telemetry adapters are used; no arbitrary shell-execution API was found in the inspected backend paths.

## Gaps and risks

- The original audit's selected backend batch was inconclusive; it is superseded by the latest full backend run (**140 passed**) against isolated test databases.
- Original credentials and the populated canonical DB were not used. Controlled browser acceptance against a temporary database exercised General/Operator login/session restore, approval/rejection, General-only 403, authenticated simulation WebSocket, audit writes, rejected-account 401, and logout/session 401.
- `GET /api/general/audit.csv` now streams all filtered records in bounded keyset batches. The regression exported 450 matching events and covered tied timestamps, search/outcome/username/date filters, CSV escaping, General access, and Operator 403 (D-02 fixed).
- Operational health and telemetry UI remain separate evidence: a ready health response does not imply a live WebSocket or PX4/Gazebo feed.
- The default persistence configuration points to `backend/trinetra.db`, but environment overrides were not disclosed/read from the active service. The browser’s backend-online indicator does not establish which path the running backend opened.

## Recommended API follow-up

1. Preserve the isolated auth/API regressions; owner may separately verify original-account access without changing credentials or database rows.
2. Keep health labels explicit: configured, reachable, and data-fresh states are distinct for optional adapters.
3. Add WebSocket load/reconnect tests before making throughput or availability claims.

## Latest verification supplement (2026-11-09)

The backend suite command `..\\.venv\\Scripts\\python.exe -m pytest -q tests` passed **140 tests** in 62.11 seconds. The frontend passed **31 tests** and its TypeScript/Vite build succeeded. Current `/api/health` returned ready with SQLite healthy and an abstract simulator; PX4/Gazebo were `not_integrated`. WSL had no running distributions. Health did not reveal the selected SQLite path. See [TRINETRA_TEST_COVERAGE_AUDIT.md](./TRINETRA_TEST_COVERAGE_AUDIT.md).
