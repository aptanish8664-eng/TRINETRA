from unittest.mock import patch
import asyncio
import importlib.util
from pathlib import Path

import pytest

from app.fleet_routes import _verified_fleet
from app.telemetry.fleet_manager import (
    build_fleet_manager_command,
    invoke_fleet_manager,
)

_manager_spec = importlib.util.spec_from_file_location(
    "wsl_fleet_manager", Path(__file__).parents[1] / "tools" / "wsl_fleet_manager.py",
)
assert _manager_spec and _manager_spec.loader
_manager = importlib.util.module_from_spec(_manager_spec)
_manager_spec.loader.exec_module(_manager)


def test_fleet_manager_command_is_fixed_and_argument_validated():
    command = build_fleet_manager_command("activate", count=5)
    assert command[:4] == ["wsl.exe", "--distribution", "Ubuntu", "--exec"]
    assert command[4:7] == ["python3", "/mnt/d/TRINETRA/backend/tools/wsl_fleet_manager.py", "activate"]
    assert command[-2:] == ["--count", "5"]
    with pytest.raises(ValueError, match="between 1 and 5"):
        build_fleet_manager_command("activate", count=6)
    with pytest.raises(ValueError, match="unsupported"):
        build_fleet_manager_command("shell")
    with pytest.raises(ValueError, match="unique"):
        build_fleet_manager_command("stop", instances=[1, 1])


def test_wsl_manager_preflights_capacity_without_reconfiguring_px4():
    assert _manager.resource_preflight(3, 1, available_memory_kib=4 * 1024 * 1024, cpu_count=8) == (True, None)
    ok, reason = _manager.resource_preflight(3, 1, available_memory_kib=1200 * 1024, cpu_count=8)
    assert not ok and "insufficient available memory" in reason
    ok, reason = _manager.resource_preflight(2, 0, available_memory_kib=8 * 1024 * 1024, cpu_count=1)
    assert not ok and "logical CPUs" in reason


def test_manager_response_is_parsed_as_structured_json_without_stderr_leak():
    completed = type("Completed", (), {"stdout": '{"instances": [], "manager": "px4_wsl_fleet_v1"}\n',
                                        "stderr": "sensitive output", "returncode": 0})()
    with patch("app.telemetry.fleet_manager.subprocess.run", return_value=completed) as run:
        result = invoke_fleet_manager("status")
    assert result["manager"] == "px4_wsl_fleet_v1"
    assert "sensitive output" not in str(result)
    assert isinstance(run.call_args.args[0], list)
    assert "shell" not in run.call_args.kwargs


def test_verified_fleet_requires_each_matching_px4_and_gazebo_identity(monkeypatch):
    vehicles = [{"system_id": 1, "status": "connected", "position": {"valid": True}},
                {"system_id": 2, "status": "connected", "position": {"valid": False}}]
    models = [{"model_name": "x500_0", "status": "connected"},
              {"model_name": "x500_1", "status": "connected"}]
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: (vehicles, models))
    verified, rows = _verified_fleet(2)
    assert [item["aircraft_id"] for item in verified] == ["px4-1"]
    assert rows[1]["px4_status"] == "connected"
    assert rows[1]["verified"] is False


def test_default_startup_degrades_without_both_receiver_transports(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "AUTO_START_DEFAULT_SITL", True)
    monkeypatch.setattr(main, "px4_udp_transport", None)
    monkeypatch.setattr(main, "gazebo_pose_udp_transport", object())
    asyncio.run(main._initialize_default_sitl())
    assert main.default_sitl_startup_status["status"] == "DEGRADED"
    assert "both be configured" in main.default_sitl_startup_status["detail"]


def test_default_startup_marks_ready_only_after_verified_fleet(monkeypatch):
    from app import main
    from app.telemetry import fleet_manager

    monkeypatch.setattr(main, "AUTO_START_DEFAULT_SITL", True)
    monkeypatch.setattr(main, "px4_udp_transport", object())
    monkeypatch.setattr(main, "gazebo_pose_udp_transport", object())
    monkeypatch.setattr(fleet_manager, "invoke_fleet_manager", lambda *args, **kwargs: {"instances": []})
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([{
        "aircraft_id": "px4-1", "system_id": 1, "gazebo_model": "x500_0",
    }], []))
    asyncio.run(main._initialize_default_sitl())
    assert main.default_sitl_startup_status["status"] == "READY"
    assert main.default_sitl_startup_status["aircraft_id"] == "px4-1"
