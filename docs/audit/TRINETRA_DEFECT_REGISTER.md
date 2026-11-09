# TRINETRA Defect and Evidence-Gap Register

Severity describes user/product impact if the issue or gap is present. Confidence describes the strength of the cited evidence. Source-derived findings have not necessarily been reproduced live.

| ID | Severity | Area | Finding | Evidence / confidence | Status |
|---|---|---|---|---|---|
| D-01 | **High** | PX4/Cesium/Gazebo UI | Selection callback identity is recreated by the telemetry parent on each render, while Cesium and Gazebo initialization effects depend on those callbacks. State updates can repeatedly tear down and recreate WebGL viewers; Gazebo also reloads X500 mesh assets in its setup effect. | Direct dataflow: [`Px4MissionControl.tsx:129`](../../frontend/src/components/Px4MissionControl.tsx#L129), [`CesiumLiveMap.tsx:82`](../../frontend/src/components/CesiumLiveMap.tsx#L82), [`GazeboPoseMirror.tsx:271`](../../frontend/src/components/GazeboPoseMirror.tsx#L271); **9/10**. No authenticated live reproduction. | Candidate functional/performance defect; verify with initialization counters before remediation. |
| D-02 | **Medium** | General audit export | CSV endpoint queries only 200 newest matching events and discards the total count. UI labels it “EXPORT CSV” without communicating truncation. | [`auth_routes.py:321`](../../backend/app/auth_routes.py#L321) calls `audit_history(limit=200, offset=0)` and ignores total; UI has no cap warning; **10/10**. | Confirmed completeness defect for histories exceeding 200 matching rows. |
| D-03 | **Medium** | Audit data | All 169 canonical audit rows lacked actor-role metadata when inspected. Historical role context is therefore absent from current records and CSV actor-role values may be empty. | Read-only SQLite query of metadata JSON; 169/169 absent; **10/10**. | Historical data limitation. Do not infer/backfill actor role from current user state. New code paths add actor role, but canonical new-event behavior was not verified. |
| D-04 | **Low** | Overview UI | React console warns that `fetchPriority` is not recognized as a DOM prop on the hero image. | Integrated browser console event and [`App.tsx:76`](../../frontend/src/App.tsx#L76); **10/10**. | Confirmed non-blocking console warning. |
| D-05 | **Medium** | Auth acceptance | Current audit did not prove original credential login, approval, role navigation, refresh, or logout in a live browser. | No credentials submitted; this was a preservation constraint. Existing frontend tests use mocks. **10/10** that acceptance was not run. | Verification gap, not evidence of broken auth. |
| D-06 | **Medium** | Runtime/integration | Current OS process/listener state and backend database path could not be established from the shell; Windows CIM process enumeration was denied and environment overrides were not read. The integrated browser reported health online. | Browser snapshot plus sandbox process error and no matching `netstat` lines in shell; **8/10**. | Runtime configuration unknown; do not restart or assume service identity. |
| D-07 | **Medium** | Frontend performance | Production build retains a 5,011.16 kB minified Cesium JavaScript chunk (1,382.61 kB gzip), exceeding Vite’s 600 kB warning threshold. | Current build output; **10/10**. | Performance risk; user impact not measured. |

## Explicit non-findings

- The 401/403 distinction is implemented and covered by the frontend API-client tests; this audit did not reproduce the original report on actual credentials.
- SQLite integrity is good at this snapshot; there are no FK violations.
- No evidence supports claiming the database accounts/history were lost.
- No real vehicle command, RF transmission, or GPS interference implementation was found in the audited scope; PX4 telemetry is read-only and software faults are confined to the abstract simulator.
- Role checks are enforced server-side in source. Frontend-only hiding is not the sole boundary.

## Ranking

1. D-01 viewer lifecycle (high).
2. D-02 incomplete General audit CSV (medium).
3. D-03 missing historical role context (medium, preservation-limited).
4. D-05, D-06, D-07 (medium acceptance/runtime/performance risks).
5. D-04 warning (low).
