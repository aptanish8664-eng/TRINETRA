"""Mission executor state machine against a fake MAVLink peer (no sockets, no PX4, no WSL, no real time).

The fake peer behaves like the PX4 mission manager and commander described in the executor's docstring: it requests
items one by one, re-requests after 250 ms, acknowledges, answers commands, and sends a heartbeat every second.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location("wsl_mission_executor", Path(__file__).parents[1] / "tools" / "wsl_mission_executor.py")
assert _spec and _spec.loader
ex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex)

EXECUTION_ID = "0123456789abcdef0123456789abcdef"


def mission(extra_items=None, **overrides):
    items = [
        {"command": 22, "latitude": 12.9716, "longitude": 77.5946, "altitude_m": 30, "route_seq": 0},
        {"command": 178, "speed_mps": 8},
        {"command": 16, "latitude": 12.9734, "longitude": 77.5946, "altitude_m": 30, "hold_s": 0, "route_seq": 1},
        {"command": 16, "latitude": 12.9734, "longitude": 77.5964, "altitude_m": 8, "hold_s": 20, "route_seq": 2, "simulated_event": "payload_released"},
        {"command": 21, "latitude": 12.9716, "longitude": 77.5946, "altitude_m": 0, "route_seq": 3},
    ] if extra_items is None else extra_items
    payload = {"execution_id": EXECUTION_ID, "instance": 0, "system_id": 1, "items": items, "route_total": sum(1 for i in items if "route_seq" in i)}
    payload.update(overrides)
    return payload


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Message(SimpleNamespace):
    def get_type(self):
        return self.kind


def custom_mode(sub, main=4):
    return (sub << 24) | (main << 16)


class FakePx4:
    """A scripted PX4: mission manager + commander, as seen from the onboard link."""

    def __init__(self, clock):
        self.clock = clock
        self.queue = []
        self.heartbeats_received = []  # times of GCS heartbeats
        self.sent = []                 # everything the executor sent, in order
        self.items = {}
        self.count = None
        self.awaiting = None
        self.last_request = None
        self.drop_counts = 0
        self.drop_item_once = set()
        self.ack_type = 0
        self.results = {300: 0, 400: 0, 176: 0, 20: 0}
        self.answer_commands = True
        self.armed = False
        self.mode = custom_mode(3)     # Hold
        self.vehicle_type = 2
        self.alive = True
        self._last_heartbeat = None
        self.arms_on_command = True

    # --- what the executor calls -----------------------------------------------------------------
    def recv(self):
        return self.queue.pop(0) if self.queue else None

    def send_heartbeat(self):
        self.heartbeats_received.append(self.clock())
        self.sent.append(("heartbeat",))

    def send_mission_count(self, count):
        self.sent.append(("count", count))
        if self.drop_counts > 0:
            self.drop_counts -= 1
            return
        self.count, self.items = count, {}
        self._request(0)

    def send_mission_item(self, fields):
        self.sent.append(("item", fields["seq"], fields))
        if fields["seq"] in self.drop_item_once:
            self.drop_item_once.discard(fields["seq"])  # lost on the way: PX4 will ask again after 250 ms
            return
        if fields["seq"] != self.awaiting:
            return
        self.items[fields["seq"]] = fields
        if fields["seq"] == self.count - 1:
            self.awaiting = None
            self.queue.append(Message(kind="MISSION_ACK", type=self.ack_type, mission_type=0))
        else:
            self._request(fields["seq"] + 1)

    def send_command(self, command, params):
        self.sent.append(("command", command, params))
        if not self.answer_commands:
            return
        result = self.results[command]
        self.queue.append(Message(kind="COMMAND_ACK", command=command, result=result))
        if result != 0:
            return
        if command == 300:
            self.mode = custom_mode(4)
        elif command == 400 and params[0] == 1.0 and self.arms_on_command:
            self.armed = True
        elif command == 400 and params[0] == 0.0:
            self.armed = False
        elif command == 176:
            self.mode = custom_mode(int(params[2]), int(params[1]))
        elif command == 20:
            self.mode = custom_mode(5)

    # --- the scripted side ------------------------------------------------------------------------
    def _request(self, seq):
        self.awaiting, self.last_request = seq, self.clock()
        self.queue.append(Message(kind="MISSION_REQUEST_INT", seq=seq, mission_type=0))

    def heartbeat(self):
        self.queue.append(Message(kind="HEARTBEAT", autopilot=12, type=self.vehicle_type, base_mode=(128 if self.armed else 0) | 1, custom_mode=self.mode))

    def tick(self):
        now = self.clock()
        if self.alive and (self._last_heartbeat is None or now - self._last_heartbeat >= 1.0):
            self._last_heartbeat = now
            self.heartbeat()
        if self.awaiting is not None and now - self.last_request >= 0.25:
            self._request(self.awaiting)

    def current(self, seq, mission_state=3):
        self.queue.append(Message(kind="MISSION_CURRENT", seq=seq, mission_state=mission_state))

    def reach(self, seq):
        self.queue.append(Message(kind="MISSION_ITEM_REACHED", seq=seq))

    def say(self, text, severity=3):
        self.queue.append(Message(kind="STATUSTEXT", text=text, severity=severity))

    def commands(self):
        return [entry[1] for entry in self.sent if entry[0] == "command"]


def make(payload=None, **fake_options):
    clock = Clock()
    fake = FakePx4(clock)
    for key, value in fake_options.items():
        setattr(fake, key, value)
    executor = ex.MissionExecutor(fake, ex.validate_mission(payload or mission()), clock=clock, wall_clock=clock)
    return executor, fake, clock


def run(executor, fake, clock, seconds, until=None, dt=0.05):
    steps = int(seconds / dt)
    for _ in range(steps):
        fake.tick()
        executor.step()
        if until and until():
            return True
        clock.now += dt
    return until is None


def types(executor):
    return [event["type"] for event in executor.events]


def fly_to_executing(executor, fake, clock):
    assert run(executor, fake, clock, 30, until=lambda: executor.state == "executing"), (executor.state, executor.reason)


# --- validation of every numeric input --------------------------------------------------------------------------

def test_a_valid_mission_is_normalised_and_mapped_to_fixed_mavlink_fields():
    valid = ex.validate_mission(mission())
    assert [item["kind"] for item in valid["items"]] == ["takeoff", "change_speed", "waypoint", "waypoint", "land"]
    assert valid["route_total"] == 4 and valid["system_id"] == 1
    takeoff, speed, waypoint, drop, land = (ex.item_fields(item) for item in valid["items"])
    assert (takeoff["command"], takeoff["frame"], takeoff["x"], takeoff["y"], takeoff["z"]) == (22, 6, 129716000, 775946000, 30.0)
    assert (speed["command"], speed["frame"], speed["param1"], speed["param2"], speed["param3"]) == (178, 2, 1.0, 8.0, -1.0)
    assert (waypoint["command"], waypoint["param1"], waypoint["z"]) == (16, 0.0, 30.0)
    assert (drop["command"], drop["param1"], drop["z"]) == (16, 20.0, 8.0)  # a hold at the drop point, nothing else
    assert (land["command"], land["z"]) == (21, 0.0)
    assert takeoff["param4"] != takeoff["param4"]  # NaN yaw: PX4 chooses


@pytest.mark.parametrize("change, message", [
    (lambda m: m["items"][0].update(latitude=91), "latitude is out of range"),
    (lambda m: m["items"][0].update(longitude=-181), "longitude is out of range"),
    (lambda m: m["items"][2].update(altitude_m=501), "altitude_m is out of range"),
    (lambda m: m["items"][2].update(altitude_m=float("nan")), "finite number"),
    (lambda m: m["items"][2].update(hold_s=-1), "hold_s is out of range"),
    (lambda m: m["items"][1].update(speed_mps=0), "speed_mps is out of range"),
    (lambda m: m["items"][1].update(speed_mps=151), "speed_mps is out of range"),
    (lambda m: m["items"][2].update(latitude="12.97"), "finite number"),
    (lambda m: m["items"][2].update(latitude=True), "finite number"),
    (lambda m: m["items"][2].update(command=183), "is not allowed"),            # DO_SET_SERVO: no actuator commands
    (lambda m: m["items"][2].update(command=176), "is not allowed"),
    (lambda m: m["items"][2].update(param1=5), "unexpected field"),             # no raw parameters
    (lambda m: m["items"][3].update(simulated_event="release_servo"), "is not allowed"),
    (lambda m: m["items"].pop(0), "first item must be the take-off"),
    (lambda m: m["items"].pop(), "last item must be the landing"),
    (lambda m: m["items"][0].update(altitude_m=0.5), "at least 1 m"),
    (lambda m: m.update(system_id=7), "managed SITL identity"),
    (lambda m: m.update(instance=9), "fleet capacity"),
    (lambda m: m.update(execution_id="../../etc/passwd"), "32 lowercase hex"),
    (lambda m: m.update(route_total=9), "route_total"),
    (lambda m: m.update(raw_mavlink="fe0102"), "unexpected mission field"),
    (lambda m: m["items"][2].update(route_seq=0), "unique and increasing"),
])
def test_every_invalid_input_is_rejected_before_anything_is_sent(change, message):
    payload = json.loads(json.dumps(mission()))
    change(payload)
    if payload["route_total"] == 4 and len([i for i in payload["items"] if "route_seq" in i]) != 4:
        payload["route_total"] = len([i for i in payload["items"] if "route_seq" in i])
    with pytest.raises(ex.MissionError, match=message):
        ex.validate_mission(payload)


def test_item_count_limits_and_non_object_payloads():
    with pytest.raises(ex.MissionError, match="2 to 500"):
        ex.validate_mission(mission(extra_items=[{"command": 22, "latitude": 1, "longitude": 1, "altitude_m": 30, "route_seq": 0}]))
    with pytest.raises(ex.MissionError, match="must be an object"):
        ex.validate_mission([1, 2, 3])
    assert set(ex.SENDABLE_COMMANDS) == {300, 400, 176, 20}  # mission start, arm/disarm, set mode, return to launch
    assert set(ex.ITEM_COMMANDS) == {16, 21, 22, 178}


# --- heartbeat cadence ----------------------------------------------------------------------------------------------

def test_gcs_heartbeat_is_sent_every_second_for_the_whole_run():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    run(executor, fake, clock, 60)
    gaps = [b - a for a, b in zip(fake.heartbeats_received, fake.heartbeats_received[1:])]
    assert len(fake.heartbeats_received) >= 60
    assert max(gaps) <= 1.06 and min(gaps) >= 0.99  # steady 1 Hz, never a gap that would trip COM_DL_LOSS_T (10 s)
    assert fake.heartbeats_received[0] == 1000.0     # the first one goes out before anything else
    assert fake.sent[0] == ("heartbeat",)
    assert executor.status()["gcs_heartbeats_sent"] == len(fake.heartbeats_received)


def test_no_command_or_upload_is_sent_before_two_seconds_of_heartbeats():
    executor, fake, clock = make()
    run(executor, fake, clock, 1.9)
    assert executor.state == "connecting" and all(entry[0] == "heartbeat" for entry in fake.sent)
    run(executor, fake, clock, 0.3)
    assert ("count", 5) in fake.sent


# --- upload handshake -------------------------------------------------------------------------------------------------

def test_full_upload_handshake_then_mission_start_then_arm():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    sent = [entry for entry in fake.sent if entry[0] != "heartbeat"]
    assert [entry[0] for entry in sent] == ["count", "item", "item", "item", "item", "item", "command", "command"]
    assert [entry[1] for entry in sent[1:6]] == [0, 1, 2, 3, 4]
    assert sorted(fake.items) == [0, 1, 2, 3, 4] and fake.items[3]["z"] == 8.0
    # MISSION_START first (it does not arm in this PX4 and it is denied for an invalid mission), then arm.
    assert sent[6][1:] == (300, (0.0, 4.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert sent[7][1:] == (400, (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert types(executor) == ["executor_started", "connected", "upload_started", "uploaded", "mission_mode", "arm_accepted", "px4_mode", "armed", "mission_started"] \
        or set(["connected", "upload_started", "uploaded", "mission_mode", "arm_accepted", "armed", "mission_started"]) <= set(types(executor))
    assert types(executor).index("uploaded") < types(executor).index("mission_mode") < types(executor).index("armed")
    status = executor.status()
    assert status["uploaded"] is True and status["armed"] is True and status["state"] == "executing" and status["px4_mode"] == "Mission"


def test_upload_survives_a_dropped_count_and_a_dropped_item():
    executor, fake, clock = make(drop_counts=2, drop_item_once={2})
    fly_to_executing(executor, fake, clock)
    assert [entry for entry in fake.sent if entry[0] == "count"] == [("count", 5)] * 3   # two were lost, the third got through
    assert [entry[1] for entry in fake.sent if entry[0] == "item"].count(2) == 2           # item 2 was sent again when PX4 re-requested it
    assert sorted(fake.items) == [0, 1, 2, 3, 4]
    assert "uploaded" in types(executor)


def test_upload_gives_up_when_px4_never_requests_an_item():
    executor, fake, clock = make(drop_counts=99)
    run(executor, fake, clock, 30, until=lambda: executor.terminal)
    assert executor.state == "failed" and "never requested the first mission item" in executor.reason
    assert len([entry for entry in fake.sent if entry[0] == "count"]) == ex.UPLOAD_COUNT_RETRIES
    assert fake.commands() == []  # nothing was armed or started


def test_upload_stall_in_the_middle_fails_without_commanding_anything():
    executor, fake, clock = make()
    fake.drop_item_once = {3}
    original = fake.tick

    def tick_without_retry():  # PX4 goes silent after item 3 is lost
        saved, fake.awaiting = fake.awaiting, None if fake.awaiting == 3 else fake.awaiting
        original()
        fake.awaiting = saved

    fake.tick = tick_without_retry
    run(executor, fake, clock, 30, until=lambda: executor.terminal)
    assert executor.state == "failed" and "stalled" in executor.reason and fake.commands() == []


@pytest.mark.parametrize("ack, name", [(1, "ERROR"), (5, "INVALID"), (13, "INVALID_SEQUENCE"), (4, "NO_SPACE"), (3, "UNSUPPORTED")])
def test_a_mission_ack_error_fails_the_run_and_never_arms(ack, name):
    executor, fake, clock = make(ack_type=ack)
    run(executor, fake, clock, 30, until=lambda: executor.terminal)
    assert executor.state == "failed" and f"MAV_MISSION_{name}" in executor.reason
    assert fake.commands() == [] and executor.status()["uploaded"] is False
    assert executor.events[-1]["type"] == "failed" and executor.events[-1]["data"]["code"] == "mission_rejected"


def test_mission_start_denied_reports_what_px4_said_and_never_arms():
    executor, fake, clock = make()
    fake.results[300] = 2
    run(executor, fake, clock, 2.5)   # after the upload, before MISSION_START: PX4's feasibility check speaks first
    fake.say("Mission rejected: takeoff altitude too low!")
    run(executor, fake, clock, 30, until=lambda: executor.terminal)
    assert executor.state == "failed" and "MISSION_START with MAV_RESULT_DENIED" in executor.reason
    assert "takeoff altitude too low" in executor.reason
    assert fake.commands() == [300]


def test_arm_rejected_and_arm_never_confirmed_both_fail_cleanly():
    executor, fake, clock = make()
    fake.results[400] = 1
    run(executor, fake, clock, 30, until=lambda: executor.terminal)
    assert executor.state == "failed" and "arm command with MAV_RESULT_TEMPORARILY_REJECTED" in executor.reason
    executor, fake, clock = make(arms_on_command=False)
    run(executor, fake, clock, 40, until=lambda: executor.terminal)
    assert executor.state == "failed" and "did not report armed" in executor.reason


def test_a_command_without_any_answer_is_retried_then_fails():
    executor, fake, clock = make(answer_commands=False)
    run(executor, fake, clock, 40, until=lambda: executor.terminal)
    assert fake.commands() == [300] * ex.COMMAND_RETRIES
    assert executor.state == "failed" and "no answer" in executor.reason


# --- flying: progress, simulated payload event, completion ------------------------------------------------------------

def test_progress_events_and_completion_and_the_drop_is_only_a_logged_event():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.current(0)
    run(executor, fake, clock, 2)
    assert executor.status()["route_reached"] == 0 and executor.status()["route_current"] == 1
    fake.reach(0); fake.reach(0); fake.current(2)   # PX4 repeats ITEM_REACHED; item 1 is the speed change
    run(executor, fake, clock, 2)
    assert types(executor).count("takeoff_complete") == 1
    fake.reach(2); fake.current(3)
    run(executor, fake, clock, 2)
    progress = executor.status()
    assert (progress["route_reached"], progress["route_total"], progress["route_current"], progress["percent"]) == (2, 4, 3, 50.0)
    before = len(fake.sent)
    fake.reach(3); fake.current(4)
    run(executor, fake, clock, 2)
    released = [event for event in executor.events if event["type"] == "payload_released_simulated"]
    assert len(released) == 1 and released[0]["data"] == {"route_seq": 2, "simulated": True}
    assert "No release command was sent" in released[0]["message"]
    assert all(entry[0] == "heartbeat" for entry in fake.sent[before:])  # reaching the drop point sends nothing but heartbeats
    fake.reach(4); fake.current(4, mission_state=5)
    run(executor, fake, clock, 2)
    assert executor.state == "executing" and "mission_finished" in types(executor)
    fake.armed = False
    run(executor, fake, clock, 3, until=lambda: executor.terminal)
    assert executor.state == "completed" and executor.status()["percent"] == 100.0 and executor.status()["route_reached"] == 4
    assert types(executor)[-2:] == ["disarmed", "completed"]
    assert set(fake.commands()) == {300, 400}  # start and arm only: no mode change, no return, no actuator


def test_disarm_before_the_mission_finished_is_a_failure_not_a_completion():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.current(2)
    run(executor, fake, clock, 1)
    fake.armed = False
    run(executor, fake, clock, 3, until=lambda: executor.terminal)
    assert executor.state == "failed" and executor.reason == "PX4 disarmed before the mission finished"


def test_still_armed_on_the_ground_after_landing_gets_one_disarm():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.reach(4); fake.current(4, mission_state=5)
    fake.queue.append(Message(kind="EXTENDED_SYS_STATE", landed_state=1, vtol_state=0))
    run(executor, fake, clock, ex.DISARM_AFTER_LANDING_S - 1)
    assert 400 not in fake.commands()[2:]
    run(executor, fake, clock, 5, until=lambda: executor.terminal)
    assert fake.sent[-1][0] in ("command", "heartbeat") and ("command", 400, (0.0,) * 7) in fake.sent
    assert executor.state == "completed"


# --- pause / resume / abort ---------------------------------------------------------------------------------------------

def test_pause_holds_resume_continues_and_both_are_refused_in_the_wrong_state():
    executor, fake, clock = make()
    assert executor.control("pause") == {"accepted": False, "detail": "pause is only possible while executing (state: connecting)"}
    fly_to_executing(executor, fake, clock)
    assert executor.control("resume")["accepted"] is False
    assert executor.control("pause")["accepted"] is True
    run(executor, fake, clock, 3, until=lambda: executor.state == "paused")
    assert executor.state == "paused" and fake.mode == custom_mode(3)
    assert ("command", 176, (1.0, 4.0, 3.0, 0.0, 0.0, 0.0, 0.0)) in fake.sent
    run(executor, fake, clock, 20)
    assert executor.state == "paused"                        # Hold while paused is expected, not a failsafe
    assert executor.control("pause")["accepted"] is False
    assert executor.control("resume")["accepted"] is True
    run(executor, fake, clock, 3, until=lambda: executor.state == "executing")
    assert executor.state == "executing" and fake.mode == custom_mode(4)
    assert ("command", 176, (1.0, 4.0, 4.0, 0.0, 0.0, 0.0, 0.0)) in fake.sent
    assert [t for t in types(executor) if t in ("pause_requested", "paused", "resume_requested", "resumed")] == ["pause_requested", "paused", "resume_requested", "resumed"]
    assert executor.control("fly_to_moon") == {"accepted": False, "detail": "unknown control command"}


def test_a_rejected_pause_leaves_the_run_executing():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.results[176] = 1
    executor.control("pause")
    run(executor, fake, clock, 3)
    assert executor.state == "executing" and "pause_rejected" in types(executor)


def test_abort_in_flight_returns_to_launch_and_ends_aborted_after_disarm():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.current(2)
    run(executor, fake, clock, 1)
    assert executor.control("abort") == {"accepted": True, "detail": "abort accepted"}
    run(executor, fake, clock, 2)
    assert executor.state == "returning" and fake.commands()[-1] == 20 and fake.mode == custom_mode(5)
    assert executor.control("pause")["accepted"] is False and executor.control("abort")["detail"] == "already returning to launch"
    run(executor, fake, clock, 30)
    assert executor.state == "returning" and fake.commands().count(20) == 1   # accepted once: not spammed
    fake.armed = False
    run(executor, fake, clock, 3, until=lambda: executor.terminal)
    assert executor.state == "aborted" and types(executor)[-1] == "aborted"
    assert [t for t in types(executor) if t in ("abort_requested", "return_to_launch", "return_accepted")] == ["abort_requested", "return_to_launch", "return_accepted"]


def test_abort_keeps_asking_until_px4_is_returning():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.answer_commands = False       # PX4 does not hear the first requests
    executor.control("abort")
    run(executor, fake, clock, 12)
    assert fake.commands().count(20) >= 5 and executor.state == "returning"
    fake.answer_commands = True
    run(executor, fake, clock, 4)
    assert fake.mode == custom_mode(5)
    count = fake.commands().count(20)
    run(executor, fake, clock, 10)
    assert fake.commands().count(20) == count


def test_abort_while_paused_and_abort_before_arming():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    executor.control("pause")
    run(executor, fake, clock, 3)
    assert executor.control("abort")["accepted"] is True
    run(executor, fake, clock, 2)
    assert executor.state == "returning"
    executor, fake, clock = make()
    run(executor, fake, clock, 2.5)    # uploading, not armed
    assert executor.control("abort")["accepted"] is True
    assert executor.state == "aborted" and executor.reason == "aborted before the aircraft was armed"
    run(executor, fake, clock, 5)
    assert fake.commands() == [] and executor.control("abort")["accepted"] is False


# --- link lost and PX4 failsafes ----------------------------------------------------------------------------------------

def test_link_lost_after_ten_seconds_without_px4_heartbeat_and_recovery():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.alive = False
    run(executor, fake, clock, 9.5)
    assert executor.state == "executing"
    run(executor, fake, clock, 2)
    assert executor.state == "link_lost" and "no PX4 heartbeat for 10 s" in executor.reason
    assert executor.terminal is False and executor.status()["px4_heartbeat_age_s"] > 10
    heartbeats = len(fake.heartbeats_received)
    run(executor, fake, clock, 5)
    assert len(fake.heartbeats_received) >= heartbeats + 4   # the GCS heartbeat keeps going while the link is lost
    fake.alive = True
    run(executor, fake, clock, 2)
    assert executor.state == "executing" and types(executor)[-1] == "link_restored"


def test_link_lost_becomes_final_when_the_link_never_returns_and_abort_still_goes_out():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.alive = False
    run(executor, fake, clock, 12)
    assert executor.state == "link_lost"
    executor2, fake2, clock2 = make()
    fly_to_executing(executor2, fake2, clock2)
    fake2.alive = False
    run(executor2, fake2, clock2, 12)
    assert executor2.control("abort")["accepted"] is True and fake2.commands()[-1] == 20   # abort is sent even with no heartbeat
    run(executor, fake, clock, ex.LINK_LOST_GIVE_UP_S + 2, until=lambda: executor.terminal)
    assert executor.terminal and executor.state == "link_lost" and executor.should_exit


def test_no_heartbeat_at_all_fails_within_the_connect_timeout():
    executor, fake, clock = make(alive=False)
    run(executor, fake, clock, 20, until=lambda: executor.terminal)
    assert executor.state == "failed" and "No PX4 heartbeat on the onboard link" in executor.reason
    assert [entry[0] for entry in fake.sent if entry[0] != "heartbeat"] == []


def test_an_uncommanded_switch_to_return_is_reported_as_a_px4_failsafe():
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.current(2)
    fake.say("GCS connection lost")
    fake.mode = custom_mode(5)
    run(executor, fake, clock, 3)
    assert executor.state == "failsafe" and "PX4 switched to Return by itself" in executor.reason and "GCS connection lost" in executor.reason
    assert 20 not in fake.commands()   # the executor did not ask for it
    fake.armed = False
    run(executor, fake, clock, 3, until=lambda: executor.terminal)
    assert executor.state == "failed" and "failsafe" in types(executor)


# --- identity: refuse anything that is not the managed SITL x500 ------------------------------------------------------------

def test_wrong_vehicle_type_and_an_already_armed_aircraft_are_refused():
    executor, fake, clock = make(vehicle_type=1)   # fixed wing
    run(executor, fake, clock, 5, until=lambda: executor.terminal)
    assert executor.state == "failed" and "not the x500 quadrotor" in executor.reason and fake.commands() == []
    executor, fake, clock = make(armed=True)
    run(executor, fake, clock, 5, until=lambda: executor.terminal)
    assert executor.state == "failed" and "already armed" in executor.reason
    assert [entry for entry in fake.sent if entry[0] != "heartbeat"] == []


def test_a_heartbeat_from_something_that_is_not_px4_is_ignored():
    executor, fake, clock = make(alive=False)
    fake.queue.append(Message(kind="HEARTBEAT", autopilot=8, type=6, base_mode=0, custom_mode=0))   # another GCS
    run(executor, fake, clock, 5)
    assert executor.state == "connecting" and executor.status()["px4_heartbeat_age_s"] is None


def _manager(found, owned):
    return SimpleNamespace(discover_px4_instances=lambda root: dict(found), _load_owned=lambda path: dict(owned), _state_path=lambda: Path("state.json"))


def test_only_a_running_instance_started_by_the_fleet_manager_is_accepted(tmp_path):
    assert ex.verify_managed_instance(0, tmp_path, _manager({0: 4242}, {0: 4242})) is None
    not_running = ex.verify_managed_instance(0, tmp_path, _manager({}, {0: 4242}))
    assert not_running["failure_code"] == "instance_not_running"
    foreign = ex.verify_managed_instance(0, tmp_path, _manager({0: 4242}, {}))
    assert foreign["failure_code"] == "instance_not_managed" and "was not started by the TRINETRA fleet manager" in foreign["failure"]
    restarted = ex.verify_managed_instance(0, tmp_path, _manager({0: 5555}, {0: 4242}))   # same instance number, different process
    assert restarted["failure_code"] == "instance_not_managed"
    assert ex.verify_managed_instance(1, tmp_path, _manager({0: 4242}, {0: 4242}))["failure_code"] == "instance_not_running"


def test_start_refuses_an_unmanaged_instance_and_an_invalid_mission_before_writing_or_spawning(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "runtime_dir", lambda instance: tmp_path)
    monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn")))
    monkeypatch.setattr(ex, "verify_managed_instance", lambda instance, root: {"failure_code": "instance_not_managed", "failure": "not managed"})
    refused = ex.cli_start(tmp_path, json.dumps(mission()))
    assert refused == {"failure_code": "instance_not_managed", "failure": "not managed"} and list(tmp_path.iterdir()) == []
    invalid = ex.cli_start(tmp_path, json.dumps(mission(system_id=3)))
    assert invalid["failure_code"] == "mission_invalid" and list(tmp_path.iterdir()) == []
    assert ex.cli_start(tmp_path, "not json")["failure_code"] == "mission_invalid"


def test_control_files_only_accept_the_three_commands_for_the_running_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "runtime_dir", lambda instance: tmp_path)
    (tmp_path / "status.json").write_text(json.dumps({"execution_id": EXECUTION_ID, "state": "executing"}), encoding="utf-8")
    (tmp_path / "daemon.pid").write_text("4321", encoding="utf-8")
    monkeypatch.setattr(ex, "_pid_alive", lambda pid: pid == 4321)
    accepted = ex.cli_control(0, EXECUTION_ID, "pause")
    assert accepted["accepted"] is True and accepted["daemon_alive"] is True
    assert json.loads((tmp_path / "control.jsonl").read_text(encoding="utf-8").splitlines()[0])["command"] == "pause"
    other = ex.cli_control(0, "f" * 32, "abort")
    assert other["failure_code"] == "execution_mismatch"
    monkeypatch.setattr(ex, "_pid_alive", lambda pid: False)
    assert ex.cli_control(0, EXECUTION_ID, "resume")["failure_code"] == "executor_not_running"
    (tmp_path / "events.jsonl").write_text('{"index": 0, "type": "a"}\n{"index": 1, "type": "b"}\nbroken\n', encoding="utf-8")
    assert [event["type"] for event in ex.read_events(tmp_path, 0)] == ["b"]


def test_start_stores_exactly_what_was_validated_so_the_daemon_can_validate_it_again(tmp_path, monkeypatch):
    """Found live: the daemon re-validates mission.json, so the file must hold the submitted form, not the normalised one."""
    monkeypatch.setattr(ex, "runtime_dir", lambda instance: tmp_path)
    monkeypatch.setattr(ex, "verify_managed_instance", lambda instance, root: None)
    monkeypatch.setattr(ex.os, "chmod", lambda *a, **k: None)
    spawned = []

    class Process:
        pid = 4321
        returncode = None

        def poll(self):
            return None

    def popen(command, **kwargs):
        spawned.append(command)
        (tmp_path / "status.json").write_text(json.dumps({"execution_id": EXECUTION_ID, "state": "connecting"}), encoding="utf-8")
        return Process()

    monkeypatch.setattr(ex.subprocess, "Popen", popen)
    result = ex.cli_start(tmp_path, json.dumps(mission()))
    assert result["started"] is True and result["status"]["execution_id"] == EXECUTION_ID
    assert spawned[0][-5:] == ["run", "--instance", "0", "--execution-id", EXECUTION_ID] and "shell" not in spawned[0]
    stored = json.loads((tmp_path / "mission.json").read_text(encoding="utf-8"))
    assert stored == mission()
    assert ex.validate_mission(stored)["route_total"] == 4   # what the daemon does first


def test_takeoff_is_recorded_from_mission_current_when_px4_sends_no_item_reached():
    """Seen live: PX4 moved from the take-off item to the next one without a MISSION_ITEM_REACHED for it."""
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.current(0)
    run(executor, fake, clock, 1)
    assert "takeoff_complete" not in types(executor)
    fake.current(2)                      # no reach(0) at all
    run(executor, fake, clock, 1)
    assert types(executor).count("takeoff_complete") == 1 and executor.status()["route_reached"] == 1
    fake.reach(0)                        # a late duplicate changes nothing
    fake.current(4)                      # two more route items passed without their own message
    run(executor, fake, clock, 1)
    assert types(executor).count("takeoff_complete") == 1 and types(executor).count("waypoint_reached") == 2
    assert types(executor).count("payload_released_simulated") == 1


def test_a_long_px4_text_split_into_chunks_is_recorded_as_one_message():
    """Seen live: "Return: start return at 519 m (31 m above destinat" + "ion)" arrived as two STATUSTEXT chunks."""
    executor, fake, clock = make()
    fly_to_executing(executor, fake, clock)
    fake.queue.append(Message(kind="STATUSTEXT", text="Return: start return at 519 m (31 m above destinat", severity=6, id=7, chunk_seq=0))
    fake.queue.append(Message(kind="STATUSTEXT", text="ion)", severity=6, id=7, chunk_seq=1))
    fake.say("Takeoff detected", severity=6)
    run(executor, fake, clock, 1)
    texts = [event["message"] for event in executor.events if event["type"] == "px4_message"]
    assert texts[-2:] == ["PX4: Return: start return at 519 m (31 m above destination)", "PX4: Takeoff detected"]
