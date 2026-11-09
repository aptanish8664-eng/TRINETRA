# TRINETRA Test Coverage Audit

**Audit date:** 2026-11-09

The latest verification below supersedes older test totals in this report; earlier entries are retained as dated historical results.

## Commands run and outcomes

| Command | Outcome | Scope/limitations |
|---|---|---|
| From `D:\TRINETRA\frontend`: `npm.cmd test` | **Passed: 10 files, 30 tests** | Re-run after D-01/D-04 fixes; Vitest/jsdom; test fixtures mock service responses and do not exercise canonical accounts or external services. Canvas and `scrollTo()` warnings are jsdom limitations. |
| From `D:\TRINETRA\frontend`: `npm.cmd run build` | **Passed** | TypeScript project build and Vite production build; 4,215 modules and 342 static assets. Cesium chunk warning (~5,011 kB minified). |
| Read-only SQLite integrity/FK inspection | **Passed: `ok`, zero FK violations** | Canonical `backend/trinetra.db` opened read-only; no writes. |
| Browser access portal | **Observed** | Browser diagnostics showed API/database online; no active WebSocket verified. No credential entry or mutation. |
| From `D:\TRINETRA\backend`: `..\.venv\Scripts\python.exe -m pytest -q tests` | **Passed: 121 tests** | Full suite re-run after D-02 and PX4 IMU-mask changes; isolated temporary SQLite DB from `tests/conftest.py`, not the canonical database. |
| From `D:\TRINETRA\frontend`: `npm.cmd test` | **Passed: 10 files, 30 tests** | Full suite re-run after D-01/D-04; jsdom canvas and `scrollTo()` notices remain. |
| From `D:\TRINETRA\frontend`: `npm.cmd run build` | **Passed** | TypeScript/Vite; 4,215 modules and 342 assets. Existing Cesium bundle warning remains. |
| Earlier selected backend subset | **Inconclusive; stopped after more than 7 minutes** | Historical attempt used the isolated temporary DB and had no final result. Superseded as suite-level evidence by the full 121-test pass above. |

## Coverage mapped to surfaces

The 10 frontend test files cover app routing/session restoration with mocked session values; API credentials and CSRF headers, token refresh, and 401-vs-403 behavior; registration receipt/status; scenario editing; AI assistant UI; command decision UI; PX4 component behavior; Gazebo pose mirror behavior; detector status; and telemetry delta merging.

Backend tests in the repository cover database, simulation, APIs, AI, telemetry, and research model/dataset paths. The current isolated full-suite result is 121 passing tests. Historical project docs also record local PostgreSQL runs; those are not current PostgreSQL acceptance evidence.

## Unverified acceptance areas

- Original General and Operator account login; end-to-end approval/rejection; refresh and navigation with a real browser session.
- Current canonical backend database selection from the running service.
- Browser denial behavior for REST and WebSocket using real role sessions.
- Authenticated current PX4+Gazebo page acceptance, live viewer initialization stability, and reconnection behavior.
- Full SITL mission sequence and staged 1/3/5 software-simulator performance/resource benchmarks.
- Live OpenAI provider behavior, production Postgres/Supabase, container build, hosted cookie/CSRF/WebSocket, backups/restore, and deployment rollback.
- Systematic keyboard/screen-reader/responsive viewport test and meaningful network/WebSocket load.

## Interpretation

Frontend tests and build provide good compile/component regression evidence. They are not browser E2E proof. Canonical DB integrity is a read-only data check, not an auth test. Do not claim the complete role workflow or operational telemetry is accepted until those exact paths are safely tested.

## Latest verification and controlled acceptance (2026-11-09)

| Command / check | Latest result | Scope / limitation |
|---|---|---|
| From `D:\TRINETRA\backend`: `..\.venv\Scripts\python.exe -m pytest -q tests` | **Passed: 140 tests** in 62.11 s | Full backend suite; test fixture database is isolated from `backend/trinetra.db`. |
| From `D:\TRINETRA\frontend`: `npm.cmd test` | **Passed: 10 files, 31 tests** in 14.32 s | Vitest/jsdom; canvas and `scrollTo()` notices are environment limitations. |
| From `D:\TRINETRA\frontend`: `npm.cmd run build` | **Passed** | `tsc -b` and Vite; 4,215 modules, 342 copied static assets. Cesium chunk is 5,011.16 kB minified / 1,382.61 kB gzip and triggers the existing chunk-size warning. |
| Browser at `http://127.0.0.1:5173/login` | **Rendered and inspected** | Live login page reported API, database, simulation, and auth API online; WebSocket unverified with no active stream. No credentials entered during this check. |
| `GET http://127.0.0.1:8000/api/health` | **HTTP 200, ready** | Reports SQLite healthy, one managed abstract simulation (not running), heuristic detector, and PX4/Gazebo not integrated. Health does not disclose the selected database file path. |
| Controlled General/Operator browser workflow | **Passed on separate temporary SQLite DB** | Login/session refresh, approval/rejection, role navigation and 403, authenticated simulation WebSocket, rejected-account 401, audit entries, and logout/session 401 were verified. Original accounts and canonical DB were not used. |
| Sensor-fusion/dataset/model/threat tests | **19 focused tests passed** in the research milestone | Synthetic ENU fixtures only; this does not validate live PX4 timing/frames or threat response. Included in the latest full backend suite. |

The `AccessPortal` registration-state type was narrowed to keep the form email a string while the API payload accepts `string | null`; this resolved a TypeScript build failure introduced by the optional-email correction. Frontend tests and production build were rerun after the fix and passed.

The latest current-state checks found no running WSL distributions; Ubuntu remains installed. Windows listener ownership is not reliable in this environment (`Get-NetTCPConnection` reported PID 0 for port 8000 and no result for 5173, while HTTP requests and the integrated browser succeeded). No PX4/Gazebo process was started, stopped, or reconfigured. Live SITL, combined PX4/Gazebo browser acceptance, original-account acceptance, PostgreSQL/Supabase, deployment, load, and accessibility testing remain outstanding.
