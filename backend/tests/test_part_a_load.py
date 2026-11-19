"""Part A (load on the laptop): Gazebo options, stale-process clean-up, light server config, fast snapshot copies, threaded MAVLink receiver."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

from app import config
from app.telemetry._copy import copy_trail, copy_value
from app.telemetry.fleet_manager import build_fleet_manager_command
from app.telemetry.gazebo import GazeboPoseStore
from app.telemetry.px4 import Px4MavlinkProtocol, Px4TelemetryStore

_spec = importlib.util.spec_from_file_location("wsl_fleet_manager_a", Path(__file__).resolve().parents[1] / "tools" / "wsl_fleet_manager.py")
manager = importlib.util.module_from_spec(_spec)
sys.modules["wsl_fleet_manager_a"] = manager
_spec.loader.exec_module(manager)


def test_gazebo_gui_and_config_are_fixed_flags_from_settings(monkeypatch):
    monkeypatch.setattr(config, "GAZEBO_GUI", False)
    monkeypatch.setattr(config, "GAZEBO_LIGHT_CONFIG", True)
    command = build_fleet_manager_command("start", count=2)
    assert command[command.index("--gazebo-gui") + 1] == "off"
    assert command[command.index("--gazebo-config") + 1] == "light"
    monkeypatch.setattr(config, "GAZEBO_GUI", True)
    monkeypatch.setattr(config, "GAZEBO_LIGHT_CONFIG", False)
    command = build_fleet_manager_command("activate", count=2)
    assert command[command.index("--gazebo-gui") + 1] == "on" and command[command.index("--gazebo-config") + 1] == "stock"
    assert "--gazebo-gui" not in build_fleet_manager_command("status")      # only the actions that start Gazebo carry the options


def test_gazebo_gui_is_off_by_default_and_the_light_config_keeps_only_what_an_x500_needs():
    assert config.GAZEBO_GUI is False
    text = manager.LIGHT_SERVER_CONFIG
    for needed in ("physics-system", "scene-broadcaster-system", "imu-system", "air-pressure-system", "navsat-system", "magnetometer-system", "user-commands-system"):
        assert needed in text
    for dropped in ("sensors-system", "ogre2", "OpticalFlow", "GstCamera", "wind-effects", "air-speed", "apply-link-wrench", "contact-system"):
        assert dropped not in text


def test_light_config_file_is_written_next_to_the_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINETRA_STATE_DIR", str(tmp_path))
    path = manager.light_server_config_path()
    assert path.parent == tmp_path and path.read_text(encoding="utf-8") == manager.LIGHT_SERVER_CONFIG


def test_stale_processes_are_a_server_without_aircraft_aircraft_without_a_server_or_bridges_alone():
    stale = manager.stale_processes
    complete = {"px4": {"px4-1": 11}, "gazebo_server": [22], "gazebo_gui": [], "bridges": {"telemetry": 33, "pose": 34}}
    assert stale(complete) == []
    assert stale({**complete, "px4": {}}) == ["gazebo-server pid 22"]
    assert stale({**complete, "gazebo_server": []}) == ["px4-1 pid 11"]
    assert stale({"px4": {}, "gazebo_server": [], "gazebo_gui": [], "bridges": {"telemetry": 33}}) == ["telemetry-bridge pid 33"]
    assert stale({"px4": {}, "gazebo_server": [], "gazebo_gui": [], "bridges": {}}) == []


def test_clean_only_stops_what_was_found_stale_and_refuses_unknown_modes(tmp_path, monkeypatch):
    found = {"px4": {}, "gazebo_server": [22], "gazebo_gui": [], "bridges": {"telemetry": 33}}
    monkeypatch.setattr(manager, "inventory", lambda root, proc_root=Path("/proc"): found)
    killed = []
    monkeypatch.setattr(manager, "_terminate", lambda pid, alive, **kw: killed.append(pid) or True)
    result = manager.clean_processes(tmp_path, "stale")
    assert killed == [22] and result["stopped"] == ["gazebo-server 22"] and result["left_running"] == []   # the bridge is not stale while a server exists
    killed.clear()
    manager.clean_processes(tmp_path, "all")
    assert sorted(killed) == [22, 33]
    with pytest.raises(ValueError):
        manager.clean_processes(tmp_path, "everything")


def test_fast_copy_is_independent_and_keeps_values():
    original = {"a": {"b": [1, 2, {"c": 3}]}, "n": None, "s": "x"}
    copied = copy_value(original)
    copied["a"]["b"][2]["c"] = 99
    copied["a"]["b"].append(4)
    assert original == {"a": {"b": [1, 2, {"c": 3}]}, "n": None, "s": "x"}
    trail = [{"x": 1.0}]
    snapshot = copy_trail(trail)
    trail.append({"x": 2.0})
    assert len(snapshot) == 1


def test_pose_store_snapshot_without_trails_has_no_trail_and_copies_independently():
    store = GazeboPoseStore()
    store._models["x500_0"] = {"model_name": "x500_0", "position_m": {"x": 1.0, "y": 2.0, "z": 3.0}, "received_at": "t", "_last_monotonic": time.monotonic(), "_trail": [{"x": 1.0}] * 5}
    plain = store.snapshot(include_trails=False)["models"][0]
    assert "flight_trail" not in plain
    plain["position_m"]["x"] = 99.0
    assert store._models["x500_0"]["position_m"]["x"] == 1.0
    assert len(store.snapshot()["models"][0]["flight_trail"]) == 5


def test_threaded_receiver_queues_off_the_loop_drops_the_oldest_when_full_and_counts_it():
    store = Px4TelemetryStore()
    calls = []
    store.ingest_datagram = lambda data, addr=None: calls.append(data) or 1          # type: ignore[assignment]
    protocol = Px4MavlinkProtocol(store, allowed_source_ip="1.2.3.4", allowed_source_port=5, threaded=True)
    protocol.datagram_received(b"x", ("9.9.9.9", 5))                                   # wrong source: rejected, never queued
    assert protocol.rejected_sources == 1 and not protocol._queue
    for index in range(Px4MavlinkProtocol.QUEUE_MAX + 10):                             # no worker running yet: the queue fills and drops the oldest
        protocol.datagram_received(bytes([index % 250 + 1]), ("1.2.3.4", 5))
    assert len(protocol._queue) == Px4MavlinkProtocol.QUEUE_MAX and protocol.dropped == 10 and protocol.queued_max == Px4MavlinkProtocol.QUEUE_MAX
    protocol.connection_made(object())                                                 # starts the worker, which ingests everything that is queued
    deadline = time.time() + 3
    while len(calls) < Px4MavlinkProtocol.QUEUE_MAX and time.time() < deadline:
        time.sleep(0.01)
    protocol.connection_lost(None)
    assert len(calls) == Px4MavlinkProtocol.QUEUE_MAX


def test_default_receiver_ingests_immediately_as_before():
    store = Px4TelemetryStore()
    seen = []
    store.ingest_datagram = lambda data, addr=None: seen.append(data) or 1             # type: ignore[assignment]
    protocol = Px4MavlinkProtocol(store, allowed_source_ip="1.2.3.4", allowed_source_port=5)
    protocol.datagram_received(b"\xfd", ("1.2.3.4", 5))
    assert seen == [b"\xfd"]


def test_status_is_cached_for_a_few_seconds_shared_between_callers_and_cleared_by_any_change(monkeypatch, blocked_wsl_calls):
    from app.telemetry import fleet_manager as fm
    real = blocked_wsl_calls.real_invoke_fleet_manager          # the conftest guard replaces the module's function so no test reaches WSL
    calls = []
    monkeypatch.setattr(fm, "_invoke_fleet_manager", lambda action, **kw: calls.append(action) or {"instances": [{"system_id": 1}], "manager": "x"})
    fm.clear_status_cache()
    first = real("status", max_age_seconds=4.0)
    first["instances"].append("changed by the caller")                             # a caller cannot corrupt what the next caller gets
    second = real("status", max_age_seconds=4.0)
    assert calls == ["status"] and second["instances"] == [{"system_id": 1}]
    real("status")                                              # default: never cached
    assert calls == ["status", "status"]
    real("start", count=1)                                      # a change clears the cache
    real("status", max_age_seconds=4.0)
    assert calls == ["status", "status", "start", "status"]
    monkeypatch.setattr(fm, "_invoke_fleet_manager", lambda action, **kw: calls.append(action) or {"failure": "wsl down"})
    fm.clear_status_cache(); calls.clear()
    real("status", max_age_seconds=4.0); real("status", max_age_seconds=4.0)
    assert calls == ["status", "status"]                                           # a failure is never cached


def test_a_zombie_counts_as_gone_and_supervisors_are_part_of_the_inventory(tmp_path):
    (tmp_path / "100").mkdir()
    (tmp_path / "100" / "stat").write_text("100 (px4) Z 1 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0")
    (tmp_path / "101").mkdir()
    (tmp_path / "101" / "stat").write_text("101 (px4) S 1 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0")
    assert manager.process_alive(100, tmp_path) is False and manager.process_alive(101, tmp_path) is True and manager.process_alive(999, tmp_path) is False
    (tmp_path / "200").mkdir()
    (tmp_path / "200" / "stat").write_text("200 (python3) S 1 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0")
    (tmp_path / "200" / "cmdline").write_bytes(b"/usr/bin/python3\0" + manager.__file__.encode() + b"\0supervise\0--count\0" + b"5\0")
    assert manager.discover_supervisors(tmp_path) == [200]
