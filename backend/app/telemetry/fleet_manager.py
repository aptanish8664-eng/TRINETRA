"""Fixed-command Windows-to-WSL adapter for the allowlisted SITL fleet manager."""
from __future__ import annotations

import json
import subprocess
from typing import Any
import uuid

from ..mission_planning.start import validate_home


WSL_DISTRIBUTION = "Ubuntu"
WSL_MANAGER_PATH = "/mnt/d/TRINETRA/backend/tools/wsl_fleet_manager.py"
PX4_ROOT = "/home/apt8055/uav-sim/PX4-Autopilot"
FLEET_CAPACITY = 5
_ACTIONS = {"status", "activate", "stop", "relocate"}


def _home_arguments(home: dict[str, Any]) -> list[str]:
    """Three range-checked numbers rendered as fixed-format decimals; nothing else from `home` reaches argv."""
    latitude, longitude, altitude = validate_home(home.get("latitude"), home.get("longitude"), home.get("altitude_m"))
    # The `=` form keeps a negative number from being read as an option by the WSL-side argument parser.
    return [f"--home-lat={latitude:.7f}", f"--home-lon={longitude:.7f}", f"--home-alt={altitude:.2f}"]


def build_fleet_manager_command(action: str, *, count: int | None = None,
                                instances: list[int] | None = None,
                                request_id: str | None = None,
                                ports: dict[str, int] | None = None,
                                home: dict[str, Any] | None = None) -> list[str]:
    if action not in _ACTIONS:
        raise ValueError("unsupported fleet operation")
    command = ["wsl.exe", "--distribution", WSL_DISTRIBUTION, "--exec", "python3",
               WSL_MANAGER_PATH, action, "--px4-root", PX4_ROOT]
    if action in {"activate", "relocate"}:
        if count is None or not 1 <= count <= FLEET_CAPACITY:
            raise ValueError(f"fleet count must be between 1 and {FLEET_CAPACITY}")
        if action == "relocate" and home is None:
            raise ValueError("relocate requires a simulation home")
        command.extend(("--count", str(count)))
        for key, option in (("telemetry_forward", "--telemetry-forward-port"),
                            ("telemetry_source", "--telemetry-source-port"),
                            ("pose_forward", "--pose-forward-port"), ("pose_source", "--pose-source-port")):
            value = (ports or {}).get(key)
            if value is not None:
                if not isinstance(value, int) or not 1024 <= value <= 65535:
                    raise ValueError("bridge ports must be integers from 1024 to 65535")
                command.extend((option, str(value)))
        if home is not None:
            command.extend(_home_arguments(home))
    elif home is not None:
        raise ValueError("a simulation home is only accepted by activate and relocate")
    elif action == "stop":
        values = instances or []
        if not values or len(values) != len(set(values)) or any(not 0 <= item < FLEET_CAPACITY for item in values):
            raise ValueError("stop requires unique managed instance IDs within fleet capacity")
        command.append("--instances")
        command.extend(str(item) for item in values)
    elif count is not None or instances:
        raise ValueError("unused fleet operation arguments are not accepted")
    if request_id is not None:
        try:
            uuid.UUID(request_id)
        except ValueError as exc:
            raise ValueError("request_id must be a UUID") from exc
        command.extend(("--request-id", request_id))
    return command


def invoke_fleet_manager(action: str, *, count: int | None = None,
                         instances: list[int] | None = None,
                         request_id: str | None = None,
                         timeout_seconds: int = 35,
                         home: dict[str, Any] | None = None) -> dict[str, Any]:
    ports = None
    if action in {"activate", "relocate"}:
        from ..config import (GAZEBO_POSE_UDP_ALLOWED_SOURCE_PORT, GAZEBO_POSE_UDP_BIND_PORT,
                              PX4_UDP_ALLOWED_SOURCE_PORT, PX4_UDP_BIND_PORT)
        ports = {"telemetry_forward": PX4_UDP_BIND_PORT, "telemetry_source": PX4_UDP_ALLOWED_SOURCE_PORT,
                 "pose_forward": GAZEBO_POSE_UDP_BIND_PORT, "pose_source": GAZEBO_POSE_UDP_ALLOWED_SOURCE_PORT}
    command = build_fleet_manager_command(action, count=count, instances=instances,
                                          request_id=request_id, ports=ports, home=home)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"failure": f"WSL fleet manager could not complete: {type(exc).__name__}",
                "manager": "px4_wsl_fleet_v1"}
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        if not isinstance(payload, dict):
            raise ValueError
    except (IndexError, json.JSONDecodeError, ValueError):
        return {"failure": "WSL fleet manager returned an invalid status response",
                "manager": "px4_wsl_fleet_v1", "exit_code": result.returncode}
    if result.returncode and not payload.get("failure"):
        payload["failure"] = "WSL fleet manager reported a non-zero exit status"
    return payload
