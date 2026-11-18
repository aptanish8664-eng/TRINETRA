"""Fixed-command Windows-to-WSL adapter for the allowlisted SITL mission executor.

Like the fleet manager adapter: the backend can only run one script with a fixed action and validated arguments.
A mission is passed on standard input as JSON built from numbers (see app/mission_execution/items.py); the
executor validates it again. Nothing from a browser reaches a shell or a MAVLink socket.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from .fleet_manager import FLEET_CAPACITY, PX4_ROOT, WSL_DISTRIBUTION

WSL_EXECUTOR_PATH = "/mnt/d/TRINETRA/backend/tools/wsl_mission_executor.py"
_ACTIONS = {"start", "status", "pause", "resume", "abort", "land", "disarm", "cleanup"}
_EXECUTION_ID = re.compile(r"^[0-9a-f]{32}$")


def build_executor_command(action: str, *, instance: int = 0, execution_id: str | None = None,
                           events_after: int | None = None) -> list[str]:
    if action not in _ACTIONS:
        raise ValueError("unsupported executor operation")
    if isinstance(instance, bool) or not isinstance(instance, int) or not 0 <= instance < FLEET_CAPACITY:
        raise ValueError("instance must be within the fleet capacity")
    command = ["wsl.exe", "--distribution", WSL_DISTRIBUTION, "--exec", "python3", WSL_EXECUTOR_PATH, action,
               "--instance", str(instance), "--px4-root", PX4_ROOT]
    if action in {"pause", "resume", "abort"} or (action == "land" and execution_id is not None):
        if execution_id is None or not _EXECUTION_ID.fullmatch(execution_id):
            raise ValueError("a control command needs the execution id (32 lowercase hex characters)")
        command.extend(("--execution-id", execution_id))
    elif execution_id is not None:
        raise ValueError("the execution id is only accepted by control commands")
    if action == "status":
        if events_after is not None:
            if isinstance(events_after, bool) or not isinstance(events_after, int) or not -1 <= events_after <= 10_000_000:
                raise ValueError("events_after must be an integer")
            command.extend(("--events-after", str(events_after)))
    elif events_after is not None:
        raise ValueError("events_after is only accepted by status")
    return command


def invoke_mission_executor(action: str, *, instance: int = 0, execution_id: str | None = None,
                            events_after: int | None = None, mission: dict[str, Any] | None = None,
                            timeout_seconds: int = 20) -> dict[str, Any]:
    if (action == "start") != (mission is not None):
        raise ValueError("a mission is required for start and only for start")
    command = build_executor_command(action, instance=instance, execution_id=execution_id, events_after=events_after)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False,
                                input=json.dumps(mission, allow_nan=False) if mission is not None else None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"failure": f"WSL mission executor could not complete: {type(exc).__name__}", "failure_code": "executor_unreachable"}
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        if not isinstance(payload, dict):
            raise ValueError
    except (IndexError, json.JSONDecodeError, ValueError):
        return {"failure": "WSL mission executor returned an invalid response", "failure_code": "executor_unreachable", "exit_code": result.returncode}
    if result.returncode and not payload.get("failure"):
        payload["failure"] = "WSL mission executor reported a non-zero exit status"
        payload.setdefault("failure_code", "executor_error")
    return payload
