# TRINETRA Test Coverage Audit

**Audit date:** 2026-11-09

## Commands run and outcomes

| Command | Outcome | Scope/limitations |
|---|---|---|
| From `D:\TRINETRA\frontend`: `npm.cmd test` | **Passed: 10 files, 29 tests** | Vitest/jsdom; test fixtures mock service responses and do not exercise canonical accounts or external services. Canvas and `scrollTo()` warnings are jsdom limitations. |
| From `D:\TRINETRA\frontend`: `npm.cmd run build` | **Passed** | TypeScript project build and Vite production build; 4,215 modules and 342 static assets. Cesium chunk warning (~5,011 kB minified). |
| Read-only SQLite integrity/FK inspection | **Passed: `ok`, zero FK violations** | Canonical `backend/trinetra.db` opened read-only; no writes. |
| Browser access portal | **Observed** | Browser diagnostics showed API/database online; no active WebSocket verified. No credential entry or mutation. |
| Selected backend suite: `..\.venv\Scripts\python.exe -m pytest -q tests/test_database.py tests/test_simulation.py tests/test_research_dataset.py tests/test_research_model.py tests/test_px4_telemetry.py tests/test_gazebo_telemetry.py` | **Inconclusive; stopped after more than 7 minutes** | Used `backend/tests/conftest.py` temporary SQLite DB, not the canonical file. Pytest showed 50 progress dots but no final summary or exit result. Do not interpret dots as a complete pass. API routes/auth workflow tests were not in this selected batch. |

## Coverage mapped to surfaces

The 10 frontend test files cover app routing/session restoration with mocked session values; API credentials and CSRF headers, token refresh, and 401-vs-403 behavior; registration receipt/status; scenario editing; AI assistant UI; command decision UI; PX4 component behavior; Gazebo pose mirror behavior; detector status; and telemetry delta merging.

Backend tests in the repository cover database, simulation, APIs, AI, telemetry, and research model/dataset paths. Historical project docs record many successful full backend runs, including isolated SQLite and local PostgreSQL. Those are historical evidence only and must not be mistaken for the currently inconclusive selected command above.

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
