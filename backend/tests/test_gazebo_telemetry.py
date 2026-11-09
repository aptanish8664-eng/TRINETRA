from __future__ import annotations

import asyncio
import json
import socket

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from pymavlink.dialects.v20 import common as mavlink2

from app import main
from app.telemetry.gazebo import GazeboPoseProtocol, GazeboPoseStore
from app.telemetry.px4 import Px4TelemetryStore
from tools.gazebo_wsl_pose_bridge import iter_pose_messages, parse_pose_message


POSE_MESSAGE = '''header {
  stamp {
    sec: 12
    nsec: 500000000
  }
}
pose {
  name: "ground_plane"
  position {
  }
  orientation {
    w: 1
  }
}
pose {
  name: "x500_0"
  position {
    x: 1.5
    y: -2
    z: 0.25
  }
  orientation {
    z: 0.707106781
    w: 0.707106781
  }
}
pose {
  name: "x500_1"
  position {
    x: 5
  }
  orientation {
    w: 1
  }
}
'''


def snapshot_payload(model_names: tuple[str, ...] = ("x500_0",)) -> bytes:
    return json.dumps({
        "source": "gazebo_pose_info",
        "world": "default",
        "models": [{
            "model_name": model_name,
            "position_m": {"x": index * 5 + 1.5, "y": -2.0, "z": 0.25},
            "orientation_quaternion": {"x": 0, "y": 0, "z": 0.70710678, "w": 0.70710678},
        } for index, model_name in enumerate(model_names)],
    }).encode()


def test_text_pose_parser_frames_and_keeps_only_x500_models():
    messages = list(iter_pose_messages(POSE_MESSAGE.splitlines(keepends=True)))
    assert len(messages) == 1
    snapshot = parse_pose_message(messages[0], world="default")
    assert snapshot is not None
    assert snapshot["source"] == "gazebo_pose_info"
    assert snapshot["sim_time_seconds"] == 12.5
    assert [model["model_name"] for model in snapshot["models"]] == ["x500_0", "x500_1"]
    assert snapshot["models"][0]["position_m"] == {"x": 1.5, "y": -2.0, "z": 0.25}


def test_gazebo_store_validates_and_tracks_actual_pose_snapshots():
    store = GazeboPoseStore(stale_after_seconds=1, disconnected_after_seconds=3)
    assert store.ingest_datagram(snapshot_payload()) is True
    vehicle = store.snapshot()["models"][0]
    assert vehicle["model_name"] == "x500_0"
    assert vehicle["status"] == "connected"
    assert vehicle["position_m"]["x"] == 1.5
    assert len(vehicle["flight_trail"]) == 1
    assert store.ingest_datagram(b'{"source":"other"}') is False
    assert store.snapshot()["transport"]["invalid_datagrams"] == 1


def test_gazebo_store_marks_stale_without_generating_new_poses():
    store = GazeboPoseStore(stale_after_seconds=1, disconnected_after_seconds=3)
    assert store.ingest_datagram(snapshot_payload()) is True
    last = store._last_datagram_monotonic
    snapshot = store.snapshot(now_monotonic=last + 2)
    assert snapshot["transport"]["status"] == "stale"
    assert snapshot["models"][0]["status"] == "stale"
    assert len(snapshot["models"][0]["flight_trail"]) == 1


def test_gazebo_protocol_accepts_only_configured_udp_source():
    store = GazeboPoseStore()
    protocol = GazeboPoseProtocol(store, allowed_source_ip="127.0.0.1", allowed_source_port=24611)
    protocol.datagram_received(snapshot_payload(), ("127.0.0.1", 24611))
    protocol.datagram_received(snapshot_payload(), ("127.0.0.1", 24612))
    assert len(store.snapshot()["models"]) == 1
    assert protocol.rejected_sources == 1


def test_delta_stream_snapshot_excludes_repeated_trail_and_returns_new_poses_only():
    store = GazeboPoseStore()
    store.ingest_datagram(snapshot_payload())
    first = store.snapshot()
    first_point = first["models"][0]["flight_trail"][0]

    import time
    time.sleep(0.002)
    next_payload = json.loads(snapshot_payload())
    next_payload["models"][0]["position_m"]["x"] = 9.0
    store.ingest_datagram(json.dumps(next_payload).encode())
    update = store.stream_snapshot(since=first["generated_at"])

    model = update["models"][0]
    assert update["stream_mode"] == "trail_delta_v1"
    assert "flight_trail" not in model
    assert model["flight_trail_delta"] == [{
        "x": 9.0, "y": -2.0, "z": 0.25,
        "received_at": model["flight_trail_delta"][0]["received_at"],
    }]
    assert model["flight_trail_delta"][0]["received_at"] > first_point["received_at"]


def test_gazebo_rest_and_websocket_require_existing_mission_read_permission(operator_client, monkeypatch):
    operator, _user = operator_client
    store = GazeboPoseStore()
    store.ingest_datagram(snapshot_payload())
    monkeypatch.setattr(main, "gazebo_pose_store", store)
    monkeypatch.setattr(main, "gazebo_pose_udp_transport", object())

    guest = TestClient(main.app)
    assert guest.get("/api/telemetry/gazebo").status_code == 401
    response = operator.get("/api/telemetry/gazebo")
    assert response.status_code == 200
    assert response.json()["models"][0]["model_name"] == "x500_0"
    with operator.websocket_connect("/api/telemetry/gazebo/stream") as websocket:
        assert websocket.receive_json()["models"][0]["model_name"] == "x500_0"
    with pytest.raises(WebSocketDisconnect) as disconnect:
        with guest.websocket_connect("/api/telemetry/gazebo/stream"):
            pass
    assert disconnect.value.code == 4401
    guest.close()


def test_px4_and_gazebo_telemetry_coexist_in_one_authenticated_mission_control_app(operator_client, monkeypatch):
    operator, _user = operator_client
    px4_store = Px4TelemetryStore()
    for system_id in (1, 2, 3):
        encoder = mavlink2.MAVLink(None, srcSystem=system_id, srcComponent=1)
        heartbeat = mavlink2.MAVLink_heartbeat_message(
            mavlink2.MAV_TYPE_QUADROTOR,
            mavlink2.MAV_AUTOPILOT_PX4,
            0,
            0,
            mavlink2.MAV_STATE_STANDBY,
            3,
        )
        position = mavlink2.MAVLink_global_position_int_message(
            1000,
            473977420 + system_id,
            85455940 + system_id,
            488000,
            12000,
            100,
            200,
            -300,
            9000,
        )
        datagram = heartbeat.pack(encoder) + position.pack(encoder)
        assert px4_store.ingest_datagram(datagram, ("127.0.0.1", 14601)) == 2

    gazebo_store = GazeboPoseStore()
    assert gazebo_store.ingest_datagram(snapshot_payload(("x500_0", "x500_1", "x500_2"))) is True
    monkeypatch.setattr(main, "px4_telemetry_store", px4_store)
    monkeypatch.setattr(main, "px4_udp_transport", object())
    monkeypatch.setattr(main, "gazebo_pose_store", gazebo_store)
    monkeypatch.setattr(main, "gazebo_pose_udp_transport", object())

    health = operator.get("/api/health")
    assert health.status_code == 200
    components = health.json()["components"]
    assert components["px4"]["vehicle_count"] == 3
    assert components["gazebo"]["model_count"] == 3

    px4_response = operator.get("/api/telemetry/px4")
    gazebo_response = operator.get("/api/telemetry/gazebo")
    assert [vehicle["system_id"] for vehicle in px4_response.json()["vehicles"]] == [1, 2, 3]
    assert [model["model_name"] for model in gazebo_response.json()["models"]] == [
        "x500_0", "x500_1", "x500_2",
    ]

    since = "2000-01-01T00%3A00%3A00Z"
    with operator.websocket_connect(f"/api/telemetry/px4/stream?trail_mode=delta-v1&since={since}") as websocket:
        update = websocket.receive_json()
        assert update["stream_mode"] == "trail_delta_v1"
        assert len(update["vehicles"]) == 3
        assert all("flight_trail" not in vehicle for vehicle in update["vehicles"])
        assert all(vehicle["flight_trail_delta"] for vehicle in update["vehicles"])
    with operator.websocket_connect(f"/api/telemetry/gazebo/stream?trail_mode=delta-v1&since={since}") as websocket:
        update = websocket.receive_json()
        assert update["stream_mode"] == "trail_delta_v1"
        assert len(update["models"]) == 3
        assert all("flight_trail" not in model for model in update["models"])
        assert all(model["flight_trail_delta"] for model in update["models"])


def test_gazebo_receiver_lifecycle_and_configuration_failure(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    monkeypatch.setattr(main, "GAZEBO_POSE_UDP_ENABLED", True)
    monkeypatch.setattr(main, "GAZEBO_POSE_UDP_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(main, "GAZEBO_POSE_UDP_BIND_PORT", free_port)
    monkeypatch.setattr(main, "GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP", "127.0.0.1")
    monkeypatch.setattr(main, "GAZEBO_POSE_UDP_ALLOWED_SOURCE_PORT", 24611)

    async def exercise_lifecycle():
        await main.start_gazebo_pose_receiver()
        try:
            assert main.gazebo_pose_udp_transport is not None
            assert main.gazebo_pose_udp_protocol is not None
        finally:
            await main.stop_gazebo_pose_receiver()

    asyncio.run(exercise_lifecycle())
    assert main.gazebo_pose_udp_transport is None

    monkeypatch.setattr(main, "GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP", "")
    monkeypatch.setattr(main, "gazebo_pose_udp_error_code", None)
    asyncio.run(main.start_gazebo_pose_receiver())
    assert main.gazebo_pose_udp_transport is None
    assert main.gazebo_pose_udp_error_code == "allowed_source_not_configured"
