from __future__ import annotations

import json
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, delete, event, func, inspect, select, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from ..config import DATABASE_URL as CONFIGURED_DATABASE_URL, PROJECT_ROOT


DB_PATH = Path(os.environ.get("TRINETRA_DB_PATH", PROJECT_ROOT / "backend" / "trinetra.db"))


def resolve_database_url(configured_url: str, sqlite_path: Path) -> URL:
    if configured_url:
        url = make_url(configured_url)
    else:
        url = make_url(f"sqlite:///{sqlite_path.as_posix()}")
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url


def _enable_sqlite_foreign_keys(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_database_engine(url: URL):
    connect_args = {"check_same_thread": False} if url.get_backend_name() == "sqlite" else {}
    database_engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    if database_engine.dialect.name == "sqlite":
        event.listen(database_engine, "connect", _enable_sqlite_foreign_keys)
    return database_engine


def begin_write_transaction(session) -> None:
    """Serialize read-then-write operations on SQLite and PostgreSQL.

    SQLite's BEGIN IMMEDIATE reserves its single writer before these operations
    inspect state. PostgreSQL uses a transaction-scoped advisory lock with the
    same application-wide scope so checks and their subsequent writes remain
    atomic across concurrent API workers.
    """
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    elif connection.dialect.name == "postgresql":
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(72623859790382856)")
    else:
        raise RuntimeError(f"Write transaction locking is unsupported for {connection.dialect.name}")


DATABASE_URL = resolve_database_url(CONFIGURED_DATABASE_URL, DB_PATH)
if DATABASE_URL.get_backend_name() == "sqlite":
    sqlite_database = DATABASE_URL.database
    if sqlite_database and sqlite_database != ":memory:" and not sqlite_database.startswith("file:"):
        Path(sqlite_database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
engine = create_database_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    config_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="ready")
    snapshots: Mapped[list["SnapshotRecord"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    events: Mapped[list["EventRecord"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class SnapshotRecord(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    tick: Mapped[int] = mapped_column(Integer, index=True)
    state_json: Mapped[str] = mapped_column(Text)
    run: Mapped[RunRecord] = relationship(back_populates="snapshots")


class EventRecord(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    event_key: Mapped[str] = mapped_column(String, index=True)
    tick: Mapped[int] = mapped_column(Integer, index=True)
    event_json: Mapped[str] = mapped_column(Text)
    run: Mapped[RunRecord] = relationship(back_populates="events")


class ExperimentRecord(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    metadata_json: Mapped[str] = mapped_column(Text)
    results_json: Mapped[str] = mapped_column(Text)


def utcnow_naive() -> datetime:
    """Return UTC as a naive datetime for consistent SQLite comparisons."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IS NULL OR role IN ('GENERAL', 'OPERATOR')", name="ck_users_role"),
        CheckConstraint(
            "requested_role IS NULL OR requested_role IN ('GENERAL', 'OPERATOR')",
            name="ck_users_requested_role",
        ),
        CheckConstraint(
            "account_status IN ('PENDING', 'ON_HOLD', 'APPROVED', 'REJECTED', 'DEACTIVATED')",
            name="ck_users_account_status",
        ),
        CheckConstraint(
            "account_status != 'APPROVED' OR (role IN ('GENERAL', 'OPERATOR') AND password_hash IS NOT NULL)",
            name="ck_approved_users_have_role_and_password",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(100))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True, unique=True, index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    requested_role: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    account_status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    registration_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SessionRecord(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AuditLogRecord(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    target_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    request_id: Mapped[str] = mapped_column(String, index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class RunOwnerRecord(Base):
    __tablename__ = "run_owners"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)


class ExperimentOwnerRecord(Base):
    __tablename__ = "experiment_owners"
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)


class ScenarioRecord(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class ScenarioVersionRecord(Base):
    __tablename__ = "scenario_versions"
    __table_args__ = (UniqueConstraint("scenario_id", "version_number", name="uq_scenario_version_number"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    config_json: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    sandbox_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ScenarioIntentRecord(Base):
    __tablename__ = "scenario_version_intent"
    version_id: Mapped[str] = mapped_column(
        ForeignKey("scenario_versions.id", ondelete="CASCADE"), primary_key=True,
    )
    intent_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ScenarioApprovalRequestRecord(Base):
    __tablename__ = "scenario_approval_requests"
    __table_args__ = (
        UniqueConstraint("scenario_version_id", name="uq_approval_request_scenario_version"),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'CHANGES_REQUESTED')",
            name="ck_scenario_approval_status",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_version_id: Mapped[str] = mapped_column(
        ForeignKey("scenario_versions.id", ondelete="CASCADE"), index=True,
    )
    submitted_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MissionProposalRecord(Base):
    __tablename__ = "mission_proposals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    proposal_json: Mapped[str] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text)
    validation_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rejected_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class CommandDecisionRecord(Base):
    __tablename__ = "command_decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reviewer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    command: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AIUsageRecord(Base):
    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)


class AIConversationRecord(Base):
    __tablename__ = "ai_conversations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    config_json: Mapped[str] = mapped_column(Text)
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    proposal_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    proposal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("mission_proposals.id", ondelete="SET NULL"), nullable=True)
    scenario_id: Mapped[Optional[str]] = mapped_column(ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


def initialize_database() -> None:
    if engine.dialect.name == "postgresql":
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        alembic_config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
        expected_heads = set(ScriptDirectory.from_config(alembic_config).get_heads())
        with engine.connect() as connection:
            if not inspect(connection).has_table("alembic_version"):
                raise RuntimeError("PostgreSQL schema is not migrated. Run `alembic upgrade head` before starting TRINETRA.")
            current_heads = set(MigrationContext.configure(connection).get_current_heads())
        if current_heads != expected_heads:
            raise RuntimeError("PostgreSQL schema revision is not current. Run `alembic upgrade head` before starting TRINETRA.")
        return
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Unsupported TRINETRA database dialect: {0}".format(engine.dialect.name))
    existing_tables = set(inspect(engine).get_table_names())
    legacy_tables = existing_tables & {"operators", "operator_sessions", "access_requests", "audit_events"}
    if legacy_tables:
        raise RuntimeError(
            "Legacy TRINETRA database detected. Back it up and run "
            "`python -m app.migrations.rebuild_two_role_schema` before starting the backend."
        )
    Base.metadata.create_all(engine)


if os.environ.get("TRINETRA_SKIP_SCHEMA_INIT") != "1":
    initialize_database()


def add_audit_event(
    *, action: str, outcome: str, reason: str, request_id: str,
    actor_id: str | None = None, actor_name: str | None = None,
    target_type: str | None = None, target_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    with SessionLocal() as session:
        actor = session.get(UserRecord, actor_id) if actor_id else None
        event_metadata = dict(metadata or {})
        if actor is not None:
            event_metadata["actor_role"] = actor.role
        session.add(AuditLogRecord(
            id=secrets.token_hex(16), actor_user_id=actor_id,
            actor_name=actor_name or (actor.full_name if actor else None),
            event_type=action, target_type=target_type, target_id=target_id, outcome=outcome,
            reason=reason, request_id=request_id, metadata_json=json.dumps(event_metadata, sort_keys=True),
        ))
        session.commit()


def add_run_owner(run_id: str, operator_id: str) -> None:
    with SessionLocal() as session:
        session.merge(RunOwnerRecord(run_id=run_id, user_id=operator_id))
        session.commit()


def run_owner(run_id: str) -> str | None:
    with SessionLocal() as session:
        record = session.get(RunOwnerRecord, run_id)
        return record.user_id if record else None


def add_experiment_owner(experiment_id: str, operator_id: str) -> None:
    with SessionLocal() as session:
        session.merge(ExperimentOwnerRecord(experiment_id=experiment_id, user_id=operator_id))
        session.commit()


def experiment_owner(experiment_id: str) -> str | None:
    with SessionLocal() as session:
        record = session.get(ExperimentOwnerRecord, experiment_id)
        return record.user_id if record else None


def save_run(run_id: str, config: dict, status: str = "ready") -> None:
    with SessionLocal() as session:
        record = session.get(RunRecord, run_id)
        if record is None:
            session.add(RunRecord(id=run_id, config_json=json.dumps(config), status=status))
        else:
            record.status = status
        session.commit()


def save_snapshot(run_id: str, state: dict) -> None:
    with SessionLocal() as session:
        session.add(SnapshotRecord(run_id=run_id, tick=state["tick"], state_json=json.dumps(state)))
        run = session.get(RunRecord, run_id)
        if run:
            run.status = state["status"]
        session.commit()


def save_events(run_id: str, events: list[dict]) -> None:
    if not events:
        return
    with SessionLocal() as session:
        session.add_all(EventRecord(run_id=run_id, event_key=event["id"], tick=event["tick"], event_json=json.dumps(event)) for event in events)
        session.commit()


def list_runs(operator_id: str, include_all: bool = False) -> list[dict]:
    with SessionLocal() as session:
        query = select(RunRecord)
        if not include_all:
            query = query.join(RunOwnerRecord, RunOwnerRecord.run_id == RunRecord.id).where(
                RunOwnerRecord.user_id == operator_id,
            )
        rows = session.scalars(query.order_by(RunRecord.created_at.desc())).all()
        return [{"id": row.id, "created_at": row.created_at.isoformat(), "status": row.status, "config": json.loads(row.config_json)} for row in rows]


def get_replay(run_id: str, operator_id: str, include_all: bool = False) -> dict | None:
    with SessionLocal() as session:
        run = session.get(RunRecord, run_id)
        owner = session.get(RunOwnerRecord, run_id)
        if run is None or (not include_all and (owner is None or owner.user_id != operator_id)):
            return None
        snapshots = session.scalars(select(SnapshotRecord).where(SnapshotRecord.run_id == run_id).order_by(SnapshotRecord.id)).all()
        events = session.scalars(select(EventRecord).where(EventRecord.run_id == run_id).order_by(EventRecord.id)).all()
        return {"id": run.id, "created_at": run.created_at.isoformat(), "config": json.loads(run.config_json), "status": run.status,
                "snapshots": [json.loads(row.state_json) for row in snapshots], "events": [json.loads(row.event_json) for row in events]}


def save_experiment(experiment_id: str, metadata: dict, results: dict, operator_id: str) -> None:
    with SessionLocal() as session:
        session.add(ExperimentRecord(id=experiment_id, metadata_json=json.dumps(metadata), results_json=json.dumps(results)))
        session.flush()
        session.add(ExperimentOwnerRecord(experiment_id=experiment_id, user_id=operator_id))
        session.commit()


def get_experiment(experiment_id: str, operator_id: str, include_all: bool = False) -> dict | None:
    with SessionLocal() as session:
        record = session.get(ExperimentRecord, experiment_id)
        owner = session.get(ExperimentOwnerRecord, experiment_id)
        if record is None or (not include_all and (owner is None or owner.user_id != operator_id)):
            return None
        return {"id": record.id, "created_at": record.created_at.isoformat(), "metadata": json.loads(record.metadata_json), "results": json.loads(record.results_json)}


def _scenario_hash(config: dict, intent: dict | None = None) -> str:
    payload = config if intent is None else {"simulation_config": config, "mission_intent": intent}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _has_scenario_intent(intent: dict | None) -> bool:
    return bool(intent and any(
        intent.get(key) for key in (
            "mission_objectives", "proposed_tasks", "uav_role_suggestions",
            "operating_area_notes", "environment_notes", "constraints",
        )
    ))


def _scenario_version_view(session, version: ScenarioVersionRecord) -> dict:
    approval = session.scalar(
        select(ScenarioApprovalRequestRecord)
        .where(ScenarioApprovalRequestRecord.scenario_version_id == version.id)
    )
    intent_record = session.get(ScenarioIntentRecord, version.id)
    return {
        "id": version.id,
        "version_number": version.version_number,
        "config": json.loads(version.config_json),
        "intent": json.loads(intent_record.intent_json) if intent_record else {
            "mission_objectives": [], "proposed_tasks": [], "uav_role_suggestions": [],
            "operating_area_notes": "", "environment_notes": "", "constraints": [],
        },
        "config_hash": version.config_hash,
        "created_at": version.created_at.isoformat() + "Z",
        "approval": {
            "id": approval.id,
            "status": approval.status,
            "reason": approval.reason,
            "submitted_at": approval.submitted_at.isoformat() + "Z",
            "reviewed_at": approval.reviewed_at.isoformat() + "Z" if approval.reviewed_at else None,
            "reviewed_by": approval.reviewed_by,
        } if approval else None,
    }


def create_scenario(owner_user_id: str, title: str, config: dict, intent: dict | None = None) -> dict:
    if not _has_scenario_intent(intent):
        intent = None
    scenario_id = uuid.uuid4().hex
    version_id = uuid.uuid4().hex
    now = utcnow_naive()
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with SessionLocal() as session:
        session.add(ScenarioRecord(
            id=scenario_id,
            owner_user_id=owner_user_id,
            title=title.strip(),
            created_at=now,
            updated_at=now,
        ))
        session.add(ScenarioVersionRecord(
            id=version_id,
            scenario_id=scenario_id,
            version_number=1,
            config_json=config_json,
            config_hash=_scenario_hash(config, intent),
            created_by=owner_user_id,
            created_at=now,
        ))
        session.flush()
        if intent is not None:
            session.add(ScenarioIntentRecord(
                version_id=version_id,
                intent_json=json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                created_at=now,
            ))
        session.commit()
        return get_scenario(scenario_id, owner_user_id)


def save_scenario_version(
    scenario_id: str, owner_user_id: str, title: str, config: dict, intent: dict | None = None,
) -> dict | None:
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with SessionLocal() as session:
        begin_write_transaction(session)
        scenario = session.get(ScenarioRecord, scenario_id)
        if scenario is None or scenario.owner_user_id != owner_user_id:
            return None
        latest = session.scalar(
            select(ScenarioVersionRecord)
            .where(ScenarioVersionRecord.scenario_id == scenario_id)
            .order_by(ScenarioVersionRecord.version_number.desc())
            .limit(1)
        )
        latest_intent_record = session.get(ScenarioIntentRecord, latest.id) if latest else None
        latest_intent = json.loads(latest_intent_record.intent_json) if latest_intent_record else None
        if intent is not None and latest_intent is None and not _has_scenario_intent(intent):
            effective_intent = None
        else:
            effective_intent = latest_intent if intent is None else intent
        config_hash = _scenario_hash(config, effective_intent)
        scenario.title = title.strip()
        scenario.updated_at = utcnow_naive()
        if latest is None or latest.config_hash != config_hash:
            version_id = uuid.uuid4().hex
            session.add(ScenarioVersionRecord(
                id=version_id,
                scenario_id=scenario_id,
                version_number=(latest.version_number + 1) if latest else 1,
                config_json=config_json,
                config_hash=config_hash,
                created_by=owner_user_id,
            ))
            session.flush()
            if effective_intent is not None:
                session.add(ScenarioIntentRecord(
                    version_id=version_id,
                    intent_json=json.dumps(effective_intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                ))
        session.commit()
    return get_scenario(scenario_id, owner_user_id)


def get_scenario(scenario_id: str, user_id: str, include_all: bool = False) -> dict | None:
    with SessionLocal() as session:
        scenario = session.get(ScenarioRecord, scenario_id)
        if scenario is None or (not include_all and scenario.owner_user_id != user_id):
            return None
        versions = session.scalars(
            select(ScenarioVersionRecord)
            .where(ScenarioVersionRecord.scenario_id == scenario_id)
            .order_by(ScenarioVersionRecord.version_number.desc())
        ).all()
        return {
            "id": scenario.id,
            "owner_user_id": scenario.owner_user_id,
            "title": scenario.title,
            "created_at": scenario.created_at.isoformat() + "Z",
            "updated_at": scenario.updated_at.isoformat() + "Z",
            "versions": [_scenario_version_view(session, version) for version in versions],
        }


def list_scenarios(user_id: str, include_all: bool = False) -> list[dict]:
    with SessionLocal() as session:
        query = select(ScenarioRecord)
        if not include_all:
            query = query.where(ScenarioRecord.owner_user_id == user_id)
        records = session.scalars(query.order_by(ScenarioRecord.updated_at.desc())).all()
        scenarios = []
        for record in records:
            latest = session.scalar(
                select(ScenarioVersionRecord)
                .where(ScenarioVersionRecord.scenario_id == record.id)
                .order_by(ScenarioVersionRecord.version_number.desc())
                .limit(1)
            )
            if latest:
                version = _scenario_version_view(session, latest)
                scenarios.append({
                    "id": record.id,
                    "owner_user_id": record.owner_user_id,
                    "title": record.title,
                    "created_at": record.created_at.isoformat() + "Z",
                    "updated_at": record.updated_at.isoformat() + "Z",
                    "latest_version": version,
                })
        return scenarios


def submit_scenario_version(scenario_id: str, version_id: str, user_id: str) -> dict:
    with SessionLocal() as session:
        begin_write_transaction(session)
        scenario = session.get(ScenarioRecord, scenario_id)
        version = session.get(ScenarioVersionRecord, version_id)
        if scenario is None or version is None or scenario.owner_user_id != user_id or version.scenario_id != scenario_id:
            raise LookupError("Scenario version not found")
        latest_id = session.scalar(
            select(ScenarioVersionRecord.id)
            .where(ScenarioVersionRecord.scenario_id == scenario_id)
            .order_by(ScenarioVersionRecord.version_number.desc())
            .limit(1)
        )
        if version.id != latest_id:
            raise ValueError("Only the latest scenario version can be submitted")
        existing = session.scalar(
            select(ScenarioApprovalRequestRecord)
            .where(ScenarioApprovalRequestRecord.scenario_version_id == version_id)
        )
        if existing is None:
            existing = ScenarioApprovalRequestRecord(
                id=uuid.uuid4().hex,
                scenario_version_id=version_id,
                submitted_by=user_id,
                status="PENDING",
            )
            session.add(existing)
            session.commit()
        return {
            "id": existing.id,
            "status": existing.status,
            "scenario_id": scenario_id,
            "scenario_version_id": version_id,
        }


def list_scenario_approval_requests(*, pending_only: bool = False) -> list[dict]:
    with SessionLocal() as session:
        query = select(ScenarioApprovalRequestRecord)
        if pending_only:
            query = query.where(ScenarioApprovalRequestRecord.status == "PENDING")
        requests = session.scalars(
            query.order_by(ScenarioApprovalRequestRecord.submitted_at.desc())
        ).all()
        items = []
        for request in requests:
            version = session.get(ScenarioVersionRecord, request.scenario_version_id)
            scenario = session.get(ScenarioRecord, version.scenario_id) if version else None
            submitter = session.get(UserRecord, request.submitted_by)
            reviewer = session.get(UserRecord, request.reviewed_by) if request.reviewed_by else None
            if version and scenario:
                item = _scenario_version_view(session, version)
                items.append({
                    "id": request.id,
                    "scenario_id": scenario.id,
                    "scenario_title": scenario.title,
                    "scenario_version_id": version.id,
                    "version_number": version.version_number,
                    "config": item["config"],
                    "intent": item["intent"],
                    "config_hash": version.config_hash,
                    "owner_user_id": scenario.owner_user_id,
                    "owner_name": submitter.full_name if submitter else "Unknown user",
                    "status": request.status,
                    "reason": request.reason,
                    "reviewer_name": reviewer.full_name if reviewer else None,
                    "submitted_at": request.submitted_at.isoformat() + "Z",
                    "reviewed_at": request.reviewed_at.isoformat() + "Z" if request.reviewed_at else None,
                    "sandbox_run_id": version.sandbox_run_id,
                    "latest_version": version.version_number == session.scalar(
                        select(func.max(ScenarioVersionRecord.version_number))
                        .where(ScenarioVersionRecord.scenario_id == scenario.id)
                    ),
                })
        return items


def review_scenario_request(
    request_id: str, reviewer_id: str, decision: str, reason: str,
) -> dict:
    status_map = {
        "approve": "APPROVED",
        "reject": "REJECTED",
        "request_changes": "CHANGES_REQUESTED",
    }
    if decision not in status_map:
        raise ValueError("Unsupported scenario review decision")
    with SessionLocal() as session:
        begin_write_transaction(session)
        approval = session.get(ScenarioApprovalRequestRecord, request_id)
        if approval is None:
            raise LookupError("Scenario approval request not found")
        if approval.status != "PENDING":
            raise ValueError("Scenario approval request is no longer pending")
        if approval.submitted_by == reviewer_id:
            raise ValueError("A user cannot review their own scenario")
        approval.status = status_map[decision]
        approval.reason = reason.strip()
        approval.reviewed_by = reviewer_id
        approval.reviewed_at = utcnow_naive()
        session.commit()
        return {
            "id": approval.id,
            "scenario_version_id": approval.scenario_version_id,
            "status": approval.status,
            "reason": approval.reason,
            "reviewed_at": approval.reviewed_at.isoformat() + "Z",
        }


def approved_scenario_config(scenario_id: str, version_id: str) -> dict | None:
    with SessionLocal() as session:
        scenario = session.get(ScenarioRecord, scenario_id)
        version = session.get(ScenarioVersionRecord, version_id)
        if scenario is None or version is None or version.scenario_id != scenario_id:
            return None
        latest_id = session.scalar(
            select(ScenarioVersionRecord.id)
            .where(ScenarioVersionRecord.scenario_id == scenario_id)
            .order_by(ScenarioVersionRecord.version_number.desc())
            .limit(1)
        )
        approval = session.scalar(
            select(ScenarioApprovalRequestRecord)
            .where(ScenarioApprovalRequestRecord.scenario_version_id == version_id)
        )
        if latest_id != version_id or approval is None or approval.status != "APPROVED":
            return None
        config = json.loads(version.config_json)
        intent_record = session.get(ScenarioIntentRecord, version.id)
        intent = json.loads(intent_record.intent_json) if intent_record else None
        if _scenario_hash(config, intent) != version.config_hash:
            return None
        return config


def attach_scenario_sandbox_run(scenario_id: str, version_id: str, user_id: str, run_id: str) -> bool:
    with SessionLocal() as session:
        result = session.execute(
            update(ScenarioVersionRecord)
            .where(
                ScenarioVersionRecord.id == version_id,
                ScenarioVersionRecord.scenario_id == scenario_id,
                ScenarioVersionRecord.created_by == user_id,
            )
            .values(sandbox_run_id=run_id)
        )
        session.commit()
        return result.rowcount == 1


def list_my_scenario_approval_requests(user_id: str) -> list[dict]:
    with SessionLocal() as session:
        request_ids = session.scalars(
            select(ScenarioApprovalRequestRecord.id)
            .where(ScenarioApprovalRequestRecord.submitted_by == user_id)
            .order_by(ScenarioApprovalRequestRecord.submitted_at.desc())
        ).all()
    all_requests = {item["id"]: item for item in list_scenario_approval_requests()}
    return [all_requests[request_id] for request_id in request_ids if request_id in all_requests]


def delete_draft_scenario(scenario_id: str, user_id: str) -> bool:
    with SessionLocal() as session:
        begin_write_transaction(session)
        scenario = session.get(ScenarioRecord, scenario_id)
        if scenario is None or scenario.owner_user_id != user_id:
            return False
        approval_exists = session.scalar(
            select(ScenarioApprovalRequestRecord.id)
            .join(ScenarioVersionRecord, ScenarioVersionRecord.id == ScenarioApprovalRequestRecord.scenario_version_id)
            .where(ScenarioVersionRecord.scenario_id == scenario_id)
            .limit(1)
        )
        if approval_exists:
            raise ValueError("Submitted scenarios cannot be deleted")
        session.execute(delete(ScenarioVersionRecord).where(ScenarioVersionRecord.scenario_id == scenario_id))
        session.delete(scenario)
        session.commit()
        return True


def create_mission_proposal(operator_id: str, status: str, proposal: dict, config: dict, issues: list[str]) -> str:
    proposal_id = uuid.uuid4().hex
    with SessionLocal() as session:
        session.add(MissionProposalRecord(
            id=proposal_id, operator_id=operator_id, status=status,
            proposal_json=json.dumps(proposal, sort_keys=True),
            config_json=json.dumps(config, sort_keys=True),
            validation_json=json.dumps(issues),
            expires_at=utcnow_naive() + timedelta(days=7) if status == "pending_review" else None,
        ))
        session.commit()
    return proposal_id


def create_ai_conversation(operator_id: str, config: dict) -> str:
    conversation_id = uuid.uuid4().hex
    now = utcnow_naive()
    with SessionLocal() as session:
        session.add(AIConversationRecord(
            id=conversation_id, operator_id=operator_id, status="collecting",
            config_json=json.dumps(config, sort_keys=True),
            messages_json="[]",
            created_at=now, updated_at=now, expires_at=now + timedelta(days=7),
        ))
        session.commit()
    return conversation_id


def get_ai_conversation(conversation_id: str, operator_id: str) -> dict | None:
    with SessionLocal() as session:
        record = session.get(AIConversationRecord, conversation_id)
        if record is None or record.operator_id != operator_id or record.expires_at <= utcnow_naive():
            return None
        return {
            "id": record.id, "status": record.status,
            "operator_config": json.loads(record.config_json),
            "messages": json.loads(record.messages_json),
            "proposal": json.loads(record.proposal_json) if record.proposal_json else None,
            "proposal_id": record.proposal_id,
            "scenario_id": record.scenario_id,
            "expires_at": record.expires_at.isoformat() + "Z",
        }


def update_ai_conversation(
    conversation_id: str, operator_id: str, messages: list[dict], status: str,
    proposal: dict | None = None, proposal_id: str | None = None,
) -> bool:
    with SessionLocal() as session:
        record = session.get(AIConversationRecord, conversation_id)
        if record is None or record.operator_id != operator_id or record.expires_at <= utcnow_naive():
            return False
        if record.status != "collecting":
            return False
        record.messages_json = json.dumps(messages, ensure_ascii=False)
        record.status = status
        record.proposal_json = json.dumps(proposal, sort_keys=True) if proposal is not None else None
        record.proposal_id = proposal_id
        record.updated_at = utcnow_naive()
        session.commit()
        return True


def transfer_ai_conversation_to_scenario(
    conversation_id: str, operator_id: str, intent: dict,
) -> dict | None:
    with SessionLocal() as session:
        record = session.get(AIConversationRecord, conversation_id)
        if record is None or record.operator_id != operator_id or record.expires_at <= utcnow_naive():
            return None
        if record.status != "ready_for_review" or not record.proposal_json:
            raise ValueError("Conversation is not ready for scenario transfer")
        if record.scenario_id:
            return get_scenario(record.scenario_id, operator_id)
        config = json.loads(record.config_json)
        proposal = json.loads(record.proposal_json)
        scenario_id = uuid.uuid4().hex
        version_id = uuid.uuid4().hex
        now = utcnow_naive()
        title = (proposal.get("mission_objectives") or ["AI-assisted abstract scenario"])[0].strip()[:120]
        session.add(ScenarioRecord(
            id=scenario_id, owner_user_id=operator_id, title=title or "AI-assisted abstract scenario",
            created_at=now, updated_at=now,
        ))
        session.add(ScenarioVersionRecord(
            id=version_id, scenario_id=scenario_id, version_number=1,
            config_json=json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            config_hash=_scenario_hash(config, intent), created_by=operator_id, created_at=now,
        ))
        session.flush()
        session.add(ScenarioIntentRecord(
            version_id=version_id,
            intent_json=json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            created_at=now,
        ))
        record.scenario_id = scenario_id
        session.commit()
        return get_scenario(scenario_id, operator_id)


def get_mission_proposal(proposal_id: str, operator_id: str, include_all: bool = False) -> dict | None:
    with SessionLocal() as session:
        record = session.get(MissionProposalRecord, proposal_id)
        if record is None or (not include_all and record.operator_id != operator_id):
            return None
        return {
            "id": record.id, "operator_id": record.operator_id, "status": record.status,
            "proposal": json.loads(record.proposal_json), "operator_config": json.loads(record.config_json),
            "validation_issues": json.loads(record.validation_json), "run_id": record.run_id,
            "decided_by": record.approved_by or record.rejected_by,
            "decision_reason": record.decision_reason,
            "expires_at": record.expires_at.isoformat() + "Z" if record.expires_at else None,
        }


def list_pending_mission_proposals() -> list[dict]:
    with SessionLocal() as session:
        records = session.scalars(
            select(MissionProposalRecord)
            .where(
                MissionProposalRecord.status == "pending_review",
                MissionProposalRecord.expires_at > utcnow_naive(),
            )
            .order_by(MissionProposalRecord.created_at)
        ).all()
        return [{
            "id": record.id, "operator_id": record.operator_id, "status": record.status,
            "proposal": json.loads(record.proposal_json),
            "operator_config": json.loads(record.config_json),
            "validation_issues": json.loads(record.validation_json), "run_id": record.run_id,
            "created_at": record.created_at.isoformat() + "Z",
            "expires_at": record.expires_at.isoformat() + "Z" if record.expires_at else None,
        } for record in records]


def expire_pending_mission_proposals() -> list[tuple[str, str]]:
    now = utcnow_naive()
    with SessionLocal() as session:
        records = session.scalars(
            select(MissionProposalRecord).where(
                MissionProposalRecord.status == "pending_review",
                MissionProposalRecord.expires_at <= now,
            )
        ).all()
        expired = [(record.id, record.operator_id) for record in records]
        if expired:
            session.execute(
                update(MissionProposalRecord)
                .where(
                    MissionProposalRecord.status == "pending_review",
                    MissionProposalRecord.expires_at <= now,
                )
                .values(status="expired", decision_reason="Approval request expired after seven days.")
            )
            session.commit()
        return expired


def reject_mission_proposal(
    proposal_id: str, requester_id: str, reviewer_id: str, reason: str,
) -> bool:
    with SessionLocal() as session:
        begin_write_transaction(session)
        result = session.execute(
            update(MissionProposalRecord)
            .where(
                MissionProposalRecord.id == proposal_id,
                MissionProposalRecord.operator_id == requester_id,
                MissionProposalRecord.status == "pending_review",
                MissionProposalRecord.expires_at > utcnow_naive(),
            )
            .values(
                status="rejected", rejected_by=reviewer_id,
                rejected_at=utcnow_naive(), decision_reason=reason,
            )
        )
        session.commit()
        return result.rowcount == 1


def claim_mission_proposal_approval(proposal_id: str, requester_id: str) -> bool:
    with SessionLocal() as session:
        begin_write_transaction(session)
        result = session.execute(
            update(MissionProposalRecord)
            .where(MissionProposalRecord.id == proposal_id,
                   MissionProposalRecord.operator_id == requester_id,
                   MissionProposalRecord.status == "pending_review",
                   MissionProposalRecord.expires_at > utcnow_naive())
            .values(status="approving")
        )
        session.commit()
        return result.rowcount == 1


def finish_mission_proposal_approval(
    proposal_id: str, requester_id: str, approver_id: str, run_id: str,
) -> None:
    with SessionLocal() as session:
        session.execute(
            update(MissionProposalRecord).where(
                MissionProposalRecord.id == proposal_id,
                MissionProposalRecord.operator_id == requester_id,
                MissionProposalRecord.status == "approving",
            ).values(status="approved", approved_by=approver_id, approved_at=utcnow_naive(), run_id=run_id)
        )
        session.commit()


def fail_mission_proposal_approval(proposal_id: str, requester_id: str) -> None:
    with SessionLocal() as session:
        session.execute(
            update(MissionProposalRecord).where(
                MissionProposalRecord.id == proposal_id,
                MissionProposalRecord.operator_id == requester_id,
                MissionProposalRecord.status == "approving",
            ).values(status="approval_failed")
        )
        session.commit()


def reserve_ai_usage(operator_id: str, endpoint: str, model: str, input_chars: int,
                     request_limit: int, output_reservation: int, daily_output_limit: int) -> str:
    from datetime import timedelta

    now = utcnow_naive()
    hour_ago = now - timedelta(hours=1)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    usage_id = uuid.uuid4().hex
    with SessionLocal() as session:
        begin_write_transaction(session)
        session.execute(delete(AIUsageRecord).where(AIUsageRecord.occurred_at < now - timedelta(days=90)))
        recent_count = session.scalar(select(func.count()).select_from(AIUsageRecord).where(
            AIUsageRecord.operator_id == operator_id, AIUsageRecord.occurred_at >= hour_ago
        )) or 0
        daily_reserved = session.scalar(select(func.coalesce(func.sum(
            AIUsageRecord.output_tokens + AIUsageRecord.reserved_output_tokens
        ), 0)).where(
            AIUsageRecord.operator_id == operator_id, AIUsageRecord.occurred_at >= day_start
        )) or 0
        if recent_count >= request_limit:
            session.rollback()
            raise ValueError("ai_rate_limit")
        if daily_reserved + output_reservation > daily_output_limit:
            session.rollback()
            raise ValueError("ai_usage_limit")
        session.add(AIUsageRecord(
            id=usage_id, operator_id=operator_id, endpoint=endpoint, model=model,
            occurred_at=now, input_chars=input_chars,
            reserved_output_tokens=output_reservation, succeeded=False,
        ))
        session.commit()
    return usage_id


def finish_ai_usage(usage_id: str, output_tokens: int, succeeded: bool) -> None:
    with SessionLocal() as session:
        usage = session.get(AIUsageRecord, usage_id)
        if usage is not None:
            usage.output_tokens = max(0, int(output_tokens))
            usage.reserved_output_tokens = 0
            usage.succeeded = succeeded
            session.commit()


def ai_usage_remaining(operator_id: str, request_limit: int, daily_output_limit: int) -> tuple[int, int]:
    from datetime import timedelta

    now = utcnow_naive()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_ago = now - timedelta(hours=1)
    with SessionLocal() as session:
        requests = session.scalar(select(func.count()).select_from(AIUsageRecord).where(
            AIUsageRecord.operator_id == operator_id, AIUsageRecord.occurred_at >= hour_ago
        )) or 0
        used_tokens = session.scalar(select(func.coalesce(func.sum(
            AIUsageRecord.output_tokens + AIUsageRecord.reserved_output_tokens
        ), 0)).where(
            AIUsageRecord.operator_id == operator_id, AIUsageRecord.occurred_at >= day_start
        )) or 0
    return max(0, request_limit - requests), max(0, daily_output_limit - used_tokens)
