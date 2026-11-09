# Database configuration and migrations

## Local default

The backend uses `backend/trinetra.db` (SQLite) when `DATABASE_URL` is unset. `TRINETRA_DB_PATH` can select a different SQLite file. SQLite keeps the existing startup behavior: it rejects legacy authentication schemas and creates missing tables from SQLAlchemy metadata. The active local database is separate from the isolated test databases used in verification.

## PostgreSQL configuration

Set `DATABASE_URL` in the backend environment to a SQLAlchemy URL such as:

```text
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE
```

The backend uses Psycopg 3. The current dependency is the pure-Python `psycopg` package, which requires a compatible `libpq` client library on the host. Install the client library through the hosting platform's supported system-package mechanism. Do not put credentials in source control, frontend variables, logs, or handoff documents. Supabase has not been configured or tested in this checkout.

## Schema upgrades

Run migrations from `D:\TRINETRA\backend` with the backend virtual environment active. The repository-root `.env` and process environment supply `DATABASE_URL` to Alembic.

```powershell
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m alembic current
```

PostgreSQL application startup checks that `alembic_version` exists and matches the repository head. It does not run `create_all` or upgrade a PostgreSQL schema at application startup. Back up and review migrations before applying them to any non-test database.

For an existing database with application tables but no `alembic_version`, do not stamp it as current without first comparing its schema and data to every migration. Take a restorable backup, verify table/column/index/constraint compatibility and row counts, then choose a reviewed baseline-adoption procedure. Never point this workflow at a populated or unknown database as an experiment.

## Current migration and verification evidence

The initial migration is `backend/alembic/versions/17ea6f9d666a_create_initial_trinetra_schema.py`. It defines the current SQLAlchemy schema. In the 2026-11-07 local validation, the migration was applied to an isolated PostgreSQL 18 cluster under `D:\TRINETRA\backend\pg-validation-cluster` on loopback port `55432`, with a separate test role/database. `alembic current` reported `17ea6f9d666a (head)`, and `alembic check` reported no new upgrade operations. The full backend suite passed against that database and against isolated SQLite test databases. This validates the local PostgreSQL adapter and schema, not Supabase, TLS configuration, cloud availability, backup/restore, production sizing, or concurrent multi-host load.

Application read-then-write paths use SQLite `BEGIN IMMEDIATE` or a PostgreSQL transaction-scoped advisory lock. That global PostgreSQL lock serializes those application paths to preserve their check-then-write behavior; it is a correctness baseline, not a throughput tuning result. Measure contention before scaling write concurrency.

## Test database isolation

Pytest uses an isolated temporary SQLite database by default. To test a separately provisioned PostgreSQL test database, set `TRINETRA_TEST_DATABASE_URL` for the test process. Tests refuse a PostgreSQL test database without the explicit `trinetra_test` database-name guard. Never set the test URL to a production or user database.

