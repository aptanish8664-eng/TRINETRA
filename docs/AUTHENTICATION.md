# Authentication, users, and scenario review

TRINETRA has one account table and exactly two application roles: `GENERAL` and `OPERATOR`. Passwords are Argon2id hashes. The browser receives an HttpOnly session cookie; server-side session rows store only a token hash and a CSRF token. The backend resolves role and status from the current database row on every protected request.

## Rebuild an existing local database

Stop the backend before migrating. The migration command checks the exact active SQLite path, creates a second timestamped backup under `backend\backups`, builds a separate database, validates foreign keys and integrity, then replaces only the configured database file.

From the `backend` directory in PowerShell:

```powershell
..\.venv\Scripts\python.exe -m app.migrations.rebuild_two_role_schema `
  --confirm-database "D:\TRINETRA\backend\trinetra.db"
```

The inspected local database was SQLite at `backend\trinetra.db`. The reviewed source contained one legacy administrator account, six old sessions, 22 runs, 279 snapshots, 160 events, seven experiments, and 47 audit events. The migration removes the old account and all sessions; it does not promote the legacy account. It preserves runs, snapshots, events, experiments, ownership rows as unassigned history, and audit events. Legacy actor references are retained only as a readable audit-name snapshot; user IDs, login identifiers, role/clearance metadata, and session credentials are not copied. If the reviewed schema or account inventory has changed, the command refuses the rebuild and requires a new inventory.

The migration backup is a recovery copy of the complete pre-migration database. It contains the legacy authentication records and therefore must be stored as sensitive data and kept access-controlled.

## Provision the first General user

There is no seeded General account, default password, or public General-registration endpoint. Once the backend database is migrated, provision the first General user interactively:

```powershell
..\.venv\Scripts\python.exe -m app.provision_general `
  --username "your-general-username" `
  --full-name "Your Name"
```

The command prompts twice for a password (12–128 characters), does not echo it, and refuses to create an account if any user already exists. Use your own chosen username and name; do not put the password in a command, environment file, or source code.

## Registration and account management

- The central registration page accepts a full name, username, optional email, password, and requested role.
- Registration creates one `PENDING` row in `users`. The password hash remains with that row; there is no duplicate account or access-request row.
- A General reviews pending requests, assigns the effective `GENERAL` or `OPERATOR` role, and approves, rejects, or holds the request. Rejection clears the unusable password hash. Pending and on-hold users cannot log in.
- General can change an approved user's role or deactivate/reactivate an account. Role changes and deactivation revoke active sessions. The last active General cannot be demoted or deactivated.
- User responses never include `password_hash`. Email remains optional.

## Scenarios and audit

Operators can save owned scenario drafts, create sandbox runs, and submit an immutable scenario version for General review. Each saved configuration is canonicalized and hashed for integrity, with a monotonically increasing per-scenario version number. A General decision applies to one exact version. A changed configuration creates a new version and requires its own review; resubmitting an unchanged version returns its existing request rather than creating a duplicate. Restricted scenario execution checks the current version, persisted approval, and configuration hash on the backend.

Persistent events use one `audit_logs` table. General can search and filter the paginated audit API and export the most recent 200 events as CSV. Audit events contain no password, password hash, session token, CSRF token, or API key.

The server-managed session cookie remains `trinetra_session`, is HttpOnly and SameSite Strict, and uses the configured `TRINETRA_SESSION_COOKIE_SECURE` setting. It defaults to `false` for plain-HTTP local development; set it explicitly to `true` behind HTTPS. The frontend defaults its API host to the same hostname used to open the frontend. In Vite development, a `VITE_API_URL` using `localhost` or `127.0.0.1` is normalized to the browser's loopback hostname when necessary, so the strict cookie is not split across those different sites. Production API URLs are not rewritten. `TRINETRA_ALLOWED_ORIGINS` must contain exact frontend origins; do not use `*` with credentialed requests.

Login returns the CSRF token for the newly created server-side session, along with the approved account's user ID, role, and `APPROVED` status. `GET /api/auth/session` returns the current session's token and re-resolved account identity on page load or when the shared frontend client needs to recover a missing or stale token. The client sends it as `X-CSRF-Token` on state-changing authenticated requests. The backend validates it against the token stored for the session identified by the HttpOnly cookie, and checks a supplied browser Origin against the configured allowlist. Safe `GET`, `HEAD`, and `OPTIONS` requests do not require a CSRF token. Login and public registration are the only state-changing auth requests that do not require an existing session token. On a CSRF rejection, the client refreshes the current session once and retries the request at most once; it does not retry other authorization failures.

Audit events created after the audit-role enhancement record the actor's `GENERAL` or `OPERATOR` role in event metadata and expose it in the General audit view and CSV export. Historical audit rows are preserved without backfilling a role that was not recorded at the time.
