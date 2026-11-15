"""Live threat assessment: aligned inputs from the PX4 telemetry buffer, states, the handover rule and the body limit."""
from __future__ import annotations

import importlib.util
import math
import time
from pathlib import Path

import numpy as np
import pytest
from pymavlink.dialects.v20 import common as mavlink2

from app import research_routes
from app.research import live_inputs
from app.research.threat_data import residual_series
from app.telemetry.px4 import Px4TelemetryStore

LAT0, LON0 = 12.9716, 77.5946
SPEED = 5.0
DELAY = live_inputs.GPS_DELAY_S
_trapezoid = getattr(np, "trapezoid", None) or np.trapz


def truth(t: float) -> tuple[float, float, float, float]:
    """(north, east, v_north, v_east): out along north, a turn, then east. Smooth, so the truth has a derivative everywhere."""
    heading = 0.5 * math.pi * min(max((t - 30.0) / 6.0, 0.0), 1.0)       # 0 = north, turning east between 30 and 36 s
    steps = np.linspace(0.0, t, max(2, int(t * 50)))
    headings = 0.5 * math.pi * np.clip((steps - 30.0) / 6.0, 0.0, 1.0)
    north = float(_trapezoid(SPEED * np.cos(headings), steps))
    east = float(_trapezoid(SPEED * np.sin(headings), steps))
    return north, east, SPEED * math.cos(heading), SPEED * math.sin(heading)


def streams(duration: float, *, gps_hz: float = 5.0, vel_hz: float = 25.0, boot: float = 100.0, gps_offset=None):
    """Raw rows as the store keeps them. GPS reports where the aircraft was DELAY seconds earlier."""
    gps, velocity = [], []
    for i in range(int(duration * gps_hz) + 1):
        t = i / gps_hz
        north, east, vn, ve = truth(max(t - DELAY, 0.0))
        if gps_offset:
            dn, de = gps_offset(t)
            north, east = north + dn, east + de
        gps.append((boot + t, LAT0 + math.degrees(north / live_inputs.EARTH_RADIUS_M),
                    LON0 + math.degrees(east / (live_inputs.EARTH_RADIUS_M * math.cos(math.radians(LAT0)))), 500.0, vn, ve))
    for i in range(int(duration * vel_hz) + 1):
        t = i / vel_hz
        _n, _e, vn, ve = truth(t)
        velocity.append((boot + t, vn, ve, 0.0))
    return gps, velocity


def window_of(gps, velocity, **kwargs):
    return live_inputs.align_window(gps, velocity, flight_start_t=gps[0][0], origin=(gps[0][1], gps[0][2], gps[0][3]), **kwargs)


# --- alignment -------------------------------------------------------------------------------------------------

def test_aligned_window_uses_the_px4_clock_interval_velocity_and_the_gps_delay():
    gps, velocity = streams(80.0)
    window = window_of(gps, velocity)
    assert window["ready"] and window["hz"] == 2.0 and window["gps_delay_s"] == live_inputs.GPS_DELAY_S == 0.04
    t = window["t"]
    assert np.allclose(np.diff(t), 0.5) and t[-1] <= 80.0 and window["px4_time_s"] == pytest.approx(100.0 + t[-1])   # PX4 time, not receive time
    aligned = residual_series(t, window["gps_position"], window["imu_velocity"], 10.0)
    assert aligned.max() < 0.05                                                    # consistent sensors give a near-zero residual, turn included
    no_delay = window_of(gps, velocity, delay_s=0.0)
    turning = (t > 31) & (t < 46)
    late = (no_delay["t"] > 31) & (no_delay["t"] < 46)
    assert residual_series(no_delay["t"], no_delay["gps_position"], no_delay["imu_velocity"], 10.0)[late].max() > 5 * max(aligned[turning].max(), 0.01)
    # instantaneous (not interval-averaged) velocity is what the trial found wrong: it must differ from ours in the turn
    instant = np.array([truth(float(moment) - DELAY)[2:] for moment in t])
    assert np.abs(instant[:, ::-1] - window["imu_velocity"][:, :2])[turning].max() > 0.1
    assert np.allclose(window["gps_velocity"][:, 2], 0.0) and window["window_starts_at_flight_start"]


def test_a_gps_offset_shows_in_the_residual_and_the_window_is_capped():
    gps, velocity = streams(400.0, gps_offset=lambda t: (0.0, 40.0 if t > 350 else 0.0))
    window = window_of(gps, velocity)
    assert window["samples"] == live_inputs.MAX_SAMPLES and not window["window_starts_at_flight_start"]
    residual = residual_series(window["t"], window["gps_position"], window["imu_velocity"], 10.0)
    assert residual[window["t"] < 349].max() < 0.1 and residual.max() > 30


def test_missing_and_late_inputs_are_named_never_zero_filled():
    gps, velocity = streams(40.0)
    with pytest.raises(live_inputs.InputUnavailable) as none:
        window_of(gps, [])
    assert none.value.name == "estimator_velocity"
    with pytest.raises(live_inputs.InputUnavailable) as few:
        window_of(gps[:2], velocity)
    assert few.value.name == "gps_position"
    no_speed = [(row[0], row[1], row[2], row[3], math.nan, math.nan) for row in gps]
    with pytest.raises(live_inputs.InputUnavailable) as speed:
        window_of(no_speed, velocity)
    assert speed.value.name == "gps_velocity"
    short = window_of(*streams(6.0))
    assert short["ready"] is False and short["seconds_needed"] == 10.0 and short["seconds_collected"] < 10.0
    holed = [row for row in velocity if not 20.0 < row[0] - 100.0 < 24.0]          # a 4 s hole in the estimator velocity
    restarted = window_of(gps, holed)
    assert restarted["ready"] and restarted["restarted_after_gap"] == ["estimator_velocity"]
    assert restarted["t"][0] > 24.0 and np.isfinite(restarted["imu_velocity"]).all()   # the window starts after the hole
    for name in live_inputs.REQUIRED_INPUTS + live_inputs.OPTIONAL_INPUTS:
        message = live_inputs.input_message(name)
        assert message["title"] and message["meaning"] and message["required"] == (name in live_inputs.REQUIRED_INPUTS)


# --- telemetry store -> live route ------------------------------------------------------------------------------

def feed(store: Px4TelemetryStore, system_id: int, duration: float, *, armed: bool = True, battery: int = 80, skip: tuple = (), gps_offset=None) -> None:
    encoder = mavlink2.MAVLink(None, srcSystem=system_id, srcComponent=1)
    source = ("127.0.0.1", 14601)
    base_mode = mavlink2.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | (mavlink2.MAV_MODE_FLAG_SAFETY_ARMED if armed else 0)

    def send(message, kind):
        if kind not in skip:
            store.ingest_datagram(message.pack(encoder), source)

    send(encoder.heartbeat_encode(mavlink2.MAV_TYPE_QUADROTOR, mavlink2.MAV_AUTOPILOT_PX4, base_mode, 0, mavlink2.MAV_STATE_ACTIVE), "HEARTBEAT")
    send(encoder.sys_status_encode(0, 0, 0, 0, 15000, 100, battery, 0, 0, 0, 0, 0, 0), "SYS_STATUS")
    gps, velocity = streams(duration, gps_offset=gps_offset)
    events = [(row[0], "gps", row) for row in gps] + [(row[0], "vel", row) for row in velocity]
    for stamp, kind, row in sorted(events, key=lambda item: item[0]):
        if kind == "gps":
            speed = math.hypot(row[4], row[5])
            course = math.degrees(math.atan2(row[5], row[4])) % 360.0
            send(encoder.gps_raw_int_encode(int(stamp * 1e6), 3, int(round(row[1] * 1e7)), int(round(row[2] * 1e7)), 500000, 80, 120, int(round(speed * 100)), int(round(course * 100)), 12), "GPS_RAW_INT")
        else:
            send(encoder.local_position_ned_encode(int(round(stamp * 1e3)), 0, 0, -30, row[1], row[2], row[3]), "LOCAL_POSITION_NED")
    end = int((100.0 + duration) * 1e6)
    send(encoder.highres_imu_encode(end, 0.1, 0.0, -9.8, 0, 0, 0, 0.2, 0, 0.4, 950, 0, 30, 25, 0xFFFF), "HIGHRES_IMU")
    send(encoder.estimator_status_encode(end, 831, 0.05, 0.04, 0.02, 0.1, 0.0, 0.0, 0.4, 0.6), "ESTIMATOR_STATUS")
    send(encoder.vibration_encode(end, 0.2, 0.2, 0.3, 0, 0, 0), "VIBRATION")


@pytest.fixture
def live(monkeypatch):
    store = Px4TelemetryStore(stale_after_seconds=60.0, disconnected_after_seconds=120.0)   # generous: these tests are not about timing
    monkeypatch.setattr(research_routes, "telemetry_store", store)
    monkeypatch.setattr(research_routes, "_live_cache", {})
    monkeypatch.setattr(research_routes, "_live_last", {})
    monkeypatch.setattr(research_routes, "_roles_cache", {})
    monkeypatch.setattr(research_routes, "INPUT_MAX_AGE_S", {kind: 60.0 for kind in research_routes.INPUT_MAX_AGE_S})
    return store


def test_store_keeps_px4_stamped_detector_streams_only_while_armed(live):
    feed(live, 1, 12.0, armed=False)
    idle = live.detector_inputs("px4-1")
    assert idle["armed"] is False and idle["gps"] == [] and idle["velocity"] == []     # nothing is buffered on the ground
    feed(live, 1, 12.0)
    inputs = live.detector_inputs("px4-1")
    assert len(inputs["gps"]) == 61 and len(inputs["velocity"]) == 301 and inputs["flight_start_t"] == pytest.approx(100.0)
    assert inputs["gps"][-1][0] == pytest.approx(112.0) and inputs["velocity"][-1][0] == pytest.approx(112.0)
    assert inputs["gps_raw"]["ground_speed_m_s"] == pytest.approx(5.0, abs=0.01) and inputs["gps_raw"]["course_deg"] == pytest.approx(0.0, abs=0.01)
    assert inputs["estimator_status"]["horizontal_position_test_ratio"] == pytest.approx(0.04) and inputs["vibration"]["accelerometer_clipping"] == [0, 0, 0]
    assert inputs["imu_raw"]["acceleration_m_s2"][2] == pytest.approx(-9.8) and set(inputs["ages_seconds"]) >= {"GPS_RAW_INT", "LOCAL_POSITION_NED", "HIGHRES_IMU"}
    snapshot = live.snapshot()["vehicles"][0]
    assert not [key for key in snapshot if key.startswith("_")] and snapshot["imu_raw"] is not None and snapshot["estimator_status"] is not None


def test_live_route_states_idle_warming_live_low_and_no_request_body(live, model_dir, general_client):
    client, _ = general_client
    empty = client.get("/api/research/live").json()
    assert empty["aircraft"] == [] and empty["flight_running"] is False and empty["handover_rule"]["min_battery_pct"] == 40.0
    feed(live, 1, 5.0, armed=False)
    research_routes._live_cache.clear()
    assert client.get("/api/research/live").json()["aircraft"][0]["state"] == "idle"
    feed(live, 1, 6.0)
    research_routes._live_cache.clear()
    warming = client.get("/api/research/live").json()["aircraft"][0]
    assert warming["state"] == "warming_up" and warming["assessment"] is None and warming["warming_up"]["seconds_needed"] == 10.0
    feed(live, 1, 90.0)                                                              # still armed: the same flight continues
    live._vehicles["px4-1"]["_detector"]["gps"].clear()
    live._vehicles["px4-1"]["_detector"]["velocity"].clear()
    live._vehicles["px4-1"]["_detector"].update(flight_start_t=None, origin=None)
    feed(live, 1, 90.0)
    research_routes._live_cache.clear()
    payload = client.get("/api/research/live").json()
    row = payload["aircraft"][0]
    assert payload["flight_running"] is True and payload["threat_score_is_probability"] is False and row["state"] == "live", row
    assessment = row["assessment"]
    assert assessment["tier"] == "Low" and assessment["source"] == "live_telemetry" and 0 <= assessment["anomaly_confidence"] <= 1
    assert {"severity", "mission_impact", "threat_score", "evidence", "px4_time_s", "computed_at"} <= set(assessment)
    quality = row["data_quality"]
    assert quality["clock"] == "px4_boot_clock" and quality["velocity"] == "interval_averaged" and quality["gps_delay_s"] == 0.04
    assert quality["velocity_reference"] == "px4_estimator_velocity" and quality["estimator"]["horizontal_position_test_ratio"] == pytest.approx(0.04)
    assert row["missing_inputs"] == [] and "GPS_RAW_INT" in row["rates_hz"]
    assert client.get("/api/research/live").json()["generated_at"] == payload["generated_at"]          # one computation per interval


def test_missing_inputs_are_named_and_a_lost_link_keeps_the_last_values_with_their_age(live, model_dir):
    feed(live, 1, 60.0, skip=("LOCAL_POSITION_NED",))
    row = research_routes.compute_live(live, {})["aircraft"][0]
    assert row["state"] == "inputs_missing" and row["assessment"] is None
    assert [item["input"] for item in row["missing_inputs"] if item["required"]] == ["estimator_velocity"]
    assert "LOCAL_POSITION_NED" in row["missing_inputs"][0]["title"] and row["missing_inputs"][0]["meaning"]
    feed(live, 2, 60.0, skip=("HIGHRES_IMU", "VIBRATION"))
    second = next(item for item in research_routes.compute_live(live, {"px4-2": "standby"})["aircraft"] if item["aircraft_id"] == "px4-2")
    assert second["state"] == "live" and second["role"] == "standby" and second["role_source"] == "deployed_plan"   # optional inputs do not block the score
    assert {item["input"] for item in second["missing_inputs"]} == {"imu", "vibration"} and not any(item["required"] for item in second["missing_inputs"])
    later = live.detector_inputs("px4-2", now_monotonic=time.monotonic() + 200.0)
    lost = research_routes._live_aircraft(later, "standby", "deployed_plan", research_routes.last_assessments({"px4-2": "standby"}))
    assert lost["state"] == "link_lost" and lost["link"]["status"] == "disconnected" and lost["assessment"]["tier"] == "Low" and lost["assessment_age_s"] >= 0


def test_a_position_jump_raises_the_live_tier(live, model_dir):
    feed(live, 1, 120.0, gps_offset=lambda t: (0.0, 150.0 if t > 100 else 0.0))
    row = research_routes.compute_live(live, {})["aircraft"][0]
    assert row["state"] == "live" and row["assessment"]["tier"] != "Low" and row["assessment"]["evidence"]


# --- body limit and the handover rule ---------------------------------------------------------------------------

def test_long_windows_in_a_body_are_refused_with_a_clear_error_and_live_needs_none(model_dir, general_client):
    client, _ = general_client
    sample = {"t": 0.0, "gps_position": [0.123456789] * 3, "gps_velocity": [0.123456789] * 3, "imu_velocity": [0.123456789] * 3}
    body = {"aircraft_id": "px4-1", "role": "primary", "battery_pct": 80, "samples": [{**sample, "t": i * 0.5} for i in range(600)]}
    response = client.post("/api/research/assess", json=body)
    assert response.status_code == 413
    detail = response.json()
    assert detail["code"] == "request_body_too_large" and detail["limit_bytes"] == 65536 and "/api/research/live" in detail["hint"]


def test_handover_below_the_minimum_battery_is_refused_by_the_server(live, model_dir, general_client):
    client, _ = general_client
    feed(live, 1, 60.0)
    feed(live, 2, 60.0, battery=20)
    feed(live, 3, 60.0, battery=80)
    research_routes.compute_live(live, {"px4-1": "primary", "px4-2": "standby", "px4-3": "standby"})
    base = {"aircraft_id": "px4-1", "action": "handover_to_standby", "source": "live_telemetry", "tier": "Critical", "threat_score": 99.0}
    low = client.post("/api/research/decisions", json={**base, "handover_candidate": "px4-2"})
    assert low.status_code == 409 and low.json()["detail"]["code"] == "handover_below_min_battery"
    assert "20%" in low.json()["detail"]["message"] and "40%" in low.json()["detail"]["message"] and low.json()["detail"]["handover_rule"]["enforced_by"] == "server"
    unknown = client.post("/api/research/decisions", json={**base, "handover_candidate": "px4-9"})
    assert unknown.status_code == 409 and unknown.json()["detail"]["code"] == "handover_battery_unknown"
    assert client.post("/api/research/decisions", json={**base, "handover_candidate": "px4-1"}).json()["detail"]["code"] == "handover_same_aircraft"
    ok = client.post("/api/research/decisions", json={**base, "handover_candidate": "px4-3"})
    assert ok.status_code == 201 and ok.json()["executed"] is False
    shown = next(item for item in client.get("/api/research/live").json()["aircraft"] if item["aircraft_id"] == "px4-1")["assessment"]
    assert (ok.json()["tier"], ok.json()["threat_score"]) == ("Low", shown["threat_score"])       # the server's values for this user, not the client's
    listed = [item for item in client.get("/api/research/decisions").json()["decisions"] if item["source"] == "live_telemetry"]
    assert [item["handover_candidate"] for item in listed] == ["px4-3"]                # refused handovers were not recorded
    assert client.get("/api/research/model-info").json()["handover_rule"]["min_battery_pct"] == 40.0


def test_replay_handover_is_checked_against_the_replay_battery(model_dir, general_client, monkeypatch):
    client, _ = general_client
    user_id = client.post("/api/research/decisions", json={"aircraft_id": "px4-1", "action": "continue_monitor", "source": "offline_replay"}).json()["user_id"]
    sim = type("Sim", (), {"battery_pct": np.linspace(100.0, 10.0, 200)})()        # 2 Hz: 30% is reached near 78 s
    replay_id = "ab" * 16
    monkeypatch.setitem(research_routes.REPLAY_STORE, replay_id, {"user_id": user_id, "sims": {"px4-2": {"sim": sim}}, "min_battery_pct": 30.0})
    base = {"aircraft_id": "px4-1", "action": "handover_to_standby", "handover_candidate": "px4-2", "source": "offline_replay", "replay_id": replay_id}
    assert client.post("/api/research/decisions", json={**base, "replay_t_s": 20.0}).status_code == 201           # about 82%
    late = client.post("/api/research/decisions", json={**base, "replay_t_s": 90.0})                              # about 19%, experiment minimum 30%
    assert late.status_code == 409 and late.json()["detail"]["code"] == "handover_below_min_battery" and "30%" in late.json()["detail"]["message"]
    assert client.post("/api/research/decisions", json={**base, "replay_id": "cd" * 16, "replay_t_s": 20.0}).json()["detail"]["code"] == "handover_battery_unknown"


# --- the bridge asks PX4 for the detector's messages on the telemetry link ---------------------------------------

def test_bridge_stream_requests_are_fixed_set_message_interval_commands():
    spec = importlib.util.spec_from_file_location("bridge_stream", Path(__file__).parents[1] / "tools" / "px4_wsl_udp_bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    parser = mavlink2.MAVLink(None)
    seen = {}
    for index, (message_id, rate) in enumerate(bridge.STREAM_REQUESTS.items()):
        decoded = None
        for byte in bridge.message_interval_frame(index, 1, message_id, rate):
            decoded = parser.parse_char(bytes((byte,))) or decoded
        assert decoded.get_type() == "COMMAND_LONG" and decoded.command == mavlink2.MAV_CMD_SET_MESSAGE_INTERVAL and decoded.target_system == 1
        seen[int(decoded.param1)] = round(1e6 / decoded.param2, 1)
    assert seen == {mavlink2.MAVLINK_MSG_ID_HIGHRES_IMU: 25.0, mavlink2.MAVLINK_MSG_ID_GPS_RAW_INT: 10.0, mavlink2.MAVLINK_MSG_ID_LOCAL_POSITION_NED: 25.0,
                    mavlink2.MAVLINK_MSG_ID_ESTIMATOR_STATUS: 2.0, mavlink2.MAVLINK_MSG_ID_VIBRATION: 1.0}
    for bad in ((0, 1, 33, 10.0), (0, 1, 105, 1000.0), (0, 0, 105, 10.0)):          # only the fixed table, sane rates, a real target
        with pytest.raises(ValueError):
            bridge.message_interval_frame(*bad)
    assert bridge.frame_system_id(bridge.message_interval_frame(0, 7, 105, 25.0)) == 255 and bridge.frame_system_id(b"\x01") is None


def test_input_help_text_matches_the_frontend_spec_so_the_assistant_cannot_drift():
    source = (Path(__file__).parents[2] / "frontend" / "src" / "components" / "attackLabTypes.ts").read_text(encoding="utf-8")
    for name in live_inputs.REQUIRED_INPUTS + live_inputs.OPTIONAL_INPUTS:
        message = live_inputs.input_message(name)
        assert f"input: '{name}'" in source or f"input: \"{name}\"" in source
        for text in (message["title"], message["meaning"]):
            assert text in source or text.replace("'", "\\'") in source or text.replace("\"", "\\\"") in source, (name, text)
        assert f"required: {'true' if message['required'] else 'false'}, meaning" in source.split(f"input: '{name}'")[1].split("}")[0] + "}"


def test_a_companion_heartbeat_is_not_an_aircraft_and_a_late_heartbeat_with_data_flowing_is_not_a_lost_link(live):
    gcs = mavlink2.MAVLink(None, srcSystem=255, srcComponent=190)
    beat = gcs.heartbeat_encode(mavlink2.MAV_TYPE_GCS, mavlink2.MAV_AUTOPILOT_INVALID, 0, 0, mavlink2.MAV_STATE_ACTIVE)
    live.ingest_datagram(beat.pack(gcs), ("127.0.0.1", 14601))
    assert live.vehicle_ids() == [] and live.snapshot()["vehicles"] == []          # the executor's GCS heartbeat does not create "px4-255"
    feed(live, 1, 20.0)
    base = time.monotonic()
    assert live.detector_inputs("px4-1", now_monotonic=base + 0.5)["status"] == "connected"
    live._vehicles["px4-1"]["_last_heartbeat_monotonic"] = base - 4.0              # heartbeat 4 s late ...
    live._vehicles["px4-1"]["_last_message_monotonic"] = base                      # ... but position and attitude arrive
    assert live.detector_inputs("px4-1", now_monotonic=base + 0.5)["status"] == "connected"
    assert live.detector_inputs("px4-1", now_monotonic=base + 70.0)["status"] == "stale"          # no data beyond the stale limit (60 s here)
    assert live.detector_inputs("px4-1", now_monotonic=base + 130.0)["status"] == "disconnected"  # no data beyond the disconnect limit (120 s here)


# --- the three live-code defects -------------------------------------------------------------------------------------

def test_handover_candidates_never_use_invented_values(live, model_dir):
    """(a) A standby aircraft on the ground keeps its real battery; unknown values are unavailable and never make it eligible."""
    feed(live, 1, 60.0)                                                   # flying primary
    feed(live, 2, 5.0, armed=False, battery=85)                           # standby on the ground with a battery reading
    feed(live, 3, 5.0, armed=False, skip=("SYS_STATUS",))                 # standby on the ground with no battery reading
    feed(live, 4, 5.0, armed=False, battery=90)                           # connected, but not in the deployed plan
    payload = research_routes.compute_live(live, {"px4-1": "primary", "px4-2": "standby", "px4-3": "standby"})
    rows = {item["aircraft_id"]: item for item in payload["aircraft"]}
    assert rows["px4-2"]["state"] == "idle" and rows["px4-2"]["battery_pct"] == 85.0 and rows["px4-3"]["battery_pct"] is None
    assessment = rows["px4-1"]["assessment"]
    candidates = {item["aircraft_id"]: item for item in assessment["handover_candidates"]}
    assert set(candidates) == {"px4-2", "px4-3", "px4-4"}
    ground = candidates["px4-2"]
    assert ground["battery_pct"] == 85.0 and ground["battery_known"] is True and ground["eligible"] is True and ground["ineligible_reason"] is None
    assert ground["distance_to_task_m"] is None and ground["distance_known"] is False and ground["workload"] is None   # unavailable, not "0 m"
    unknown = candidates["px4-3"]
    assert unknown["battery_pct"] is None and unknown["battery_known"] is False and unknown["eligible"] is False
    assert unknown["ineligible_reason"] == "battery unavailable: no live battery reading"
    stranger = candidates["px4-4"]
    assert stranger["role"] is None and stranger["eligible"] is False and "role unavailable" in stranger["ineligible_reason"]
    assert [item["aircraft_id"] for item in assessment["handover_candidates"] if item["eligible"]] == ["px4-2"]
    assert "unavailable" in assessment["handover_candidates_note"]
    # the unknown distance and workload earned no credit: 0.4 * battery + 0.1 * standby role only
    assert ground["score"] == pytest.approx(0.4 * 0.85 + 0.1, abs=1e-3)
    handover = next(item for item in assessment["actions"] if item["action"] == "handover_to_standby")
    assert handover["eligible"] is True and "px4-2" in handover["reason"]
    only_unknown = research_routes.compute_live(live, {"px4-1": "primary", "px4-3": "standby"})
    actions = next(item for item in only_unknown["aircraft"] if item["aircraft_id"] == "px4-1")["assessment"]["actions"]
    assert next(item for item in actions if item["action"] == "handover_to_standby")["eligible"] is False     # no eligibility on invented numbers


def test_an_aircraft_that_landed_before_the_link_dropped_is_shown_as_landed_not_link_lost(live, model_dir):
    """(b) Landed keeps the last assessment with its age; only a link that drops IN FLIGHT is 'link lost'."""
    roles = {"px4-1": "primary", "px4-2": "standby"}
    feed(live, 1, 60.0)
    feed(live, 2, 60.0)
    flying = {item["aircraft_id"]: item for item in research_routes.compute_live(live, roles)["aircraft"]}
    assert flying["px4-1"]["state"] == "live" and flying["px4-2"]["state"] == "live"
    final_score = flying["px4-1"]["assessment"]["threat_score"]
    encoder = mavlink2.MAVLink(None, srcSystem=1, srcComponent=1)             # px4-1 lands: PX4 reports disarmed
    live.ingest_datagram(encoder.heartbeat_encode(mavlink2.MAV_TYPE_QUADROTOR, mavlink2.MAV_AUTOPILOT_PX4, mavlink2.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                                                  0, mavlink2.MAV_STATE_STANDBY).pack(encoder), ("127.0.0.1", 14601))
    landed = next(item for item in research_routes.compute_live(live, roles)["aircraft"] if item["aircraft_id"] == "px4-1")
    assert landed["state"] == "landed" and landed["armed"] is False and landed["link"]["status"] == "connected"
    assert landed["assessment"]["threat_score"] == final_score and landed["assessment_age_s"] >= 0 and landed["landed_age_s"] is not None
    last = research_routes.last_assessments(roles)
    now = time.monotonic()
    dropped = research_routes._live_aircraft(live.detector_inputs("px4-1", now_monotonic=now + 200.0), "primary", "deployed_plan", last)
    assert dropped["link"]["status"] == "disconnected" and dropped["state"] == "landed"            # the link dropped AFTER landing: still "landed"
    assert dropped["assessment"]["threat_score"] == final_score and dropped["landed_age_s"] >= 200.0
    in_flight = research_routes._live_aircraft(live.detector_inputs("px4-2", now_monotonic=now + 200.0), "standby", "deployed_plan", last)
    assert in_flight["state"] == "link_lost" and in_flight["armed"] is True and in_flight["assessment"] is not None   # dropped while flying
    expired = research_routes._live_aircraft(live.detector_inputs("px4-1", now_monotonic=now + research_routes.LANDED_KEEP_S + 300.0), "primary", "deployed_plan", last)
    assert expired["state"] == "offline" and expired["assessment"] is None and "px4-1" not in last   # long landed: back to waiting for a flight
    gone = research_routes._live_aircraft(live.detector_inputs("px4-2", now_monotonic=now + research_routes.LINK_LOST_KEEP_S + 300.0), "standby", "deployed_plan", last)
    assert gone["state"] == "offline" and gone["assessment"] is None


def test_live_results_are_not_shared_between_users_whose_roles_differ(live, model_dir, monkeypatch):
    """(c) The cache is keyed by the role map: each user gets the result computed for their own deployed plan."""
    from types import SimpleNamespace
    feed(live, 1, 60.0)
    general = SimpleNamespace(user=SimpleNamespace(id="general", role="GENERAL"))
    operator = SimpleNamespace(user=SimpleNamespace(id="operator", role="OPERATOR"))
    same_plan = SimpleNamespace(user=SimpleNamespace(id="colleague", role="GENERAL"))
    plans = {"general": {"px4-1": "primary"}, "colleague": {"px4-1": "primary"}, "operator": {"px4-1": "standby"}}
    monkeypatch.setattr(research_routes, "_deployment_roles", lambda context: plans[context.user.id])
    computed = []
    real = research_routes.compute_live
    monkeypatch.setattr(research_routes, "compute_live", lambda store, roles: computed.append(dict(roles)) or real(store, roles))
    first = research_routes.live_payload(general)
    second = research_routes.live_payload(operator)                       # within the same second: must NOT be handed the general's result
    mine, theirs = first["aircraft"][0], second["aircraft"][0]
    assert (mine["role"], theirs["role"]) == ("primary", "standby")
    assert mine["assessment"]["mission_impact"] > theirs["assessment"]["mission_impact"]          # the role changes the output
    assert research_routes.live_payload(general) is first and research_routes.live_payload(operator) is second   # each cached separately
    assert research_routes.live_payload(same_plan) is first               # same role map: one computation is shared
    assert computed == [{"px4-1": "primary"}, {"px4-1": "standby"}]
    assert research_routes.last_assessments({"px4-1": "primary"})["px4-1"]["assessment"]["mission_impact"] == mine["assessment"]["mission_impact"]
    assert research_routes.last_assessments({"px4-1": "standby"})["px4-1"]["assessment"]["mission_impact"] == theirs["assessment"]["mission_impact"]


def test_a_late_required_input_is_reported_with_its_age(live, model_dir, monkeypatch):
    feed(live, 1, 60.0)
    monkeypatch.setitem(research_routes.INPUT_MAX_AGE_S, "GPS_RAW_INT", -1.0)       # any age now counts as late
    row = research_routes.compute_live(live, {})["aircraft"][0]
    assert row["state"] == "inputs_missing" and row["missing_inputs"][0]["input"] == "gps_position"
    assert row["missing_inputs"][0]["detail"].startswith("last GPS_RAW_INT was ")
