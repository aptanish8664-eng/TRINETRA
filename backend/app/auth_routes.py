from __future__ import annotations

import csv
import io
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .authorization import Permission, has_permission, permissions_for
from .config import ALLOWED_ORIGINS, SESSION_COOKIE_NAME, SESSION_COOKIE_SECURE, SESSION_TTL_SECONDS
from .persistence.database import add_audit_event
from .schemas import (
    LoginRequest,
    RegistrationDecisionRequest,
    RegistrationRequest,
    UserRoleRequest,
)
from .security import (
    AuthContext,
    UserPrincipal,
    audit_denial,
    audit_history,
    authenticate,
    clear_login_failures,
    create_session,
    decide_registration,
    get_auth_context,
    iter_audit_history,
    list_users,
    login_is_throttled,
    note_login_failure,
    note_registration_attempt,
    public_user,
    registration_is_throttled,
    registration_status,
    register_user,
    revoke_session,
    set_user_active,
    update_user_role,
)


router = APIRouter()


def require_permission(permission: Permission):
    def dependency(request: Request, context: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not has_permission(context.user.role, permission):
            audit_denial(request, f"permission_required:{permission.value}", context.user)
            raise HTTPException(status_code=403, detail=f"Permission required: {permission.value}")
        return context
    return dependency


def require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        audit_denial(request, "untrusted_origin")
        raise HTTPException(status_code=403, detail="Request origin is not allowed")


def require_csrf(request: Request, context: AuthContext = Depends(get_auth_context)) -> AuthContext:
    require_trusted_origin(request)
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied or not secrets.compare_digest(supplied, context.csrf_token):
        audit_denial(request, "csrf_validation_failed", context.user)
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    return context


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "request-unavailable")


def _audit_login(
    request: Request,
    event_type: str,
    outcome: str,
    reason: str,
    user: UserPrincipal | None = None,
    metadata: dict | None = None,
) -> None:
    add_audit_event(
        action=event_type,
        outcome=outcome,
        reason=reason,
        request_id=_request_id(request),
        actor_id=user.id if user else None,
        actor_name=user.full_name if user else None,
        metadata={"client_ip": request.client.host if request.client else "unknown", **(metadata or {})},
    )


def _session_payload(context: AuthContext) -> dict:
    return {
        "user": public_user(context.user),
        "permissions": sorted(
            permission.value for permission in permissions_for(context.user.role)
        ),
        "csrf_token": context.csrf_token,
        "expires_at": context.expires_at.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
    }


@router.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    require_trusted_origin(request)
    username = payload.username.strip().casefold()
    client_ip = request.client.host if request.client else "unknown"
    if login_is_throttled(client_ip):
        _audit_login(request, "login_throttled", "denied", "Login rate limit exceeded")
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")

    user, authenticated = authenticate(username, payload.password.get_secret_value())
    if not authenticated or user is None:
        note_login_failure(client_ip)
        _audit_login(
            request,
            "login_failed",
            "failed",
            "Invalid credentials or inactive account",
            metadata={"attempted_username": username},
        )
        raise HTTPException(status_code=401, detail="Invalid username or password")

    clear_login_failures(client_ip)
    raw_token, csrf_token, session_id, expires_at = create_session(user)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="strict",
        path="/api",
    )
    _audit_login(request, "login_succeeded", "success", "User session created", user)
    return _session_payload(AuthContext(user, session_id, csrf_token, expires_at))


@router.post("/api/auth/register", status_code=201)
def register(payload: RegistrationRequest, request: Request) -> dict:
    require_trusted_origin(request)
    client_ip = request.client.host if request.client else "unknown"
    if registration_is_throttled(client_ip):
        _audit_login(request, "registration_throttled", "denied", "Registration rate limit exceeded")
        raise HTTPException(status_code=429, detail="Too many registration attempts. Try again later.")
    note_registration_attempt(client_ip)
    try:
        user_id = register_user(
            full_name=payload.full_name,
            username=payload.username,
            email=payload.email,
            password=payload.password.get_secret_value(),
            requested_role=payload.requested_role,
            registration_note=payload.justification,
            request_id=_request_id(request),
        )
    except ValueError as exc:
        _audit_login(request, "registration_rejected", "denied", str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"application_id": user_id, "status": "PENDING"}


@router.get("/api/auth/registration/{application_id}")
def get_registration_status(application_id: str) -> dict:
    status = registration_status(application_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Registration request not found")
    return status


@router.get("/api/auth/session")
def current_session(context: AuthContext = Depends(get_auth_context)) -> dict:
    return _session_payload(context)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, context: AuthContext = Depends(require_csrf)) -> dict:
    revoke_session(context.session_id)
    _audit_login(request, "logout", "success", "User session revoked", context.user)
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/api",
        secure=SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )
    return {"status": "signed_out"}


@router.get("/api/general/users")
def users(
    status: Optional[str] = None,
    context: AuthContext = Depends(require_permission(Permission.USER_MANAGE)),
) -> dict:
    valid_statuses = {"PENDING", "ON_HOLD", "APPROVED", "REJECTED", "DEACTIVATED"}
    if status and status.upper() not in valid_statuses:
        raise HTTPException(status_code=422, detail="Unsupported account status")
    return {"users": list_users(status)}


@router.post("/api/general/users/{user_id}/decision")
def review_registration(
    user_id: str,
    payload: RegistrationDecisionRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.REGISTRATION_REVIEW)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    try:
        decide_registration(
            user_id,
            decision=payload.decision,
            role=payload.role,
            rejection_message=payload.rejection_message,
            actor=context.user,
            request_id=_request_id(request),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        audit_denial(request, "registration_decision_rejected", context.user)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": payload.decision.upper()}


@router.put("/api/general/users/{user_id}/role")
def change_user_role(
    user_id: str,
    payload: UserRoleRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.USER_MANAGE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    try:
        update_user_role(user_id, payload.role, context.user, _request_id(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        audit_denial(request, "user_role_change_rejected", context.user)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "updated"}


@router.post("/api/general/users/{user_id}/{action}")
def update_user_status(
    user_id: str,
    action: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.USER_MANAGE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    if action not in {"activate", "deactivate"}:
        raise HTTPException(status_code=404, detail="User action not found")
    try:
        set_user_active(
            user_id,
            action == "activate",
            context.user,
            _request_id(request),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        audit_denial(request, "user_status_change_rejected", context.user)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "activated" if action == "activate" else "deactivated"}


@router.get("/api/general/audit")
def get_audit_history(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    event_type: Optional[str] = None,
    outcome: Optional[str] = None,
    username: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    context: AuthContext = Depends(require_permission(Permission.AUDIT_READ)),
) -> dict:
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid audit pagination")
    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00")).replace(tzinfo=None) if since else None
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00")).replace(tzinfo=None) if until else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Audit dates must be ISO 8601 timestamps") from exc
    entries, total = audit_history(
        limit=limit,
        offset=offset,
        search=search,
        event_type=event_type,
        outcome=outcome,
        username=username,
        since=since_dt,
        until=until_dt,
    )
    return {"events": entries, "total": total, "limit": limit, "offset": offset}


@router.get("/api/general/audit.csv")
def export_audit_history(
    search: Optional[str] = None,
    event_type: Optional[str] = None,
    outcome: Optional[str] = None,
    username: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    context: AuthContext = Depends(require_permission(Permission.AUDIT_READ)),
) -> StreamingResponse:
    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00")).replace(tzinfo=None) if since else None
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00")).replace(tzinfo=None) if until else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Audit dates must be ISO 8601 timestamps") from exc
    fieldnames = (
        "created_at", "actor_user_id", "actor_name", "actor_role", "event_type",
        "target_type", "target_id", "outcome", "reason", "request_id",
    )

    def csv_rows():
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for entry in iter_audit_history(
            search=search, event_type=event_type, outcome=outcome,
            username=username, since=since_dt, until=until_dt,
        ):
            writer.writerow({key: entry.get(key) for key in fieldnames})
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    return StreamingResponse(
        csv_rows(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trinetra-audit.csv"},
    )
