from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pymavlink.dialects.v20 import common as mavlink2
from pymavlink import mavutil


_LOGGER = logging.getLogger("trinetra.telemetry.px4")
_MAVLINK_FRAME_STARTS = {0xFE, 0xFD}
_SUPPORTED_TYPES = {
    "HEARTBEAT", "GLOBAL_POSITION_INT", "ATTITUDE", "GPS_RAW_INT",
    "HIGHRES_IMU", "SYS_STATUS", "BATTERY_STATUS", "MISSION_CURRENT",
    "LOCAL_POSITION_NED", "ESTIMATOR_STATUS", "VIBRATION", "EXTENDED_SYS_STATE",
}
_LANDED_STATE_NAMES = {0: "undefined", 1: "on_ground", 2: "in_air", 3: "takeoff", 4: "landing"}
_TRAIL_MAX_POINTS = 3000
_RATE_WINDOW_S = 5.0
# Raw detector streams kept since arming (PX4-clock stamps). Sized for the detector's 300 s window at the requested rates.
_DETECTOR_GPS_MAX = 4000
_DETECTOR_VELOCITY_MAX = 9000


def _new_detector_buffer() -> dict[str, Any]:
    return {"gps": deque(maxlen=_DETECTOR_GPS_MAX), "velocity": deque(maxlen=_DETECTOR_VELOCITY_MAX),
            "flight_start_t": None, "origin": None, "armed_at_monotonic": None, "disarmed_at_monotonic": None}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_or_none(value: float | int | None) -> float | int | None:
    if value is None or not math.isfinite(value):
        return None
    return value


def _updated_imu_value(value: float, fields_updated: int, field_flag: int) -> float | None:
    if not fields_updated & field_flag:
        return None
    result = _finite_or_none(float(value))
    return None if result is None else float(result)


class Px4TelemetryStore:
    """Decode MAVLink frames into an in-memory, explicitly sourced PX4 view."""

    def __init__(self, *, stale_after_seconds: float = 3.0, disconnected_after_seconds: float = 10.0):
        if stale_after_seconds <= 0 or disconnected_after_seconds <= stale_after_seconds:
            raise ValueError("Telemetry freshness thresholds are invalid")
        self.stale_after_seconds = stale_after_seconds
        self.disconnected_after_seconds = disconnected_after_seconds
        self._parser = mavlink2.MAVLink(None)
        self._vehicles: dict[str, dict[str, Any]] = {}
        self._last_datagram_at: str | None = None
        self._last_datagram_monotonic: float | None = None
        self.datagrams_received = 0
        self.frames_decoded = 0
        self.invalid_datagrams = 0

    def ingest_datagram(self, payload: bytes, source: tuple[str, int] | None = None) -> int:
        if not payload or payload[0] not in _MAVLINK_FRAME_STARTS:
            self.invalid_datagrams += 1
            return 0

        now_utc = _utc_now()
        now_monotonic = time.monotonic()
        self.datagrams_received += 1
        self._last_datagram_at = now_utc
        self._last_datagram_monotonic = now_monotonic
        decoded = 0

        # UDP datagrams usually contain one MAVLink frame, but byte-wise parsing
        # also handles multiple concatenated frames without dropping the tail.
        for value in payload:
            try:
                message = self._parser.parse_char(bytes((value,)))
            except (ValueError, IndexError) as exc:
                self.invalid_datagrams += 1
                _LOGGER.debug("Discarded malformed MAVLink frame (%s)", type(exc).__name__)
                continue
            if message is None:
                continue
            self._apply_message(message, now_utc, now_monotonic, source)
            self.frames_decoded += 1
            decoded += 1
        return decoded

    def _apply_message(
        self,
        message: Any,
        received_at: str,
        received_monotonic: float,
        source: tuple[str, int] | None,
    ) -> None:
        system_id = int(message.get_srcSystem())
        component_id = int(message.get_srcComponent())
        kind = message.get_type()
        if kind not in _SUPPORTED_TYPES:
            return
        if kind == "HEARTBEAT" and int(message.autopilot) != mavlink2.MAV_AUTOPILOT_PX4 and f"px4-{system_id}" not in self._vehicles:
            return   # a ground-station or companion heartbeat (for example the mission executor's) is not an aircraft
        # MAVLink system IDs identify aircraft; component IDs identify autopilot,
        # IMU, gimbal and other subsystems within that same aircraft.
        vehicle_key = f"px4-{system_id}"
        vehicle = self._vehicles.setdefault(vehicle_key, {
            "id": vehicle_key,
            "system_id": system_id,
            "component_id": component_id,
            "source": "px4_mavlink",
            "armed": None,
            "heartbeat": None,
            "position": None,
            "velocity": None,
            "attitude": None,
            "gps_raw": None,
            "imu_raw": None,
            "battery": None,
            "mission": None,
            "threat_assessment": {
                "status": "not_available",
                "reason": "Not part of this snapshot. Live assessments are served by GET /api/research/live.",
            },
            "last_heartbeat_at": None,
            "last_message_at": received_at,
            "_last_message_monotonic": received_monotonic,
            "_last_heartbeat_monotonic": None,
            "_source": source,
            "_trail": [],
            "estimator_status": None,
            "vibration": None,
            "landed_state": None,
            "_detector": _new_detector_buffer(),
            "_rates": {},
            "_received": {},
        })
        rate = vehicle["_rates"].setdefault(kind, [0, received_monotonic, None])
        rate[0] += 1
        if received_monotonic - rate[1] >= _RATE_WINDOW_S:
            rate[2] = rate[0] / (received_monotonic - rate[1])
            rate[0], rate[1] = 0, received_monotonic
        vehicle["_received"][kind] = received_monotonic
        detector = vehicle["_detector"]
        vehicle["last_message_at"] = received_at
        vehicle["_last_message_monotonic"] = received_monotonic
        vehicle["_source"] = source
        if kind == "HEARTBEAT":
            if int(message.autopilot) != mavlink2.MAV_AUTOPILOT_PX4:
                return
            vehicle["component_id"] = component_id
            armed_flag = bool(message.base_mode & mavlink2.MAV_MODE_FLAG_SAFETY_ARMED)
            if armed_flag and not vehicle["armed"]:      # a new flight: the detector window starts again
                vehicle["_detector"] = detector = _new_detector_buffer()
                detector["armed_at_monotonic"] = received_monotonic
            elif not armed_flag and vehicle["armed"]:    # the flight ended: PX4 disarmed (after landing)
                detector["disarmed_at_monotonic"] = received_monotonic
            vehicle["armed"] = armed_flag
            vehicle["heartbeat"] = {
                "vehicle_type": int(message.type),
                "autopilot": int(message.autopilot),
                "base_mode": int(message.base_mode),
                "custom_mode": int(message.custom_mode),
                "flight_mode": (
                    mavutil.interpret_px4_mode(message.base_mode, message.custom_mode)
                    if int(message.autopilot) == mavlink2.MAV_AUTOPILOT_PX4
                    else None
                ),
                "system_status": int(message.system_status),
                "received_at": received_at,
            }
            vehicle["last_heartbeat_at"] = received_at
            vehicle["_last_heartbeat_monotonic"] = received_monotonic

        elif kind == "EXTENDED_SYS_STATE":
            value = int(message.landed_state)
            vehicle["landed_state"] = {"value": value, "name": _LANDED_STATE_NAMES.get(value, "undefined"), "source_message": kind, "received_at": received_at}

        elif kind == "GLOBAL_POSITION_INT":
            heading = int(message.hdg)
            vehicle["position"] = {
                "latitude": int(message.lat) / 1e7,
                "longitude": int(message.lon) / 1e7,
                "altitude_m": int(message.alt) / 1000.0,
                "relative_altitude_m": int(message.relative_alt) / 1000.0,
                "heading_deg": None if heading == 65535 else heading / 100.0,
                "valid": None if vehicle["gps_raw"] is None else vehicle["gps_raw"]["valid"],
                "coordinate_frame": "WGS84",
                "source_message": kind,
                "source_time_boot_ms": int(message.time_boot_ms),
                "received_at": received_at,
            }
            vehicle["_trail"].append({
                "latitude": int(message.lat) / 1e7,
                "longitude": int(message.lon) / 1e7,
                "altitude_m": int(message.relative_alt) / 1000.0,
                "received_at": received_at,
            })
            if len(vehicle["_trail"]) > _TRAIL_MAX_POINTS:
                del vehicle["_trail"][:-_TRAIL_MAX_POINTS]
            north, east, down = (int(message.vx) / 100.0, int(message.vy) / 100.0, int(message.vz) / 100.0)
            vehicle["velocity"] = {
                "north_m_s": north,
                "east_m_s": east,
                "down_m_s": down,
                "ground_speed_m_s": (north * north + east * east) ** 0.5,
                "source_message": kind,
                "received_at": received_at,
            }

        elif kind == "ATTITUDE":
            vehicle["attitude"] = {
                "roll_rad": float(message.roll),
                "pitch_rad": float(message.pitch),
                "yaw_rad": float(message.yaw),
                "source_message": kind,
                "source_time_boot_ms": int(message.time_boot_ms),
                "received_at": received_at,
            }

        elif kind == "GPS_RAW_INT":
            fix_type = int(message.fix_type)
            speed = None if int(message.vel) == 65535 else int(message.vel) / 100.0
            course = None if int(message.cog) == 65535 else int(message.cog) / 100.0
            if vehicle["armed"] and fix_type >= 3:
                stamp = int(message.time_usec) / 1e6
                if detector["flight_start_t"] is None:
                    detector["flight_start_t"] = stamp
                    detector["origin"] = (int(message.lat) / 1e7, int(message.lon) / 1e7, int(message.alt) / 1000.0)
                moving = speed is not None and course is not None
                detector["gps"].append((
                    stamp, int(message.lat) / 1e7, int(message.lon) / 1e7, int(message.alt) / 1000.0,
                    speed * math.cos(math.radians(course)) if moving else math.nan,
                    speed * math.sin(math.radians(course)) if moving else math.nan,
                ))
            vehicle["gps_raw"] = {
                "ground_speed_m_s": speed,
                "course_deg": course,
                "latitude": int(message.lat) / 1e7,
                "longitude": int(message.lon) / 1e7,
                "altitude_m": int(message.alt) / 1000.0,
                "fix_type": fix_type,
                "valid": fix_type >= 3,
                "source_time_usec": int(message.time_usec),
                "eph_m": None if int(message.eph) == 65535 else int(message.eph) / 100.0,
                "epv_m": None if int(message.epv) == 65535 else int(message.epv) / 100.0,
                "satellites_visible": None if int(message.satellites_visible) == 255 else int(message.satellites_visible),
                "source_message": kind,
                "received_at": received_at,
            }
            if vehicle["position"] is not None:
                vehicle["position"]["valid"] = fix_type >= 3

        elif kind == "HIGHRES_IMU":
            fields_updated = int(message.fields_updated)
            vehicle["imu_raw"] = {
                "acceleration_m_s2": [
                    _updated_imu_value(value, fields_updated, flag)
                    for value, flag in zip((message.xacc, message.yacc, message.zacc), (1, 2, 4))
                ],
                "angular_rate_rad_s": [
                    _updated_imu_value(value, fields_updated, flag)
                    for value, flag in zip((message.xgyro, message.ygyro, message.zgyro), (8, 16, 32))
                ],
                "magnetic_field_gauss": [
                    _updated_imu_value(value, fields_updated, flag)
                    for value, flag in zip((message.xmag, message.ymag, message.zmag), (64, 128, 256))
                ],
                "temperature_c": _updated_imu_value(message.temperature, fields_updated, 4096),
                "fields_updated": fields_updated,
                "source_message": kind,
                "source_time_usec": int(message.time_usec),
                "received_at": received_at,
            }

        elif kind == "LOCAL_POSITION_NED":
            if vehicle["armed"]:
                detector["velocity"].append((int(message.time_boot_ms) / 1e3, float(message.vx), float(message.vy), float(message.vz)))

        elif kind == "ESTIMATOR_STATUS":
            vehicle["estimator_status"] = {
                "flags": int(message.flags),
                "velocity_test_ratio": _finite_or_none(float(message.vel_ratio)),
                "horizontal_position_test_ratio": _finite_or_none(float(message.pos_horiz_ratio)),
                "vertical_position_test_ratio": _finite_or_none(float(message.pos_vert_ratio)),
                "magnetometer_test_ratio": _finite_or_none(float(message.mag_ratio)),
                "horizontal_position_accuracy_m": _finite_or_none(float(message.pos_horiz_accuracy)),
                "source_message": kind,
                "source_time_usec": int(message.time_usec),
                "received_at": received_at,
            }

        elif kind == "VIBRATION":
            vehicle["vibration"] = {
                "vibration_m_s2": [_finite_or_none(float(value)) for value in (message.vibration_x, message.vibration_y, message.vibration_z)],
                "accelerometer_clipping": [int(message.clipping_0), int(message.clipping_1), int(message.clipping_2)],
                "source_message": kind,
                "source_time_usec": int(message.time_usec),
                "received_at": received_at,
            }

        elif kind == "SYS_STATUS":
            voltage_mv = int(message.voltage_battery)
            current_ca = int(message.current_battery)
            remaining = int(message.battery_remaining)
            vehicle["battery"] = {
                "voltage_v": None if voltage_mv == 65535 else voltage_mv / 1000.0,
                "current_a": None if current_ca < 0 else current_ca / 100.0,
                "remaining_percent": None if remaining < 0 else remaining,
                "source_message": kind,
                "received_at": received_at,
            }

        elif kind == "BATTERY_STATUS":
            voltage_mv = [int(value) for value in message.voltages if int(value) != 65535]
            current_ca = int(message.current_battery)
            remaining = int(message.battery_remaining)
            vehicle["battery"] = {
                "voltage_v": sum(voltage_mv) / 1000.0 if voltage_mv else None,
                "current_a": None if current_ca < 0 else current_ca / 100.0,
                "remaining_percent": None if remaining < 0 else remaining,
                "source_message": kind,
                "received_at": received_at,
            }

        elif kind == "MISSION_CURRENT":
            total_sequence = int(message.total)
            vehicle["mission"] = {
                "current_sequence": int(message.seq),
                # MAVLink defines 0 as unsupported and UINT16_MAX as no
                # mission loaded; neither is a usable waypoint count.
                "total_sequence": None if total_sequence in (0, 65535) else total_sequence,
                "mission_state": int(message.mission_state),
                "source_message": kind,
                "received_at": received_at,
            }

    def snapshot(self, *, now_monotonic: float | None = None, include_trails: bool = True) -> dict[str, Any]:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        vehicles = []
        for current in self._vehicles.values():
            vehicle = deepcopy({key: value for key, value in current.items() if not key.startswith("_")})
            message_time = current["_last_message_monotonic"]
            age = max(0.0, now - message_time)
            heartbeat_time = current["_last_heartbeat_monotonic"]
            heartbeat_age = None if heartbeat_time is None else max(0.0, now - heartbeat_time)
            if heartbeat_age is None or heartbeat_age > self.disconnected_after_seconds:
                status = "disconnected"
            elif heartbeat_age > self.stale_after_seconds:
                status = "stale"
            else:
                status = "connected"
            vehicle["status"] = status
            landed_time = current["_received"].get("EXTENDED_SYS_STATE")
            if vehicle.get("landed_state") is not None:
                vehicle["landed_state"]["age_s"] = None if landed_time is None else round(max(0.0, now - landed_time), 2)
            vehicle["telemetry_age_seconds"] = round(age, 3)
            vehicle["heartbeat_age_seconds"] = None if heartbeat_age is None else round(heartbeat_age, 3)
            if include_trails:
                vehicle["flight_trail"] = deepcopy(current["_trail"])
            vehicles.append(vehicle)
        vehicles.sort(key=lambda item: (item["system_id"], item["component_id"]))
        transport_age = (
            None if self._last_datagram_monotonic is None
            else max(0.0, now - self._last_datagram_monotonic)
        )
        if transport_age is None:
            transport_status = "awaiting_data"
        elif transport_age <= self.stale_after_seconds:
            transport_status = "receiving"
        elif transport_age <= self.disconnected_after_seconds:
            transport_status = "stale"
        else:
            transport_status = "disconnected"
        return {
            "source": "px4_mavlink",
            "generated_at": _utc_now(),
            "transport": {
                "status": transport_status,
                "datagrams_received": self.datagrams_received,
                "frames_decoded": self.frames_decoded,
                "invalid_datagrams": self.invalid_datagrams,
                "last_datagram_at": self._last_datagram_at,
                "last_datagram_age_seconds": None if transport_age is None else round(transport_age, 3),
            },
            "capabilities": {
                "gps_spoof_detection": "not_validated_for_px4_telemetry",
                "independent_navigation_estimate": "not_integrated",
                "telemetry_persistence": "in_memory_only",
            },
            "vehicles": vehicles,
        }

    def detector_inputs(self, vehicle_id: str, *, now_monotonic: float | None = None) -> dict[str, Any] | None:
        """Raw detector streams for one aircraft since arming, with per-message ages and measured rates.

        Sensors only: nothing here comes from simulator truth.
        """
        current = self._vehicles.get(vehicle_id)
        if current is None:
            return None
        now = time.monotonic() if now_monotonic is None else now_monotonic
        detector = current["_detector"]
        heartbeat_time = current["_last_heartbeat_monotonic"]
        heartbeat_age = None if heartbeat_time is None else max(0.0, now - heartbeat_time)
        message_age = max(0.0, now - current["_last_message_monotonic"])
        if heartbeat_age is None or heartbeat_age > self.disconnected_after_seconds or message_age > self.disconnected_after_seconds:
            status = "disconnected"
        elif message_age > self.stale_after_seconds:
            status = "stale"
        else:
            # A late heartbeat (1 Hz) while position and attitude keep arriving is a busy host, not a lost link.
            status = "connected"
        rates = {}
        for kind, (_count, _started, rate) in list(current["_rates"].items()):
            age = now - current["_received"][kind]
            # a stream that stopped must not keep showing its last measured rate
            rates[kind] = None if rate is None else (0.0 if age > _RATE_WINDOW_S else round(rate, 2))
        return {
            "id": vehicle_id, "status": status, "armed": current["armed"],
            "telemetry_age_seconds": round(max(0.0, now - current["_last_message_monotonic"]), 3),
            "heartbeat_age_seconds": None if heartbeat_age is None else round(heartbeat_age, 3),
            "ages_seconds": {kind: round(max(0.0, now - at), 3) for kind, at in list(current["_received"].items())},
            "rates_hz": rates,
            "gps": list(detector["gps"]), "velocity": list(detector["velocity"]),
            "flight_start_t": detector["flight_start_t"], "origin": detector["origin"],
            "armed_for_seconds": None if detector["armed_at_monotonic"] is None or not current["armed"] else round(now - detector["armed_at_monotonic"], 1),
            "disarmed_for_seconds": None if detector["disarmed_at_monotonic"] is None or current["armed"] else round(max(0.0, now - detector["disarmed_at_monotonic"]), 1),
            "battery": deepcopy(current["battery"]), "mission": deepcopy(current["mission"]),
            "gps_raw": deepcopy(current["gps_raw"]), "imu_raw": deepcopy(current["imu_raw"]),
            "estimator_status": deepcopy(current["estimator_status"]), "vibration": deepcopy(current["vibration"]),
            "flight_mode": (current["heartbeat"] or {}).get("flight_mode"),
        }

    def forget_except(self, keep_ids: set[str]) -> list[str]:
        """Drops every aircraft that is not in `keep_ids` (a restarted simulation no longer has them). Returns what was dropped.
        The dict is replaced, not edited, so a reader that is iterating the old one is not disturbed."""
        dropped = [key for key in self._vehicles if key not in keep_ids]
        if dropped:
            self._vehicles = {key: value for key, value in self._vehicles.items() if key in keep_ids}
        return dropped

    def vehicle_ids(self) -> list[str]:
        """Aircraft that have sent a PX4 autopilot heartbeat (other MAVLink senders on the link are not aircraft)."""
        return sorted((key for key, item in self._vehicles.items() if item["_last_heartbeat_monotonic"] is not None),
                      key=lambda key: self._vehicles[key]["system_id"])

    def stream_snapshot(self, *, since: str | None = None) -> dict[str, Any]:
        """Return a full first frame or compact trail deltas after a cursor."""
        cursor = _parse_trail_cursor(since)
        if cursor is None:
            snapshot = self.snapshot()
            snapshot["stream_mode"] = "trail_delta_v1"
            return snapshot

        snapshot = self.snapshot(include_trails=False)
        for vehicle in snapshot["vehicles"]:
            current = self._vehicles[vehicle["id"]]
            vehicle["flight_trail_delta"] = deepcopy(_trail_points_after(current["_trail"], cursor))
        snapshot["stream_mode"] = "trail_delta_v1"
        return snapshot


def _parse_trail_cursor(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trail_points_after(points: list[dict[str, Any]], cursor: datetime) -> list[dict[str, Any]]:
    delta = []
    for point in reversed(points):
        point_time = _parse_trail_cursor(point.get("received_at"))
        if point_time is None or point_time <= cursor:
            break
        delta.append(point)
    delta.reverse()
    return delta


class Px4MavlinkProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        store: Px4TelemetryStore,
        *,
        allowed_source_ip: str,
        allowed_source_port: int,
    ):
        self.store = store
        self.allowed_source_ip = allowed_source_ip
        self.allowed_source_port = allowed_source_port
        self.rejected_sources = 0
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if addr != (self.allowed_source_ip, self.allowed_source_port):
            self.rejected_sources += 1
            return
        self.store.ingest_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.warning("PX4 UDP telemetry socket error (%s)", type(exc).__name__)
