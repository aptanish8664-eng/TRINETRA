# TRINETRA Database and Security Audit

**Status:** source review and read-only canonical SQLite inspection completed; no migration, backup creation, database mutation, or credential use occurred.

## Canonical database snapshot

`backend/trinetra.db` was opened in SQLite read-only mode. Results:

| Check/table | Result |
|---|---:|
| SQLite integrity check | `ok` |
| Foreign-key violations | 0 |
| Application tables | 17 |
| Users | 2 |
| Sessions | 23 (5 active by expiry/revocation fields; 12 expired; 6 revoked) |
| Audit logs | 169 (69 success; 90 denied; 10 failed) |
| Runs / snapshots / events | 35 / 659 / 368 |
| Experiments / AI usage | 10 / 4 |
| Scenarios / versions / scenario approvals | 0 / 0 / 0 |

The two account roles/statuses are one approved General and one pending Operator request. Password hash formats observed were Argon2id. No user names, IDs, plaintext credentials, session tokens, or hash strings are included.

## Schema and data protection

The ORM defines role/status check constraints, foreign keys, ownership records, session token digests, scenario/version/approval tables, AI usage, and audit metadata. SQLite foreign keys are enabled. The SQLite runtime default is the canonical file; `TRINETRA_DB_PATH` and `DATABASE_URL` can override it. PostgreSQL uses Psycopg 3 and Alembic startup revision validation. Prior local isolated PostgreSQL test/migration evidence is documented, but this audit did not connect to Supabase or any cloud database.

The database was not changed. Do not run first-General bootstrap, legacy rebuild, migration, seed, or test reset against the populated canonical database. Pytest’s fixture points to an isolated temporary database by default; preserve that guard.

## Security evidence and limitations

- Password hashing and session token storage use Argon2id and SHA-256 token digests, respectively. Session records are revocable and expire.
- Origin checks, credentialed CORS allowlisting, CSRF, backend permission checks, owner checks, input bounds, and structured HTTP request logging are present in inspected source.
- Audit reads are restricted to `AUDIT_READ`; General has that permission. The API supports pagination. CSV export now streams the complete filtered result in bounded keyset batches; an isolated regression exported 450 matching rows with filtering and CSV escaping (D-02 fixed).
- None of the 169 canonical historical audit rows had `actor_role` in metadata (D-03). Current actor role fields are not retroactively reconstructed.
- Login and registration throttles are in-process and are not shared across multiple workers. MFA is not implemented.
- Environment overrides for the active backend were not inspected, so current process-to-file selection is unconfirmed. Keep production credentials and environment contents out of audit logs.

The latest full backend suite passed **140 tests** using isolated test databases. Controlled browser authentication/registration acceptance used a separate temporary database. Neither result involved a write to the canonical file.

## Safe next steps

If any migration or environment change is later required, first obtain an owner-reviewed backup and a non-destructive migration plan, compare schema/index/constraint/row counts and foreign keys, validate only on an isolated copy, then request approval before applying anything to the canonical DB. No such work is authorized or recommended as part of this report-only audit.
