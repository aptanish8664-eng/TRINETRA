from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


os.environ["TRINETRA_SKIP_SCHEMA_INIT"] = "1"

from app.persistence.database import Base, DB_PATH, engine  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402


PRESERVED_TABLES = ("runs", "snapshots", "events", "experiments")
OBSOLETE_DATA_TABLES = ("mission_proposals", "command_decisions", "ai_usage")
NEW_SCHEMA_TABLES = (
    "users", "sessions", "audit_logs", "scenarios", "scenario_versions",
    "scenario_approval_requests",
)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def _backup_database(source: Path) -> Path:
    backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{source.stem}-pre-two-role-rebuild-{timestamp}.db"
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_db:
        with sqlite3.connect(backup_path) as backup_db:
            source_db.backup(backup_db)
    with sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True) as backup_db:
        integrity = backup_db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Backup integrity check failed: {integrity}")
    return backup_path


def _clean_legacy_metadata(value: str, legacy_user_ids: set[str]) -> str:
    try:
        metadata = json.loads(value or "{}")
    except json.JSONDecodeError:
        return "{}"
    forbidden_keys = {
        "role", "clearance", "requested_role", "requested_clearance",
        "identifier", "attempted_identifier", "attempted_username",
        "username", "security_id", "actor_security_id", "operator_id",
    }

    def clean(item):
        if isinstance(item, dict):
            return {
                key: clean(child)
                for key, child in item.items()
                if key.casefold() not in forbidden_keys
            }
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, str) and item in legacy_user_ids:
            return "[legacy account removed]"
        return item

    return json.dumps(clean(metadata), sort_keys=True)


def migrate_database(database_path: Path) -> tuple[Path, dict[str, int]]:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"Database file does not exist: {database_path}")
    if Path(str(database_path) + "-wal").exists() or Path(str(database_path) + "-shm").exists():
        raise RuntimeError("Stop the backend and remove no files manually; a SQLite WAL sidecar is present.")

    probe = sqlite3.connect(database_path, timeout=1)
    try:
        probe.execute("BEGIN EXCLUSIVE")
        probe.rollback()
    except sqlite3.OperationalError as exc:
        raise RuntimeError("Database is locked. Stop the backend before migration.") from exc
    finally:
        probe.close()

    backup_path = _backup_database(database_path)
    temporary_path = database_path.with_name(f"{database_path.stem}.rebuild.tmp.db")
    if temporary_path.exists():
        raise FileExistsError(f"Migration output already exists; inspect it before retrying: {temporary_path}")

    source_uri = f"file:{database_path.as_posix()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    source.row_factory = sqlite3.Row
    target_engine = create_engine(f"sqlite:///{temporary_path.as_posix()}")
    try:
        tables = _table_names(source)
        required = {
            "operators", "operator_sessions", "access_requests", "audit_events",
            "run_owners", "experiment_owners", *PRESERVED_TABLES,
        }
        if not required.issubset(tables):
            raise RuntimeError(f"Database schema differs from the inspected local schema: missing {sorted(required - tables)}")
        for table in NEW_SCHEMA_TABLES:
            if table in tables and _count(source, table):
                raise RuntimeError(
                    f"{table} contains data that is not covered by the reviewed migration plan. "
                    "Preserve and map it before retrying."
                )

        operator_rows = source.execute(
            "SELECT id, display_name, role FROM operators"
        ).fetchall()
        if len(operator_rows) != 1 or operator_rows[0]["role"] in {"GENERAL", "OPERATOR"}:
            raise RuntimeError(
                "The legacy account inventory changed since inspection. Re-inspect the database and make a new migration plan."
            )
        for table in ("access_requests", *OBSOLETE_DATA_TABLES):
            if table in tables and _count(source, table):
                raise RuntimeError(
                    f"{table} contains data not present in the reviewed inventory. Preserve and map it before retrying."
                )

        source_counts = {table: _count(source, table) for table in PRESERVED_TABLES}
        source_counts["audit_events"] = _count(source, "audit_events")
        Base.metadata.create_all(target_engine)
        target = sqlite3.connect(temporary_path)
        target.row_factory = sqlite3.Row
        try:
            target.execute("PRAGMA foreign_keys=ON")
            target.execute("BEGIN IMMEDIATE")
            for table in PRESERVED_TABLES:
                columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
                names = ", ".join(f'"{column}"' for column in columns)
                placeholders = ", ".join("?" for _ in columns)
                rows = source.execute(f'SELECT {names} FROM "{table}"').fetchall()
                target.executemany(
                    f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                    [tuple(row[column] for column in columns) for row in rows],
                )

            for row in source.execute("SELECT run_id FROM run_owners"):
                target.execute(
                    "INSERT INTO run_owners (run_id, user_id) VALUES (?, NULL)",
                    (row["run_id"],),
                )
            for row in source.execute("SELECT experiment_id FROM experiment_owners"):
                target.execute(
                    "INSERT INTO experiment_owners (experiment_id, user_id) VALUES (?, NULL)",
                    (row["experiment_id"],),
                )

            legacy_user_ids = {row["id"] for row in operator_rows}
            names = {row["id"]: row["display_name"] for row in operator_rows}
            for row in source.execute("SELECT * FROM audit_events"):
                actor_name = names.get(row["actor_id"])
                target_id = row["target_id"]
                if row["target_type"] == "operator" and target_id in legacy_user_ids:
                    target_id = None
                target.execute(
                    """
                    INSERT INTO audit_logs
                    (id, actor_user_id, actor_name, event_type, target_type, target_id,
                     outcome, reason, request_id, metadata_json, created_at)
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"], actor_name, row["action"], row["target_type"], target_id,
                        row["outcome"], row["reason"], row["request_id"],
                        _clean_legacy_metadata(row["metadata_json"], legacy_user_ids),
                        row["occurred_at"],
                    ),
                )
            target.commit()
            foreign_key_errors = target.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise RuntimeError(f"Foreign-key validation failed: {foreign_key_errors[:5]}")
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"New database integrity check failed: {integrity}")
            rebuilt_tables = _table_names(target)
            obsolete = {"operators", "operator_sessions", "access_requests", "audit_events"}
            if rebuilt_tables & obsolete:
                raise RuntimeError(f"Obsolete tables remain in the rebuilt schema: {sorted(rebuilt_tables & obsolete)}")
            if _count(target, "users") != 0 or _count(target, "sessions") != 0:
                raise RuntimeError("Legacy users or sessions were unexpectedly copied.")
            verified_counts = {table: _count(target, table) for table in PRESERVED_TABLES}
            verified_counts["audit_logs"] = _count(target, "audit_logs")
            if any(verified_counts[table] != source_counts[table] for table in PRESERVED_TABLES):
                raise RuntimeError("Preserved mission or experiment row counts do not match the source database.")
            if verified_counts["audit_logs"] != source_counts["audit_events"]:
                raise RuntimeError("Audit event count changed during migration.")
        except Exception:
            target.rollback()
            raise
        finally:
            target.close()
        target_engine.dispose()
        source.close()
        engine.dispose()
        os.replace(temporary_path, database_path)
        return backup_path, verified_counts
    except Exception:
        source.close()
        target_engine.dispose()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Back up and migrate the inspected local SQLite database to the two-role schema."
    )
    parser.add_argument(
        "--confirm-database",
        required=True,
        help="Must exactly match the active SQLite path shown by the backend configuration.",
    )
    args = parser.parse_args()
    database_path = Path(DB_PATH).resolve()
    if args.confirm_database != str(database_path):
        print(f"Database path confirmation did not match: {database_path}", file=sys.stderr)
        return 2
    try:
        backup_path, counts = migrate_database(database_path)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Migrated SQLite database: {database_path}")
    print(f"Backup: {backup_path}")
    print(f"Preserved records: {counts}")
    print("Removed legacy users, access-request tables, auth sessions, and legacy role/clearance assignments.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
