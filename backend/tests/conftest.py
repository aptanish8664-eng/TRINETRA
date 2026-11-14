from __future__ import annotations

import sys
import os
import shutil
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB_DIR = Path(tempfile.gettempdir()) / f"trinetra-tests-{uuid.uuid4().hex}"
TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
TEST_DATABASE_URL = os.environ.get("TRINETRA_TEST_DATABASE_URL", "").strip()
if TEST_DATABASE_URL:
    from sqlalchemy.engine import make_url

    configured_test_url = make_url(TEST_DATABASE_URL)
    if configured_test_url.get_backend_name() == "postgresql" and configured_test_url.database != "trinetra_test":
        raise RuntimeError(
            "PostgreSQL test runs require the dedicated database name 'trinetra_test'; "
            "use a separate test database and never point pytest at user or production data."
        )
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ.pop("TRINETRA_DB_PATH", None)
else:
    os.environ.pop("DATABASE_URL", None)
    os.environ["TRINETRA_DB_PATH"] = str(TEST_DB_DIR / "test.db")
# The HTTP test client uses plain localhost; production defaults to Secure cookies.
os.environ["TRINETRA_SESSION_COOKIE_SECURE"] = "false"
os.environ["TRINETRA_AUTO_START_DEFAULT_SITL"] = "false"
# Real process environment overrides .env (load_dotenv uses override=False), so a local
# .env that enables the PX4/Gazebo UDP receivers must not bind real ports during tests.
# Tests that exercise the receivers configure their own transports explicitly.
os.environ["TRINETRA_PX4_UDP_ENABLED"] = "false"
os.environ["TRINETRA_GAZEBO_POSE_UDP_ENABLED"] = "false"


import pytest


GENERAL_PASSWORD = "General-Test-Password-953!"
OPERATOR_PASSWORD = "Operator-Test-Password-953!"


@pytest.fixture(autouse=True)
def reset_test_database():
    from app.main import runs
    from app.persistence.database import Base, engine
    from app.security import _failed_logins, _registration_attempts

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    runs.clear()
    _failed_logins.clear()
    _registration_attempts.clear()
    yield


@pytest.fixture
def general_client():
    from app.main import app
    from app.security import bootstrap_general
    from fastapi.testclient import TestClient

    username = f"general-{uuid.uuid4().hex[:8]}"
    user = bootstrap_general(
        full_name="Test General",
        username=username,
        password=GENERAL_PASSWORD,
        request_id=f"test-bootstrap-{uuid.uuid4().hex}",
    )
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": username, "password": GENERAL_PASSWORD})
    assert login.status_code == 200
    client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
    try:
        yield client, user
    finally:
        client.close()


def register_and_approve(general_client, *, role: str = "OPERATOR", username: str | None = None):
    from app.main import app
    from app.security import principal_from_record
    from app.persistence.database import SessionLocal, UserRecord
    from fastapi.testclient import TestClient

    client, _ = general_client
    username = username or f"user-{uuid.uuid4().hex[:8]}"
    applicant = TestClient(app)
    response = applicant.post("/api/auth/register", json={
        "full_name": f"Test {role.title()}",
        "username": username,
        "email": None,
        "password": OPERATOR_PASSWORD,
        "requested_role": role,
        "justification": "Automated workflow test",
    })
    assert response.status_code == 201
    user_id = response.json()["application_id"]
    decision = client.post(
        f"/api/general/users/{user_id}/decision",
        json={"decision": "approve", "role": role},
    )
    assert decision.status_code == 200
    with SessionLocal() as db:
        user = db.get(UserRecord, user_id)
        assert user is not None
        principal = principal_from_record(user)
    applicant.close()
    return principal


@pytest.fixture
def operator_client(general_client):
    from app.main import app
    from fastapi.testclient import TestClient

    operator = register_and_approve(general_client)
    client = TestClient(app)
    login = client.post("/api/auth/login", json={
        "username": operator.username,
        "password": OPERATOR_PASSWORD,
    })
    assert login.status_code == 200
    client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
    try:
        yield client, operator
    finally:
        client.close()


@pytest.fixture(scope="session", autouse=True)
def remove_test_database():
    yield
    try:
        from app.persistence.database import engine
        engine.dispose()
    finally:
        shutil.rmtree(TEST_DB_DIR, ignore_errors=True)
