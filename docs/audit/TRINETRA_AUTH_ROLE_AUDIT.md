# TRINETRA Authentication and Role Audit

**Status:** source and canonical database inspected read-only; no original credentials were used; end-to-end login/approval is not certified by this audit.

## Role model

The two role values remain exactly `GENERAL` and `OPERATOR`. `GENERAL` has administrative user management, registration review, audit reading, scenario review, all-mission read, command approval, and other explicitly assigned permissions. `OPERATOR` has own-mission read/create/control, experiment/export, AI proposal, command request, scenario create/execute permissions. Unknown roles map to no permissions. The backend independently enforces permissions; the UI is not the security boundary.

Registration records start pending with a requested role but no effective role. A General approval requires assignment of one of the two allowed roles and sets approval metadata. Rejection removes the password hash and records rejection status/reason. Hold/resume are constrained state transitions. Role changes revoke active sessions; the last active General cannot be demoted, and users cannot deactivate themselves.

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

All 169 existing audit rows lacked `actor_role` in metadata at inspection time. Current code adds actor-role metadata on authenticated actor audit writes, but it does not rewrite history. New behavior has not been verified against a newly generated record in the canonical database during this audit. Preserve the historical records and do not infer/backfill role from present-day account assignments.

## Acceptance not performed

No existing General or Operator credentials were entered. Therefore this audit does **not** certify original credential authentication, session persistence across a live refresh, General approval/rejection on the canonical DB, approved-Operator navigation, or browser logout. This is intentional preservation, not evidence that login is broken. Use controlled, owner-authorized test accounts and an isolated test database for end-to-end acceptance; do not create or alter accounts in the canonical DB as part of this audit.

## Follow-up

Run the end-to-end role/registration workflow against an isolated DB and explicitly authorized test accounts. Verify audit event actor, role, target, request ID, result, and timestamp. Separately test unauthorized REST and WebSocket requests. Keep historical actor-role gaps visible as unknown.
