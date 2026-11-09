# TRINETRA Engineering Rules

## Workspace and change control

- The only project workspace is `D:\TRINETRA`. Keep source, tests, docs, checkpoints, and handoff notes here.
- Inspect the existing implementation and this file, `TRINETRA_IMPLEMENTATION_PROGRESS.md`, and the relevant architecture docs before changing code.
- Preserve local user data. Never reset Git, overwrite `backend/trinetra.db`, delete backups, or perform a production migration without an explicitly reviewed backup and a non-destructive migration plan.
- Do not commit or push automatically. The current `.git` metadata is incomplete; do not repair or reinitialize it without explicit direction.
- Do not use another project directory as a substitute for this repository.

## Product and safety boundary

- TRINETRA is a software-only research simulator. All GPS faults, vehicle commands, and experiments must remain inside PX4 SITL or the deterministic software simulator.
- Do not add RF transmission, SDR/jamming, physical UAV control, real flight-controller commands, or interference with real navigation systems.
- Keep simulator truth, raw sensors, PX4 estimates, TRINETRA estimates, and reported GPS separate. Never claim illustrative UI or simulated results are real flight evidence.
- Existing abstract 2D coordinates are not geographic coordinates. Do not present them as latitude/longitude.

## Architecture and security

- Keep FastAPI as the application backend. Preserve React, Vite, TypeScript, and existing component/style choices.
- Preserve the `GENERAL` / `OPERATOR` account model, Argon2 password hashing, backend role checks, server-side sessions, CSRF and origin checks, ownership checks, audit trail, scenario approvals, and immutable versions.
- Do not hardcode or print credentials. OpenAI and database credentials stay server-side; do not add secret values to frontend `VITE_` variables.
- Do not expose arbitrary shell execution through the API. Future simulator lifecycle controls must use fixed commands and validated configuration.
- Keep REST/WebSocket state authoritative. Do not invent live telemetry in the frontend.
- Avoid duplicate subsystems and public API changes unless documented with migration compatibility and regression coverage.

## Implementation and verification

- Follow phases in `TRINETRA_IMPLEMENTATION_ROADMAP.md`. Respect dependencies: verify real telemetry before final Cesium integration, validate data before training, validate single-UAV detection before adaptive multi-UAV response, and do not migrate production data before migration validation.
- Update `TRINETRA_IMPLEMENTATION_PROGRESS.md` after each milestone with files, commands, actual results, defects, blockers, and the next exact step.
- Run the relevant existing tests after code changes. Do not state a feature works unless its code path and verification support the claim.
- Current Windows commands (run from indicated directories):
  - Backend tests: from `D:\TRINETRA\backend`, `..\.venv\Scripts\python.exe -m pytest -q tests`.
  - Frontend tests: from `D:\TRINETRA\frontend`, `npm.cmd test`.
  - Frontend production build: from `D:\TRINETRA\frontend`, `npm.cmd run build`.
- Use the checked-in local Python environment when available. Do not install system packages or run privileged WSL setup commands without first identifying the exact command and asking the user to execute/approve it.
- Keep documentation explicit about `implemented`, `partial`, `proposed`, `blocked`, `tested`, and `not tested`.

## Handoff

If work must pause, save all completed work and write the exact stopping point, last successful action, remaining work, commands, test results, risks, and continuation prompt to `TRINETRA_IMPLEMENTATION_PROGRESS.md` before ending.
