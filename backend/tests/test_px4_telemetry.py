from __future__ import annotations

import asyncio
import socket
import time

from pymavlink.dialects.v20 import common as mavlink2
import pytest

from app.telemetry.px4 import Px4MavlinkProtocol, Px4TelemetryStore


def _frame(*messages, system_id=1, component_id=1):
    encoder = mavlink2.MAVLink(None, srcSystem=system_id, srcComponent=component_id)
    return b"".join(message.pack(encoder) for message in messages)


def test_delta_stream_snapshot_excludes_repeated_trail_and_returns_new_positions_only():
    store = Px4TelemetryStore()
    first_position = mavlink2.MAVLink_global_position_int_message(
        1000, 473977420, 85455940, 488000, 12000, 100, 200, -300, 9000,
    )
    store.ingest_datagram(_frame(first_position), ("172.23.85.230", 14601))
    first = store.snapshot()
    time.sleep(0.002)

    next_position = mavlink2.MAVLink_global_position_int_message(
        2000, 473977430, 85455950, 489000, 13000, 100, 200, -300, 9000,
    )
    store.ingest_datagram(_frame(next_position), ("172.23.85.230", 14601))
    update = store.stream_snapshot(since=first["generated_at"])

    vehicle = update["vehicles"][0]
    assert update["stream_mode"] == "trail_delta_v1"
    assert "flight_trail" not in vehicle
    assert [point["latitude"] for point in vehicle["flight_trail_delta"]] == [47.397743]
    assert vehicle["position"]["latitude"] == 47.397743


def test_delta_stream_without_cursor_starts_with_a_full_trail_snapshot():
    store = Px4TelemetryStore()
    position = mavlink2.MAVLink_global_position_int_message(
        1000, 473977420, 85455940, 488000, 12000, 100, 200, -300, 9000,
    )
    store.ingest_datagram(_frame(position))

    initial = store.stream_snapshot()

    assert initial["stream_mode"] == "trail_delta_v1"
    assert len(initial["vehicles"][0]["flight_trail"]) == 1
    assert "flight_trail_delta" not in initial["vehicles"][0]


def test_decoder_builds_timestamped_per_vehicle_state_with_unit_conversions():
    frames = _frame(
        mavlink2.MAVLink_heartbeat_message(
            mavlink2.MAV_TYPE_QUADROTOR,
            mavlink2.MAV_AUTOPILOT_PX4,
            mavlink2.MAV_MODE_FLAG_SAFETY_ARMED,
            3 << 16,
            mavlink2.MAV_STATE_ACTIVE,
            3,
        ),
        mavlink2.MAVLink_global_position_int_message(
            1234, 473977420, 85455940, 488000, 12000, 100, 200, -300, 9000,
        ),
        mavlink2.MAVLink_attitude_message(1234, 0.1, -0.2, 1.2, 0.0, 0.0, 0.0),
        mavlink2.MAVLink_gps_raw_int_message(
            123456, 3, 473977420, 85455940, 488000, 120, 250, 150, 18000, 12,
        ),
        mavlink2.MAVLink_highres_imu_message(
            123456, 1.0, 2.0, -9.8, 0.01, 0.02, 0.03, 0.2, 0.3, 0.4,
            1013.0, 0.0, 120.0, 24.5, 0x1FFF,
        ),
        mavlink2.MAVLink_sys_status_message(
            0, 0, 0, 0, 16000, 250, 75, 0, 0, 0, 0, 0, 0,
        ),
        mavlink2.MAVLink_mission_current_message(2, 8, 2, 0),
    )
    store = Px4TelemetryStore()

    decoded = store.ingest_datagram(frames, ("172.23.85.230", 14601))

    assert decoded == 7  # HEARTBEAT, position, attitude, raw GPS/IMU, battery and mission.
    snapshot = store.snapshot()
    vehicle = snapshot["vehicles"][0]
    assert vehicle["id"] == "px4-1"
    assert vehicle["source"] == "px4_mavlink"
    assert vehicle["status"] == "connected"
    assert vehicle["armed"] is True
    assert vehicle["heartbeat"]["flight_mode"] == "POSCTL"
    assert vehicle["position"] == {
        "latitude": 47.397742,
        "longitude": 8.545594,
        "altitude_m": 488.0,
        "relative_altitude_m": 12.0,
        "heading_deg": 90.0,
        "valid": True,
        "coordinate_frame": "WGS84",
        "source_message": "GLOBAL_POSITION_INT",
        "source_time_boot_ms": 1234,
        "received_at": vehicle["position"]["received_at"],
    }
    assert vehicle["velocity"]["north_m_s"] == 1.0
    assert vehicle["velocity"]["east_m_s"] == 2.0
    assert vehicle["velocity"]["down_m_s"] == -3.0
    assert round(vehicle["velocity"]["ground_speed_m_s"], 3) == 2.236
    assert vehicle["attitude"]["yaw_rad"] == pytest.approx(1.2)
    assert vehicle["gps_raw"]["fix_type"] == 3
    assert vehicle["gps_raw"]["satellites_visible"] == 12
    assert vehicle["gps_raw"]["source_time_usec"] == 123456
    assert vehicle["imu_raw"]["acceleration_m_s2"] == pytest.approx([1.0, 2.0, -9.8])
    assert vehicle["imu_raw"]["angular_rate_rad_s"] == pytest.approx([0.01, 0.02, 0.03])
    assert vehicle["battery"]["voltage_v"] == 16.0
    assert vehicle["battery"]["current_a"] == 2.5
    assert vehicle["battery"]["remaining_percent"] == 75
    assert vehicle["mission"]["current_sequence"] == 2
    assert vehicle["mission"]["total_sequence"] == 8
    assert vehicle["flight_trail"][-1]["latitude"] == 47.397742
    assert vehicle["threat_assessment"]["status"] == "not_available"
    assert snapshot["source"] == "px4_mavlink"
    assert snapshot["capabilities"]["independent_navigation_estimate"] == "not_integrated"
    assert snapshot["transport"]["datagrams_received"] == 1
    assert snapshot["transport"]["frames_decoded"] == 7
    assert snapshot["transport"]["status"] == "receiving"


def test_highres_imu_masks_fields_not_updated_in_the_current_message():
    store = Px4TelemetryStore()
    imu = mavlink2.MAVLink_highres_imu_message(
        123456, 1.0, 2.0, 3.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6,
        1013.0, 0.0, 120.0, 24.5,
        1 | 8 | 128 | 4096,
    )

    store.ingest_datagram(_frame(imu))

    decoded = store.snapshot()["vehicles"][0]["imu_raw"]
    assert decoded["acceleration_m_s2"] == [1.0, None, None]
    assert decoded["angular_rate_rad_s"][0] == pytest.approx(0.1)
    assert decoded["angular_rate_rad_s"][1:] == [None, None]
    assert decoded["magnetic_field_gauss"] == [None, 0.5, None]
    assert decoded["temperature_c"] == 24.5
    assert decoded["fields_updated"] == 1 | 8 | 128 | 4096


@pytest.mark.parametrize(("total", "mission_state"), [(0, 0), (65535, 1)])
def test_mission_current_sentinel_is_not_exposed_as_waypoint_count(total, mission_state):
    store = Px4TelemetryStore()
    mission = mavlink2.MAVLink_mission_current_message(0, total, mission_state, 0)
    store.ingest_datagram(_frame(mission), ("172.23.85.230", 14601))

    vehicle = store.snapshot()["vehicles"][0]

    assert vehicle["mission"]["total_sequence"] is None
    assert vehicle["mission"]["mission_state"] == mission_state


def test_multiple_mavlink_system_ids_remain_separate_and_become_stale():
    store = Px4TelemetryStore(stale_after_seconds=3, disconnected_after_seconds=10)
    heartbeat = mavlink2.MAVLink_heartbeat_message(
        mavlink2.MAV_TYPE_QUADROTOR, mavlink2.MAV_AUTOPILOT_PX4, 0, 0,
        mavlink2.MAV_STATE_STANDBY, 3,
    )
    store.ingest_datagram(_frame(heartbeat, system_id=1), ("172.23.85.230", 14601))
    store.ingest_datagram(_frame(heartbeat, system_id=2), ("172.23.85.230", 14601))
    base = time.monotonic()

    assert [vehicle["system_id"] for vehicle in store.snapshot()["vehicles"]] == [1, 2]
    assert {vehicle["status"] for vehicle in store.snapshot(now_monotonic=base + 4)["vehicles"]} == {"stale"}
    assert {vehicle["status"] for vehicle in store.snapshot(now_monotonic=base + 11)["vehicles"]} == {"disconnected"}
    assert store.snapshot(now_monotonic=base + 11)["transport"]["status"] == "disconnected"


def test_component_ids_from_one_system_do_not_create_duplicate_aircraft():
    store = Px4TelemetryStore()
    autopilot = mavlink2.MAVLink_heartbeat_message(
        mavlink2.MAV_TYPE_QUADROTOR, mavlink2.MAV_AUTOPILOT_PX4, 0,
        3 << 16, mavlink2.MAV_STATE_STANDBY, 3,
    )
    sensor = mavlink2.MAVLink_attitude_message(100, 0.1, 0.2, 0.3, 0, 0, 0)

    store.ingest_datagram(_frame(autopilot, system_id=4, component_id=1))
    store.ingest_datagram(_frame(sensor, system_id=4, component_id=200))

    vehicles = store.snapshot()["vehicles"]
    assert len(vehicles) == 1
    assert vehicles[0]["id"] == "px4-4"
    assert vehicles[0]["system_id"] == 4
    assert vehicles[0]["component_id"] == 1
    assert vehicles[0]["attitude"]["yaw_rad"] == pytest.approx(0.3)


def test_receiver_rejects_unconfigured_sources_and_non_mavlink_datagrams():
    store = Px4TelemetryStore()
    protocol = Px4MavlinkProtocol(
        store,
        allowed_source_ip="172.23.85.230",
        allowed_source_port=14601,
    )

    protocol.datagram_received(b"not-mavlink", ("172.23.85.230", 14601))
    protocol.datagram_received(b"\xfd\x00", ("172.23.85.231", 14601))
    protocol.datagram_received(b"\xfd\x00", ("172.23.85.230", 14602))

    snapshot = store.snapshot()
    assert snapshot["transport"]["invalid_datagrams"] == 1
    assert snapshot["transport"]["datagrams_received"] == 0
    assert protocol.rejected_sources == 2


def test_receiver_lifecycle_binds_only_when_enabled(monkeypatch):
    from app import main

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    monkeypatch.setattr(main, "PX4_UDP_ENABLED", True)
    monkeypatch.setattr(main, "PX4_UDP_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(main, "PX4_UDP_BIND_PORT", free_port)
    monkeypatch.setattr(main, "PX4_UDP_ALLOWED_SOURCE_IP", "127.0.0.1")
    monkeypatch.setattr(main, "PX4_UDP_ALLOWED_SOURCE_PORT", 24601)

    async def exercise_lifecycle():
        await main.start_px4_telemetry_receiver()
        try:
            assert main.px4_udp_transport is not None
            assert main.px4_udp_protocol is not None
            assert main.px4_udp_protocol.allowed_source_ip == "127.0.0.1"
        finally:
            await main.stop_px4_telemetry_receiver()

    asyncio.run(exercise_lifecycle())
    assert main.px4_udp_transport is None


def test_receiver_configuration_failure_is_reported_without_failing_startup(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "PX4_UDP_ENABLED", True)
    monkeypatch.setattr(main, "PX4_UDP_ALLOWED_SOURCE_IP", "")
    monkeypatch.setattr(main, "px4_udp_transport", None)
    monkeypatch.setattr(main, "px4_udp_error_code", None)

    asyncio.run(main.start_px4_telemetry_receiver())

    assert main.px4_udp_transport is None
    assert main.px4_udp_error_code == "allowed_source_not_configured"
