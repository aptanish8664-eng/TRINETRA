# TRINETRA Frontend Audit

**Status:** source-inspected; the authenticated General/Operator workflow was browser-tested with controlled accounts in a separate temporary database. Original-account acceptance and live PX4/Gazebo browser acceptance remain unverified.

## Architecture and user surfaces

The frontend is React 18, TypeScript, and Vite. `App.tsx` provides Overview, Mission Control, Mission Planner, Scenario Lab, Fleet, Analytics, History, and Research routes, plus login, registration, and the General Control Center. AccessPortal contains login, the Operator registration/status flow, registration management, scenario review, and audit history. Mission Control supports two explicitly separate modes: deterministic abstract simulation and read-only PX4 telemetry. The telemetry view includes Cesium and a Gazebo pose mirror.

Navigation uses client-side history state. A restored session selects the route and authenticated role; missing sessions direct protected routes to login. General navigation is role-aware, while frontend checks are not relied upon as the authorization boundary: the backend separately guards protected endpoints.

## Evidence and findings

- Latest `npm.cmd test`: **10 files / 31 tests passed**. Coverage includes app navigation, API client credentials/CSRF handling and 401-vs-403 distinction, registration receipt/status, Scenario Lab, planner UI, command decisions, PX4 view, Gazebo mirror, detector status, and telemetry stream merging.
- `npm.cmd run build`: passed TypeScript and Vite. 4,215 modules transformed; 342 static assets copied. The Cesium JavaScript bundle is 5,011.16 kB minified / 1,382.61 kB gzip and triggers Vite’s >600 kB warning.
- Initial non-mutating browser inspection showed the access portal and live system status. API and database were reported online; WebSocket was unverified because no stream was active. Controlled credentialed acceptance is reported separately below.
- Supplemental browser acceptance used disposable General/Operator accounts in an isolated temporary SQLite database: General login and reload restored the session; General approved a pending Operator; approved Operator login and reload worked; Operator navigation omitted General-only controls and the General-only users API returned 403; a simulation was deployed over the authenticated WebSocket; logout revoked the session and session retrieval returned 401. A second registration was rejected through the authenticated browser-origin API and the rejected account's login returned 401. The prompt-driven Reject button could not be completed because the browser harness does not implement `window.prompt()`.
- A real registration initially exposed an optional-email defect: blank email was posted as `""` and received 422. `AccessPortal` now trims and sends `null` for blank optional email; the actual browser submission returned 201/PENDING and the frontend regression asserts the null payload.
- PX4 details now label roll/pitch/yaw as PX4-provided, separate track heading, expose GPS accuracy and raw acceleration/angular-rate/magnetic vectors with the `fields_updated` mask, and state that no independent fusion has been applied. Component regression tests use backend-shaped telemetry fixtures; the current local PX4/Gazebo source is unavailable, so no live feed was browser-verified.
- D-04 (low) was fixed by removing the nonessential hero-image `fetchPriority` prop; an App regression checks the rendered image and absence of the React warning. No authenticated browser run was performed after the change.
- D-01 was mitigated by stable parent callbacks and callback refs in the Cesium/Gazebo viewers. A repeated telemetry-snapshot regression verifies callback identity stability and vehicle selection. Actual WebGL construction/disposal counts and mesh reload behavior remain unverified in a live authenticated telemetry session.
- Responsive layout rules exist, but no systematic viewport, keyboard-only, screen-reader, or contrast audit was performed. Do not mark accessibility or responsive acceptance complete based on source inspection.

## UX and boundary assessment

Product copy repeatedly identifies the abstract simulation as non-geographic and software-only. The PX4 page marks threat detection as not validated and labels PX4 data as actual MAVLink. The Gazebo section states that native world geometry, camera/video, and physics overlays are not streamed. These are appropriate boundary cues. The Overview currently uses aspirational “autonomous aerial intelligence” language; preserve adjacent software-only and research-demonstrator qualifications.

The login portal’s diagnostic status is derived from backend health and client observations. The supplemental controlled workflow tested an authenticated simulation WebSocket, but health remains only one signal and does not prove which DB file is active or that PX4/Gazebo is connected.

## Recommendations

1. D-01 callback stability is implemented and regression-tested; prove viewer construction/disposal counts remain constant during repeated telemetry updates in an authenticated live PX4/Gazebo browser session.
2. Before claiming original-account acceptance, have the owner test the existing credentials through the normal browser login and verify role-specific pages; never copy controlled test accounts into the canonical database.
3. Evaluate the Cesium bundle split and loading strategy against actual browser performance budgets before adding a threshold-based claim.
4. D-04's invalid DOM prop warning is fixed and covered by an App regression; add accessibility/viewport checks without changing unrelated visual design.
