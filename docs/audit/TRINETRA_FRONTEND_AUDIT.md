# TRINETRA Frontend Audit

**Status:** source-inspected; tested UI unit suite and build pass; authenticated end-to-end acceptance remains unverified.

## Architecture and user surfaces

The frontend is React 18, TypeScript, and Vite. `App.tsx` provides Overview, Mission Control, Mission Planner, Scenario Lab, Fleet, Analytics, History, and Research routes, plus login, registration, and the General Control Center. AccessPortal contains login, the Operator registration/status flow, registration management, scenario review, and audit history. Mission Control supports two explicitly separate modes: deterministic abstract simulation and read-only PX4 telemetry. The telemetry view includes Cesium and a Gazebo pose mirror.

Navigation uses client-side history state. A restored session selects the route and authenticated role; missing sessions direct protected routes to login. General navigation is role-aware, while frontend checks are not relied upon as the authorization boundary: the backend separately guards protected endpoints.

## Evidence and findings

- `npm.cmd test`: 10 files / 29 tests passed. Coverage includes app navigation, API client credentials/CSRF handling and 401-vs-403 distinction, registration receipt/status, Scenario Lab, planner UI, command decisions, PX4 view, Gazebo mirror, detector status, and telemetry stream merging.
- `npm.cmd run build`: passed TypeScript and Vite. 4,215 modules transformed; 342 static assets copied. The Cesium JavaScript bundle is 5,011.16 kB minified / 1,382.61 kB gzip and triggers Vite’s >600 kB warning.
- Integrated browser inspection showed the access portal and live system status. API and database were reported online; WebSocket was explicitly unverified because no stream was active. No account credentials were entered.
- Browser console emitted a React warning for `fetchPriority` being forwarded as an unrecognized DOM property in the overview hero image. Logged as D-04 (low).
- D-01 (high, source-supported): `Px4MissionControl` defines `selectPx4Vehicle` and `selectGazeboModel` inside the render. Telemetry updates `snapshot` at stream cadence, rerendering that component and changing callback identity. `CesiumLiveMap` and `GazeboPoseMirror` initialization effects depend on `onSelect`. Their cleanup/setup therefore runs on callback identity changes. The Gazebo effect initializes WebGL and reloads several mesh assets, increasing the impact. This is not a browser-reproduced finding because no authenticated live telemetry session was exercised.
- Responsive layout rules exist, but no systematic viewport, keyboard-only, screen-reader, or contrast audit was performed. Do not mark accessibility or responsive acceptance complete based on source inspection.

## UX and boundary assessment

Product copy repeatedly identifies the abstract simulation as non-geographic and software-only. The PX4 page marks threat detection as not validated and labels PX4 data as actual MAVLink. The Gazebo section states that native world geometry, camera/video, and physics overlays are not streamed. These are appropriate boundary cues. The Overview currently uses aspirational “autonomous aerial intelligence” language; preserve adjacent software-only and research-demonstrator qualifications.

The login portal’s diagnostic status is derived from backend health and client observations. It is not proof of a functioning authenticated session, websocket, or PX4/Gazebo connection.

## Recommendations

1. Stabilize viewer initialization callbacks and prove viewer construction/disposal counts remain constant during repeated telemetry updates (D-01).
2. Add a real browser acceptance test with explicitly authorized test accounts and isolated backend data; cover refresh, navigation, logout, role denial, and safe read-only telemetry display.
3. Evaluate the Cesium bundle split and loading strategy against actual browser performance budgets before adding a threshold-based claim.
4. Remove the invalid DOM prop warning and add accessibility/viewport checks, without changing unrelated visual design.
