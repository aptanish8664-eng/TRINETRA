"""Allowlisted WSL-side mission executor for TRINETRA: flies a validated route on the managed PX4 SITL instance.

SITL ONLY. This process talks MAVLink to the ONBOARD link of a PX4 instance that the TRINETRA fleet manager started
(never the GCS port 18570, which carries the telemetry stream). It can do exactly these things, all built from
range-checked numbers: send a GCS heartbeat, upload a mission (MISSION_COUNT / MISSION_ITEM_INT), MISSION_START,
arm, change mode to Mission / Hold, Return to launch, and disarm once PX4 reports it is on the ground. There is no
command passthrough, no parameter write and no raw MAVLink input.

Control channel (local to WSL, directory mode 0700 under XDG_RUNTIME_DIR): `mission.json` (the validated items),
`status.json` (state, written atomically), `events.jsonl` (append-only), `control.jsonl` (pause / resume / abort
requests appended by this script's own CLI). The Windows backend only ever runs this script with fixed arguments.

Facts checked against the PX4 checkout in use (commit 14a7aec):
* the onboard link listens on 14580+instance and streams to 14540+instance; it latches onto the first localhost
  sender, so this process binds 127.0.0.1:14540+instance;
* the x500 SITL airframe sets NAV_DLL_ACT=2 (Return) with COM_DL_LOSS_T=10 s, so a steady GCS heartbeat is sent for
  the whole run;
* only MISSION_ITEM_INT is accepted (MISSION_ITEM is answered UNSUPPORTED); PX4 re-requests an item after 250 ms and
  gives up after 5 s; sequence 0 is an ordinary first item (not a home placeholder) and a take-off, if present,
  must be the first waypoint item; MIS_DIST_1WP (10 km) only warns;
* MAV_CMD_MISSION_START switches to Mission mode and does NOT arm ("the mission starts once the vehicle is armed"),
  and is denied when the mission is invalid, so it is sent before the arm command.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Optional

FLEET_CAPACITY = 16       # technical ceiling of instance slots; the policy limit is the backend's TRINETRA_SIM_MAX_AIRCRAFT
MAX_ITEMS = 500

# MAVLink constants (common dialect); kept local so this file imports without pymavlink (unit tests, CLI status).
MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_DO_SET_MODE = 176
MAV_CMD_DO_CHANGE_SPEED = 178
MAV_CMD_MISSION_START = 300
MAV_CMD_SET_MESSAGE_INTERVAL = 511
MSG_EXTENDED_SYS_STATE = 245
MAV_CMD_COMPONENT_ARM_DISARM = 400
MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6
MAV_FRAME_MISSION = 2
MAV_TYPE_QUADROTOR = 2
MAV_AUTOPILOT_PX4 = 12
MAV_MODE_FLAG_SAFETY_ARMED = 128
MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 1
MAV_LANDED_STATE_ON_GROUND = 1
MAV_LANDED_STATE_LANDING = 4
LANDED_STATE_NAMES = {0: "undefined", 1: "on_ground", 2: "in_air", 3: "takeoff", 4: "landing"}
ON_GROUND_ALT_M = 1.5
ON_GROUND_SPEED_M_S = 0.7
MISSION_STATE_COMPLETE = 5
PX4_MAIN_AUTO = 4
PX4_SUB = {"takeoff": 2, "hold": 3, "mission": 4, "return": 5, "land": 6}
PX4_SUB_NAMES = {1: "Ready", 2: "Takeoff", 3: "Hold", 4: "Mission", 5: "Return", 6: "Land"}
PX4_MAIN_NAMES = {1: "Manual", 2: "Altitude", 3: "Position", 4: "Auto", 5: "Acro", 6: "Offboard", 7: "Stabilized"}
MISSION_ACK_NAMES = {0: "ACCEPTED", 1: "ERROR", 2: "UNSUPPORTED_FRAME", 3: "UNSUPPORTED", 4: "NO_SPACE", 5: "INVALID",
                     6: "INVALID_PARAM1", 7: "INVALID_PARAM2", 8: "INVALID_PARAM3", 9: "INVALID_PARAM4", 10: "INVALID_PARAM5_X",
                     11: "INVALID_PARAM6_Y", 12: "INVALID_PARAM7", 13: "INVALID_SEQUENCE", 14: "DENIED", 15: "OPERATION_CANCELLED"}
COMMAND_RESULT_NAMES = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED", 3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS", 6: "CANCELLED"}

# The only mission commands an item may carry, and the only commands this process may send.
ITEM_COMMANDS = {MAV_CMD_NAV_TAKEOFF: "takeoff", MAV_CMD_NAV_WAYPOINT: "waypoint", MAV_CMD_NAV_LAND: "land", MAV_CMD_DO_CHANGE_SPEED: "change_speed"}
SENDABLE_COMMANDS = frozenset({MAV_CMD_MISSION_START, MAV_CMD_COMPONENT_ARM_DISARM, MAV_CMD_DO_SET_MODE, MAV_CMD_NAV_RETURN_TO_LAUNCH, MAV_CMD_SET_MESSAGE_INTERVAL})
CONTROL_COMMANDS = ("pause", "resume", "abort", "land")
TERMINAL_STATES = frozenset({"completed", "aborted", "failed", "link_lost"})

HEARTBEAT_INTERVAL_S = 1.0
CONNECT_TIMEOUT_S = 15.0
PRE_COMMAND_HEARTBEAT_S = 2.0
UPLOAD_COUNT_RETRY_S = 1.0
UPLOAD_COUNT_RETRIES = 5
UPLOAD_STALL_S = 6.0
FEASIBILITY_WAIT_S = 1.0
COMMAND_RETRY_S = 1.5
COMMAND_RETRIES = 3
ARM_CONFIRM_S = 6.0
LINK_LOST_S = 10.0
LINK_LOST_GIVE_UP_S = 120.0
DISARM_AFTER_LANDING_S = 10.0
DISARM_AFTER_TOUCHDOWN_S = 4.0      # Land here: PX4 normally disarms itself after touchdown; if not, a NORMAL disarm after this long
LAND_GIVE_UP_S = 300.0
LANDED_STATE_STALE_S = 5.0
LANDED_STATE_INTERVAL_US = 500000   # EXTENDED_SYS_STATE at 2 Hz

_EXECUTION_ID = re.compile(r"^[0-9a-f]{32}$")


class MissionError(ValueError):
    """The mission payload is not acceptable; nothing is sent to the aircraft."""


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MissionError(f"{name} must be a finite number")
    if not low <= value <= high:
        raise MissionError(f"{name} is out of range ({low:g} to {high:g})")
    return float(value)


def validate_mission(payload: Any) -> dict[str, Any]:
    """Strict check of the mission the backend sends. Returns a normalised copy; raises MissionError otherwise."""
    if not isinstance(payload, dict):
        raise MissionError("mission must be an object")
    unknown = set(payload) - {"execution_id", "instance", "system_id", "items", "route_total", "in_air"}
    if unknown:
        raise MissionError(f"unexpected mission field(s): {', '.join(sorted(unknown))}")
    execution_id = payload.get("execution_id")
    if not isinstance(execution_id, str) or not _EXECUTION_ID.fullmatch(execution_id):
        raise MissionError("execution_id must be 32 lowercase hex characters")
    instance = payload.get("instance")
    if isinstance(instance, bool) or not isinstance(instance, int) or not 0 <= instance < FLEET_CAPACITY:
        raise MissionError("instance must be an integer within the fleet capacity")
    if payload.get("system_id") != instance + 1:
        raise MissionError("system_id must be instance + 1 (the managed SITL identity)")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not 2 <= len(raw_items) <= MAX_ITEMS:
        raise MissionError(f"items must be a list of 2 to {MAX_ITEMS} entries")
    items: list[dict[str, Any]] = []
    route_seqs: list[int] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise MissionError(f"item {index} must be an object")
        command = raw.get("command")
        if isinstance(command, bool) or command not in ITEM_COMMANDS:
            raise MissionError(f"item {index}: command {command!r} is not allowed")
        allowed = {"command", "route_seq", "simulated_event"} | ({"speed_mps"} if command == MAV_CMD_DO_CHANGE_SPEED else {"latitude", "longitude", "altitude_m", "hold_s"})
        if set(raw) - allowed:
            raise MissionError(f"item {index}: unexpected field(s) {sorted(set(raw) - allowed)}")
        item: dict[str, Any] = {"seq": index, "command": command, "kind": ITEM_COMMANDS[command], "route_seq": None, "simulated_event": None}
        if command == MAV_CMD_DO_CHANGE_SPEED:
            item["speed_mps"] = _number(raw.get("speed_mps"), f"item {index} speed_mps", 0.5, 150.0)
        else:
            item["latitude"] = _number(raw.get("latitude"), f"item {index} latitude", -90.0, 90.0)
            item["longitude"] = _number(raw.get("longitude"), f"item {index} longitude", -180.0, 180.0)
            item["altitude_m"] = _number(raw.get("altitude_m", 0.0), f"item {index} altitude_m", 0.0, 500.0)
            item["hold_s"] = _number(raw.get("hold_s", 0.0), f"item {index} hold_s", 0.0, 3600.0)
            if command == MAV_CMD_NAV_TAKEOFF and item["altitude_m"] < 1.0:
                raise MissionError(f"item {index}: take-off altitude must be at least 1 m")
            route_seq = raw.get("route_seq")
            if isinstance(route_seq, bool) or not isinstance(route_seq, int) or route_seq < 0:
                raise MissionError(f"item {index}: route_seq must be a non-negative integer")
            item["route_seq"] = route_seq
            route_seqs.append(route_seq)
            event = raw.get("simulated_event")
            if event not in (None, "payload_released"):
                raise MissionError(f"item {index}: simulated_event {event!r} is not allowed")
            item["simulated_event"] = event  # a logged software event only: never an actuator command
        items.append(item)
    if items[0]["command"] != MAV_CMD_NAV_TAKEOFF:
        raise MissionError("the first item must be the take-off (PX4 requires take-off to be the first waypoint item)")
    if items[-1]["command"] != MAV_CMD_NAV_LAND:
        raise MissionError("the last item must be the landing")
    if sum(1 for item in items if item["command"] == MAV_CMD_NAV_TAKEOFF) != 1 or sum(1 for item in items if item["command"] == MAV_CMD_NAV_LAND) != 1:
        raise MissionError("exactly one take-off and one landing are allowed")
    if route_seqs != sorted(set(route_seqs)):
        raise MissionError("route_seq values must be unique and increasing")
    route_total = payload.get("route_total")
    if route_total != len(route_seqs):
        raise MissionError("route_total must equal the number of route waypoints")
    in_air = payload.get("in_air", False)
    if not isinstance(in_air, bool):
        raise MissionError("in_air must be true or false")
    return {"execution_id": execution_id, "instance": instance, "system_id": instance + 1, "items": items, "route_total": route_total, "in_air": in_air}


def item_fields(item: dict[str, Any]) -> dict[str, Any]:
    """MISSION_ITEM_INT fields for one validated item (frame, params and scaled integers are fixed here)."""
    if item["command"] == MAV_CMD_DO_CHANGE_SPEED:
        # param1 = 1 (ground speed), param2 = speed, param3 = -1 (throttle unchanged)
        return {"seq": item["seq"], "frame": MAV_FRAME_MISSION, "command": MAV_CMD_DO_CHANGE_SPEED, "param1": 1.0, "param2": item["speed_mps"],
                "param3": -1.0, "param4": 0.0, "x": 0, "y": 0, "z": 0.0}
    hold = item["hold_s"] if item["command"] == MAV_CMD_NAV_WAYPOINT else 0.0
    return {"seq": item["seq"], "frame": MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, "command": item["command"], "param1": hold, "param2": 0.0, "param3": 0.0,
            "param4": float("nan"),  # yaw: let PX4 choose
            "x": int(round(item["latitude"] * 1e7)), "y": int(round(item["longitude"] * 1e7)),
            "z": 0.0 if item["command"] == MAV_CMD_NAV_LAND else item["altitude_m"]}


def mode_name(custom_mode: int) -> str:
    main, sub = (custom_mode >> 16) & 0xFF, (custom_mode >> 24) & 0xFF
    if main == PX4_MAIN_AUTO:
        return PX4_SUB_NAMES.get(sub, f"Auto/{sub}")
    return PX4_MAIN_NAMES.get(main, f"mode {main}")


class MissionExecutor:
    """The state machine. `link` is the only way out to the aircraft; `step()` is one non-blocking iteration."""

    def __init__(self, link: Any, mission: dict[str, Any], *, clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 on_event: Optional[Callable[[dict[str, Any]], None]] = None) -> None:
        self.link = link
        self.mission = mission
        # `in_air`: a hand-over or a resume from the air. The aircraft is already armed and flying: the run does not arm and does not refuse an armed aircraft.
        self.in_air = bool(mission.get("in_air"))
        self.items: list[dict[str, Any]] = mission["items"]
        self.target_system = int(mission["system_id"])
        self.clock = clock
        self.wall_clock = wall_clock
        self.on_event = on_event
        self.events: list[dict[str, Any]] = []
        now = clock()
        self.state = "connecting"
        self.reason = ""
        self.state_since = now
        self.started = now
        self.heartbeats_sent = 0
        self._last_heartbeat_sent: Optional[float] = None
        self._first_heartbeat_sent: Optional[float] = None
        self._last_px4_heartbeat: Optional[float] = None
        self.armed = False
        self.was_armed = False
        self.armed_at_wall: Optional[float] = None
        self.custom_mode: Optional[int] = None
        self.landed_state: Optional[int] = None
        self.current_item: Optional[int] = None
        self.reached_items: set[int] = set()
        self.mission_finished = False
        self.uploaded = False
        self._count_sent_at: Optional[float] = None
        self._count_sends = 0
        self._last_upload_activity: Optional[float] = None
        self._highest_requested = -1
        self._uploaded_at: Optional[float] = None
        self._pending: Optional[dict[str, Any]] = None
        self._before_link_lost: Optional[str] = None
        self._expected_mode: Optional[str] = None
        self._statustexts: list[str] = []
        self._text_chunks: dict[int, str] = {}
        self._landed_since: Optional[float] = None
        self._disarm_sent = False
        self._abort_requested = False
        self._return_failed = False
        self._finish_link_lost = False
        self._rtl_last_sent: Optional[float] = None
        self._landed_state_at: Optional[float] = None
        self._landed_state_requested: Optional[float] = None
        self.rel_alt_m: Optional[float] = None
        self.ground_speed_m_s: Optional[float] = None
        self._land_result: Optional[str] = None      # accepted | refused: <PX4's reason> (read by the backend right after the request)
        self._land_started: Optional[float] = None
        self._land_last_sent: Optional[float] = None
        self._before_land: Optional[str] = None
        self._fallback_noted = False
        self._emit("executor_started", "Mission executor started; sending a GCS heartbeat every second.", {"items": len(self.items), "route_waypoints": mission["route_total"]})

    # --- bookkeeping -------------------------------------------------------------------------------------------
    @property
    def terminal(self) -> bool:
        """Final: nothing more will happen. `link_lost` is final only once the executor has given up waiting."""
        return self.state in ("completed", "aborted", "failed") or (self.state == "link_lost" and self._finish_link_lost)

    def _emit(self, kind: str, message: str, data: Optional[dict[str, Any]] = None) -> None:
        event = {"index": len(self.events), "t": self.wall_clock(), "type": kind, "message": message, "data": data or {}}
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    def _set_state(self, state: str, reason: str = "") -> None:
        if state == self.state and reason == self.reason:
            return
        self.state, self.reason, self.state_since = state, reason, self.clock()

    def _fail(self, code: str, message: str) -> None:
        recent = "; ".join(self._statustexts[-3:])
        full = f"{message}{f' PX4 said: {recent}' if recent else ''}"
        self._pending = None
        if self.armed:
            # Never leave an armed aircraft without a decision: bring it home, then report the failure.
            self._emit("failure", full, {"code": code})
            self._begin_return(f"failed: {full}", failed=True)
            return
        self._set_state("failed", full)
        self._emit("failed", full, {"code": code})

    def route_progress(self) -> dict[str, Any]:
        route_items = [item for item in self.items if item["route_seq"] is not None]
        reached = set(self.reached_items)
        if self.current_item is not None:
            reached |= {item["seq"] for item in route_items if item["seq"] < self.current_item}
        if self.state == "completed":
            reached = {item["seq"] for item in route_items}
        done = [item for item in route_items if item["seq"] in reached]
        current = next((item for item in route_items if item["seq"] not in reached), None)
        total = len(route_items)
        return {"route_total": total, "route_reached": len(done), "route_current": (current["route_seq"] + 1) if current else total,
                "percent": round(100.0 * len(done) / total, 1) if total else 0.0}

    def status(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "execution_id": self.mission["execution_id"], "instance": self.mission["instance"], "system_id": self.target_system,
            "state": self.state, "reason": self.reason, "terminal": self.terminal, "updated_at": self.wall_clock(),
            "uploaded": self.uploaded, "armed": self.armed, "px4_mode": mode_name(self.custom_mode) if self.custom_mode is not None else None,
            "landed_state": self.landed_state, "landed_state_name": LANDED_STATE_NAMES.get(self.landed_state) if self.landed_state is not None else None,
            "on_ground": self.on_ground(), "on_ground_source": self.on_ground_source(), "rel_alt_m": self.rel_alt_m, "land_result": self._land_result, "current_item": self.current_item, "items": len(self.items),
            "px4_heartbeat_age_s": None if self._last_px4_heartbeat is None else round(now - self._last_px4_heartbeat, 2),
            "gcs_heartbeats_sent": self.heartbeats_sent, "armed_at": self.armed_at_wall, "events": len(self.events),
            "px4_messages": self._statustexts[-5:], **self.route_progress(),
        }

    # --- landed state --------------------------------------------------------------------------------------------
    def _landed_state_fresh(self, now: Optional[float] = None) -> bool:
        now = self.clock() if now is None else now
        return self.landed_state is not None and self._landed_state_at is not None and now - self._landed_state_at <= LANDED_STATE_STALE_S

    def on_ground_source(self) -> str:
        return "px4_landed_state" if self._landed_state_fresh() else "altitude_speed_fallback"

    def on_ground(self) -> Optional[bool]:
        """On the ground from PX4's EXTENDED_SYS_STATE; only when that is missing or stale, from relative altitude and ground speed."""
        if self._landed_state_fresh():
            return self.landed_state == MAV_LANDED_STATE_ON_GROUND
        if self.rel_alt_m is None:
            return None
        return abs(self.rel_alt_m) < ON_GROUND_ALT_M and (self.ground_speed_m_s is None or self.ground_speed_m_s < ON_GROUND_SPEED_M_S)

    def _request_landed_state(self, now: float) -> None:
        """Ask PX4 for EXTENDED_SYS_STATE at 2 Hz on this onboard link (retried every 5 s while none has arrived)."""
        if self._landed_state_requested is not None and (self._landed_state_fresh(now) or now - self._landed_state_requested < 5.0):
            return
        self._landed_state_requested = now
        self.link.send_command(MAV_CMD_SET_MESSAGE_INTERVAL, (float(MSG_EXTENDED_SYS_STATE), float(LANDED_STATE_INTERVAL_US), 0.0, 0.0, 0.0, 0.0, 0.0))

    # --- commands (allowlisted) ----------------------------------------------------------------------------------
    def _command(self, command: int, params: tuple[float, ...], label: str, on_ok: Callable[[], None], on_fail: Callable[[str], None]) -> None:
        if command not in SENDABLE_COMMANDS:
            raise RuntimeError("command is not on the allowlist")
        padded = tuple(params) + (0.0,) * (7 - len(params))
        self._pending = {"command": command, "params": padded, "label": label, "sent_at": self.clock(), "tries": 1, "on_ok": on_ok, "on_fail": on_fail}
        self.link.send_command(command, padded)

    def _set_mode(self, sub: str, label: str, on_ok: Callable[[], None], on_fail: Callable[[str], None]) -> None:
        self._expected_mode = sub
        self._command(MAV_CMD_DO_SET_MODE, (float(MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), float(PX4_MAIN_AUTO), float(PX4_SUB[sub])), label, on_ok, on_fail)

    def _begin_return(self, why: str, *, failed: bool = False) -> None:
        self._abort_requested = True
        self._return_failed = failed
        self._expected_mode = "return"
        self._set_state("returning", why)
        self._rtl_last_sent = self.clock()
        self._emit("return_to_launch", "Return to launch commanded.", {"why": why})
        self._command(MAV_CMD_NAV_RETURN_TO_LAUNCH, (), "return to launch", lambda: self._emit("return_accepted", "PX4 accepted Return to launch."),
                      lambda result: self._emit("return_rejected", f"PX4 answered Return to launch with {result}; it is sent again."))

    # --- operator controls ---------------------------------------------------------------------------------------
    def control(self, command: str) -> dict[str, Any]:
        """Apply pause / resume / abort. Returns {accepted, detail}; abort is accepted in every non-terminal state."""
        if command not in CONTROL_COMMANDS:
            return {"accepted": False, "detail": "unknown control command"}
        if self.terminal:
            return {"accepted": False, "detail": f"the run is already {self.state}"}
        if command == "land":
            return self._begin_land()
        if command == "abort":
            if self.state == "returning":
                return {"accepted": True, "detail": "already returning to launch"}
            if self.state == "landing":
                return {"accepted": False, "detail": "landing at the current position; Abort (return to launch) is not available while landing"}
            self._emit("abort_requested", "Abort requested.")
            if self.armed:
                self._begin_return("aborted by the operator")
            else:
                self._pending = None
                self._set_state("aborted", "aborted before the aircraft was armed")
                self._emit("aborted", "Aborted before the aircraft was armed; nothing was flown.")
            return {"accepted": True, "detail": "abort accepted"}
        if command == "pause":
            if self.state != "executing":
                return {"accepted": False, "detail": f"pause is only possible while executing (state: {self.state})"}
            self._emit("pause_requested", "Pause requested: switching to Hold.")
            self._set_state("pausing")
            self._set_mode("hold", "hold", self._paused, lambda result: self._control_failed("pause", result, "executing"))
            return {"accepted": True, "detail": "pause accepted"}
        if self.state != "paused":
            return {"accepted": False, "detail": f"resume is only possible while paused (state: {self.state})"}
        self._emit("resume_requested", "Resume requested: switching back to Mission.")
        self._set_state("resuming")
        self._set_mode("mission", "mission", self._resumed, lambda result: self._control_failed("resume", result, "paused"))
        return {"accepted": True, "detail": "resume accepted"}

    def _begin_land(self) -> dict[str, Any]:
        """Land at the current position and disarm (not a motor kill, not return to launch). Idempotent."""
        if self.state == "landing":
            return {"accepted": True, "detail": "already landing"}
        if not self.armed:
            return {"accepted": False, "detail": "the aircraft is already disarmed"}
        self._before_land = self.state
        self._land_result, self._land_started, self._abort_requested = None, self.clock(), True
        self._expected_mode = "land"
        self._set_state("landing", "landing at the current position")
        before = {"state": self._before_land, "rel_alt_m": self.rel_alt_m, "landed_state": LANDED_STATE_NAMES.get(self.landed_state), "on_ground": self.on_ground(), "source": self.on_ground_source()}
        self._emit("land_requested", f"Land here requested ({'on the ground' if self.on_ground() else 'in the air'}; {self.on_ground_source().replace('_', ' ')}).", before)
        if self.on_ground():
            self._land_result = "accepted"
            self._disarm_normal("armed on the ground: normal disarm")
        else:
            self._pending = None
            self._set_mode("land", "land", lambda: self._land_ok(), lambda result: self._land_refused(result))
        return {"accepted": True, "detail": "land accepted"}

    def _land_ok(self) -> None:
        self._land_result = "accepted"
        self._emit("land_accepted", "PX4 accepted Land: the aircraft descends at its current position.")

    def _land_refused(self, result: str) -> None:
        recent = "; ".join(self._statustexts[-3:])
        reason = f"PX4 answered Land with {result}" + (f" (PX4 said: {recent})" if recent else "") + f"; values used: AUTO LAND at relative altitude {self.rel_alt_m if self.rel_alt_m is not None else 'unknown'} m, armed {self.armed}"
        self._land_result = f"refused: {reason}"
        self._emit("land_rejected", reason, {"result": result})
        back = self._before_land or "executing"
        self._abort_requested = back == "returning"
        self._expected_mode = {"returning": "return", "paused": "hold"}.get(back, "mission")
        self._set_state(back, f"Land was refused: {reason}")

    def _disarm_normal(self, why: str) -> None:
        """A NORMAL disarm (param2 = 0, never the force flag)."""
        self._disarm_sent = True
        self._emit("disarm_sent", f"Disarm sent ({why}).")
        self._command(MAV_CMD_COMPONENT_ARM_DISARM, (0.0,), "disarm", lambda: self._emit("disarm_accepted", "PX4 accepted the disarm."),
                      lambda result: (self._emit("disarm_rejected", f"PX4 answered disarm with {result}."), setattr(self, "_disarm_sent", False)))

    def _paused(self) -> None:
        self._set_state("paused")
        self._emit("paused", "Paused: the aircraft holds its position.")

    def _resumed(self) -> None:
        self._set_state("executing")
        self._emit("resumed", "Resumed: the aircraft continues the mission.")

    def _control_failed(self, what: str, result: str, back_to: str) -> None:
        self._expected_mode = "mission" if back_to == "executing" else "hold"
        self._set_state(back_to)
        self._emit(f"{what}_rejected", f"PX4 answered the {what} request with {result}; the run stays {back_to}.")

    # --- one iteration -------------------------------------------------------------------------------------------
    def step(self) -> None:
        now = self.clock()
        if self._last_heartbeat_sent is None or now - self._last_heartbeat_sent >= HEARTBEAT_INTERVAL_S:
            self.link.send_heartbeat()
            self.heartbeats_sent += 1
            self._last_heartbeat_sent = now
            if self._first_heartbeat_sent is None:
                self._first_heartbeat_sent = now
        while True:
            message = self.link.recv()
            if message is None:
                break
            self._handle(message)
            if self.terminal:
                return
        self._timers(self.clock())

    def _handle(self, message: Any) -> None:
        kind = message.get_type()
        now = self.clock()
        if kind == "HEARTBEAT":
            if getattr(message, "autopilot", None) != MAV_AUTOPILOT_PX4:
                return  # another GCS or component
            self._last_px4_heartbeat = now
            if self.state == "link_lost" and self._before_link_lost:
                self._emit("link_restored", "PX4 heartbeat is back.")
                self._set_state(self._before_link_lost)
                self._before_link_lost = None
            if self.state == "connecting" and getattr(message, "type", None) != MAV_TYPE_QUADROTOR:
                self._fail("not_a_multicopter", f"The vehicle on this link is MAV_TYPE {getattr(message, 'type', '?')}, not the x500 quadrotor.")
                return
            armed = bool(message.base_mode & MAV_MODE_FLAG_SAFETY_ARMED)
            previous_mode = self.custom_mode
            self.custom_mode = int(message.custom_mode)
            self._armed_changed(armed)
            if not self.terminal and previous_mode is not None and previous_mode != self.custom_mode:
                self._mode_changed()
        elif kind == "STATUSTEXT":
            text = str(getattr(message, "text", "")).strip("\x00\t")
            chunk_id = int(getattr(message, "id", 0) or 0)
            if chunk_id:  # a long text arrives in 50-character chunks with the same id; the last chunk is shorter
                self._text_chunks[chunk_id] = self._text_chunks.get(chunk_id, "") + text
                if len(text) >= 50 and len(self._text_chunks[chunk_id]) < 250:
                    return
                text = self._text_chunks.pop(chunk_id)
            text = text.strip()
            if text:
                self._statustexts.append(text)
                del self._statustexts[:-20]
                self._emit("px4_message", f"PX4: {text}", {"severity": int(getattr(message, "severity", 6))})
        elif kind in ("MISSION_REQUEST_INT", "MISSION_REQUEST"):
            self._mission_request(int(message.seq), getattr(message, "mission_type", 0))
        elif kind == "MISSION_ACK":
            self._mission_ack(int(message.type), getattr(message, "mission_type", 0))
        elif kind == "COMMAND_ACK":
            self._command_ack(int(message.command), int(message.result))
        elif kind == "MISSION_CURRENT":
            self._mission_current(int(message.seq), getattr(message, "mission_state", None))
        elif kind == "MISSION_ITEM_REACHED":
            self._item_reached(int(message.seq))
        elif kind == "EXTENDED_SYS_STATE":
            self.landed_state = int(message.landed_state)
            self._landed_state_at = now
        elif kind == "GLOBAL_POSITION_INT":
            self.rel_alt_m = round(int(message.relative_alt) / 1000.0, 2)
            self.ground_speed_m_s = round(((int(message.vx) / 100.0) ** 2 + (int(message.vy) / 100.0) ** 2) ** 0.5, 2)

    def _armed_changed(self, armed: bool) -> None:
        if armed and self.state == "connecting":
            if self.in_air:
                self.armed = True
                self.was_armed = True
                self.armed_at_wall = self.wall_clock()
                self._emit("armed", "PX4 reports armed: this run continues from the air (a hand-over or a resume).")
                return
            self._fail_unarmed("already_armed", "The aircraft is already armed; a run only starts from a disarmed, landed aircraft.")
            return
        if armed == self.armed:
            return
        self.armed = armed
        if armed:
            self.was_armed = True
            self.armed_at_wall = self.wall_clock()
            self._emit("armed", "PX4 reports armed.")
            if self.state == "arming":
                self._pending = None
                self._set_state("executing")
                self._expected_mode = "mission"
                self._emit("mission_started", "Mission started: the aircraft takes off and flies the route.")
            return
        self._emit("disarmed", "PX4 reports disarmed.")
        if self.state == "returning":
            if self._return_failed:
                self._set_state("failed", self.reason)
                self._emit("failed", f"Landed and disarmed after a failure ({self.reason}).")
            else:
                self._set_state("aborted", "returned to launch and disarmed")
                self._emit("aborted", "Aborted: the aircraft returned to launch, landed and disarmed.")
        elif self.state == "landing":
            self._set_state("aborted", "landed in place")
            self._emit("aborted", "Aborted: landed in place. The aircraft landed at its current position and PX4 reports disarmed.", {"reason": "landed in place"})
        elif self.state == "failsafe":
            self._set_state("failed", self.reason)
            self._emit("failed", f"Landed and disarmed after a PX4 failsafe ({self.reason}).")
        elif self.state in ("executing", "paused", "pausing", "resuming"):
            last = self.items[-1]["seq"]
            finished = self.mission_finished or last in self.reached_items or (self.current_item is not None and self.current_item >= last)
            if finished:
                self.reached_items.add(last)
                self._set_state("completed")
                self._emit("completed", "Mission completed: the aircraft landed and disarmed.")
            else:
                self._set_state("failed", "PX4 disarmed before the mission finished")
                self._emit("failed", "PX4 disarmed before the mission finished.", {"code": "disarmed_early"})

    def _fail_unarmed(self, code: str, message: str) -> None:
        self._pending = None
        self._set_state("failed", message)
        self._emit("failed", message, {"code": code})

    def _mode_changed(self) -> None:
        name = mode_name(self.custom_mode or 0)
        self._emit("px4_mode", f"PX4 mode: {name}.", {"mode": name})
        if self.state not in ("executing", "paused") or not self.armed or self.mission_finished:
            return
        main, sub = ((self.custom_mode or 0) >> 16) & 0xFF, ((self.custom_mode or 0) >> 24) & 0xFF
        expected = PX4_SUB["mission"] if self.state == "executing" else PX4_SUB["hold"]
        on_last_item = self.current_item is not None and self.current_item >= self.items[-1]["seq"]
        if main == PX4_MAIN_AUTO and sub in (expected, PX4_SUB["takeoff"]) or on_last_item:
            return
        if main == PX4_MAIN_AUTO and sub in (PX4_SUB["return"], PX4_SUB["land"]):
            recent = "; ".join(self._statustexts[-3:])
            self._set_state("failsafe", f"PX4 switched to {name} by itself{f' ({recent})' if recent else ''}")
            self._emit("failsafe", f"PX4 switched to {name} without a request from the executor: a PX4 failsafe.", {"mode": name})

    def _mission_request(self, seq: int, mission_type: int) -> None:
        if self.state != "uploading" or mission_type != 0:
            return
        if not 0 <= seq < len(self.items):
            self._fail("bad_request", f"PX4 requested mission item {seq}, which does not exist.")
            return
        self._last_upload_activity = self.clock()
        self._highest_requested = max(self._highest_requested, seq)
        self.link.send_mission_item(item_fields(self.items[seq]))

    def _mission_ack(self, ack_type: int, mission_type: int) -> None:
        if self.state != "uploading" or mission_type != 0:
            return
        name = MISSION_ACK_NAMES.get(ack_type, str(ack_type))
        if ack_type != 0:
            self._fail("mission_rejected", f"PX4 rejected the mission upload: MAV_MISSION_{name}.")
        elif self._highest_requested != len(self.items) - 1:
            self._fail("upload_incomplete", "PX4 acknowledged the mission before requesting every item.")
        else:
            self.uploaded = True
            self._uploaded_at = self.clock()
            self._set_state("starting")
            self._emit("uploaded", f"Mission uploaded to PX4: {len(self.items)} items acknowledged.", {"items": len(self.items)})

    def _command_ack(self, command: int, result: int) -> None:
        pending = self._pending
        if not pending or pending["command"] != command or result == 5:  # 5 = in progress
            return
        self._pending = None
        if result == 0:
            pending["on_ok"]()
        else:
            pending["on_fail"](f"MAV_RESULT_{COMMAND_RESULT_NAMES.get(result, result)}")

    def _mission_current(self, seq: int, mission_state: Optional[int]) -> None:
        if not self.uploaded or not self.was_armed:
            return
        if 0 <= seq < len(self.items) and seq != self.current_item:
            self.current_item = seq
            # PX4 does not always send MISSION_ITEM_REACHED (seen live: none for the take-off item). Moving on to a
            # later item means every earlier route item was reached, so those events are recorded here.
            for item in self.items[:seq]:
                if item["route_seq"] is not None and item["seq"] not in self.reached_items:
                    self._item_reached(item["seq"])
        if mission_state == MISSION_STATE_COMPLETE and not self.mission_finished and self.state in ("executing", "paused"):
            self.mission_finished = True
            self._emit("mission_finished", "PX4 reports the mission finished; waiting for it to disarm.")

    def _item_reached(self, seq: int) -> None:
        if not self.was_armed or seq in self.reached_items or not 0 <= seq < len(self.items):
            return
        self.reached_items.add(seq)
        item = self.items[seq]
        if item["route_seq"] is None:
            return
        progress = self.route_progress()
        position = f"waypoint {item['route_seq'] + 1} of {progress['route_total']}"
        if item["command"] == MAV_CMD_NAV_TAKEOFF:
            self._emit("takeoff_complete", f"Take-off complete ({position}).", {"route_seq": item["route_seq"]})
        elif item["command"] == MAV_CMD_NAV_LAND:
            self._emit("landed", f"Landing point reached ({position}).", {"route_seq": item["route_seq"]})
        else:
            self._emit("waypoint_reached", f"Reached {position}.", {"route_seq": item["route_seq"], "percent": progress["percent"]})
        if item["simulated_event"] == "payload_released":
            # A software event only. No actuator, servo or release command exists in this process.
            self._emit("payload_released_simulated", f"payload_released (simulated) at {position}. No release command was sent.", {"route_seq": item["route_seq"], "simulated": True})

    def _timers(self, now: float) -> None:
        if self._last_px4_heartbeat is not None and now - self._last_px4_heartbeat > LINK_LOST_S and self.state != "link_lost":
            self._before_link_lost = self.state
            self._set_state("link_lost", f"no PX4 heartbeat for {LINK_LOST_S:g} s")
            self._emit("link_lost", f"Link lost: no PX4 heartbeat for more than {LINK_LOST_S:g} s. The run is no longer reported as executing.")
        if self._last_px4_heartbeat is not None and self.state not in ("link_lost", "connecting"):
            self._request_landed_state(now)
        if self.state == "link_lost":
            if now - self.state_since > LINK_LOST_GIVE_UP_S:
                self._before_link_lost = None
                self._emit("link_lost_final", "The link did not come back; the executor stops. PX4's own GCS-loss failsafe (Return) applies.")
                self._finish_link_lost = True
            return
        if self.state == "connecting":
            if self._last_px4_heartbeat is not None and self._first_heartbeat_sent is not None and now - self._first_heartbeat_sent >= PRE_COMMAND_HEARTBEAT_S:
                self._emit("connected", f"PX4 system {self.target_system} heartbeat received on the onboard link.", {"mode": mode_name(self.custom_mode or 0)})
                self._set_state("uploading")
                self._send_count(now)
            elif now - self.started > CONNECT_TIMEOUT_S:
                self._fail_unarmed("no_heartbeat", f"No PX4 heartbeat on the onboard link within {CONNECT_TIMEOUT_S:g} s.")
            return
        if self.state == "uploading":
            if self._highest_requested < 0 and self._count_sent_at is not None and now - self._count_sent_at >= UPLOAD_COUNT_RETRY_S:
                if self._count_sends >= UPLOAD_COUNT_RETRIES:
                    self._fail("upload_timeout", "PX4 never requested the first mission item.")
                else:
                    self._send_count(now)
            elif self._last_upload_activity is not None and now - self._last_upload_activity > UPLOAD_STALL_S:
                self._fail("upload_timeout", f"The mission upload stalled for more than {UPLOAD_STALL_S:g} s.")
            return
        if self.state == "starting" and self._pending is None and self._uploaded_at is not None and now - self._uploaded_at >= FEASIBILITY_WAIT_S:
            last = float(len(self.items) - 1)
            self._uploaded_at = None
            self._command(MAV_CMD_MISSION_START, (0.0, last), "mission start", self._mission_start_ok,
                          lambda result: self._fail("mission_start_denied", f"PX4 answered MISSION_START with {result}: the uploaded mission is not valid for flight."))
        if self._pending is not None and now - self._pending["sent_at"] >= COMMAND_RETRY_S:
            pending = self._pending
            if pending["tries"] >= COMMAND_RETRIES and self.state != "returning":
                self._pending = None
                pending["on_fail"]("no answer")
            else:
                pending["tries"] += 1
                pending["sent_at"] = now
                self.link.send_command(pending["command"], pending["params"])
        if self.state == "arming" and now - self.state_since > ARM_CONFIRM_S and self._pending is None:
            self._fail_unarmed("arm_not_confirmed", f"PX4 accepted the arm command but did not report armed within {ARM_CONFIRM_S:g} s." + (f" PX4 said: {'; '.join(self._statustexts[-3:])}" if self._statustexts else ""))
        if self.state == "returning" and self._pending is None and self._rtl_last_sent is not None and now - self._rtl_last_sent >= 3.0:
            main, sub = ((self.custom_mode or 0) >> 16) & 0xFF, ((self.custom_mode or 0) >> 24) & 0xFF
            if self.armed and not (main == PX4_MAIN_AUTO and sub in (PX4_SUB["return"], PX4_SUB["land"])):
                self._rtl_last_sent = now  # abort must work: keep asking until PX4 is in Return or Land
                self._command(MAV_CMD_NAV_RETURN_TO_LAUNCH, (), "return to launch", lambda: None, lambda result: None)
        if self.state == "landing" and self._pending is None and self.armed and self._land_started is not None and now - (self._land_last_sent or self._land_started) >= 3.0:
            main, sub = ((self.custom_mode or 0) >> 16) & 0xFF, ((self.custom_mode or 0) >> 24) & 0xFF
            if not self.on_ground() and not (main == PX4_MAIN_AUTO and sub == PX4_SUB["land"]):
                self._land_last_sent = now      # Land here must work: keep asking until PX4 is in Land mode
                self._set_mode("land", "land", lambda: self._land_ok(), lambda result: None)
        self._disarm_when_landed(now)

    def _send_count(self, now: float) -> None:
        self._count_sends += 1
        self._count_sent_at = now
        self._last_upload_activity = now
        self.link.send_mission_count(len(self.items))
        if self._count_sends == 1:
            self._emit("upload_started", f"Uploading {len(self.items)} mission items.", {"items": len(self.items)})

    def _mission_start_ok(self) -> None:
        self._emit("mission_mode", "PX4 accepted MISSION_START (Mission mode; it does not arm by itself).")
        if self.in_air and self.armed:                     # already flying: nothing to arm; the mission is running
            self._set_state("executing")
            self._expected_mode = "mission"
            self._emit("mission_started", "Mission started from the air: the aircraft flies the remaining route.")
            return
        self._set_state("arming")
        self._command(MAV_CMD_COMPONENT_ARM_DISARM, (1.0,), "arm", lambda: self._emit("arm_accepted", "PX4 accepted the arm command."),
                      lambda result: self._fail_unarmed("arm_rejected", f"PX4 answered the arm command with {result}." + (f" PX4 said: {'; '.join(self._statustexts[-3:])}" if self._statustexts else "")))

    def _disarm_when_landed(self, now: float) -> None:
        """After the landing item, PX4 normally disarms itself (COM_DISARM_LAND). If it stays armed on the ground, disarm."""
        if self.state == "landing":
            if not self.armed or self._disarm_sent or self._pending is not None:
                return
            if self.on_ground_source() != "px4_landed_state" and not self._fallback_noted:
                self._fallback_noted = True
                self._emit("landed_state_missing", "PX4's landed state is not arriving; on the ground is judged from relative altitude and ground speed.")
            if not self.on_ground():
                self._landed_since = None
            elif self._landed_since is None:
                self._landed_since = now
            elif now - self._landed_since >= DISARM_AFTER_TOUCHDOWN_S:
                self._disarm_normal("PX4 reports on the ground but is still armed")
            return
        if not self.armed or self._disarm_sent or self.state not in ("executing", "returning", "failsafe") or self._pending is not None:
            return
        on_ground = self.landed_state == MAV_LANDED_STATE_ON_GROUND
        if not on_ground or not (self.mission_finished or self.items[-1]["seq"] in self.reached_items or self.state in ("returning", "failsafe")):
            self._landed_since = None
            return
        if self._landed_since is None:
            self._landed_since = now
        elif now - self._landed_since >= DISARM_AFTER_LANDING_S:
            self._disarm_sent = True
            self._emit("disarm_sent", "PX4 reports on ground but still armed: disarm sent.")
            self._command(MAV_CMD_COMPONENT_ARM_DISARM, (0.0,), "disarm", lambda: None, lambda result: self._emit("disarm_rejected", f"PX4 answered disarm with {result}."))

    @property
    def should_exit(self) -> bool:
        return self.terminal


# --- runtime files (control channel) -----------------------------------------------------------------------------

def runtime_dir(instance: int) -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"trinetra-mission-exec-{os.getuid()}" / f"instance-{instance}"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return base


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pid_alive(pid: Any, word: str = "run") -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "ignore")
    except OSError:
        return False
    return Path(__file__).name in cmdline and word in cmdline.split("\0")


def read_events(directory: Path, after: int) -> list[dict[str, Any]]:
    events = []
    try:
        for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("index"), int) and event["index"] > after:
                events.append(event)
    except OSError:
        pass
    return events


def read_status(instance: int, events_after: int = -1) -> dict[str, Any]:
    directory = runtime_dir(instance)
    status = _read_json(directory / "status.json")
    pid = _read_json(directory / "daemon.pid")
    alive = _pid_alive(pid)
    land = _read_json(directory / "land.json")
    return {"status": status if isinstance(status, dict) else None, "daemon_alive": alive, "events": read_events(directory, events_after),
            "land": land if isinstance(land, dict) else None, "land_alive": _pid_alive(_read_json(directory / "land.pid"), "land-run"),
            "now": time.time(), "executor": "trinetra_mission_executor_v1"}


# --- identity: only the managed SITL x500 instance may be commanded ------------------------------------------------

def _fleet_manager() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("trinetra_fleet_manager", Path(__file__).resolve().parent / "wsl_fleet_manager.py")
    assert spec and spec.loader
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    return manager


def verify_managed_instance(instance: int, px4_root: Path, manager: Any = None) -> Optional[dict[str, str]]:
    """None when instance `instance` is a running gz_x500 PX4 SITL of this checkout that the fleet manager started."""
    manager = manager or _fleet_manager()
    found = manager.discover_px4_instances(px4_root.resolve())  # only build/px4_sitl_default/bin/px4 with PX4_SIM_MODEL=gz_x500
    if instance not in found:
        return {"failure_code": "instance_not_running", "failure": f"PX4 SITL instance {instance} (gz_x500) of the TRINETRA checkout is not running."}
    owned = manager._load_owned(manager._state_path())
    if owned.get(instance) != found[instance]:
        return {"failure_code": "instance_not_managed", "failure": (
            f"PX4 instance {instance} was not started by the TRINETRA fleet manager, so it is not commanded. "
            "Use Activate and verify (or Move simulation to mission start) so the manager starts it.")}
    return None


# --- MAVLink link (pymavlink, WSL only) ------------------------------------------------------------------------------

class PymavlinkLink:
    """Thin adapter: every method is one fixed MAVLink operation on the ONBOARD link of one SITL instance."""

    def __init__(self, instance: int, target_system: int) -> None:
        from pymavlink import mavutil

        self._mavutil = mavutil
        self.target_system = target_system
        # PX4's onboard link (local 14580+i) streams to 127.0.0.1:14540+i and latches onto the first localhost sender.
        self.connection = mavutil.mavlink_connection(f"udpin:127.0.0.1:{14540 + instance}", source_system=255, source_component=190, dialect="common")

    def recv(self) -> Any:
        while True:
            message = self.connection.recv_match(blocking=False)
            if message is None:
                return None
            if message.get_type() == "BAD_DATA" or message.get_srcSystem() != self.target_system:
                continue
            return message

    def send_heartbeat(self) -> None:
        m = self._mavutil.mavlink
        self.connection.mav.heartbeat_send(m.MAV_TYPE_GCS, m.MAV_AUTOPILOT_INVALID, 0, 0, m.MAV_STATE_ACTIVE)

    def send_mission_count(self, count: int) -> None:
        self.connection.mav.mission_count_send(self.target_system, 1, count, 0)

    def send_mission_item(self, fields: dict[str, Any]) -> None:
        self.connection.mav.mission_item_int_send(
            self.target_system, 1, fields["seq"], fields["frame"], fields["command"], 1 if fields["seq"] == 0 else 0, 1,
            fields["param1"], fields["param2"], fields["param3"], fields["param4"], fields["x"], fields["y"], fields["z"], 0)

    def send_command(self, command: int, params: tuple[float, ...]) -> None:
        if command not in SENDABLE_COMMANDS:
            raise RuntimeError("command is not on the allowlist")
        self.connection.mav.command_long_send(self.target_system, 1, command, 0, *params)


# --- daemon ------------------------------------------------------------------------------------------------------------

def run_daemon(instance: int, execution_id: str) -> int:
    directory = runtime_dir(instance)
    mission = validate_mission(_read_json(directory / "mission.json"))
    if mission["execution_id"] != execution_id or mission["instance"] != instance:
        raise MissionError("the stored mission does not belong to this execution")
    _write_json(directory / "daemon.pid", os.getpid())
    events_file = (directory / "events.jsonl").open("a", encoding="utf-8")

    def on_event(event: dict[str, Any]) -> None:
        events_file.write(json.dumps(event, allow_nan=False) + "\n")
        events_file.flush()

    executor = MissionExecutor(PymavlinkLink(instance, mission["system_id"]), mission, on_event=on_event)
    stop = {"requested": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("requested", True))
    control_path = directory / "control.jsonl"
    control_offset = 0
    last_status = 0.0
    last_state = None
    while not executor.should_exit:
        try:
            with control_path.open("r", encoding="utf-8") as handle:
                handle.seek(control_offset)
                for line in handle:
                    if not line.endswith("\n"):
                        break
                    control_offset += len(line.encode("utf-8"))
                    try:
                        request = json.loads(line)
                    except ValueError:
                        continue
                    if request.get("execution_id") == execution_id and request.get("command") in CONTROL_COMMANDS:
                        executor.control(request["command"])
        except OSError:
            pass
        if stop["requested"] and not executor.terminal:
            stop["requested"] = False
            executor.control("abort")  # a terminated executor never leaves an armed aircraft without a decision
        executor.step()
        now = time.monotonic()
        if executor.state != last_state or now - last_status >= 0.5:
            _write_json(directory / "status.json", {**executor.status(), "pid": os.getpid()})
            last_status, last_state = now, executor.state
        time.sleep(0.02)
    _write_json(directory / "status.json", {**executor.status(), "pid": os.getpid(), "exited": True})
    events_file.close()
    return 0


def emergency_return(instance: int, seconds: float = 6.0) -> dict[str, Any]:
    """Abort when no executor is alive: heartbeat plus Return to launch, straight to the managed instance."""
    link = PymavlinkLink(instance, instance + 1)
    deadline = time.monotonic() + seconds
    last = 0.0
    acked, armed = None, None
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now - last >= 1.0:
            link.send_heartbeat()
            link.send_command(MAV_CMD_NAV_RETURN_TO_LAUNCH, (0.0,) * 7)
            last = now
        message = link.recv()
        if message is None:
            time.sleep(0.02)
            continue
        if message.get_type() == "HEARTBEAT" and getattr(message, "autopilot", None) == MAV_AUTOPILOT_PX4:
            armed = bool(message.base_mode & MAV_MODE_FLAG_SAFETY_ARMED)
            if armed is False:
                break
        if message.get_type() == "COMMAND_ACK" and int(message.command) == MAV_CMD_NAV_RETURN_TO_LAUNCH:
            acked = COMMAND_RESULT_NAMES.get(int(message.result), str(message.result))
            if acked == "ACCEPTED":
                break
    return {"emergency_return": True, "armed": armed, "ack": acked}


def run_land(instance: int) -> int:
    """Land here without a mission run: its own short-lived onboard connection. Heartbeat every second, PX4 Land mode, a NORMAL
    disarm a few seconds after touchdown if PX4 has not disarmed itself, then exit. Status goes to land.json for the backend."""
    directory = runtime_dir(instance)
    _write_json(directory / "land.pid", os.getpid())
    link = PymavlinkLink(instance, instance + 1)
    state: dict[str, Any] = {"state": "connecting", "result": None, "reason": "", "armed": None, "rel_alt_m": None, "landed_state": None, "on_ground_source": None,
                             "started_at": time.time(), "finished_at": None, "events": [], "px4_messages": []}
    land_params = (float(MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), float(PX4_MAIN_AUTO), float(PX4_SUB["land"]), 0.0, 0.0, 0.0, 0.0)

    def note(kind: str, message: str) -> None:
        state["events"].append({"t": time.time(), "type": kind, "message": message})
        del state["events"][:-30]

    def publish() -> None:
        _write_json(directory / "land.json", {**state, "updated_at": time.time(), "pid": os.getpid()})

    def refuse(reason: str) -> int:
        state.update(state="failed", result=f"refused: {reason}", reason=reason, finished_at=time.time())
        note("land_rejected", reason)
        publish()
        return 2

    start = time.monotonic()
    last_heartbeat = last_request = last_land = last_publish = 0.0
    first_heartbeat_at, px4_seen = None, False
    custom_mode, landed_at, rel_alt, speed = None, None, None, None
    pending: Optional[dict[str, Any]] = None
    landed_since: Optional[float] = None
    disarm_sent = False
    stop = {"requested": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("requested", True))
    publish()
    while time.monotonic() - start < LAND_GIVE_UP_S and not stop["requested"]:
        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            link.send_heartbeat()
            first_heartbeat_at = first_heartbeat_at or now
            last_heartbeat = now
        if px4_seen and now - last_request >= (60.0 if landed_at is not None else 5.0):
            last_request = now
            link.send_command(MAV_CMD_SET_MESSAGE_INTERVAL, (float(MSG_EXTENDED_SYS_STATE), float(LANDED_STATE_INTERVAL_US), 0.0, 0.0, 0.0, 0.0, 0.0))
        message = link.recv()
        while message is not None:
            kind = message.get_type()
            if kind == "HEARTBEAT" and getattr(message, "autopilot", None) == MAV_AUTOPILOT_PX4:
                px4_seen = True
                armed = bool(message.base_mode & MAV_MODE_FLAG_SAFETY_ARMED)
                custom_mode = int(message.custom_mode)
                if state["armed"] is True and not armed:
                    state.update(state="landed", result="landed", reason="landed in place and disarmed", finished_at=time.time(), armed=False)
                    note("disarmed", "PX4 reports disarmed.")
                    publish()
                    return 0
                state["armed"] = armed
            elif kind == "EXTENDED_SYS_STATE":
                state["landed_state"] = int(message.landed_state)
                landed_at = now
            elif kind == "GLOBAL_POSITION_INT":
                rel_alt = int(message.relative_alt) / 1000.0
                speed = (((int(message.vx) / 100.0) ** 2 + (int(message.vy) / 100.0) ** 2) ** 0.5)
                state["rel_alt_m"] = round(rel_alt, 2)
            elif kind == "STATUSTEXT":
                text = str(getattr(message, "text", "")).strip("\x00 ")[:160]
                if text:
                    state["px4_messages"] = (state["px4_messages"] + [text])[-5:]
            elif kind == "COMMAND_ACK" and pending is not None and int(message.command) == pending["command"] and int(message.result) != 5:
                result = COMMAND_RESULT_NAMES.get(int(message.result), str(message.result))
                if pending["label"] == "land":
                    if result == "ACCEPTED":
                        state["result"] = "accepted"
                        note("land_accepted", "PX4 accepted Land.")
                    else:
                        said = f" (PX4 said: {'; '.join(state['px4_messages'][-3:])})" if state["px4_messages"] else ""
                        return refuse(f"PX4 answered Land with MAV_RESULT_{result}{said}; values used: AUTO LAND at relative altitude {state['rel_alt_m']} m")
                elif result != "ACCEPTED":
                    disarm_sent = False
                    note("disarm_rejected", f"PX4 answered disarm with MAV_RESULT_{result}.")
                pending = None
            message = link.recv()
        fresh = landed_at is not None and now - landed_at <= LANDED_STATE_STALE_S
        if fresh:
            on_ground: Optional[bool] = state["landed_state"] == MAV_LANDED_STATE_ON_GROUND
        else:
            on_ground = None if rel_alt is None else (abs(rel_alt) < ON_GROUND_ALT_M and (speed is None or speed < ON_GROUND_SPEED_M_S))
        state["on_ground_source"] = "px4_landed_state" if fresh else "altitude_speed_fallback"
        if state["state"] == "connecting" and px4_seen and first_heartbeat_at is not None and now - first_heartbeat_at >= PRE_COMMAND_HEARTBEAT_S:
            if state["armed"] is False:
                state.update(state="failed", result="already_disarmed", reason="The aircraft is already disarmed.", finished_at=time.time())
                publish()
                return 0
            state["state"] = "landing"
            where = "armed on the ground, normal disarm" if on_ground else "PX4 Land mode at the current position"
            note("land_requested", f"Land here: {where} (relative altitude {state['rel_alt_m']} m, {state['on_ground_source'].replace('_', ' ')}).")
            if on_ground:
                state["result"] = "accepted"
                pending = {"command": MAV_CMD_COMPONENT_ARM_DISARM, "label": "disarm", "sent": now, "tries": 1}
                link.send_command(MAV_CMD_COMPONENT_ARM_DISARM, (0.0,) * 7)
                disarm_sent = True
            else:
                pending = {"command": MAV_CMD_DO_SET_MODE, "label": "land", "sent": now, "tries": 1}
                link.send_command(MAV_CMD_DO_SET_MODE, land_params)
                last_land = now
        elif state["state"] == "landing" and state["armed"]:
            if pending is not None and now - pending["sent"] >= COMMAND_RETRY_S:
                if pending["tries"] >= COMMAND_RETRIES:
                    if pending["label"] == "land":
                        return refuse(f"PX4 did not answer the Land command; values used: AUTO LAND at relative altitude {state['rel_alt_m']} m")
                    pending, disarm_sent = None, False
                else:
                    pending["tries"] += 1
                    pending["sent"] = now
                    link.send_command(pending["command"], (0.0,) * 7 if pending["label"] == "disarm" else land_params)
            main, sub = ((custom_mode or 0) >> 16) & 0xFF, ((custom_mode or 0) >> 24) & 0xFF
            if pending is None and not on_ground and not (main == PX4_MAIN_AUTO and sub == PX4_SUB["land"]) and now - last_land >= 3.0:
                last_land = now
                pending = {"command": MAV_CMD_DO_SET_MODE, "label": "land", "sent": now, "tries": 1}
                link.send_command(MAV_CMD_DO_SET_MODE, land_params)
            if on_ground and not disarm_sent and pending is None:
                landed_since = landed_since or now
                if now - landed_since >= DISARM_AFTER_TOUCHDOWN_S:
                    disarm_sent = True
                    note("disarm_sent", "PX4 reports on the ground but is still armed: normal disarm sent.")
                    pending = {"command": MAV_CMD_COMPONENT_ARM_DISARM, "label": "disarm", "sent": now, "tries": 1}
                    link.send_command(MAV_CMD_COMPONENT_ARM_DISARM, (0.0,) * 7)
            elif not on_ground:
                landed_since = None
        if now - last_publish >= 0.5:
            last_publish = now
            publish()
        time.sleep(0.02)
    state.update(state="failed", result="timeout", reason="stopped" if stop["requested"] else "Landing was not finished within the time limit.", finished_at=time.time())
    publish()
    return 1


def cleanup_processes(proc_root: Path = Path("/proc"), instance: Optional[int] = None) -> dict[str, Any]:
    """Stop every mission-executor and land process of this checkout (they hold the onboard UDP ports) and report what was stopped. With `instance`, only that instance's processes."""
    killed: list[dict[str, Any]] = []
    me = os.getpid()
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            argv = (entry / "cmdline").read_bytes().decode("utf-8", "ignore").split("\0")
        except OSError:
            continue
        if not any(part and Path(part).name == Path(__file__).name for part in argv[:4]) or not set(argv) & {"run", "land-run"}:
            continue
        pid = int(entry.name)
        if instance is not None and ("--instance" not in argv or argv[argv.index("--instance") + 1] != str(instance)):
            continue
        try:
            os.kill(pid, getattr(signal, "SIGKILL", 9))
            killed.append({"kind": "land" if "land-run" in argv else "executor", "pid": pid, "instance": argv[argv.index("--instance") + 1] if "--instance" in argv else None})
        except OSError:
            pass
    for number in range(FLEET_CAPACITY) if instance is None else [instance]:
        directory = runtime_dir(number)
        for name in ("daemon.pid", "land.pid"):
            try:
                (directory / name).unlink()
            except OSError:
                pass
    return {"cleanup": True, "killed": killed}


def cli_land(instance: int, px4_root: Path, execution_id: Optional[str]) -> dict[str, Any]:
    refusal = verify_managed_instance(instance, px4_root)
    if refusal:
        return refusal
    try:
        import pymavlink  # noqa: F401
    except ImportError:
        return {"failure_code": "pymavlink_missing", "failure": "pymavlink is not installed in WSL (python3 -m pip install pymavlink)."}
    directory = runtime_dir(instance)
    if _pid_alive(_read_json(directory / "daemon.pid")):
        if execution_id is None:
            previous = _read_json(directory / "status.json") or {}
            return {"failure_code": "execution_running", "failure": f"An execution ({previous.get('execution_id', 'unknown')}) is running on instance {instance}; land through that execution."}
        return cli_control(instance, execution_id, "land")
    if _pid_alive(_read_json(directory / "land.pid"), "land-run"):
        return {"accepted": True, "already_landing": True, "detail": "a landing is already in progress", **read_status(instance)}
    try:
        (directory / "land.json").unlink()
    except OSError:
        pass
    with (directory / "land.log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "land-run", "--instance", str(instance)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 9.0
    while time.monotonic() < deadline:
        land = _read_json(directory / "land.json")
        if process.poll() is not None and not (isinstance(land, dict) and land.get("finished_at")):
            tail = (directory / "land.log").read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-1:]
            return {"failure_code": "land_start_failed", "failure": f"The landing process exited at once: {tail[0][:200] if tail else process.returncode}"}
        if isinstance(land, dict) and (land.get("result") or land.get("state") in ("failed", "landed")):
            break
        time.sleep(0.15)
    land = _read_json(directory / "land.json") or {}
    result = str(land.get("result") or "")
    if result.startswith("refused"):
        return {"accepted": False, "refused": True, "reason": land.get("reason"), "px4_messages": land.get("px4_messages", []), **read_status(instance)}
    if result == "already_disarmed":
        return {"accepted": False, "already_disarmed": True, "reason": land.get("reason"), **read_status(instance)}
    if not result:
        return {"failure_code": "no_heartbeat", "failure": "PX4 did not answer on the onboard link, so nothing was sent.", **read_status(instance)}
    return {"accepted": True, "detail": "landing started", **read_status(instance)}


# --- CLI (the only entry points the Windows backend uses) -----------------------------------------------------------------

def disarm_once(instance: int, seconds: float = 9.0) -> dict[str, Any]:
    """One NORMAL disarm (MAV_CMD_COMPONENT_ARM_DISARM, param1 = 0, param2 = 0: no force flag) on the onboard link.

    PX4 itself refuses a normal disarm that is not safe (for example in the air); its reason is returned as it said it.
    """
    link = PymavlinkLink(instance, instance + 1)
    deadline = time.monotonic() + seconds
    last_heartbeat, sent_at = 0.0, None
    armed, ack, texts, confirmed = None, None, [], False
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now - last_heartbeat >= 1.0:
            link.send_heartbeat()
            last_heartbeat = now
        if armed is True and sent_at is None:
            link.send_command(MAV_CMD_COMPONENT_ARM_DISARM, (0.0,) * 7)     # param1 = 0 disarm, param2 = 0 normal (never 21196)
            sent_at = now
        message = link.recv()
        if message is None:
            time.sleep(0.02)
            continue
        kind = message.get_type()
        if kind == "HEARTBEAT" and getattr(message, "autopilot", None) == MAV_AUTOPILOT_PX4:
            armed = bool(message.base_mode & MAV_MODE_FLAG_SAFETY_ARMED)
            if armed is False:
                confirmed = True
                break
        elif kind == "COMMAND_ACK" and int(message.command) == MAV_CMD_COMPONENT_ARM_DISARM:
            ack = COMMAND_RESULT_NAMES.get(int(message.result), str(message.result))
            if ack != "ACCEPTED":
                break
        elif kind == "STATUSTEXT":
            texts.append(str(getattr(message, "text", "")).strip("\x00 ")[:160])
    if armed is None:
        return {"failure_code": "no_heartbeat", "failure": "PX4 did not answer on the onboard link, so nothing was sent."}
    return {"disarm": True, "was_armed": sent_at is not None, "accepted": confirmed, "ack": ack, "armed": not confirmed if armed is not None else None,
            "px4_messages": texts[-4:]}


def cli_disarm(instance: int, px4_root: Path) -> dict[str, Any]:
    refusal = verify_managed_instance(instance, px4_root)
    if refusal:
        return refusal
    directory = runtime_dir(instance)
    if _pid_alive(_read_json(directory / "daemon.pid")):
        previous = _read_json(directory / "status.json") or {}
        return {"failure_code": "execution_running", "failure": f"An execution ({previous.get('execution_id', 'unknown')}) is running on instance {instance}; abort it first."}
    try:
        import pymavlink  # noqa: F401
    except ImportError:
        return {"failure_code": "pymavlink_missing", "failure": "pymavlink is not installed in WSL (python3 -m pip install pymavlink)."}
    return disarm_once(instance)


def cli_start(px4_root: Path, stdin_text: str) -> dict[str, Any]:
    try:
        submitted = json.loads(stdin_text)
        mission = validate_mission(submitted)
    except ValueError as error:
        return {"failure_code": "mission_invalid", "failure": str(error)}
    instance = mission["instance"]
    refusal = verify_managed_instance(instance, px4_root)
    if refusal:
        return refusal
    directory = runtime_dir(instance)
    if _pid_alive(_read_json(directory / "daemon.pid")):
        previous = _read_json(directory / "status.json") or {}
        return {"failure_code": "execution_running", "failure": f"An execution ({previous.get('execution_id', 'unknown')}) is already running on instance {instance}."}
    try:
        import pymavlink  # noqa: F401
    except ImportError:
        return {"failure_code": "pymavlink_missing", "failure": "pymavlink is not installed in WSL (python3 -m pip install pymavlink)."}
    for name in ("status.json", "events.jsonl", "control.jsonl", "daemon.pid"):
        try:
            (directory / name).unlink()
        except OSError:
            pass
    (directory / "control.jsonl").write_text("", encoding="utf-8")
    _write_json(directory / "mission.json", submitted)  # exactly what was validated; the daemon validates it again on load
    os.chmod(directory / "mission.json", 0o600)
    with (directory / "daemon.log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run", "--instance", str(instance), "--execution-id", mission["execution_id"]],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            tail = (directory / "daemon.log").read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-1:]
            return {"failure_code": "executor_start_failed", "failure": f"The executor exited at once: {tail[0][:200] if tail else process.returncode}"}
        status = _read_json(directory / "status.json")
        if isinstance(status, dict) and status.get("execution_id") == mission["execution_id"]:
            break
        time.sleep(0.1)
    return {"started": True, "pid": process.pid, **read_status(instance)}


def cli_control(instance: int, execution_id: str, command: str) -> dict[str, Any]:
    directory = runtime_dir(instance)
    status = _read_json(directory / "status.json")
    known = isinstance(status, dict) and status.get("execution_id") == execution_id
    if not _pid_alive(_read_json(directory / "daemon.pid")):
        if command == "land":
            return cli_land(instance, Path(os.environ.get("TRINETRA_PX4_ROOT", "/home/apt8055/uav-sim/PX4-Autopilot")), None)
        if command == "abort":
            # Abort must work even without a live executor: go straight to the managed instance.
            try:
                import pymavlink  # noqa: F401
            except ImportError:
                return {"failure_code": "pymavlink_missing", "failure": "pymavlink is not installed in WSL."}
            return {"accepted": True, "detail": "no live executor; Return to launch was sent directly", **emergency_return(instance), **read_status(instance)}
        return {"failure_code": "executor_not_running", "failure": "No executor is running for this aircraft.", **read_status(instance)}
    if not known:
        return {"failure_code": "execution_mismatch", "failure": "A different execution is running on this aircraft.", **read_status(instance)}
    with (directory / "control.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"execution_id": execution_id, "command": command, "t": time.time()}) + "\n")
    return {"accepted": True, "detail": f"{command} queued", **read_status(instance)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "pause", "resume", "abort", "land", "disarm", "run", "land-run", "cleanup", "end"))
    parser.add_argument("--instance", type=int, default=0, choices=range(FLEET_CAPACITY))
    parser.add_argument("--execution-id")
    parser.add_argument("--events-after", type=int, default=-1)
    parser.add_argument("--px4-root", type=Path, default=Path(os.environ.get("TRINETRA_PX4_ROOT", "/home/apt8055/uav-sim/PX4-Autopilot")))
    args = parser.parse_args()
    try:
        if args.execution_id is not None and not _EXECUTION_ID.fullmatch(args.execution_id):
            raise ValueError("execution id must be 32 lowercase hex characters")
        if args.action == "run":
            if args.execution_id is None:
                raise ValueError("run requires --execution-id")
            return run_daemon(args.instance, args.execution_id)
        if args.action == "land-run":
            return run_land(args.instance)
        if args.action == "end":
            result = {**cleanup_processes(instance=args.instance), "ended_instance": args.instance}      # one aircraft's executor only; PX4 stays in whatever mode it is in
        elif args.action == "cleanup":
            result = cleanup_processes()
        elif args.action == "land":
            result = cli_land(args.instance, args.px4_root, args.execution_id)
        elif args.action == "start":
            result = cli_start(args.px4_root, sys.stdin.read(2_000_000))
        elif args.action == "status":
            result = read_status(args.instance, args.events_after)
        elif args.action == "disarm":
            result = cli_disarm(args.instance, args.px4_root)
        else:
            if args.execution_id is None:
                raise ValueError(f"{args.action} requires --execution-id")
            result = cli_control(args.instance, args.execution_id, args.action)
    except (OSError, RuntimeError, ValueError) as error:
        result = {"failure": str(error), "failure_code": "executor_error"}
    result.setdefault("executor", "trinetra_mission_executor_v1")
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if not result.get("failure") else 2


if __name__ == "__main__":
    sys.exit(main())
