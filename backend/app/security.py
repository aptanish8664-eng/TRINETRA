from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque, Iterator

from argon2 import PasswordHasher, exceptions as argon2_exceptions
from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from .config import LOGIN_FAILURE_LIMIT, LOGIN_WINDOW_SECONDS, SESSION_TTL_SECONDS
from .persistence.database import (
    AuditLogRecord,
    SessionLocal,
    SessionRecord,
    UserRecord,
    add_audit_event,
    begin_write_transaction,
    utcnow_naive,
)


password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(48))
_failed_logins: dict[str, Deque[float]] = defaultdict(deque)
_registration_attempts: dict[str, Deque[float]] = defaultdict(deque)


@dataclass(frozen=True)
class UserPrincipal:
    id: str
    username: str
    full_name: str
    email: str | None
    role: str


@dataclass(frozen=True)
class AuthContext:
    user: UserPrincipal
    session_id: str
    csrf_token: str
    expires_at: datetime


def principal_from_record(record: UserRecord) -> UserPrincipal:
    return UserPrincipal(
        id=record.id,
        username=record.username,
        full_name=record.full_name,
        email=record.email,
        role=record.role or "",
    )


def public_user(user: UserPrincipal) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "account_status": "APPROVED",
    }


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _audit_request_id(request) -> str:
    return getattr(request.state, "request_id", "request-unavailable")


def audit_denial(request, reason: str, user: UserPrincipal | None = None) -> None:
    add_audit_event(
        action="access_denied",
        outcome="denied",
        reason=reason,
        request_id=_audit_request_id(request),
        actor_id=user.id if user else None,
        actor_name=user.full_name if user else None,
        target_type="route",
        target_id=request.url.path,
        metadata={"method": getattr(request, "method", "websocket")},
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return password_hasher.verify(encoded_hash, password)
    except (
        argon2_exceptions.VerifyMismatchError,
        argon2_exceptions.VerificationError,
        argon2_exceptions.InvalidHashError,
    ):
        return False


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain between 12 and 128 characters")
    return password_hasher.hash(password)


def _rate_limited(attempts: deque[float]) -> bool:
    cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
    while attempts and attempts[0] <= cutoff:
        attempts.popleft()
    return len(attempts) >= LOGIN_FAILURE_LIMIT


def login_is_throttled(key: str) -> bool:
    return _rate_limited(_failed_logins[key])


def note_login_failure(key: str) -> None:
    _failed_logins[key].append(time.monotonic())


def clear_login_failures(key: str) -> None:
    _failed_logins.pop(key, None)


def registration_is_throttled(key: str) -> bool:
    return _rate_limited(_registration_attempts[key])


def note_registration_attempt(key: str) -> None:
    _registration_attempts[key].append(time.monotonic())


def authenticate(username: str, password: str) -> tuple[UserPrincipal | None, bool]:
    normalized = username.strip().casefold()
    with SessionLocal() as session:
        user = session.scalar(select(UserRecord).where(UserRecord.username == normalized))
        encoded_hash = user.password_hash if user and user.password_hash else _dummy_password_hash
        valid_password = verify_password(password, encoded_hash)
        if (
            user is None
            or user.account_status != "APPROVED"
            or user.role not in {"GENERAL", "OPERATOR"}
            or user.password_hash is None
            or not valid_password
        ):
            return None, False
        user.last_login_at = utcnow_naive()
        principal = principal_from_record(user)
        session.commit()
        return principal, True


def register_user(
    *,
    full_name: str,
    username: str,
    email: str | None,
    password: str,
    requested_role: str,
    registration_note: str | None,
    request_id: str,
) -> str:
    normalized_username = username.strip().casefold()
    normalized_email = email.strip().casefold() if email and email.strip() else None
    if requested_role not in {"GENERAL", "OPERATOR"}:
        raise ValueError("Unsupported requested role")
    if not full_name.strip():
        raise ValueError("Full name is required")
    if not 3 <= len(normalized_username) <= 64:
        raise ValueError("Username must contain between 3 and 64 characters")

    user_id = uuid.uuid4().hex
    with SessionLocal() as session:
        begin_write_transaction(session)
        if session.scalar(
            select(UserRecord.id).where(
                or_(
                    UserRecord.username == normalized_username,
                    UserRecord.email == normalized_email if normalized_email else False,
                )
            )
        ):
            raise ValueError("A user or registration request already uses that username or email")
        session.add(UserRecord(
            id=user_id,
            full_name=full_name.strip(),
            username=normalized_username,
            email=normalized_email,
            password_hash=hash_password(password),
            requested_role=requested_role,
            role=None,
            account_status="PENDING",
            registration_note=registration_note.strip() if registration_note and registration_note.strip() else None,
        ))
        session.flush()
        session.add(AuditLogRecord(
            id=secrets.token_hex(16),
            actor_user_id=None,
            actor_name=full_name.strip(),
            event_type="registration_submitted",
            target_type="user",
            target_id=user_id,
            outcome="success",
            reason="Registration request submitted for review",
            request_id=request_id,
            metadata_json=json.dumps({"requested_role": requested_role}, sort_keys=True),
        ))
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError("A user or registration request already uses that username or email") from exc
    return user_id


def registration_status(user_id: str) -> dict | None:
    with SessionLocal() as session:
        user = session.get(UserRecord, user_id)
        if user is None:
            return None
        return {
            "application_id": user.id,
            "status": user.account_status,
            "rejection_message": user.rejection_reason,
            "assigned_role": user.role,
        }


def _user_view(user: UserRecord) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "username": user.username,
        "email": user.email,
        "requested_role": user.requested_role,
        "registration_note": user.registration_note,
        "role": user.role,
        "account_status": user.account_status,
        "rejection_reason": user.rejection_reason,
        "created_at": user.created_at.isoformat() + "Z",
        "updated_at": user.updated_at.isoformat() + "Z",
        "approved_at": user.approved_at.isoformat() + "Z" if user.approved_at else None,
        "last_login_at": user.last_login_at.isoformat() + "Z" if user.last_login_at else None,
    }


def list_users(status: str | None = None) -> list[dict]:
    with SessionLocal() as session:
        query = select(UserRecord)
        if status:
            query = query.where(UserRecord.account_status == status.upper())
        rows = session.scalars(query.order_by(UserRecord.created_at.desc())).all()
        return [_user_view(user) for user in rows]


def decide_registration(
    user_id: str,
    *,
    decision: str,
    role: str | None,
    rejection_message: str | None,
    actor: UserPrincipal,
    request_id: str,
) -> None:
    if decision not in {"approve", "reject", "hold", "resume"}:
        raise ValueError("Unsupported registration decision")
    if decision == "approve" and role not in {"GENERAL", "OPERATOR"}:
        raise ValueError("Approval requires an assigned role")
    if decision != "approve" and role is not None:
        raise ValueError("Only an approved registration can receive a role")
    now = utcnow_naive()
    with SessionLocal() as session:
        begin_write_transaction(session)
        user = session.get(UserRecord, user_id)
        if user is None:
            raise LookupError("Registration request not found")
        if decision in {"approve", "reject", "hold"} and user.account_status != "PENDING":
            raise ValueError("Only pending registrations can be held, approved, or rejected")
        if decision == "resume" and user.account_status != "ON_HOLD":
            raise ValueError("Only an on-hold registration can be resumed")
        if decision == "approve":
            user.role = role
            user.account_status = "APPROVED"
            user.approved_by = actor.id
            user.approved_at = now
            user.rejection_reason = None
            event_type = "registration_approved"
            reason = "General approved registration and assigned role"
            metadata = {"actor_role": actor.role, "role": role}
        elif decision == "reject":
            user.account_status = "REJECTED"
            user.password_hash = None
            user.rejection_reason = rejection_message.strip() if rejection_message and rejection_message.strip() else None
            event_type = "registration_rejected"
            reason = "General rejected registration"
            metadata = {"actor_role": actor.role}
        elif decision == "hold":
            user.account_status = "ON_HOLD"
            event_type = "registration_held"
            reason = "General placed registration on hold"
            metadata = {"actor_role": actor.role}
        else:
            user.account_status = "PENDING"
            event_type = "registration_resumed"
            reason = "General returned registration to pending review"
            metadata = {"actor_role": actor.role}
        user.updated_at = now
        session.add(AuditLogRecord(
            id=secrets.token_hex(16),
            actor_user_id=actor.id,
            actor_name=actor.full_name,
            event_type=event_type,
            target_type="user",
            target_id=user.id,
            outcome="success",
            reason=reason,
            request_id=request_id,
            metadata_json=json.dumps(metadata, sort_keys=True),
            created_at=now,
        ))
        session.commit()


def bootstrap_general(*, full_name: str, username: str, password: str, request_id: str) -> UserPrincipal:
    normalized = username.strip().casefold()
    if not 3 <= len(normalized) <= 64:
        raise ValueError("Username must contain between 3 and 64 characters")
    if not full_name.strip():
        raise ValueError("Full name is required")
    encoded_hash = hash_password(password)
    user_id = uuid.uuid4().hex
    with SessionLocal() as session:
        begin_write_transaction(session)
        if session.scalar(select(func.count()).select_from(UserRecord)):
            raise ValueError("General bootstrap is only available before the first user exists")
        record = UserRecord(
            id=user_id,
            full_name=full_name.strip(),
            username=normalized,
            email=None,
            password_hash=encoded_hash,
            requested_role=None,
            role="GENERAL",
            account_status="APPROVED",
            approved_at=utcnow_naive(),
        )
        session.add(record)
        session.flush()
        session.add(AuditLogRecord(
            id=secrets.token_hex(16),
            actor_user_id=user_id,
            actor_name=record.full_name,
            event_type="general_bootstrapped",
            target_type="user",
            target_id=user_id,
            outcome="success",
            reason="Initial General account provisioned through backend CLI",
            request_id=request_id,
            metadata_json=json.dumps({"actor_role": "GENERAL"}, sort_keys=True),
        ))
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError("Username is already in use") from exc
        return principal_from_record(record)


def create_session(user: UserPrincipal) -> tuple[str, str, str, datetime]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = utcnow_naive() + timedelta(seconds=SESSION_TTL_SECONDS)
    session_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(SessionRecord(
            id=session_id,
            user_id=user.id,
            token_hash=token_digest(token),
            csrf_token=csrf_token,
            expires_at=expires_at,
        ))
        db.commit()
    return token, csrf_token, session_id, expires_at


def get_auth_context(request: Request) -> AuthContext:
    from fastapi import HTTPException

    from .config import SESSION_COOKIE_NAME

    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        if request.url.path != "/api/auth/session":
            audit_denial(request, "session_required")
        raise HTTPException(status_code=401, detail="Authentication required")
    now = utcnow_naive()
    with SessionLocal() as db:
        session_record = db.scalar(
            select(SessionRecord).where(SessionRecord.token_hash == token_digest(raw_token))
        )
        context = None
        user = None
        if session_record and session_record.revoked_at is None and session_record.expires_at > now:
            user_record = db.get(UserRecord, session_record.user_id)
            if user_record and user_record.account_status == "APPROVED" and user_record.role in {"GENERAL", "OPERATOR"}:
                user = principal_from_record(user_record)
                context = AuthContext(user, session_record.id, session_record.csrf_token, session_record.expires_at)
    if context is None:
        audit_denial(request, "invalid_or_expired_session", user)
        raise HTTPException(status_code=401, detail="Authentication required")
    request.state.auth_context = context
    return context


def revoke_session(session_id: str) -> None:
    with SessionLocal() as db:
        session_record = db.get(SessionRecord, session_id)
        if session_record and session_record.revoked_at is None:
            session_record.revoked_at = utcnow_naive()
            db.commit()


def set_user_active(user_id: str, active: bool, actor: UserPrincipal, request_id: str) -> None:
    with SessionLocal() as db:
        begin_write_transaction(db)
        target = db.get(UserRecord, user_id)
        if target is None:
            raise LookupError("User not found")
        if target.id == actor.id:
            raise ValueError("You cannot deactivate or reactivate your own account")
        if target.account_status == "PENDING":
            raise ValueError("Pending registrations must be reviewed, not activated directly")
        if active and target.account_status != "DEACTIVATED":
            raise ValueError("Only deactivated users can be activated")
        if not active and target.account_status != "APPROVED":
            raise ValueError("Only approved users can be deactivated")
        if not active and target.role == "GENERAL" and target.account_status == "APPROVED":
            active_generals = db.scalar(
                select(func.count()).select_from(UserRecord).where(
                    UserRecord.role == "GENERAL",
                    UserRecord.account_status == "APPROVED",
                )
            ) or 0
            if active_generals <= 1:
                raise ValueError("The last active General cannot be deactivated")
        target.account_status = "APPROVED" if active else "DEACTIVATED"
        target.updated_at = utcnow_naive()
        if not active:
            db.execute(
                SessionRecord.__table__.update()
                .where(SessionRecord.user_id == user_id, SessionRecord.revoked_at.is_(None))
                .values(revoked_at=utcnow_naive())
            )
        db.commit()
    add_audit_event(
        action="user_activated" if active else "user_deactivated",
        outcome="success",
        reason="General changed user account status",
        request_id=request_id,
        actor_id=actor.id,
        actor_name=actor.full_name,
        target_type="user",
        target_id=user_id,
    )


def update_user_role(user_id: str, role: str, actor: UserPrincipal, request_id: str) -> None:
    if role not in {"GENERAL", "OPERATOR"}:
        raise ValueError("Invalid role")
    with SessionLocal() as db:
        begin_write_transaction(db)
        target = db.get(UserRecord, user_id)
        if target is None:
            raise LookupError("User not found")
        if target.id == actor.id:
            raise ValueError("You cannot change your own role")
        if target.account_status != "APPROVED":
            raise ValueError("Only an approved account can be assigned a different role")
        if target.role == "GENERAL" and role != "GENERAL":
            active_generals = db.scalar(
                select(func.count()).select_from(UserRecord).where(
                    UserRecord.role == "GENERAL",
                    UserRecord.account_status == "APPROVED",
                )
            ) or 0
            if active_generals <= 1:
                raise ValueError("The last active General cannot be demoted")
        target.role = role
        target.updated_at = utcnow_naive()
        db.execute(
            SessionRecord.__table__.update()
            .where(SessionRecord.user_id == user_id, SessionRecord.revoked_at.is_(None))
            .values(revoked_at=utcnow_naive())
        )
        db.commit()
    add_audit_event(
        action="user_role_changed",
        outcome="success",
        reason="General assigned an application role",
        request_id=request_id,
        actor_id=actor.id,
        actor_name=actor.full_name,
        target_type="user",
        target_id=user_id,
        metadata={"role": role},
    )


def audit_history(
    *, limit: int = 50, offset: int = 0, search: str | None = None,
    event_type: str | None = None, outcome: str | None = None,
    username: str | None = None, since: datetime | None = None, until: datetime | None = None,
) -> tuple[list[dict], int]:
    with SessionLocal() as db:
        query = _audit_history_query(
            search=search, event_type=event_type, outcome=outcome, username=username,
            since=since, until=until,
        )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(
            query.order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
            .offset(offset).limit(limit)
        ).all()
        return [_audit_history_entry(row) for row in rows], total


def iter_audit_history(
    *, batch_size: int = 200, search: str | None = None,
    event_type: str | None = None, outcome: str | None = None,
    username: str | None = None, since: datetime | None = None, until: datetime | None = None,
) -> Iterator[dict]:
    if batch_size < 1:
        raise ValueError("Audit history batch size must be positive")
    with SessionLocal() as db:
        query = _audit_history_query(
            search=search, event_type=event_type, outcome=outcome, username=username,
            since=since, until=until,
        )
        cursor_created_at: datetime | None = None
        cursor_id: str | None = None
        while True:
            page_query = query
            if cursor_created_at is not None and cursor_id is not None:
                page_query = page_query.where(or_(
                    AuditLogRecord.created_at < cursor_created_at,
                    (AuditLogRecord.created_at == cursor_created_at) & (AuditLogRecord.id < cursor_id),
                ))
            rows = db.scalars(
                page_query.order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
                .limit(batch_size)
            ).all()
            if not rows:
                return
            for row in rows:
                yield _audit_history_entry(row)
            cursor_created_at = rows[-1].created_at
            cursor_id = rows[-1].id


def _audit_history_query(
    *, search: str | None, event_type: str | None, outcome: str | None,
    username: str | None, since: datetime | None, until: datetime | None,
):
    query = select(AuditLogRecord)
    if event_type:
        query = query.where(AuditLogRecord.event_type == event_type)
    if outcome:
        query = query.where(AuditLogRecord.outcome == outcome)
    if since:
        query = query.where(AuditLogRecord.created_at >= since)
    if until:
        query = query.where(AuditLogRecord.created_at <= until)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(
            AuditLogRecord.event_type.ilike(pattern),
            AuditLogRecord.reason.ilike(pattern),
            AuditLogRecord.target_id.ilike(pattern),
            AuditLogRecord.actor_name.ilike(pattern),
        ))
    if username:
        query = query.join(UserRecord, AuditLogRecord.actor_user_id == UserRecord.id).where(
            UserRecord.username == username.casefold()
        )
    return query


def _audit_history_entry(row: AuditLogRecord) -> dict:
    metadata = json.loads(row.metadata_json)
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() + "Z",
        "actor_user_id": row.actor_user_id,
        "actor_name": row.actor_name,
        "actor_role": metadata.get("actor_role"),
        "event_type": row.event_type,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "outcome": row.outcome,
        "reason": row.reason,
        "request_id": row.request_id,
        "metadata": metadata,
    }
