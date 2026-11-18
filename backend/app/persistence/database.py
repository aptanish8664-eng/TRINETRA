from __future__ import annotations

import json
import hashlib
import os
import secrets
import time
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


class MissionDeploymentRecord(Base):
    __tablename__ = "mission_deployments"
    __table_args__ = (UniqueConstraint("scenario_version_id", name="uq_deployment_scenario_version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    scenario_version_id: Mapped[str] = mapped_column(
        ForeignKey("scenario_versions.id", ondelete="CASCADE"), index=True,
    )
    deployed_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mission_id: Mapped[str] = mapped_column(String, index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class AttackExperimentRecord(Base):
    """Attack Lab experiment: pinned to an exact schema-2 scenario version; never embedded in the mission plan."""
    __tablename__ = "attack_experiments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    scenario_version_id: Mapped[str] = mapped_column(ForeignKey("scenario_versions.id", ondelete="CASCADE"), index=True)
    preset_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    seed: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), index=True, default="draft")
    config_json: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class ExperimentResultRecord(Base):
    """A stored result of running an Attack Lab experiment. It deliberately has no foreign key to the experiment: deleting an
    experiment keeps its results (they are then marked as coming from a deleted experiment). The runner that writes these
    does not exist yet; the data rules are in place and tested."""
    __tablename__ = "experiment_results"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String, index=True)
    experiment_name: Mapped[str] = mapped_column(String(120))
    owner_user_id: Mapped[str] = mapped_column(String, index=True)
    scenario_id: Mapped[str] = mapped_column(String)
    scenario_version_id: Mapped[str] = mapped_column(String)
    config_hash: Mapped[str] = mapped_column(String(64))
    config_json: Mapped[str] = mapped_column(Text)          # the exact attack definition that produced the result
    payload_json: Mapped[str] = mapped_column(Text)
    experiment_deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class ResearchDecisionRecord(Base):
    """An operator's chosen response to a threat assessment. Recorded only; nothing is executed."""
    __tablename__ = "research_decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    username: Mapped[str] = mapped_column(String(64))
    aircraft_id: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str] = mapped_column(String(40))
    handover_candidate: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(24))
    source_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    tier: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    threat_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class MissionExecutionRecord(Base):
    """One run of a deployed mission on the managed PX4 SITL instance (software simulation only)."""
    __tablename__ = "mission_executions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("mission_deployments.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    scenario_version_id: Mapped[str] = mapped_column(ForeignKey("scenario_versions.id", ondelete="CASCADE"), index=True)
    aircraft_id: Mapped[str] = mapped_column(String(16), index=True)
    instance_number: Mapped[int] = mapped_column(Integer)
    started_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    started_by_name: Mapped[str] = mapped_column(String(120))
    started_by_role: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(24), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    progress_json: Mapped[str] = mapped_column(Text, default="{}")
    last_event_index: Mapped[int] = mapped_column(Integer, default=-1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MissionExecutionEventRecord(Base):
    """Every command and state change of an execution: who, when, what. Append-only."""
    __tablename__ = "mission_execution_events"
    __table_args__ = (UniqueConstraint("execution_id", "executor_index", name="uq_execution_event_index"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("mission_executions.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # user | backend | executor
    type: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    actor_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    executor_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


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
            "mission_objectives", "mission_description", "proposed_tasks", "uav_role_suggestions",
            "operating_area_notes", "environment_notes", "constraints", "geographic_mission",
            "uav_fault_assignments", "planner_conversation", "mission_plan",
        )
    ))


def _scenario_version_view(session, version: ScenarioVersionRecord) -> dict:
    approval = session.scalar(
        select(ScenarioApprovalRequestRecord)
        .where(ScenarioApprovalRequestRecord.scenario_version_id == version.id)
    )
    intent_record = session.get(ScenarioIntentRecord, version.id)
    intent = json.loads(intent_record.intent_json) if intent_record else {
        "mission_objectives": [], "proposed_tasks": [], "uav_role_suggestions": [],
        "mission_description": "", "operating_area_notes": "", "environment_notes": "", "constraints": [],
        "geographic_mission": None,
    }
    intent.setdefault("mission_description", "")
    intent.setdefault("uav_fault_assignments", [])
    intent.setdefault("planner_conversation", [])
    intent.setdefault("schema_version", 1)  # versions saved before schema 2 are legacy; stored JSON is never rewritten
    intent.setdefault("mission_plan", None)
    geographic_mission = intent.get("geographic_mission")
    if geographic_mission is not None:
        geographic_mission.setdefault("objectives", [])
        geographic_mission.setdefault("fleet_size", None)
        geographic_mission.setdefault("mission_description", "")
        geographic_mission.setdefault("mission_area_description", "")
        geographic_mission.setdefault("workflow_stage", "general")
        geographic_mission.setdefault("current_uav_step", "role")
        geographic_mission.setdefault("current_uav_index", 0)
        geographic_mission.setdefault("selected_uav_ids", [])
        geographic_mission.setdefault("required_uav_count", geographic_mission.get("fleet_size"))
        geographic_mission.setdefault("activated_uav_ids", [])
        for assignment in geographic_mission.get("uav_assignments", []):
            assignment.setdefault("zone_ids", [])
            assignment.setdefault("start_location", None)
            assignment.setdefault("destination", None)
            assignment.setdefault("waypoints", [])
            assignment.setdefault("constraints", [])
            assignment.setdefault("individual_objective", "")
            assignment.setdefault("flight_altitude_m", None)
            assignment.setdefault("speed_limit_m_s", None)
            assignment.setdefault("destination_action", "")
            assignment.setdefault("action_duration_seconds", None)
            assignment.setdefault("data_requirements", "")
            assignment.setdefault("completion_condition", "")
            assignment.setdefault("expected_result", "")
            assignment.setdefault("return_destination", None)
            assignment.setdefault("return_waypoints", [])
            assignment.setdefault("return_completion_condition", "")
            assignment.setdefault("confirmed", False)
    return {
        "id": version.id,
        "version_number": version.version_number,
        "config": json.loads(version.config_json),
        "intent": intent,
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
    scenario_id: str, owner_user_id: str, title: str, config: dict, intent: dict | None = None, *, allow_any_owner: bool = False,
) -> dict | None:
    """Saves a new immutable version. Only the owner may, except that a GENERAL (`allow_any_owner`) may open and save any user's scenario."""
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with SessionLocal() as session:
        begin_write_transaction(session)
        scenario = session.get(ScenarioRecord, scenario_id)
        if scenario is None or (scenario.owner_user_id != owner_user_id and not allow_any_owner):
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
    return get_scenario(scenario_id, owner_user_id, include_all=allow_any_owner)


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


def _deployment_view(record: MissionDeploymentRecord) -> dict:
    return {"id": record.id, "mission_id": record.mission_id,
            "scenario_id": record.scenario_id, "scenario_version_id": record.scenario_version_id,
            "deployed_by": record.deployed_by, "created_at": record.created_at.isoformat() + "Z",
            **json.loads(record.payload_json)}


def save_mission_deployment(
    scenario_id: str, version_id: str, user_id: str, role: str, payload: dict,
) -> dict:
    """Persist one idempotent handoff for a validated immutable scenario version."""
    with SessionLocal() as session:
        begin_write_transaction(session)
        scenario = session.get(ScenarioRecord, scenario_id)
        version = session.get(ScenarioVersionRecord, version_id)
        if scenario is None or version is None or scenario.owner_user_id != user_id or version.scenario_id != scenario_id:
            raise LookupError("Scenario version not found")
        latest_id = session.scalar(
            select(ScenarioVersionRecord.id).where(ScenarioVersionRecord.scenario_id == scenario_id)
            .order_by(ScenarioVersionRecord.version_number.desc()).limit(1)
        )
        if version_id != latest_id:
            raise ValueError("Only the latest immutable scenario version can be deployed")
        existing = session.scalar(select(MissionDeploymentRecord).where(
            MissionDeploymentRecord.scenario_version_id == version_id,
        ))
        approval = session.scalar(select(ScenarioApprovalRequestRecord).where(
            ScenarioApprovalRequestRecord.scenario_version_id == version_id,
        ))
        if role == "OPERATOR" and (approval is None or approval.status != "APPROVED"):
            raise ValueError("Operator deployment requires approval of this exact immutable scenario version")
        if role not in {"OPERATOR", "GENERAL"}:
            raise ValueError("This account role cannot deploy missions")
        now = utcnow_naive()
        if existing is not None:
            # Deploying the same immutable version again keeps one record (same id) but makes it the current
            # deployment: Mission Control reads the most recently deployed mission, not the first one ever made.
            existing.created_at = now
            # What can differ between two deployments of the same immutable version is refreshed: whether it is a plan only and which aircraft were verified.
            current = json.loads(existing.payload_json)
            for key in ("plan_only", "execution_status", "activated_uav_count", "fleet", "requested_uav_count"):
                if key in payload:
                    current[key] = payload[key]
            existing.payload_json = json.dumps(current, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            session.commit()
            session.refresh(existing)
            return _deployment_view(existing)
        record = MissionDeploymentRecord(
            id=uuid.uuid4().hex, scenario_id=scenario_id, scenario_version_id=version_id,
            deployed_by=user_id, mission_id=scenario_id,
            payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            created_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return _deployment_view(record)


def deployment_times_for_versions(version_ids: set[str]) -> dict[str, str]:
    """Deployment time per deployed scenario version id (versions that were never deployed are absent)."""
    if not version_ids:
        return {}
    with SessionLocal() as session:
        records = session.scalars(select(MissionDeploymentRecord).where(
            MissionDeploymentRecord.scenario_version_id.in_(version_ids)))
        return {record.scenario_version_id: record.created_at.isoformat() + "Z" for record in records}


def latest_mission_deployment(user_id: str, *, include_all: bool = False) -> dict | None:
    with SessionLocal() as session:
        query = select(MissionDeploymentRecord).join(
            ScenarioRecord, MissionDeploymentRecord.scenario_id == ScenarioRecord.id,
        )
        if not include_all:
            query = query.where(ScenarioRecord.owner_user_id == user_id)
        record = session.scalar(query.order_by(MissionDeploymentRecord.created_at.desc()).limit(1))
        return _deployment_view(record) if record else None


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
    conversation_id: str, operator_id: str, intent: dict, scenario_id: str | None = None,
) -> dict | None:
    with SessionLocal() as session:
        record = session.get(AIConversationRecord, conversation_id)
        if record is None or record.operator_id != operator_id or record.expires_at <= utcnow_naive():
            return None
        if record.status != "ready_for_review" or not record.proposal_json:
            raise ValueError("Conversation is not ready for scenario transfer")
        if record.scenario_id:
            if scenario_id is not None and record.scenario_id != scenario_id:
                raise ValueError("Conversation is already attached to another scenario")
            return get_scenario(record.scenario_id, operator_id)
        proposal = json.loads(record.proposal_json)
        version_id = uuid.uuid4().hex
        now = utcnow_naive()
        if scenario_id is None:
            config = json.loads(record.config_json)
            scenario_id = uuid.uuid4().hex
            title = (proposal.get("mission_objectives") or ["AI-assisted abstract scenario"])[0].strip()[:120]
            session.add(ScenarioRecord(
                id=scenario_id, owner_user_id=operator_id, title=title or "AI-assisted abstract scenario",
                created_at=now, updated_at=now,
            ))
            version_number = 1
        else:
            scenario = session.get(ScenarioRecord, scenario_id)
            if scenario is None or scenario.owner_user_id != operator_id:
                return None
            latest = session.scalar(
                select(ScenarioVersionRecord)
                .where(ScenarioVersionRecord.scenario_id == scenario_id)
                .order_by(ScenarioVersionRecord.version_number.desc())
                .limit(1)
            )
            if latest is None:
                raise ValueError("Scenario has no version to update")
            config = json.loads(latest.config_json)
            version_number = latest.version_number + 1
            scenario.updated_at = now
        session.add(ScenarioVersionRecord(
            id=version_id, scenario_id=scenario_id, version_number=version_number,
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


# --- Attack Lab experiments and recorded research decisions ------------------------------------------------------

def _experiment_view(record: AttackExperimentRecord) -> dict:
    return {
        "id": record.id, "owner_user_id": record.owner_user_id, "name": record.name, "scenario_id": record.scenario_id,
        "scenario_version_id": record.scenario_version_id, "preset_id": record.preset_id, "seed": record.seed,
        "status": record.status, "config": json.loads(record.config_json), "config_hash": record.config_hash,
        "created_at": record.created_at.isoformat() + "Z", "updated_at": record.updated_at.isoformat() + "Z",
    }


def save_attack_experiment(owner_user_id: str, name: str, scenario_id: str, scenario_version_id: str, preset_id: str | None,
                           seed: int, status: str, config: dict, experiment_id: str | None = None) -> dict:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    identity = json.dumps({"config": config, "seed": seed, "preset_id": preset_id, "scenario_version_id": scenario_version_id},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    config_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()  # covers config, seed, preset and the pinned version
    with SessionLocal() as session:
        begin_write_transaction(session)
        record = session.get(AttackExperimentRecord, experiment_id) if experiment_id else None
        if record is None:
            record = AttackExperimentRecord(id=experiment_id or uuid.uuid4().hex, owner_user_id=owner_user_id)
            session.add(record)
        record.name, record.scenario_id, record.scenario_version_id = name, scenario_id, scenario_version_id
        record.preset_id, record.seed, record.status = preset_id, seed, status
        record.config_json, record.config_hash = canonical, config_hash
        record.updated_at = utcnow_naive()
        session.commit()
        session.refresh(record)
        return _experiment_view(record)


def get_attack_experiment(experiment_id: str) -> dict | None:
    with SessionLocal() as session:
        record = session.get(AttackExperimentRecord, experiment_id)
        return _experiment_view(record) if record else None


def list_attack_experiments(user_id: str, include_all: bool = False) -> list[dict]:
    with SessionLocal() as session:
        query = select(AttackExperimentRecord)
        if not include_all:  # read-only readers see experiments pinned to missions they own
            query = query.join(ScenarioRecord, AttackExperimentRecord.scenario_id == ScenarioRecord.id).where(ScenarioRecord.owner_user_id == user_id)
        rows = session.scalars(query.order_by(AttackExperimentRecord.created_at.desc()).limit(200)).all()
        return [_experiment_view(row) for row in rows]


def delete_attack_experiment(experiment_id: str) -> bool:
    """Deletes the experiment definition. Stored results are kept and marked as coming from a deleted experiment."""
    with SessionLocal() as session:
        begin_write_transaction(session)
        record = session.get(AttackExperimentRecord, experiment_id)
        if record is None:
            return False
        if experiment_results_available():
            for result in session.scalars(select(ExperimentResultRecord).where(ExperimentResultRecord.experiment_id == experiment_id)):
                result.experiment_deleted_at = utcnow_naive()
        session.delete(record)
        session.commit()
        return True


RESULTS_STORAGE_MISSING = ("Experiment result storage is not available: the `experiment_results` table does not exist. "
                           "Restart the backend (SQLite creates it at startup) or run `alembic upgrade head` (PostgreSQL).")
_results_table = {"present": False, "checked_at": 0.0}


def experiment_results_available() -> bool:
    """True when the `experiment_results` table exists. Without it no experiment can have results, so experiments are
    simply treated as never run (edit in place, delete outright) instead of failing with a database error."""
    if _results_table["present"]:
        return True
    now = time.monotonic()
    if now - _results_table["checked_at"] < 15.0 and _results_table["checked_at"]:
        return False
    _results_table["checked_at"] = now
    _results_table["present"] = inspect(engine).has_table(ExperimentResultRecord.__tablename__)
    return _results_table["present"]


def update_experiment_result_payload(result_id: str, updates: dict) -> bool:
    """Merges keys into a stored result's payload (used to add a mission-continuity outcome that is only known after the run record was written)."""
    if not experiment_results_available():
        return False
    with SessionLocal() as session:
        begin_write_transaction(session)
        record = session.get(ExperimentResultRecord, result_id)
        if record is None:
            return False
        payload = json.loads(record.payload_json)
        payload.update(updates)
        record.payload_json = json.dumps(payload, sort_keys=True, allow_nan=False)
        session.commit()
        return True


def _result_view(record: ExperimentResultRecord) -> dict:
    return {"id": record.id, "experiment_id": record.experiment_id, "experiment_name": record.experiment_name, "scenario_id": record.scenario_id,
            "scenario_version_id": record.scenario_version_id, "config_hash": record.config_hash, "config": json.loads(record.config_json),
            "result": json.loads(record.payload_json), "created_at": record.created_at.isoformat() + "Z",
            "from_deleted_experiment": record.experiment_deleted_at is not None,
            "label": "from a deleted experiment" if record.experiment_deleted_at is not None else None}


def save_experiment_result(experiment_id: str, payload: dict) -> dict:
    """Stores a result with a snapshot of the experiment as it was run (used by the experiment runner, once it exists)."""
    if not experiment_results_available():
        raise RuntimeError(RESULTS_STORAGE_MISSING)
    with SessionLocal() as session:
        begin_write_transaction(session)
        experiment = session.get(AttackExperimentRecord, experiment_id)
        if experiment is None:
            raise ValueError("Unknown experiment")
        record = ExperimentResultRecord(
            id=uuid.uuid4().hex, experiment_id=experiment.id, experiment_name=experiment.name, owner_user_id=experiment.owner_user_id,
            scenario_id=experiment.scenario_id, scenario_version_id=experiment.scenario_version_id, config_hash=experiment.config_hash,
            config_json=experiment.config_json, payload_json=json.dumps(payload, sort_keys=True, allow_nan=False))
        session.add(record)
        session.commit()
        session.refresh(record)
        return _result_view(record)


def experiment_result_counts(experiment_ids: list[str]) -> dict[str, int]:
    if not experiment_ids or not experiment_results_available():
        return {}
    with SessionLocal() as session:
        rows = session.execute(select(ExperimentResultRecord.experiment_id, func.count()).where(
            ExperimentResultRecord.experiment_id.in_(experiment_ids)).group_by(ExperimentResultRecord.experiment_id)).all()
        return {experiment_id: int(count) for experiment_id, count in rows}


def list_experiment_results(owner_user_id: str, include_all: bool = False, limit: int = 200) -> list[dict]:
    if not experiment_results_available():
        return []
    with SessionLocal() as session:
        query = select(ExperimentResultRecord)
        if not include_all:
            query = query.where(ExperimentResultRecord.owner_user_id == owner_user_id)
        return [_result_view(row) for row in session.scalars(query.order_by(ExperimentResultRecord.created_at.desc()).limit(limit))]


def _decision_view(record: ResearchDecisionRecord) -> dict:
    return {
        "id": record.id, "user_id": record.user_id, "username": record.username, "aircraft_id": record.aircraft_id,
        "action": record.action, "handover_candidate": record.handover_candidate, "source": record.source,
        "source_name": record.source_name, "tier": record.tier, "threat_score": record.threat_score,
        "created_at": record.created_at.isoformat() + "Z", "executed": str(record.source_name or "").startswith("executed:"),
    }


def record_research_decision(user_id: str, username: str, aircraft_id: str, action: str, handover_candidate: str | None,
                             source: str, source_name: str | None, tier: str | None, threat_score: float | None) -> dict:
    with SessionLocal() as session:
        begin_write_transaction(session)
        record = ResearchDecisionRecord(
            id=uuid.uuid4().hex, user_id=user_id, username=username, aircraft_id=aircraft_id, action=action,
            handover_candidate=handover_candidate, source=source, source_name=source_name, tier=tier, threat_score=threat_score,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return _decision_view(record)


def list_research_decisions(limit: int = 50) -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(select(ResearchDecisionRecord).order_by(ResearchDecisionRecord.created_at.desc()).limit(limit)).all()
        return [_decision_view(row) for row in rows]


# --- mission executions (PX4 SITL) -------------------------------------------------------------------------------

def get_mission_deployment(deployment_id: str) -> dict | None:
    with SessionLocal() as session:
        record = session.get(MissionDeploymentRecord, deployment_id)
        return _deployment_view(record) if record else None


def _execution_view(record: MissionExecutionRecord) -> dict:
    return {"id": record.id, "deployment_id": record.deployment_id, "scenario_id": record.scenario_id,
            "scenario_version_id": record.scenario_version_id, "aircraft_id": record.aircraft_id,
            "instance_number": record.instance_number, "started_by": record.started_by, "started_by_name": record.started_by_name,
            "started_by_role": record.started_by_role, "state": record.state, "reason": record.reason,
            "progress": json.loads(record.progress_json or "{}"), "last_event_index": record.last_event_index,
            "created_at": record.created_at.isoformat() + "Z", "updated_at": record.updated_at.isoformat() + "Z",
            "finished_at": record.finished_at.isoformat() + "Z" if record.finished_at else None, "active": record.finished_at is None}


def create_mission_execution(*, execution_id: str, deployment_id: str, scenario_id: str, scenario_version_id: str, aircraft_id: str,
                             instance_number: int, user_id: str, user_name: str, role: str) -> dict:
    """Create the run, refusing a second active run on the same aircraft inside one write transaction."""
    with SessionLocal() as session:
        begin_write_transaction(session)
        active = session.scalar(select(MissionExecutionRecord).where(
            MissionExecutionRecord.aircraft_id == aircraft_id, MissionExecutionRecord.finished_at.is_(None)))
        if active is not None:
            raise ValueError(f"execution {active.id} is already running on {aircraft_id}")
        record = MissionExecutionRecord(
            id=execution_id, deployment_id=deployment_id, scenario_id=scenario_id, scenario_version_id=scenario_version_id,
            aircraft_id=aircraft_id, instance_number=instance_number, started_by=user_id, started_by_name=user_name,
            started_by_role=role, state="connecting", reason="", progress_json="{}", last_event_index=-1)
        session.add(record)
        session.commit()
        session.refresh(record)
        return _execution_view(record)


def get_mission_execution(execution_id: str) -> dict | None:
    with SessionLocal() as session:
        record = session.get(MissionExecutionRecord, execution_id)
        return _execution_view(record) if record else None


def latest_mission_execution(user_id: str, *, include_all: bool = False) -> dict | None:
    with SessionLocal() as session:
        query = select(MissionExecutionRecord).join(ScenarioRecord, MissionExecutionRecord.scenario_id == ScenarioRecord.id)
        if not include_all:
            query = query.where(ScenarioRecord.owner_user_id == user_id)
        record = session.scalar(query.order_by(MissionExecutionRecord.created_at.desc()).limit(1))
        return _execution_view(record) if record else None


def deployment_mission_executions(deployment_id: str) -> list[dict]:
    """The newest run of every aircraft of one deployment (a multi-aircraft start creates one run per flying aircraft)."""
    with SessionLocal() as session:
        rows = session.scalars(select(MissionExecutionRecord).where(MissionExecutionRecord.deployment_id == deployment_id)
                               .order_by(MissionExecutionRecord.created_at.desc())).all()
        newest: dict[str, MissionExecutionRecord] = {}
        for row in rows:
            newest.setdefault(row.aircraft_id, row)
        return [_execution_view(row) for row in sorted(newest.values(), key=lambda item: item.aircraft_id)]


def active_mission_execution(aircraft_id: str) -> dict | None:
    with SessionLocal() as session:
        record = session.scalar(select(MissionExecutionRecord).where(
            MissionExecutionRecord.aircraft_id == aircraft_id, MissionExecutionRecord.finished_at.is_(None))
            .order_by(MissionExecutionRecord.created_at.desc()).limit(1))
        return _execution_view(record) if record else None


def update_mission_execution(execution_id: str, *, state: str | None = None, reason: str | None = None, progress: dict | None = None,
                             last_event_index: int | None = None, finished: bool = False) -> dict | None:
    with SessionLocal() as session:
        begin_write_transaction(session)
        record = session.get(MissionExecutionRecord, execution_id)
        if record is None:
            return None
        if record.finished_at is None:  # a finished run is never rewritten
            if state is not None:
                record.state = state
            if reason is not None:
                record.reason = reason
            if progress is not None:
                record.progress_json = json.dumps(progress, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if last_event_index is not None:
                record.last_event_index = max(record.last_event_index, last_event_index)
            record.updated_at = utcnow_naive()
            if finished:
                record.finished_at = utcnow_naive()
        session.commit()
        session.refresh(record)
        return _execution_view(record)


def add_mission_execution_events(execution_id: str, events: list[dict]) -> int:
    """Append events. Executor events carry `executor_index` and are stored once, whatever the polling overlap."""
    stored = 0
    with SessionLocal() as session:
        begin_write_transaction(session)
        known = set(session.scalars(select(MissionExecutionEventRecord.executor_index).where(
            MissionExecutionEventRecord.execution_id == execution_id, MissionExecutionEventRecord.executor_index.is_not(None))))
        for item in events:
            index = item.get("executor_index")
            if index is not None and index in known:
                continue
            session.add(MissionExecutionEventRecord(
                execution_id=execution_id, source=item["source"], type=item["type"][:48], message=item["message"],
                data_json=json.dumps(item.get("data") or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                actor_id=item.get("actor_id"), actor_name=item.get("actor_name"), executor_index=index,
                occurred_at=item.get("occurred_at") or utcnow_naive()))
            if index is not None:
                known.add(index)
            stored += 1
        session.commit()
    return stored


def list_mission_execution_events(execution_id: str, *, after_id: int = 0, limit: int = 500) -> list[dict]:
    with SessionLocal() as session:
        records = session.scalars(select(MissionExecutionEventRecord).where(
            MissionExecutionEventRecord.execution_id == execution_id, MissionExecutionEventRecord.id > after_id)
            .order_by(MissionExecutionEventRecord.id).limit(limit)).all()
        return [{"id": record.id, "execution_id": record.execution_id, "source": record.source, "type": record.type,
                 "message": record.message, "data": json.loads(record.data_json or "{}"), "actor_name": record.actor_name,
                 "occurred_at": record.occurred_at.isoformat() + "Z"} for record in records]


# --- General: all saved scenarios, export, and the cascade delete -----------------------------------------------------------------

def _scenario_admin_rows(session) -> list[dict]:
    """One row per scenario of every user (GENERAL's view). Read-only."""
    users = {user.id: user for user in session.scalars(select(UserRecord)).all()}
    rows = []
    for scenario in session.scalars(select(ScenarioRecord).order_by(ScenarioRecord.updated_at.desc())).all():
        versions = session.scalars(select(ScenarioVersionRecord).where(ScenarioVersionRecord.scenario_id == scenario.id)
                                   .order_by(ScenarioVersionRecord.version_number.desc())).all()
        if not versions:
            continue
        latest = versions[0]
        approvals = {item.scenario_version_id: item for item in session.scalars(select(ScenarioApprovalRequestRecord).where(
            ScenarioApprovalRequestRecord.scenario_version_id.in_([v.id for v in versions]))).all()}
        deployments = session.scalars(select(MissionDeploymentRecord).where(MissionDeploymentRecord.scenario_id == scenario.id)).all()
        executions = session.scalars(select(MissionExecutionRecord).where(MissionExecutionRecord.scenario_id == scenario.id)).all()
        deployed_versions = {item.scenario_version_id for item in deployments}
        latest_approval = approvals.get(latest.id)
        status = "deployed" if latest.id in deployed_versions else "approved" if latest_approval and latest_approval.status == "APPROVED" \
            else "submitted" if latest_approval else "draft"
        intents = {v.id: session.get(ScenarioIntentRecord, v.id) for v in versions}

        def source_of(version):
            record = intents.get(version.id)
            if record is None:
                return "legacy"
            data = json.loads(record.intent_json)
            return "quick" if data.get("quick_source") else "manual" if data.get("schema_version") == 2 else "legacy"
        owner = users.get(scenario.owner_user_id)
        rows.append({
            "id": scenario.id, "title": scenario.title, "owner_user_id": scenario.owner_user_id,
            "owner_username": owner.username if owner else None, "owner_name": owner.full_name if owner else None, "owner_role": owner.role if owner else None,
            "latest_version_number": latest.version_number, "status": status, "approval_status": latest_approval.status if latest_approval else None,
            "created_at": scenario.created_at.isoformat() + "Z", "updated_at": scenario.updated_at.isoformat() + "Z",
            "source": source_of(latest), "deployments": len(deployments), "executions": len(executions),
            "active_execution": any(item.finished_at is None for item in executions),
            "versions": [{"id": v.id, "number": v.version_number, "hash": v.config_hash, "source": source_of(v),
                          "approval_status": approvals[v.id].status if v.id in approvals else None, "deployed": v.id in deployed_versions,
                          "created_at": v.created_at.isoformat() + "Z"} for v in versions],
        })
    return rows


def list_scenarios_admin() -> list[dict]:
    with SessionLocal() as session:
        return _scenario_admin_rows(session)


def _scenario_children(session, scenario_id: str) -> dict:
    versions = session.scalars(select(ScenarioVersionRecord).where(ScenarioVersionRecord.scenario_id == scenario_id)).all()
    version_ids = [v.id for v in versions]
    executions = session.scalars(select(MissionExecutionRecord).where(MissionExecutionRecord.scenario_id == scenario_id)).all()
    experiments = session.scalars(select(AttackExperimentRecord).where(AttackExperimentRecord.scenario_id == scenario_id)).all()
    experiment_ids = [e.id for e in experiments]
    return {
        "versions": versions, "executions": executions, "experiments": experiments,
        "approvals": session.scalars(select(ScenarioApprovalRequestRecord).where(ScenarioApprovalRequestRecord.scenario_version_id.in_(version_ids))).all() if version_ids else [],
        "deployments": session.scalars(select(MissionDeploymentRecord).where(MissionDeploymentRecord.scenario_id == scenario_id)).all(),
        "events": session.scalars(select(MissionExecutionEventRecord).where(MissionExecutionEventRecord.execution_id.in_([e.id for e in executions]))).all() if executions else [],
        "results": session.scalars(select(ExperimentResultRecord).where(
            (ExperimentResultRecord.experiment_id.in_(experiment_ids)) | (ExperimentResultRecord.scenario_id == scenario_id))).all(),
    }


def scenario_delete_preview(scenario_id: str) -> dict | None:
    """Exactly what a General delete would remove, and what blocks it. None when the scenario does not exist."""
    with SessionLocal() as session:
        scenario = session.get(ScenarioRecord, scenario_id)
        if scenario is None:
            return None
        children = _scenario_children(session, scenario_id)
        blockers = []
        active = [e for e in children["executions"] if e.finished_at is None]
        if active:
            blockers.append({"code": "execution_active", "message": f"A flight of this scenario is still active ({active[0].state} on {active[0].aircraft_id}). Abort it and wait until it is final first."})
        for owner_scope, label in ((None, "Mission Control shows it"), (scenario.owner_user_id, "Mission Control shows it for its owner")):
            query = select(MissionDeploymentRecord).join(ScenarioRecord, MissionDeploymentRecord.scenario_id == ScenarioRecord.id)
            if owner_scope:
                query = query.where(ScenarioRecord.owner_user_id == owner_scope)
            newest = session.scalar(query.order_by(MissionDeploymentRecord.created_at.desc()).limit(1))
            if newest is not None and newest.scenario_id == scenario_id and not any(b["code"] == "pinned_in_mission_control" for b in blockers):
                blockers.append({"code": "pinned_in_mission_control", "message": f"{label}: its deployment is the newest one. Deploy another mission (or switch Mission Control to one) first."})
        return {
            "scenario_id": scenario_id, "title": scenario.title,
            "counts": {"versions": len(children["versions"]), "approvals": len(children["approvals"]), "deployments": len(children["deployments"]),
                       "executions": len(children["executions"]), "execution_events": len(children["events"]), "run_records": 0,
                       "experiments": len(children["experiments"]), "experiment_results": len(children["results"])},
            "blockers": blockers,
        }


def scenario_export(scenario_id: str) -> dict | None:
    """The full record of one scenario as plain JSON: versions with hashes and intents, approvals, deployments, executions with events, experiments and results."""
    with SessionLocal() as session:
        scenario = session.get(ScenarioRecord, scenario_id)
        if scenario is None:
            return None
        children = _scenario_children(session, scenario_id)
        owner = session.get(UserRecord, scenario.owner_user_id)
        versions = []
        for version in sorted(children["versions"], key=lambda item: item.version_number):
            view = _scenario_version_view(session, version)
            versions.append(view)
        return {
            "exported_format": "trinetra-scenario-record-1", "scenario": {"id": scenario.id, "title": scenario.title, "owner_user_id": scenario.owner_user_id,
                                                                          "owner_username": owner.username if owner else None, "owner_role": owner.role if owner else None,
                                                                          "created_at": scenario.created_at.isoformat() + "Z", "updated_at": scenario.updated_at.isoformat() + "Z"},
            "versions": versions,
            "approvals": [{"id": a.id, "scenario_version_id": a.scenario_version_id, "status": a.status, "reason": a.reason, "submitted_by": a.submitted_by,
                           "reviewed_by": a.reviewed_by, "submitted_at": a.submitted_at.isoformat() + "Z", "reviewed_at": a.reviewed_at.isoformat() + "Z" if a.reviewed_at else None}
                          for a in children["approvals"]],
            "deployments": [_deployment_view(d) for d in children["deployments"]],
            "executions": [{**_execution_view(e), "events": [{"id": ev.id, "source": ev.source, "type": ev.type, "message": ev.message, "actor_name": ev.actor_name,
                                                              "data": json.loads(ev.data_json or "{}"), "occurred_at": ev.occurred_at.isoformat() + "Z"}
                                                             for ev in children["events"] if ev.execution_id == e.id]} for e in children["executions"]],
            "experiments": [{"id": x.id, "name": x.name, "scenario_version_id": x.scenario_version_id, "preset_id": x.preset_id, "seed": x.seed, "status": x.status,
                             "config": json.loads(x.config_json), "config_hash": x.config_hash} for x in children["experiments"]],
            "experiment_results": [{"id": r.id, "experiment_id": r.experiment_id, "experiment_name": r.experiment_name, "config_hash": r.config_hash,
                                    "payload": json.loads(r.payload_json), "experiment_deleted_at": r.experiment_deleted_at.isoformat() + "Z" if r.experiment_deleted_at else None}
                                   for r in children["results"]],
            "run_records": [],
        }


def delete_scenario_cascade(scenario_id: str, *, delete_results: bool) -> dict:
    """GENERAL delete of a whole scenario in ONE transaction. Raises ValueError (with the blocker text) while it is active or pinned.

    Deletes events, executions, deployments, experiments, approvals, intents, versions and the scenario. Experiment results are kept and marked as coming
    from a deleted experiment unless `delete_results`. Returns what was deleted, with counts and a hash of the exported content.
    """
    export = scenario_export(scenario_id)
    preview = scenario_delete_preview(scenario_id)
    if export is None or preview is None:
        raise LookupError("Scenario not found.")
    if preview["blockers"]:
        raise ValueError(preview["blockers"][0]["message"])
    content_hash = hashlib.sha256(json.dumps(export, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    with SessionLocal() as session:
        begin_write_transaction(session)
        children = _scenario_children(session, scenario_id)
        now = utcnow_naive()
        execution_ids = [e.id for e in children["executions"]]
        version_ids = [v.id for v in children["versions"]]
        if execution_ids:
            session.execute(delete(MissionExecutionEventRecord).where(MissionExecutionEventRecord.execution_id.in_(execution_ids)))
        session.execute(delete(MissionExecutionRecord).where(MissionExecutionRecord.scenario_id == scenario_id))
        session.execute(delete(MissionDeploymentRecord).where(MissionDeploymentRecord.scenario_id == scenario_id))
        result_ids = [r.id for r in children["results"]]
        if result_ids and delete_results:
            session.execute(delete(ExperimentResultRecord).where(ExperimentResultRecord.id.in_(result_ids)))
        elif result_ids:
            session.execute(update(ExperimentResultRecord).where(ExperimentResultRecord.id.in_(result_ids), ExperimentResultRecord.experiment_deleted_at.is_(None))
                            .values(experiment_deleted_at=now))
        session.execute(delete(AttackExperimentRecord).where(AttackExperimentRecord.scenario_id == scenario_id))
        if version_ids:
            session.execute(delete(ScenarioApprovalRequestRecord).where(ScenarioApprovalRequestRecord.scenario_version_id.in_(version_ids)))
            session.execute(delete(ScenarioIntentRecord).where(ScenarioIntentRecord.version_id.in_(version_ids)))
        session.execute(delete(ScenarioVersionRecord).where(ScenarioVersionRecord.scenario_id == scenario_id))
        session.execute(delete(ScenarioRecord).where(ScenarioRecord.id == scenario_id))
        session.commit()
    counts = dict(preview["counts"])
    counts["experiment_results_deleted"] = counts["experiment_results"] if delete_results else 0
    counts["experiment_results_kept"] = 0 if delete_results else counts["experiment_results"]
    return {"scenario_id": scenario_id, "title": preview["title"], "counts": counts, "content_hash": content_hash}
