# TRINETRA Authentication and Role Audit

**Status:** source and canonical database inspected read-only; controlled General/Operator workflows were exercised in a browser against a separate temporary database. Original credentials were not used, and the canonical database was not modified.

## Role model

The two role values remain exactly `GENERAL` and `OPERATOR`. `GENERAL` has administrative user management, registration review, audit reading, scenario review, all-mission read, command approval, and other explicitly assigned permissions. `OPERATOR` has own-mission read/create/control, experiment/export, AI proposal, command request, scenario create/execute permissions. Unknown roles map to no permissions. The backend independently enforces permissions; the UI is not the security boundary.

Registration records start pending with a requested role but no effective role. A General approval requires assignment of one of the two allowed roles and sets approval metadata. Rejection removes the password hash and records rejection status/reason. Hold/resume are constrained state transitions. Role changes revoke active sessions; the last active General cannot be demoted, and users cannot deactivate themselves. The browser acceptance used only controlled disposable accounts; the original General identity and credentials were preserved.

## Authentication/session source review

- Password hashes are created and verified with Argon2id (`argon2-cffi`).
- Login requires an approved account with an allowed effective role and valid password.
- A successful login creates a random opaque session token; the database stores its SHA-256 digest. The response sets an HttpOnly, SameSite Strict cookie scoped to `/api`, with configurable expiry. The local HTTP `Secure` default remains false; HTTPS deployments must configure it appropriately.
- CSRF tokens are stored with the server-side session and required for writes. Trusted Origin validation checks supplied Origin headers against the allowlist. CORS allows credentials only for the configured origins.
- Session retrieval rechecks revocation, expiry, user approval, and role. Logout revokes server-side session state and expires the cookie.
- Missing/invalid session is intended to be 401; authenticated insufficient permission is 403. Frontend API tests passed for this distinction and for credential inclusion/CSRF refresh behavior.
- Login/registration throttles are in-process. They do not coordinate across multiple workers and are not a production-scale shared rate limit.

## Canonical DB findings

Read-only inspection found one approved General and one pending Operator application. There are 23 session rows (5 currently unrevoked and unexpired), 169 audit rows, and no stored scenario approval requests. Both stored password hash strings inspected by format are Argon2id. No account identifiers or secrets are included in this report.

All 169 existing audit rows lacked `actor_role` in metadata at inspection time. Current code adds actor-role metadata on authenticated actor audit writes, but it does not rewrite history. The canonical records remain unchanged; preserve historical actor-role gaps as unknown and do not infer/backfill role from present-day account assignments.

## Controlled browser acceptance (separate temporary SQLite database)

The following paths were exercised using controlled disposable accounts, not the canonical database:

- General UI login returned 200 with role `GENERAL`; the General area and session endpoint returned 200. Reload preserved the session.
- General saw and approved a pending Operator registration. The registration moved to approved Operator status and its audit event was visible.
- Approved Operator login returned 200. Session restoration after refresh preserved the approved role. General-only navigation was absent and the Operator received HTTP 403 from a General-only users endpoint.
- An authenticated abstract simulation was created and deployed; the authenticated simulation WebSocket delivered live state.
- A second Operator registration was rejected through the browser-origin authenticated API. The isolated DB recorded two `registration_submitted` events, one `registration_approved`, and one `registration_rejected`; approval/rejection entries had a General actor ID/role, target user ID, outcome, and timestamp. Unauthenticated submissions correctly had no actor identity.
- The rejected account could not log in (401). Logout revoked the session; session retrieval returned 401.
- The browser harness does not implement `window.prompt()`, so the rejection action was validated through the actual authenticated browser-origin REST request rather than completing that prompt-driven button. This is a harness limitation, not evidence that the backend rejection workflow failed.
- A blank optional email was found to be submitted as `""` and rejected with 422. The frontend now trims the field and sends `null` when blank; a component regression test verifies the payload and a browser registration returned 201/PENDING.

These results establish a controlled local workflow, not acceptance of the original General credentials, current canonical-account access, live PX4 telemetry, or hosted deployment. The test service used a temporary database at `C:\Users\DELL\AppData\Local\Temp\trinetra-browser-acceptance-20261109-121641.db`; it must not be treated as canonical data.

## Follow-up

If manual acceptance of existing accounts is required, use the owner’s normal login process without disclosing credentials to the assistant. Do not copy test accounts or audit rows into the canonical database. Live PX4 and Gazebo acceptance requires a running, owner-authorized SITL and verified timestamps/coordinate frames; no such process was running at the latest status check.

The controlled end-to-end workflow above was run against an isolated DB. Future regression runs should continue to use disposable accounts and verify audit actor, role, target, request ID, result, and timestamp. Keep historical actor-role gaps visible as unknown; separately retain unauthorized REST and WebSocket denial tests.
