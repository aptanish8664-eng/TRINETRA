from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.authorization import Permission, permissions_for
from app.main import ManagedRun, _validate_command_state, app
from app.persistence.database import AuditLogRecord, ScenarioIntentRecord, SessionLocal, engine
from app.security import bootstrap_general
from conftest import GENERAL_PASSWORD, OPERATOR_PASSWORD, register_and_approve


@pytest.fixture(scope="module")
def trained_research_model():
    from app.research.dataset import generate_dataset
    from app.research.model import train_model

    dataset = generate_dataset(
        [101, 202, 303, 404, 505], duration=30, uav_count=2, task_count=40,
        scenario_types=("none", "sudden", "velocity_inconsistency"),
    )
    return train_model(dataset)


def test_health_and_simulation_control_and_replay(operator_client):
    client, _ = operator_client
    health = client.get("/api/health")
    assert health.json()["status"] == "ready"
    assert health.json()["database_status"] == "healthy"
    assert health.json()["detector_mode"] == "heuristic"
    assert health.json()["model_id"] is None
    components = health.json()["components"]
    assert components["api"]["status"] == "available"
    assert components["database"] == {"status": "healthy", "dialect": engine.dialect.name}
    assert components["simulation"]["kind"] == "abstract_software"
    assert components["simulation"]["managed_runs"] == health.json()["active_simulations"]
    assert components["websocket"]["status"] == "available"
    assert components["detector"]["mode"] == "heuristic"
    assert components["ai_provider"]["status"] in {"configured", "not_configured"}
    assert components["px4"]["status"] == "not_integrated"
    assert components["telemetry"] == {"status": "not_integrated", "freshness": "not_available"}
    assert components["gazebo"]["status"] == "not_integrated"
    created = client.post("/api/simulations", json={"seed": 703, "spoofing_type": "none", "duration": 20})
    assert created.status_code == 200
    run_id = created.json()["id"]
    stepped = client.post(f"/api/simulations/{run_id}/step").json()
    assert stepped["tick"] == 1
    assert client.post(f"/api/simulations/{run_id}/resume").json()["status"] == "running"
    assert client.post(f"/api/simulations/{run_id}/pause").json()["status"] == "paused"
    replay = client.get(f"/api/replay/{run_id}")
    assert replay.status_code == 200
    assert any(snapshot["tick"] == 1 for snapshot in replay.json()["snapshots"])
    assert client.get("/api/history").json()["runs"]


def test_http_request_log_is_structured_correlated_and_excludes_query(caplog):
    import json
    import logging

    caplog.set_level(logging.INFO, logger="trinetra.http")
    with TestClient(app) as client:
        response = client.get("/api/health?secret=must-not-be-logged")

    assert response.status_code == 200
    records = [record for record in caplog.records if record.name == "trinetra.http"]
    assert len(records) == 1
    event = json.loads(records[0].message)
    assert event["event"] == "http_request"
    assert event["request_id"] == response.headers["X-Request-ID"]
    assert event["method"] == "GET"
    assert event["path"] == "/api/health"
    assert event["status_code"] == 200
    assert event["duration_ms"] >= 0
    assert "must-not-be-logged" not in records[0].message


@pytest.mark.parametrize(
    ("content_length", "expected_status"),
    (("invalid", 400), ("oversized", 413)),
)
def test_rejected_http_requests_keep_request_id_and_structured_log(
    caplog, content_length, expected_status,
):
    import json
    import logging

    from app.config import MAX_HTTP_BODY_BYTES

    if expected_status == 413:
        content_length = str(MAX_HTTP_BODY_BYTES + 1)
    caplog.set_level(logging.INFO, logger="trinetra.http")
    with TestClient(app) as client:
        response = client.get("/api/health", headers={"content-length": content_length})

    assert response.status_code == expected_status
    assert response.headers["X-Request-ID"]
    records = [record for record in caplog.records if record.name == "trinetra.http"]
    assert len(records) == 1
    event = json.loads(records[0].message)
    assert event["request_id"] == response.headers["X-Request-ID"]
    assert event["status_code"] == expected_status


def test_http_request_id_correlates_with_persisted_audit_event(
    caplog, general_client, operator_client,
):
    import json
    import logging

    caplog.set_level(logging.INFO, logger="trinetra.http")
    client, _ = operator_client
    general, _ = general_client
    response = client.post("/api/auth/logout")
    assert response.status_code == 200

    audit = general.get("/api/general/audit?limit=100").json()
    logout_event = next(event for event in audit["events"] if event["event_type"] == "logout")
    request_id = response.headers["X-Request-ID"]
    assert logout_event["request_id"] == request_id

    matching_logs = [
        record for record in caplog.records
        if record.name == "trinetra.http"
        and json.loads(record.message).get("request_id") == request_id
    ]
    assert len(matching_logs) == 1
    assert json.loads(matching_logs[0].message)["status_code"] == 200


def test_px4_telemetry_api_is_authenticated_and_reports_disabled_transport(operator_client):
    from fastapi.testclient import TestClient

    operator, _ = operator_client
    guest = TestClient(app)

    assert guest.get("/api/telemetry/px4").status_code == 401
    response = operator.get("/api/telemetry/px4")
    assert response.status_code == 503
    assert response.json()["detail"] == "PX4 telemetry receiver is not enabled"
    with pytest.raises(WebSocketDisconnect):
        with guest.websocket_connect("/api/telemetry/px4/stream"):
            pass


def test_px4_telemetry_rest_and_websocket_serve_authoritative_store(operator_client, monkeypatch):
    from app import main
    from app.telemetry.px4 import Px4TelemetryStore
    from pymavlink.dialects.v20 import common as mavlink2

    operator, _ = operator_client
    guest = TestClient(app)
    store = Px4TelemetryStore()
    encoder = mavlink2.MAVLink(None, srcSystem=7, srcComponent=1)
    heartbeat = mavlink2.MAVLink_heartbeat_message(
        mavlink2.MAV_TYPE_QUADROTOR, mavlink2.MAV_AUTOPILOT_PX4, 0, 42,
        mavlink2.MAV_STATE_STANDBY, 3,
    ).pack(encoder)
    position = mavlink2.MAVLink_global_position_int_message(
        900, 10000000, 20000000, 3000, 2000, 0, 0, 0, 0,
    ).pack(encoder)
    store.ingest_datagram(heartbeat + position, ("127.0.0.1", 14601))
    monkeypatch.setattr(main, "px4_telemetry_store", store)
    monkeypatch.setattr(main, "px4_udp_transport", object())

    assert guest.get("/api/telemetry/px4").status_code == 401
    response = operator.get("/api/telemetry/px4")
    assert response.status_code == 200
    assert response.json()["vehicles"][0]["system_id"] == 7
    assert response.json()["vehicles"][0]["position"]["latitude"] == 1.0

    with operator.websocket_connect("/api/telemetry/px4/stream") as websocket:
        streamed = websocket.receive_json()
    assert streamed["source"] == "px4_mavlink"
    assert streamed["vehicles"][0]["position"]["longitude"] == 2.0
    with pytest.raises(WebSocketDisconnect):
        with guest.websocket_connect("/api/telemetry/px4/stream"):
            pass


def test_manual_task_reassignment_uses_resource_eligibility_rules():
    from app.simulation.engine import Simulation, SimulationConfig

    simulation = Simulation(SimulationConfig(uav_count=2, task_count=2, spoofing_type="none"))
    task = simulation.state["tasks"]["T-01"]
    source_id = task["uav_id"]
    target_id = "UAV-02" if source_id != "UAV-02" else "UAV-01"
    simulation.state["uavs"][target_id]["communications_available"] = False

    error = _validate_command_state(
        SimpleNamespace(simulation=simulation), "reassign_task",
        {"task_id": task["id"], "target_uav_id": target_id},
    )

    assert error == "The target aircraft is not eligible: communications unavailable."


def test_health_reports_database_failure_without_exposing_driver_details(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app import main

    class FailedSession:
        def __enter__(self):
            raise OperationalError("SELECT 1", {}, RuntimeError("private database details"))

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(main, "SessionLocal", FailedSession)
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["active_simulations"] == 0
    assert body["database"] == engine.dialect.name
    assert body["database_status"] == "unavailable"
    assert body["detector_mode"] == "heuristic"
    assert body["model_id"] is None
    assert body["components"]["database"] == {"status": "unavailable", "dialect": engine.dialect.name}
    assert body["components"]["px4"]["status"] == "not_integrated"
    assert body["components"]["telemetry"]["freshness"] == "not_available"
    assert body["components"]["gazebo"]["status"] == "not_integrated"
    assert "private database details" not in response.text


def test_health_reports_component_readiness_and_live_in_process_counts(monkeypatch):
    from app import main

    monkeypatch.setattr(
        main,
        "runs",
        {
            "running": SimpleNamespace(
                simulation=SimpleNamespace(status="running"),
                sockets={object(), object()},
            ),
            "paused": SimpleNamespace(
                simulation=SimpleNamespace(status="paused"),
                sockets=set(),
            ),
        },
    )

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["active_simulations"] == 2
    assert body["components"]["simulation"] == {
        "status": "available",
        "kind": "abstract_software",
        "managed_runs": 2,
        "running_runs": 1,
    }
    assert body["components"]["websocket"] == {"status": "available", "active_clients": 2}
    assert body["components"]["px4"] == {"status": "not_integrated"}
    assert body["components"]["telemetry"] == {
        "status": "not_integrated",
        "freshness": "not_available",
    }
    assert body["components"]["gazebo"] == {"status": "not_integrated"}


def test_websocket_sends_authoritative_state(operator_client):
    client, _ = operator_client
    created = client.post("/api/simulations", json={"spoofing_type": "none"}).json()
    with client.websocket_connect(f"/api/simulations/{created['id']}/stream") as websocket:
        state = websocket.receive_json()
        assert state["tick"] == 0
        assert state["coordinate_system"].startswith("abstract")


def test_websocket_broadcast_keeps_bounded_queue_of_latest_states():
    from app.main import ManagedRun, StreamClient, WEBSOCKET_SEND_QUEUE_SIZE, _broadcast
    from app.simulation.engine import Simulation, SimulationConfig

    async def exercise():
        managed = ManagedRun(Simulation(SimulationConfig(uav_count=2, task_count=1)))
        client = StreamClient(websocket=object())
        managed.sockets.add(client)

        for tick in range(WEBSOCKET_SEND_QUEUE_SIZE + 3):
            await _broadcast(managed, {"tick": tick})

        assert client.outbound.qsize() == WEBSOCKET_SEND_QUEUE_SIZE
        ticks = [client.outbound.get_nowait()["tick"] for _ in range(client.outbound.qsize())]
        assert ticks == list(range(3, WEBSOCKET_SEND_QUEUE_SIZE + 3))
        assert client.dropped_states == 3

    asyncio.run(exercise())


def test_websocket_sender_closes_clients_that_exceed_send_timeout(monkeypatch):
    from app import main

    class SlowSocket:
        def __init__(self):
            self.close_args = None

        async def send_json(self, _state):
            await asyncio.sleep(60)

        async def close(self, *, code, reason):
            self.close_args = (code, reason)

    async def exercise():
        socket = SlowSocket()
        client = main.StreamClient(websocket=socket)
        client.outbound.put_nowait({"tick": 1})
        await main._stream_sender(client)
        assert socket.close_args == (
            1013,
            "Telemetry stream client could not keep up.",
        )

    monkeypatch.setattr(main, "WEBSOCKET_SEND_TIMEOUT_SECONDS", 0.01)
    asyncio.run(exercise())


def test_validation_and_experiment_comparison(operator_client):
    client, _ = operator_client
    assert client.post("/api/simulations", json={"uav_count": 1}).status_code == 422
    result = client.post("/api/experiments/compare", json={
        "duration": 8, "task_count": 2, "spoofing_type": "none",
    })
    assert result.status_code == 200
    data = result.json()
    assert data["baseline"]["policy"] == "baseline"
    assert data["adaptive"]["policy"] == "adaptive"
    assert data["metadata"]["seed"] == 21
    metadata = data["metadata"]
    assert metadata["metadata_schema_version"] == 3
    assert metadata["results_schema_version"] == 3
    assert metadata["simulation_backend"] == "deterministic_python"
    assert metadata["simulation_version"] == "deterministic-abstract-2d-v2"
    assert metadata["scenario_id"] is None
    assert metadata["scenario_version_id"] is None
    assert metadata["px4_version"] is None
    assert metadata["gazebo_version"] is None
    assert metadata["dataset_version"] is None
    assert metadata["model_version"] is None
    assert metadata["detector_mode"] == "heuristic"
    assert metadata["model_confidence_calibrated"] is None
    assert len(metadata["configuration_hashes"]["baseline"]) == 64
    assert metadata["configuration_hashes"]["baseline"] != metadata["configuration_hashes"]["adaptive"]
    assert metadata["detection_metrics_scope"] == "run_level_event_presence_not_timestep_classifier_metrics"
    assert data["baseline"]["execution"]["started_at"].endswith("+00:00")
    assert data["adaptive"]["execution"]["completed_at"].endswith("+00:00")
    assert data["baseline"]["execution"]["trial_id"] != data["adaptive"]["execution"]["trial_id"]
    experiment_id = data["experiment_id"]
    stored = client.get(f"/api/experiments/{experiment_id}")
    assert stored.status_code == 200
    assert stored.json()["metadata"] == metadata
    assert stored.json()["results"]["baseline"]["execution"] == data["baseline"]["execution"]
    exported = client.get(f"/api/experiments/{experiment_id}.csv")
    assert exported.status_code == 200
    assert "continuity_score" in exported.json()["content"]
    assert exported.json()["metadata"] == metadata

    repeated = client.post("/api/experiments/compare", json={
        "duration": 8, "task_count": 2, "spoofing_type": "none",
    }).json()
    assert repeated["metadata"]["configuration_hashes"] == metadata["configuration_hashes"]
    for policy in ("baseline", "adaptive"):
        original_result = {key: value for key, value in data[policy].items() if key != "execution"}
        repeated_result = {key: value for key, value in repeated[policy].items() if key != "execution"}
        assert repeated_result == original_result


def test_configured_ml_model_drives_authoritative_simulation_state(operator_client, monkeypatch, trained_research_model):
    from app import main

    model = trained_research_model
    monkeypatch.setattr(main, "ACTIVE_ML_MODEL", model)
    client, _ = operator_client
    response = client.post("/api/simulations", json={
        "duration": 12, "task_count": 2, "spoofing_type": "velocity_inconsistency", "spoof_start": 1,
        "spoof_duration": 8, "spoof_magnitude": 3.0,
    })
    assert response.status_code == 200
    state = response.json()["state"]
    assert state["detector"] == {"mode": "trained_ml", "model_id": model["model_id"]}
    state = client.post(f"/api/simulations/{response.json()['id']}/step").json()
    detection = state["uavs"]["UAV-02"]["ml_detection"]
    assert detection["model_id"] == model["model_id"]
    assert detection["confidence_is_calibrated"] is False
    assert detection["detected"] is True


def test_model_enabled_experiment_records_detector_provenance_and_repeatability(operator_client, monkeypatch, trained_research_model):
    from app import main

    model = trained_research_model
    monkeypatch.setattr(main, "ACTIVE_ML_MODEL", model)
    client, _ = operator_client
    payload = {
        "duration": 18, "task_count": 3, "spoofing_type": "sudden", "spoof_start": 1,
        "spoof_duration": 12, "spoof_magnitude": 3.0,
    }

    first = client.post("/api/experiments/compare", json=payload)
    second = client.post("/api/experiments/compare", json=payload)

    assert first.status_code == second.status_code == 200
    first_data, second_data = first.json(), second.json()
    metadata = first_data["metadata"]
    assert metadata["detector_mode"] == "trained_ml"
    assert metadata["model_version"] == model["model_id"]
    assert metadata["model_confidence_calibrated"] is False
    assert metadata["configuration_hashes"] == second_data["metadata"]["configuration_hashes"]
    for policy in ("baseline", "adaptive"):
        result = {key: value for key, value in first_data[policy].items() if key != "execution"}
        repeated = {key: value for key, value in second_data[policy].items() if key != "execution"}
        assert result == repeated


def test_authentication_csrf_and_persistent_audit(general_client, operator_client):
    client, user = operator_client
    general, _ = general_client
    guest = TestClient(app)
    assert guest.get("/api/auth/session").status_code == 401
    assert guest.post("/api/simulations", json={}).status_code == 401
    assert client.post("/api/simulations", json={}).status_code == 200
    assert client.get("/api/auth/session").json()["user"]["account_status"] == "APPROVED"

    csrf = client.headers.pop("X-CSRF-Token")
    assert client.get("/api/history").status_code == 200
    denied = client.post("/api/simulations", json={})
    assert denied.status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.post("/api/auth/logout").json()["status"] == "signed_out"
    assert client.get("/api/history").status_code == 401

    audit = general.get("/api/general/audit?limit=100").json()
    assert audit["total"] >= 1
    assert any(
        event["event_type"] == "mission_created"
        and event["actor_user_id"] == user.id
        and event["actor_role"] == "OPERATOR"
        for event in audit["events"]
    )
    assert any(event["reason"] == "csrf_validation_failed" for event in audit["events"])
    with SessionLocal() as db:
        assert db.query(AuditLogRecord).count() == audit["total"]


def test_cors_preflight_allows_csrf_protected_http_methods():
    client = TestClient(app)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = client.options(
            "/api/scenarios/example",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type,x-csrf-token",
            },
        )
        assert response.status_code == 200
        assert method in response.headers["access-control-allow-methods"]
        assert response.headers["access-control-allow-credentials"] == "true"


def test_authentication_mutations_reject_untrusted_origins(general_client):
    client, user = general_client
    untrusted = {"Origin": "https://attacker.example"}
    login = client.post(
        "/api/auth/login",
        json={"username": user.username, "password": GENERAL_PASSWORD},
        headers=untrusted,
    )
    registration = client.post(
        "/api/auth/register",
        json={
            "full_name": "Untrusted Origin",
            "username": f"origin-{uuid.uuid4().hex[:8]}",
            "password": GENERAL_PASSWORD,
            "requested_role": "OPERATOR",
        },
        headers=untrusted,
    )
    assert login.status_code == 403
    assert registration.status_code == 403


def test_exact_two_role_permissions_and_user_management(general_client, operator_client):
    general, _ = general_client
    operator, operator_user = operator_client
    assert set(permissions_for("GENERAL")) == set(Permission) - {Permission.COMMAND_REQUEST}
    assert permissions_for("OPERATOR")
    client_roles = {item["role"] for item in general.get("/api/general/users").json()["users"]}
    assert client_roles <= {"GENERAL", "OPERATOR", None}
    assert operator.get("/api/general/users").status_code == 403
    assert operator.post(
        f"/api/general/users/{operator_user.id}/decision",
        json={"decision": "reject"},
    ).status_code == 403
    assert operator.post(f"/api/general/users/{operator_user.id}/activate").status_code == 403

    self_role = general.put(
        f"/api/general/users/{general_client[1].id}/role",
        json={"role": "OPERATOR"},
    )
    assert self_role.status_code == 409
    self_deactivation = general.post(f"/api/general/users/{general_client[1].id}/deactivate")
    assert self_deactivation.status_code == 409


def test_registration_hold_approval_and_role_assignment(general_client):
    general, _ = general_client
    username = f"pending-{uuid.uuid4().hex[:8]}"
    applicant = TestClient(app)
    registration = applicant.post("/api/auth/register", json={
        "full_name": "Pending User",
        "username": username,
        "email": None,
        "password": OPERATOR_PASSWORD,
        "requested_role": "OPERATOR",
        "justification": "Needs operator access.",
    })
    assert registration.status_code == 201
    user_id = registration.json()["application_id"]
    assert registration.json()["status"] == "PENDING"
    pending_users = general.get("/api/general/users?status=PENDING")
    assert pending_users.status_code == 200
    registered_user = next(item for item in pending_users.json()["users"] if item["id"] == user_id)
    assert registered_user["full_name"] == "Pending User"
    assert registered_user["username"] == username
    assert applicant.post("/api/auth/login", json={
        "username": username, "password": OPERATOR_PASSWORD,
    }).status_code == 401
    assert applicant.get(f"/api/auth/registration/{user_id}").json()["status"] == "PENDING"

    assert general.post(
        f"/api/general/users/{user_id}/decision", json={"decision": "hold"},
    ).status_code == 200
    assert applicant.get(f"/api/auth/registration/{user_id}").json()["status"] == "ON_HOLD"
    assert general.post(
        f"/api/general/users/{user_id}/decision", json={"decision": "resume"},
    ).status_code == 200
    approved = general.post(
        f"/api/general/users/{user_id}/decision",
        json={"decision": "approve", "role": "OPERATOR"},
    )
    assert approved.status_code == 200
    approval_event = general.get(f"/api/general/audit?event_type=registration_approved&limit=1").json()
    assert approval_event["events"][0]["actor_role"] == "GENERAL"

    login = applicant.post("/api/auth/login", json={
        "username": username, "password": OPERATOR_PASSWORD,
    })
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "OPERATOR"
    assert login.json()["user"]["account_status"] == "APPROVED"
    assert set(login.json()["permissions"]) == {permission.value for permission in permissions_for("OPERATOR")}
    assert "clearance" not in login.json()
    assert applicant.get("/api/general/users").status_code == 403
    assert applicant.get(f"/api/auth/registration/{user_id}").json()["status"] == "APPROVED"
    assert "password_hash" not in general.get("/api/general/users").text

    rejected_username = f"rejected-{uuid.uuid4().hex[:8]}"
    rejected_applicant = TestClient(app)
    rejected_registration = rejected_applicant.post("/api/auth/register", json={
        "full_name": "Rejected User",
        "username": rejected_username,
        "email": None,
        "password": OPERATOR_PASSWORD,
        "requested_role": "OPERATOR",
        "justification": "Controlled rejection workflow test.",
    })
    assert rejected_registration.status_code == 201
    rejected_id = rejected_registration.json()["application_id"]
    rejected_users = general.get("/api/general/users?status=PENDING").json()["users"]
    assert any(item["id"] == rejected_id for item in rejected_users)
    assert general.post(
        f"/api/general/users/{rejected_id}/decision",
        json={"decision": "reject"},
    ).status_code == 200
    assert rejected_applicant.post("/api/auth/login", json={
        "username": rejected_username,
        "password": OPERATOR_PASSWORD,
    }).status_code == 401
    assert rejected_applicant.get(f"/api/auth/registration/{rejected_id}").json()["status"] == "REJECTED"
    rejected_event = general.get(
        f"/api/general/audit?event_type=registration_rejected&limit=1"
    ).json()["events"][0]
    assert rejected_event["target_id"] == rejected_id
    assert rejected_event["actor_role"] == "GENERAL"


def test_operator_run_ownership_and_general_global_scope(general_client):
    general, _ = general_client
    owner = register_and_approve(general_client, username=f"owner-{uuid.uuid4().hex[:8]}")
    observer = register_and_approve(general_client, username=f"observer-{uuid.uuid4().hex[:8]}")
    owner_client = TestClient(app)
    observer_client = TestClient(app)
    for client, user in ((owner_client, owner), (observer_client, observer)):
        login = client.post("/api/auth/login", json={
            "username": user.username, "password": OPERATOR_PASSWORD,
        })
        client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
    owned_run = owner_client.post("/api/simulations", json={"spoofing_type": "none"}).json()["id"]
    foreign_run = observer_client.post("/api/simulations", json={"spoofing_type": "none"}).json()["id"]
    assert owner_client.get(f"/api/simulations/{foreign_run}").status_code == 404
    assert general.get(f"/api/simulations/{owned_run}").status_code == 200
    assert general.get(f"/api/simulations/{foreign_run}").status_code == 200


def test_role_change_deactivation_revokes_existing_sessions(general_client):
    general, _ = general_client
    operator = register_and_approve(general_client, username=f"manage-{uuid.uuid4().hex[:8]}")
    user_client = TestClient(app)
    login = user_client.post("/api/auth/login", json={
        "username": operator.username, "password": OPERATOR_PASSWORD,
    })
    user_client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
    assert general.put(
        f"/api/general/users/{operator.id}/role", json={"role": "GENERAL"},
    ).status_code == 200
    assert user_client.get("/api/auth/session").status_code == 401

    new_login = user_client.post("/api/auth/login", json={
        "username": operator.username, "password": OPERATOR_PASSWORD,
    })
    user_client.headers["X-CSRF-Token"] = new_login.json()["csrf_token"]
    assert general.post(f"/api/general/users/{operator.id}/deactivate").status_code == 200
    assert user_client.get("/api/auth/session").status_code == 401


def test_scenario_approval_binds_exact_immutable_version(general_client, operator_client):
    general, _ = general_client
    operator, _ = operator_client
    config = {
        "uav_count": 4, "task_count": 10, "seed": 21, "duration": 30,
        "spoofing_type": "none", "target_uav": "UAV-02", "spoof_start": 15,
        "spoof_duration": 35, "spoof_magnitude": 3.0, "response_policy": "adaptive",
        "detector_threshold": None,
    }
    created = operator.post("/api/scenarios", json={"title": "Approval test", "config": config})
    assert created.status_code == 201
    scenario = created.json()
    version_one = scenario["versions"][0]
    submitted = operator.post(
        f"/api/scenarios/{scenario['id']}/versions/{version_one['id']}/submit",
    )
    assert submitted.status_code == 200
    assert operator.get("/api/general/scenario-approvals").status_code == 403
    queue = general.get("/api/general/scenario-approvals").json()["requests"]
    assert queue[0]["scenario_version_id"] == version_one["id"]
    decision = general.post(
        f"/api/general/scenario-approvals/{submitted.json()['id']}/decision",
        json={"decision": "approve", "reason": "Version reviewed and approved."},
    )
    assert decision.status_code == 200
    assert operator.post(
        f"/api/scenarios/{scenario['id']}/execute", json={"version_id": version_one["id"]},
    ).status_code == 200

    config["seed"] = 22
    changed = operator.put(
        f"/api/scenarios/{scenario['id']}",
        json={"title": "Approval test", "config": config},
    )
    assert changed.status_code == 200
    version_two = changed.json()["versions"][0]
    assert version_two["version_number"] == version_one["version_number"] + 1
    assert version_two["id"] != version_one["id"]
    assert operator.post(
        f"/api/scenarios/{scenario['id']}/execute", json={"version_id": version_one["id"]},
    ).status_code == 403
    assert operator.post(
        f"/api/scenarios/{scenario['id']}/execute", json={"version_id": version_two["id"]},
    ).status_code == 403


def test_scenario_intent_is_versioned_and_legacy_updates_preserve_it(operator_client):
    operator, _ = operator_client
    config = {
        "uav_count": 4, "task_count": 10, "seed": 21, "duration": 30,
        "spoofing_type": "none", "target_uav": "UAV-02", "spoof_start": 15,
        "spoof_duration": 35, "spoof_magnitude": 3.0, "response_policy": "adaptive",
        "detector_threshold": None,
    }
    intent = {
        "mission_objectives": ["Inspect abstract task markers"],
        "proposed_tasks": [{"description": "Review task alpha", "task_reference": "T-01", "priority": 2}],
        "uav_role_suggestions": [{"role": "search", "count": 2, "rationale": "Advisory role."}],
        "operating_area_notes": "Abstract grid sector east",
        "environment_notes": "Calm conditions for repeatability",
        "constraints": ["Roles and task assignments remain advisory."],
    }
    created = operator.post("/api/scenarios", json={"title": "Intent test", "config": config, "intent": intent})
    assert created.status_code == 201, created.text
    scenario = created.json()
    version_one = scenario["versions"][0]
    assert version_one["intent"]["mission_objectives"] == intent["mission_objectives"]

    unchanged = operator.put(
        f"/api/scenarios/{scenario['id']}",
        json={"title": "Intent test", "config": config, "intent": intent},
    )
    assert unchanged.status_code == 200
    assert len(unchanged.json()["versions"]) == 1

    edited_intent = {**intent, "mission_objectives": ["Updated objective"]}
    same_config = operator.put(
        f"/api/scenarios/{scenario['id']}",
        json={"title": "Intent test", "config": config, "intent": edited_intent},
    )
    assert same_config.status_code == 200, same_config.text
    version_two = same_config.json()["versions"][0]
    assert version_two["version_number"] == 2
    assert version_two["intent"]["mission_objectives"] == ["Updated objective"]
    assert same_config.json()["versions"][1]["intent"]["mission_objectives"] == intent["mission_objectives"]

    config["seed"] = 22
    legacy_update = operator.put(
        f"/api/scenarios/{scenario['id']}", json={"title": "Intent test", "config": config},
    )
    assert legacy_update.status_code == 200, legacy_update.text
    version_three = legacy_update.json()["versions"][0]
    assert version_three["version_number"] == 3
    assert version_three["intent"] == version_two["intent"]


def test_scenario_intent_validation_and_approval_hash_cover_intent(general_client, operator_client):
    general, _ = general_client
    operator, _ = operator_client
    config = {
        "uav_count": 4, "task_count": 10, "seed": 21, "duration": 30,
        "spoofing_type": "none", "target_uav": "UAV-02", "spoof_start": 15,
        "spoof_duration": 35, "spoof_magnitude": 3.0, "response_policy": "adaptive",
        "detector_threshold": None,
    }
    invalid = {
        "mission_objectives": [], "proposed_tasks": [],
        "uav_role_suggestions": [{"role": "search", "count": 5}],
        "operating_area_notes": "", "environment_notes": "", "constraints": [],
    }
    assert operator.post("/api/scenarios", json={"title": "Bad roles", "config": config, "intent": invalid}).status_code == 422
    invalid_reference = {**invalid, "uav_role_suggestions": [], "proposed_tasks": [{
        "description": "Unsupported task reference", "task_reference": "T-99", "priority": 1,
    }]}
    assert operator.post("/api/scenarios", json={"title": "Bad task", "config": config, "intent": invalid_reference}).status_code == 422
    coordinate_intent = {**invalid, "uav_role_suggestions": [], "operating_area_notes": "37.7749, -122.4194"}
    assert operator.post("/api/scenarios", json={"title": "Bad area", "config": config, "intent": coordinate_intent}).status_code == 422

    intent = {**coordinate_intent, "operating_area_notes": "Abstract grid only"}
    created = operator.post("/api/scenarios", json={"title": "Hash test", "config": config, "intent": intent})
    assert created.status_code == 201, created.text
    scenario = created.json()
    version = scenario["versions"][0]
    approval = operator.post(f"/api/scenarios/{scenario['id']}/versions/{version['id']}/submit")
    assert approval.status_code == 200
    review_item = general.get("/api/general/scenario-approvals").json()["requests"][0]
    assert review_item["intent"]["operating_area_notes"] == "Abstract grid only"
    assert review_item["intent"]["constraints"] == []
    decided = general.post(
        f"/api/general/scenario-approvals/{approval.json()['id']}/decision",
        json={"decision": "approve", "reason": "Intent reviewed."},
    )
    assert decided.status_code == 200
    assert operator.post(
        f"/api/scenarios/{scenario['id']}/execute", json={"version_id": version["id"]},
    ).status_code == 200

    with SessionLocal() as db:
        intent_row = db.get(ScenarioIntentRecord, version["id"])
        assert intent_row is not None
        intent_row.intent_json = '{"mission_objectives":["tampered"]}'
        db.commit()
    assert operator.post(
        f"/api/scenarios/{scenario['id']}/execute", json={"version_id": version["id"]},
    ).status_code == 403


def test_per_uav_fault_profiles_are_versioned_and_multi_profile_runs_are_blocked(operator_client):
    operator, _ = operator_client
    config = {
        "uav_count": 4, "task_count": 10, "seed": 21, "duration": 30,
        "spoofing_type": "none", "target_uav": "UAV-01", "spoof_start": 0,
        "spoof_duration": 10, "spoof_magnitude": 1.0, "response_policy": "adaptive",
        "detector_threshold": None,
    }
    intent = {
        "mission_objectives": ["Compare software-only fault profiles"],
        "proposed_tasks": [], "uav_role_suggestions": [],
        "operating_area_notes": "", "environment_notes": "", "constraints": [],
        "uav_fault_assignments": [
            {"uav_id": "UAV-01", "fault_type": "sudden", "spoof_start": 2, "spoof_duration": 8, "spoof_magnitude": 1.5},
            {"uav_id": "UAV-02", "fault_type": "noise", "spoof_start": 3, "spoof_duration": 9, "spoof_magnitude": 0.0},
            {"uav_id": "UAV-03", "fault_type": "none", "spoof_start": 0, "spoof_duration": 1, "spoof_magnitude": 0.0},
        ],
    }
    created = operator.post("/api/scenarios", json={"title": "Per-UAV fault profiles", "config": config, "intent": intent})
    assert created.status_code == 201, created.text
    scenario = created.json()
    version = scenario["versions"][0]
    assert version["intent"]["uav_fault_assignments"] == intent["uav_fault_assignments"]
    sandbox = operator.post(
        f"/api/scenarios/{scenario['id']}/versions/{version['id']}/sandbox",
    )
    assert sandbox.status_code == 409
    assert "only one active GPS fault at a time" in sandbox.json()["detail"]
    execution = operator.post(
        f"/api/scenarios/{scenario['id']}/execute", json={"version_id": version["id"]},
    )
    assert execution.status_code == 409
    assert "only one active GPS fault at a time" in execution.json()["detail"]

    invalid = {**intent, "uav_fault_assignments": [{
        "uav_id": "UAV-05", "fault_type": "sudden", "spoof_start": 0,
        "spoof_duration": 1, "spoof_magnitude": 1.0,
    }]}
    rejected = operator.post("/api/scenarios", json={"title": "Invalid fault target", "config": config, "intent": invalid})
    assert rejected.status_code == 422


def test_geographic_mission_is_versioned_and_never_runs_in_abstract_simulator(
    general_client, operator_client, monkeypatch,
):
    general, _ = general_client
    operator, _ = operator_client
    from app import main

    class TelemetryStore:
        def snapshot(self):
            return {"vehicles": [{
                "id": "px4-7", "status": "connected",
                "position": {"valid": True},
            }]}

    monkeypatch.setattr(main, "px4_udp_transport", object())
    monkeypatch.setattr(main, "px4_telemetry_store", TelemetryStore())
    config = {
        "uav_count": 4, "task_count": 10, "seed": 21, "duration": 30,
        "spoofing_type": "none", "target_uav": "UAV-02", "spoof_start": 15,
        "spoof_duration": 35, "spoof_magnitude": 3.0, "response_policy": "adaptive",
        "detector_threshold": None,
    }
    intent = {
        "mission_objectives": ["Area surveying"],
        "proposed_tasks": [], "uav_role_suggestions": [],
        "operating_area_notes": "", "environment_notes": "", "constraints": [],
        "geographic_mission": {
            "objective": "area_survey",
            "start_location": {"latitude": 12.9716, "longitude": 77.5946},
            "destination": None,
            "survey_zones": [{
                "id": "zone-1", "name": "North survey",
                "vertices": [
                    {"latitude": 13.0, "longitude": 77.6},
                    {"latitude": 13.1, "longitude": 77.6},
                    {"latitude": 13.05, "longitude": 77.7},
                ],
            }],
            "uav_assignments": [{
                "uav_id": "px4-7", "role": "area_survey",
                "task": "Survey North survey", "zone_id": None,
                "zone_ids": ["zone-1"],
                "start_location": {"latitude": 12.98, "longitude": 77.59},
                "destination": {"latitude": 13.02, "longitude": 77.62},
                "waypoints": [{"latitude": 13.0, "longitude": 77.61}],
            }],
            "constraints": [], "ai_recommendations": [],
        },
    }
    created = operator.post("/api/scenarios", json={"title": "Geographic test", "config": config, "intent": intent})
    assert created.status_code == 201, created.text
    scenario = created.json()
    version_one = scenario["versions"][0]
    assert version_one["intent"]["geographic_mission"]["start_location"] == intent["geographic_mission"]["start_location"]
    assignment = version_one["intent"]["geographic_mission"]["uav_assignments"][0]
    assert assignment["start_location"] == intent["geographic_mission"]["uav_assignments"][0]["start_location"]
    assert assignment["destination"] == intent["geographic_mission"]["uav_assignments"][0]["destination"]
    assert assignment["waypoints"] == intent["geographic_mission"]["uav_assignments"][0]["waypoints"]
    assert operator.post(
        f"/api/scenarios/{scenario['id']}/versions/{version_one['id']}/sandbox",
    ).status_code == 409

    updated_config = {**config, "seed": 22}
    updated = operator.put(
        f"/api/scenarios/{scenario['id']}",
        json={"title": "Geographic test", "config": updated_config},
    )
    assert updated.status_code == 200
    version_two = updated.json()["versions"][0]
    assert version_two["intent"]["geographic_mission"] == version_one["intent"]["geographic_mission"]
    submitted = operator.post(
        f"/api/scenarios/{scenario['id']}/versions/{version_two['id']}/submit",
    )
    assert submitted.status_code == 200
    decision = general.post(
        f"/api/general/scenario-approvals/{submitted.json()['id']}/decision",
        json={"decision": "approve", "reason": "Geographic intent reviewed."},
    )
    assert decision.status_code == 200
    assert operator.post(
        f"/api/scenarios/{scenario['id']}/execute",
        json={"version_id": version_two["id"]},
    ).status_code == 409


def test_geographic_mission_rejects_self_intersecting_zone(operator_client, monkeypatch):
    from app import main

    class TelemetryStore:
        def snapshot(self):
            return {"vehicles": [{
                "id": "px4-7", "status": "connected",
                "position": {"valid": True},
            }]}

    monkeypatch.setattr(main, "px4_udp_transport", object())
    monkeypatch.setattr(main, "px4_telemetry_store", TelemetryStore())
    operator, _ = operator_client
    config = {
        "uav_count": 4, "task_count": 10, "seed": 21, "duration": 30,
        "spoofing_type": "none", "target_uav": "UAV-02", "spoof_start": 15,
        "spoof_duration": 35, "spoof_magnitude": 3.0, "response_policy": "adaptive",
        "detector_threshold": None,
    }
    intent = {
        "mission_objectives": ["Area surveying"],
        "proposed_tasks": [], "uav_role_suggestions": [],
        "operating_area_notes": "", "environment_notes": "", "constraints": [],
        "geographic_mission": {
            "objective": "area_survey",
            "start_location": {"latitude": 12.0, "longitude": 77.0},
            "survey_zones": [{
                "id": "zone-1", "name": "Crossed boundary",
                "vertices": [
                    {"latitude": 12.0, "longitude": 77.0},
                    {"latitude": 13.0, "longitude": 78.0},
                    {"latitude": 12.0, "longitude": 78.0},
                    {"latitude": 13.0, "longitude": 77.0},
                ],
            }],
            "uav_assignments": [{
                "uav_id": "px4-7", "role": "area_survey",
                "task": "Survey zone", "zone_id": "zone-1",
            }],
        },
    }
    response = operator.post("/api/scenarios", json={"title": "Invalid geography", "config": config, "intent": intent})
    assert response.status_code == 422


def test_audit_filtering_and_csv_export(general_client, operator_client):
    general, _ = general_client
    operator, _ = operator_client
    assert operator.post("/api/simulations", json={"spoofing_type": "none"}).status_code == 200
    filtered = general.get("/api/general/audit?event_type=mission_created&limit=1")
    assert filtered.status_code == 200
    payload = filtered.json()
    assert payload["total"] == 1
    assert payload["events"][0]["event_type"] == "mission_created"
    assert payload["events"][0]["actor_role"] == "OPERATOR"
    exported = general.get("/api/general/audit.csv")
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    assert "mission_created" in exported.text
    assert "actor_role" in exported.text.splitlines()[0]
    assert "OPERATOR" in exported.text


def test_audit_csv_exports_all_filtered_rows_with_csv_escaping(general_client, operator_client):
    import csv
    import io
    from datetime import datetime, timedelta

    general, general_user = general_client
    operator, _ = operator_client
    created_at = datetime(2025, 1, 1)
    reason = 'csv export, "complete"\nwith a second line'
    with SessionLocal() as db:
        db.add_all([
            AuditLogRecord(
                id=f"audit-csv-{index:04d}-{uuid.uuid4().hex}",
                actor_user_id=general_user.id,
                actor_name=general_user.full_name,
                event_type="audit_csv_export_test",
                target_type="test",
                target_id=f"target-{index}",
                outcome="success",
                reason=reason,
                request_id=f"audit-csv-request-{index}",
                metadata_json='{"actor_role":"GENERAL"}',
                created_at=created_at + timedelta(seconds=index // 3),
            )
            for index in range(450)
        ])
        db.commit()

    exported = general.get(
        "/api/general/audit.csv",
        params={
            "event_type": "audit_csv_export_test",
            "search": "csv export",
            "outcome": "success",
            "username": general_user.username,
            "since": "2025-01-01T00:00:00Z",
            "until": "2025-01-01T00:02:29Z",
        },
    )
    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert len(rows) == 450
    assert rows[0]["actor_role"] == "GENERAL"
    assert rows[0]["reason"] == reason
    assert operator.get("/api/general/audit.csv").status_code == 403


def test_first_general_bootstrap_is_explicit_and_one_time():
    created = bootstrap_general(
        full_name="Initial General",
        username=f"initial-{uuid.uuid4().hex[:8]}",
        password=GENERAL_PASSWORD,
        request_id="bootstrap-test",
    )
    assert created.role == "GENERAL"
    with pytest.raises(ValueError, match="only available before the first user exists"):
        bootstrap_general(
            full_name="Second General",
            username=f"second-{uuid.uuid4().hex[:8]}",
            password=GENERAL_PASSWORD,
            request_id="bootstrap-second-test",
        )
