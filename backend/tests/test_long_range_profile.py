"""Long-range simulation profile: PX4 parameters applied through PX4_PARAM_* at instance start (SITL only)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.telemetry.fleet_manager import build_fleet_manager_command

_spec = importlib.util.spec_from_file_location("wsl_fleet_manager_profile", Path(__file__).parents[1] / "tools" / "wsl_fleet_manager.py")
assert _spec and _spec.loader
manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manager)


def test_profile_on_sets_only_allowlisted_parameters_in_range():
    env = manager.profile_environment(manager.validate_profile(True, 50, 10, 60))
    assert set(env) == {f"PX4_PARAM_{name}" for name in manager.PROFILE_PARAMS}
    assert env["PX4_PARAM_MPC_XY_VEL_MAX"] == "50" and env["PX4_PARAM_MPC_XY_CRUISE"] == "50"
    assert env["PX4_PARAM_MPC_ACC_HOR"] == "10" and env["PX4_PARAM_MPC_TILTMAX_AIR"] == "60"
    assert env["PX4_PARAM_COM_FLT_TIME_MAX"] == "-1" and env["PX4_PARAM_GF_MAX_HOR_DIST"] == "0"        # no flight-time failsafe, no geofence distance
    assert env["PX4_PARAM_SIM_BAT_DRAIN"] == "18000" and env["PX4_PARAM_SIM_BAT_MIN_PCT"] == "50"        # drains visibly, floor far above the failsafe levels
    for name, value in env.items():
        low, high = manager.PROFILE_PARAMS[name[len("PX4_PARAM_"):]][1:]
        assert low <= float(value) <= high


def test_profile_off_writes_px4_defaults_back_because_parameters_persist():
    env = manager.profile_environment(manager.validate_profile(False))
    assert env["PX4_PARAM_MPC_XY_VEL_MAX"] == "12" and env["PX4_PARAM_MPC_XY_CRUISE"] == "5" and env["PX4_PARAM_SIM_BAT_DRAIN"] == "60"
    assert env["PX4_PARAM_MPC_TILTMAX_AIR"] == "45" and env["PX4_PARAM_NAV_ACC_RAD"] == "10"


@pytest.mark.parametrize("args", [(True, 0), (True, 151), (True, float("nan")), (True, "50"), (True, 50, 0), (True, 50, 10, 90), ("yes",), (True, True)])
def test_invalid_profile_numbers_are_rejected(args):
    with pytest.raises(ValueError):
        manager.validate_profile(*args)


def test_spawn_passes_profile_variables_and_never_inherits_overrides(tmp_path, monkeypatch):
    build = tmp_path / "build/px4_sitl_default"
    (build / "bin").mkdir(parents=True)
    (build / "bin/px4").write_text("")
    (build / "etc").mkdir()
    seen = {}
    monkeypatch.setattr(manager.subprocess, "Popen", lambda command, **kwargs: seen.update(env=kwargs["env"]) or object())
    monkeypatch.setenv("PX4_PARAM_MPC_XY_VEL_MAX", "999")      # a stray shell value must not reach PX4
    monkeypatch.setenv("PX4_PARAM_ARBITRARY", "1")
    manager._spawn(tmp_path, 0)
    assert not [name for name in seen["env"] if name.startswith("PX4_PARAM_")]
    manager._spawn(tmp_path, 0, None, manager.validate_profile(True, 40, 10, 60))
    assert seen["env"]["PX4_PARAM_MPC_XY_VEL_MAX"] == "40" and "PX4_PARAM_ARBITRARY" not in seen["env"]


def test_running_instance_profile_is_read_from_its_environment_and_compared(tmp_path):
    proc = tmp_path / "proc" / "42"
    proc.mkdir(parents=True)
    env = manager.profile_environment(manager.validate_profile(True, 40, 10, 60))
    (proc / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in {**env, "PX4_SIM_MODEL": "gz_x500"}.items()) + b"\0")
    running = manager.instance_profile(42, tmp_path / "proc")
    assert running["active"] is True and running["speed_cap_mps"] == 40.0
    assert running["flight_time_failsafe"] == "disabled" and running["geofence_distance"] == "disabled"
    assert manager.same_profile(running, manager.validate_profile(True, 40, 10, 60))
    assert not manager.same_profile(running, manager.validate_profile(True, 50, 10, 60))
    assert not manager.same_profile(running, manager.validate_profile(False))
    assert manager.instance_profile(7, tmp_path / "proc") is None and not manager.same_profile(None, manager.validate_profile(True, 40))


def test_adapter_builds_fixed_profile_arguments_and_rejects_bad_ones():
    command = build_fleet_manager_command("activate", count=1, profile={"enabled": True, "speed_cap_mps": 50, "accel_mps2": 10, "tilt_deg": 60})
    assert command[-4:] == ["--long-range-profile", "on", "--speed-cap=50.0", "--accel-cap=10.0"] or "--tilt-max=60.0" in command
    assert "--long-range-profile" in build_fleet_manager_command("relocate", count=1, home={"latitude": 1, "longitude": 1, "altitude_m": 0}, profile={"enabled": False})
    for bad in ({"enabled": "on"}, {"enabled": True, "speed_cap_mps": 500}, {"enabled": True, "tilt_deg": 5}, {"enabled": True, "accel_mps2": True}):
        with pytest.raises(ValueError):
            build_fleet_manager_command("activate", count=1, profile=bad)
    with pytest.raises(ValueError, match="only accepted"):
        build_fleet_manager_command("status", profile={"enabled": True})


def test_activation_applies_the_profile_to_started_instances_and_reports_a_running_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_available_memory_kib", lambda: 8 * 1024 * 1024)
    monkeypatch.setattr(manager, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(manager, "ensure_bridges", lambda existing, ports, proc_root=None: {"telemetry": {"status": "running"}, "pose": {"status": "running"}})
    monkeypatch.setattr(manager, "_environment_report", lambda proc_root=None: {"wsl_address": "x", "windows_host": "y", "gazebo_server": "running"})
    monkeypatch.setattr(manager, "instance_profile", lambda pid, proc_root=None: None)
    monkeypatch.setattr(manager, "discover_px4_instances", lambda root, proc_root=None: {0: 4242})
    monkeypatch.setattr(manager, "_spawn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not restart a running instance")))
    profile = manager.validate_profile(True, 50, 10, 60)
    result = manager.activate_fleet(1, px4_root=tmp_path, profile=profile)
    assert result["profile_mismatch"] == ["px4-1"] and result["profile_requested"] == profile
    spawned = {}
    monkeypatch.setattr(manager, "discover_px4_instances", lambda root, proc_root=None: spawned.get("found", {}))

    def spawn(root, instance, home=None, prof=None):
        spawned.update(found={instance: 77}, profile=prof)
        return type("Process", (), {"poll": lambda self: None, "pid": 77})()

    monkeypatch.setattr(manager, "_spawn", spawn)
    again = manager.activate_fleet(1, px4_root=tmp_path, profile=profile)
    assert spawned["profile"] == profile and again["instances"][0]["status"] == "process_started"


def test_runtime_world_widens_only_the_ground_and_refuses_an_unexpected_file(tmp_path):
    worlds = tmp_path / "px4/Tools/simulation/gz/worlds"
    worlds.mkdir(parents=True)
    stock = (Path(__file__).parent / "fixtures" / "default_world_excerpt.sdf").read_text(encoding="utf-8")
    (worlds / "default.sdf").write_text(stock, encoding="utf-8")
    target = manager.build_runtime_world(tmp_path / "px4", 4_000_000, tmp_path / "runtime")
    text = target.read_text(encoding="utf-8")
    assert "<box><size>4000000 4000000 100</size></box>" in text and "<pose>0 0 -50 0 0 0</pose>" in text   # a wide static box, top at z = 0
    assert "<size>20000 20000</size>" in text                                                             # the visual plane stays modest
    assert "<size>1 1</size>" not in text and "<size>500 500</size>" not in text and '<world name="default">' in text
    assert "<plane>" in text and text.count("<collision") == 1                                              # the visual plane is still a plane
    assert (worlds / "default.sdf").read_text(encoding="utf-8") == stock                 # the PX4 tree is untouched
    assert (tmp_path / "runtime" / "world.json").read_text(encoding="utf-8") == '{"ground_extent_m": 4000000.0}'
    for bad in (0, 999, 5e7, float("nan"), True, "100000"):
        with pytest.raises(ValueError):
            manager.build_runtime_world(tmp_path / "px4", bad, tmp_path / "runtime")
    (worlds / "default.sdf").write_text(stock.replace("<size>1 1</size>", "<size>2 2</size>"), encoding="utf-8")
    with pytest.raises(RuntimeError, match="expected ground plane"):
        manager.build_runtime_world(tmp_path / "px4", 100000, tmp_path / "runtime")


def test_gazebo_is_started_from_the_runtime_world_only_when_the_profile_needs_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(manager, "_available_memory_kib", lambda: 8 * 1024 * 1024)
    monkeypatch.setattr(manager, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(manager, "ensure_bridges", lambda existing, ports, proc_root=None: {"telemetry": {"status": "running"}, "pose": {"status": "running"}})
    monkeypatch.setattr(manager, "_environment_report", lambda proc_root=None: {"wsl_address": "x", "windows_host": "y", "gazebo_server": "running"})
    monkeypatch.setattr(manager, "instance_profile", lambda pid, proc_root=None: None)
    monkeypatch.setattr(manager, "runtime_world_extent", lambda proc_root=None: None)
    monkeypatch.setattr(manager, "discover_px4_instances", lambda root, proc_root=None: {})
    monkeypatch.setattr(manager, "discover_gazebo_processes", lambda root, proc_root=None, any_world=False: {"server": [], "gui": []})
    monkeypatch.setattr(manager, "build_runtime_world", lambda root, extent, target=None: calls.append(("world", extent)) or tmp_path / "default.sdf")
    monkeypatch.setattr(manager, "start_gazebo_with_world", lambda root, world, **k: calls.append(("gazebo", str(world))) or {"status": "started"})

    def spawn(root, instance, home=None, profile=None):
        raise OSError("stop here")

    monkeypatch.setattr(manager, "_spawn", spawn)
    manager.activate_fleet(1, px4_root=tmp_path, profile=manager.validate_profile(True, 50, 10, 60, 4_000_000))
    assert calls == [("world", 4_000_000.0), ("gazebo", str(tmp_path / "default.sdf"))]
    calls.clear()
    manager.activate_fleet(1, px4_root=tmp_path, profile=manager.validate_profile(False))
    manager.activate_fleet(1, px4_root=tmp_path)
    assert calls == []                                                                        # profile off or absent: Gazebo is left to PX4
