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
# The tests describe the capacity rule with 5 aircraft whatever the measured default of the machine is (tests/test_multi_aircraft_planning.py pins the default itself).
os.environ["TRINETRA_SIM_MAX_AIRCRAFT"] = "5"
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


@pytest.fixture(autouse=True)
def block_real_wsl(monkeypatch):
    """Safety guard: no test may ever reach the real WSL executor or fleet manager (a real `cleanup`, `land` or `relocate` would act on the
    user's live simulator). Two layers, both active in every test:

    1. the adapters' `subprocess` is replaced, so even a test that bypasses the route-level stubs cannot start `wsl.exe`: the call is
       recorded in the returned list (`blocked_wsl_calls`) and answers like an unreachable WSL;
    2. the route-level functions get harmless defaults (`cleanup` succeeds with nothing killed, everything else is "unreachable").

    A test that needs an executor or manager installs its own fake after this fixture ran.
    """
    import subprocess
    import types

    from app import execution_routes, fleet_routes
    from app.telemetry import fleet_manager, mission_executor

    class Blocked(list):
        """The recorded would-be WSL commands; also keeps the real (unpatched) adapter functions so a test can prove the layer-1 block."""

    blocked = Blocked()
    blocked.real_invoke_fleet_manager = fleet_manager.invoke_fleet_manager
    blocked.real_invoke_mission_executor = mission_executor.invoke_mission_executor

    def blocked_run(command, *args, **kwargs):
        blocked.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="blocked: tests never start the real WSL executor or fleet manager")

    def blocked_popen(*args, **kwargs):
        blocked.append(["Popen"])
        raise OSError("blocked: tests never start the real WSL executor or fleet manager")

    stub = types.SimpleNamespace(run=blocked_run, Popen=blocked_popen, TimeoutExpired=subprocess.TimeoutExpired, CalledProcessError=subprocess.CalledProcessError,
                                 CompletedProcess=subprocess.CompletedProcess, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT)
    monkeypatch.setattr(mission_executor, "subprocess", stub)
    monkeypatch.setattr(fleet_manager, "subprocess", stub)

    def refuse_executor(action, **kwargs):
        if action == "cleanup":
            return {"cleanup": True, "killed": []}
        return {"failure": "No executor in tests.", "failure_code": "executor_unreachable"}

    def refuse_manager(*args, **kwargs):
        return {"failure": "No fleet manager in tests.", "failure_code": "manager_unavailable"}

    monkeypatch.setattr(execution_routes, "invoke_mission_executor", refuse_executor)
    monkeypatch.setattr(fleet_routes, "invoke_fleet_manager", refuse_manager)
    monkeypatch.setattr(fleet_manager, "invoke_fleet_manager", refuse_manager)
    return blocked


@pytest.fixture
def blocked_wsl_calls(block_real_wsl):
    return block_real_wsl


@pytest.fixture(scope="session", autouse=True)
def remove_test_database():
    yield
    try:
        from app.persistence.database import engine
        engine.dispose()
    finally:
        shutil.rmtree(TEST_DB_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def trained(tmp_path_factory):
    """A small trained threat model (synthetic data), shared by the threat pipeline and live threat tests."""
    from app.research.threat_data import generate_dataset
    from app.research.threat_models import train_pipeline
    root = tmp_path_factory.mktemp("threat")
    manifest = generate_dataset(root / "dataset", seed=11, flights_per_cell=8)
    metrics = train_pipeline(root / "dataset", root / "artifacts", seed=11)
    return {"root": root, "manifest": manifest, "metrics": metrics, "artifacts": root / "artifacts", "dataset": root / "dataset"}


@pytest.fixture
def model_dir(trained, monkeypatch):
    from app import research_routes
    monkeypatch.setattr(research_routes, "model_dir", trained["artifacts"])
    return trained["artifacts"]
