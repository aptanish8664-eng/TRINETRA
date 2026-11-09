# TRINETRA Backend and API Audit

**Status:** source-inspected; endpoint inventory extracted statically; full API integration test execution did not complete during the audit window.

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

- The backend test batch (`test_database.py`, `test_simulation.py`, `test_research_dataset.py`, `test_research_model.py`, `test_px4_telemetry.py`, `test_gazebo_telemetry.py`) remained active beyond the initial 180-second wait. Results are reported only after the process completes; no API test result is inferred from this.
- Prior documented API/TestClient runs include historical successful suites, but they are not current-run evidence. Authentication and protected API workflows were not exercised in this audit against existing credentials or the populated canonical DB.
- `GET /api/general/audit.csv` returns only the first 200 newest filtered records; it ignores the total count and offers no truncation indication. A General account can paginate the API history, but the export is not a complete-history export once more than 200 rows match (D-02, medium).
- Operational health and telemetry UI remain separate evidence: a ready health response does not imply a live WebSocket or PX4/Gazebo feed.
- The default persistence configuration points to `backend/trinetra.db`, but environment overrides were not disclosed/read from the active service. The browser’s backend-online indicator does not establish which path the running backend opened.

## Recommended API follow-up

1. Make audit CSV completeness explicit: stream all filtered entries or expose a deliberate paginated export protocol; include a regression proving that a matching history larger than 200 entries is fully exported or clearly labeled.
2. Complete isolated API/auth/role/CSRF/WebSocket tests on the current checkout and capture the final command outcome.
3. Add a safe health acceptance contract distinguishing configured, reachable, and data-fresh states for optional adapters.
4. Add WebSocket load/reconnect tests before making throughput or availability claims.
