from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


_LOGGER = logging.getLogger("trinetra.telemetry.gazebo")
_MODEL_NAME = re.compile(r"^x500_[0-9]{1,3}$")
_MAX_DATAGRAM_BYTES = 64 * 1024
_MAX_MODELS = 32
_TRAIL_MAX_POINTS = 3000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_number(value: Any, *, limit: float = 100_000.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or abs(number) > limit:
        return None
    return number


class GazeboPoseStore:
    """Validated, read-only snapshots of X500 poses published by Gazebo."""

    def __init__(self, *, stale_after_seconds: float = 3.0, disconnected_after_seconds: float = 10.0):
        if stale_after_seconds <= 0 or disconnected_after_seconds <= stale_after_seconds:
            raise ValueError("Gazebo freshness thresholds are invalid")
        self.stale_after_seconds = stale_after_seconds
        self.disconnected_after_seconds = disconnected_after_seconds
        self._world: str | None = None
        self._models: dict[str, dict[str, Any]] = {}
        self._last_datagram_at: str | None = None
        self._last_datagram_monotonic: float | None = None
        self.datagrams_received = 0
        self.invalid_datagrams = 0

    def ingest_datagram(self, payload: bytes) -> bool:
        if not payload or len(payload) > _MAX_DATAGRAM_BYTES:
            self.invalid_datagrams += 1
            return False
        try:
            data = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.invalid_datagrams += 1
            return False
        if not isinstance(data, dict) or data.get("source") != "gazebo_pose_info":
            self.invalid_datagrams += 1
            return False
        world = data.get("world")
        models = data.get("models")
        if not isinstance(world, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", world):
            self.invalid_datagrams += 1
            return False
        if not isinstance(models, list) or len(models) > _MAX_MODELS:
            self.invalid_datagrams += 1
            return False

        now = time.monotonic()
        received_at = _utc_now()
        seen: set[str] = set()
        parsed: list[tuple[str, dict[str, float], dict[str, float]]] = []
        for model in models:
            if not isinstance(model, dict):
                continue
            name = model.get("model_name")
            if not isinstance(name, str) or not _MODEL_NAME.fullmatch(name) or name in seen:
                continue
            position = model.get("position_m")
            orientation = model.get("orientation_quaternion")
            if not isinstance(position, dict) or not isinstance(orientation, dict):
                continue
            point = {axis: _finite_number(position.get(axis)) for axis in ("x", "y", "z")}
            quaternion = {axis: _finite_number(orientation.get(axis), limit=2.0) for axis in ("x", "y", "z", "w")}
            if any(value is None for value in point.values()) or any(value is None for value in quaternion.values()):
                continue
            norm = math.sqrt(sum(float(value) ** 2 for value in quaternion.values()))
            if not math.isfinite(norm) or norm < 0.5 or norm > 1.5:
                continue
            quaternion = {axis: float(value) / norm for axis, value in quaternion.items()}
            seen.add(name)
            parsed.append((name, {axis: float(value) for axis, value in point.items()}, quaternion))
        if not parsed:
            self.invalid_datagrams += 1
            return False

        self._world = world
        self.datagrams_received += 1
        self._last_datagram_at = received_at
        self._last_datagram_monotonic = now
        for name, position, orientation in parsed:
            model = self._models.setdefault(name, {"model_name": name, "_trail": []})
            model["position_m"] = position
            model["orientation_quaternion"] = orientation
            model["received_at"] = received_at
            model["_last_monotonic"] = now
            trail = model["_trail"]
            trail.append({"x": position["x"], "y": position["y"], "z": position["z"], "received_at": received_at})
            if len(trail) > _TRAIL_MAX_POINTS:
                del trail[:-_TRAIL_MAX_POINTS]
        return True

    def snapshot(self, *, now_monotonic: float | None = None, include_trails: bool = True) -> dict[str, Any]:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        transport_age = None if self._last_datagram_monotonic is None else max(0.0, now - self._last_datagram_monotonic)
        if transport_age is None:
            transport_status = "awaiting_data"
        elif transport_age <= self.stale_after_seconds:
            transport_status = "receiving"
        elif transport_age <= self.disconnected_after_seconds:
            transport_status = "stale"
        else:
            transport_status = "disconnected"

        models: list[dict[str, Any]] = []
        for current in self._models.values():
            model = {key: deepcopy(value) for key, value in current.items() if not key.startswith("_")}
            age = max(0.0, now - current["_last_monotonic"])
            model["status"] = "connected" if age <= self.stale_after_seconds else "stale" if age <= self.disconnected_after_seconds else "disconnected"
            model["telemetry_age_seconds"] = round(age, 3)
            if include_trails:
                model["flight_trail"] = deepcopy(current["_trail"])
            models.append(model)
        models.sort(key=lambda item: item["model_name"])
        return {
            "source": "gazebo_pose_info",
            "world": self._world,
            "generated_at": _utc_now(),
            "transport": {
                "status": transport_status,
                "datagrams_received": self.datagrams_received,
                "invalid_datagrams": self.invalid_datagrams,
                "last_datagram_at": self._last_datagram_at,
                "last_datagram_age_seconds": None if transport_age is None else round(transport_age, 3),
            },
            "models": models,
        }

    def stream_snapshot(self, *, since: str | None = None) -> dict[str, Any]:
        """Return a full first frame or compact trail deltas after a cursor."""
        cursor = _parse_trail_cursor(since)
        if cursor is None:
            snapshot = self.snapshot()
            snapshot["stream_mode"] = "trail_delta_v1"
            return snapshot

        snapshot = self.snapshot(include_trails=False)
        for model in snapshot["models"]:
            current = self._models[model["model_name"]]
            model["flight_trail_delta"] = deepcopy(_trail_points_after(current["_trail"], cursor))
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


class GazeboPoseProtocol(asyncio.DatagramProtocol):
    def __init__(self, store: GazeboPoseStore, *, allowed_source_ip: str, allowed_source_port: int):
        self.store = store
        self.allowed_source_ip = allowed_source_ip
        self.allowed_source_port = allowed_source_port
        self.rejected_sources = 0

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if addr != (self.allowed_source_ip, self.allowed_source_port):
            self.rejected_sources += 1
            return
        self.store.ingest_datagram(data)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.warning("Gazebo pose UDP socket error (%s)", type(exc).__name__)

