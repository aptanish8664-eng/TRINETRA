import importlib
import os
from pathlib import Path

from sqlalchemy import text

from app.persistence.database import create_database_engine, resolve_database_url


def test_default_session_cookie_succeeds_over_local_http(monkeypatch):
    monkeypatch.delenv("TRINETRA_SESSION_COOKIE_SECURE", raising=False)
    import app.config as app_config
    importlib.reload(app_config)
    try:
        assert app_config.SESSION_COOKIE_SECURE is False
    finally:
        monkeypatch.setenv("TRINETRA_SESSION_COOKIE_SECURE", "false")
        importlib.reload(app_config)


def test_default_database_url_uses_sqlite_path():
    url = resolve_database_url("", Path("D:/TRINETRA/backend/local.db"))

    assert url.drivername == "sqlite"
    assert url.database == "D:/TRINETRA/backend/local.db"


def test_postgresql_urls_use_psycopg_three_without_connecting():
    url = resolve_database_url("postgresql://test_user@127.0.0.1:55432/trinetra_test", Path("unused.db"))
    postgres_engine = create_database_engine(url)
    try:
        assert url.drivername == "postgresql+psycopg"
        assert postgres_engine.dialect.name == "postgresql"
    finally:
        postgres_engine.dispose()


def test_created_sqlite_engines_keep_foreign_keys_enabled():
    sqlite_engine = create_database_engine(resolve_database_url("sqlite:///:memory:", Path("unused.db")))
    try:
        with sqlite_engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    finally:
        sqlite_engine.dispose()


def test_new_audit_events_capture_actor_role():
    from app.persistence.database import SessionLocal, UserRecord, add_audit_event
    from app.security import audit_history

    actor_id = "audit-role-operator"
    with SessionLocal() as session:
        session.add(UserRecord(
            id=actor_id,
            full_name="Test Operator",
            username=actor_id,
            password_hash="test-hash",
            role="OPERATOR",
            account_status="APPROVED",
        ))
        session.commit()

    add_audit_event(
        action="test_actor_role",
        outcome="success",
        reason="Verify new audit role metadata",
        request_id="audit-role-test",
        actor_id=actor_id,
    )
    events, total = audit_history(event_type="test_actor_role")

    assert total == 1
    assert events[0]["actor_role"] == "OPERATOR"
    assert events[0]["metadata"]["actor_role"] == "OPERATOR"
