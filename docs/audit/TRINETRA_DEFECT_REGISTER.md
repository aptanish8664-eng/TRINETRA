# TRINETRA Defect and Evidence-Gap Register

Severity describes user/product impact if the issue or gap is present. Confidence describes the strength of the cited evidence. Source-derived findings have not necessarily been reproduced live.

| ID | Severity | Area | Finding | Evidence / confidence | Status |
|---|---|---|---|---|---|
| D-01 | **High** | PX4/Cesium/Gazebo UI | Selection callback identity was recreated by the telemetry parent on each render, while Cesium and Gazebo initialization effects depended on those callbacks. | Fixed by stable parent callbacks and latest-callback refs in both viewers. Regression sends repeated telemetry snapshots, asserts stable callback identity, and verifies PX4 selection; `frontend/src/components/Px4MissionControl.test.tsx`. **Verified locally; no authenticated live viewer-initialization counters or SITL browser reproduction.** | Source fix and focused regression implemented. Live lifecycle, cleanup, and mesh-load behavior remain unverified. |
| D-02 | **Medium** | General audit export | CSV endpoint previously queried only 200 newest matching events and discarded the total count. | Export now streams bounded keyset batches with the same search/type/outcome/username/date filters. Isolated API regression exports 450 matching rows with tied timestamps and embedded comma/quotes/newline, and confirms Operator denial. **Verified.** | Fixed and regression-tested; historical actor-role gaps remain unchanged. |
| D-03 | **Medium** | Audit data | All 169 canonical audit rows lacked actor-role metadata when inspected. Historical role context is therefore absent from current records and CSV actor-role values may be empty. | Read-only SQLite query of metadata JSON; 169/169 absent; **10/10**. | Historical data limitation. Do not infer/backfill actor role from current user state. New code paths add actor role, but canonical new-event behavior was not verified. |
| D-04 | **Low** | Overview UI | React console warned that `fetchPriority` was not recognized as a DOM prop on the hero image. | Removed the nonessential prop; App regression verifies the rendered hero image has no `fetchpriority` attribute and captures no `fetchPriority` console warning. **Verified in frontend tests.** | Fixed and regression-tested. |
| D-05 | **Medium** | Auth acceptance | Original canonical account credential acceptance remains unverified. A controlled General/Operator browser workflow was subsequently exercised using a separate temporary database. | Controlled login/session restore, approval/rejection, 403 role denial, simulation WebSocket, rejected-account 401, audit entries, and logout/session 401 were observed. No original credentials were used. **9/10**. | Owner-only original-account acceptance gap; not evidence of a current auth defect. |
| D-06 | **Medium** | Runtime/integration | Current service health is observable, but Windows listener ownership and the backend's selected SQLite path are still unknown. | Browser rendered at `127.0.0.1:5173`; `/api/health` returned ready at `127.0.0.1:8000`, SQLite healthy and abstract simulator available; CIM reported PID 0/no 5173 result. WSL reports no running distributions. **9/10**. | Do not assume the live backend uses the canonical DB; no service or simulator was restarted. |
| D-07 | **Medium** | Frontend performance | Production build retains a 5,011.16 kB minified Cesium JavaScript chunk (1,382.61 kB gzip), exceeding Vite’s 600 kB warning threshold. | Current build output; **10/10**. | Performance risk; user impact not measured. |

## Explicit non-findings

- The 401/403 distinction is implemented and covered by the frontend API-client tests; this audit did not reproduce the original report on actual credentials.
- SQLite integrity is good at this snapshot; there are no FK violations.
- No evidence supports claiming the database accounts/history were lost.
- No real vehicle command, RF transmission, or GPS interference implementation was found in the audited scope; PX4 telemetry is read-only and software faults are confined to the abstract simulator.
- Role checks are enforced server-side in source. Frontend-only hiding is not the sole boundary.

## Ranking

1. D-01 live viewer lifecycle acceptance (high; source mitigation and component regression exist, but no current live source).
2. D-03 missing historical role context (medium, preservation-limited).
3. D-05, D-06, D-07 (medium original-account/runtime/performance evidence gaps).
4. D-02 and D-04 are fixed with focused regressions; neither is an open defect.
