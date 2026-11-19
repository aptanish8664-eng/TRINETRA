"""Mocked tests for WSL bridge adoption and structured fleet-activation failures.

No WSL, PX4 or Gazebo process is started; process discovery uses fake /proc trees.
"""
import importlib.util
from pathlib import Path

import pytest

from app.telemetry.fleet_manager import build_fleet_manager_command

_spec = importlib.util.spec_from_file_location(
    "wsl_fleet_manager_activation", Path(__file__).parents[1] / "tools" / "wsl_fleet_manager.py",
)
assert _spec and _spec.loader
_manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_manager)


def _fake_proc(root, pid, *args):
    (root / str(pid)).mkdir()
    (root / str(pid) / "cmdline").write_bytes(b"\0".join(item.encode() for item in args) + b"\0")


def test_default_gateway_is_parsed_from_the_route_table(tmp_path):
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\tFlags\n"
                     "eth0\t00000000\t015017AC\t0003\neth0\t005017AC\t00000000\t0001\n", encoding="ascii")
    assert _manager.windows_host_address(route) == "172.23.80.1"
    assert _manager.windows_host_address(tmp_path / "missing") is None


def test_running_bridge_is_adopted_and_never_duplicated(tmp_path):
    _fake_proc(tmp_path, 321, "python3", str(_manager.TELEMETRY_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1")

    def forbidden(*args, **kwargs):
        raise AssertionError("a duplicate bridge must not be started")

    result = _manager.ensure_bridge("telemetry", _manager.TELEMETRY_BRIDGE_SCRIPT, [],
                                    proc_root=tmp_path, spawn=forbidden)
    assert result == {"status": "running", "pid": 321, "adopted": True}


def test_missing_bridge_is_started_once_and_early_exit_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(_manager.os, "getuid", lambda: 1000, raising=False)
    launched = []

    class Alive:
        pid = 777
        returncode = None

        def poll(self):
            return None

    def spawn(command, **kwargs):
        launched.append(command)
        assert kwargs["start_new_session"] is True and "shell" not in kwargs
        return Alive()

    started = _manager.ensure_bridge("pose", _manager.POSE_BRIDGE_SCRIPT, ["--windows-host", "172.23.80.1"],
                                     proc_root=tmp_path, settle_s=0.2, spawn=spawn)
    assert started["status"] == "started" and len(launched) == 1
    assert launched[0][1] == str(_manager.POSE_BRIDGE_SCRIPT)

    class Dead:
        pid = 778
        returncode = 1

        def poll(self):
            return 1

    failed = _manager.ensure_bridge("pose", _manager.POSE_BRIDGE_SCRIPT, [], proc_root=tmp_path,
                                    settle_s=0.2, spawn=lambda *a, **k: Dead())
    assert failed["status"] == "failed"


def _patch_manager(monkeypatch, tmp_path, bridges):
    monkeypatch.setattr(_manager, "discover_px4_instances", lambda root, proc_root=None: {0: 4242})
    monkeypatch.setattr(_manager, "_available_memory_kib", lambda: 8 * 1024 * 1024)
    monkeypatch.setattr(_manager, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(_manager, "_spawn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("duplicate PX4")))
    monkeypatch.setattr(_manager, "ensure_bridges", lambda existing, ports, proc_root=None: bridges)
    monkeypatch.setattr(_manager, "_environment_report", lambda proc_root=None: {
        "wsl_address": "172.23.85.230", "windows_host": "172.23.80.1", "gazebo_server": "running"})


def test_activation_adopts_running_instance_zero_without_starting_a_duplicate(tmp_path, monkeypatch):
    _patch_manager(monkeypatch, tmp_path, {"telemetry": {"status": "started", "pid": 1},
                                           "pose": {"status": "running", "pid": 2}})
    result = _manager.activate_fleet(1, px4_root=tmp_path)
    assert result["instances"][0]["status"] == "process_present"
    assert result["failure"] is None and result["bridges"]["telemetry"]["status"] == "started"


def test_bridge_start_failure_is_reported_by_name(tmp_path, monkeypatch):
    _patch_manager(monkeypatch, tmp_path, {"telemetry": {"status": "failed", "detail": "Address already in use"},
                                           "pose": {"status": "running", "pid": 2}})
    result = _manager.activate_fleet(1, px4_root=tmp_path)
    assert result["failure_code"] == "bridge_start_failed"
    assert "telemetry bridge: Address already in use" in result["failure"]


def test_bridge_ports_are_validated_and_passed_as_fixed_arguments():
    command = build_fleet_manager_command("activate", count=1,
                                          ports={"telemetry_forward": 14600, "pose_source": 14611})
    start = command.index("--telemetry-forward-port")
    assert command[start:start + 4] == ["--telemetry-forward-port", "14600", "--pose-source-port", "14611"]
    with pytest.raises(ValueError, match="bridge ports"):
        build_fleet_manager_command("activate", count=1, ports={"pose_forward": 80})


def _good_process(**overrides):
    base = {"instances": [{"instance": 0, "status": "process_present"}], "failure": None, "failure_code": None,
            "bridges": {"telemetry": {"status": "running"}, "pose": {"status": "running"}},
            "wsl_address": "172.23.85.230", "gazebo_server": "running", "manager": "px4_wsl_fleet_v1"}
    base.update(overrides)
    return base


_ROW_MISSING = [{"aircraft_id": "px4-1", "px4_status": "not_seen", "gazebo_status": "not_seen", "verified": False}]
_ROW_OK = [{"aircraft_id": "px4-1", "px4_status": "connected", "gazebo_status": "connected", "verified": True}]


def _classify(process, rows, *, final):
    from app.fleet_routes import build_activation_checks, classify_activation_failure

    checks = build_activation_checks(
        1, process, rows, receivers={"px4": True, "gazebo": True},
        configured_ips={"px4_telemetry": "172.23.85.230", "gazebo_pose": "172.23.85.230"})
    return checks, classify_activation_failure(1, process, checks, final=final)


def test_failure_classification_uses_accurate_status_codes():
    checks, failure = _classify({"failure": "WSL fleet manager could not complete: OSError", "manager": "x"},
                                _ROW_MISSING, final=False)
    assert failure[:2] == (503, "wsl_manager_unavailable") and checks["wsl"]["status"] == "unavailable"
    _, failure = _classify(_good_process(
        failure="telemetry bridge: boom", failure_code="bridge_start_failed",
        bridges={"telemetry": {"status": "failed", "detail": "boom"}, "pose": {"status": "running"}}),
        _ROW_MISSING, final=False)
    assert failure[:2] == (502, "bridge_start_failed")
    checks, failure = _classify(_good_process(wsl_address="172.23.99.9"), _ROW_MISSING, final=False)
    assert failure[:2] == (409, "source_ip_mismatch") and checks["source_ip"]["status"] == "mismatch"
    checks, failure = _classify(_good_process(), _ROW_MISSING, final=True)
    assert failure[:2] == (504, "verification_timeout")
    assert checks["verification"]["unverified"][0]["px4"] == "not_seen"
    checks, failure = _classify(_good_process(), _ROW_OK, final=True)
    assert failure is None and checks["verification"]["status"] == "ok"
    assert {"wsl", "gazebo", "px4", "telemetry_bridge", "pose_bridge", "receivers", "verification"} <= set(checks)


def _enable_receivers(monkeypatch, px4=True):
    from app import main

    monkeypatch.setattr(main, "px4_udp_transport", object() if px4 else None)
    monkeypatch.setattr(main, "gazebo_pose_udp_transport", object())


def test_activate_endpoint_returns_structured_503_when_receivers_are_unbound(general_client, monkeypatch):
    client, _ = general_client
    _enable_receivers(monkeypatch, px4=False)
    response = client.post("/api/fleet/activate", json={"required_uav_count": 1})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "receivers_unavailable" and detail["verified"] is False
    assert detail["checks"]["receivers"]["px4"] is False and detail["checks"]["receivers"]["gazebo"] is True


def test_activate_endpoint_reports_missing_bridge_as_502_without_waiting(general_client, monkeypatch):
    client, _ = general_client
    _enable_receivers(monkeypatch)
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: _good_process(
        failure="pose bridge: gz not found", failure_code="bridge_start_failed",
        bridges={"telemetry": {"status": "running"}, "pose": {"status": "failed", "detail": "gz not found"}}))
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([], _ROW_MISSING))
    response = client.post("/api/fleet/activate", json={"required_uav_count": 1})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "bridge_start_failed"
    assert detail["checks"]["pose_bridge"] == {"status": "failed", "detail": "gz not found"}
    assert detail["checks"]["telemetry_bridge"]["status"] == "running"


def test_activate_endpoint_succeeds_only_when_telemetry_and_pose_are_both_verified(general_client, monkeypatch):
    client, _ = general_client
    _enable_receivers(monkeypatch)
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: _good_process())
    row = {**_ROW_OK[0], "instance_number": 0, "system_id": 1, "gazebo_model": "x500_0"}
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([row], [row]))
    response = client.post("/api/fleet/activate", json={"required_uav_count": 1})
    assert response.status_code == 200
    assert response.json()["verified"] is True and response.json()["checks"]["verification"]["status"] == "ok"


def test_gazebo_server_is_detected_from_a_space_joined_process_title(tmp_path):
    _fake_proc(tmp_path, 666, "gz sim --verbose=1 -r -s /worlds/default.sdf")
    _fake_proc(tmp_path, 667, "gz sim -g")
    assert _manager.gazebo_server_running(tmp_path) is True
    only_gui = tmp_path / "gui"
    only_gui.mkdir()
    _fake_proc(only_gui, 667, "gz", "sim", "-g")
    assert _manager.gazebo_server_running(only_gui) is False


# --- adopted bridges must send where the backend listens -----------------------------------------------------------

def _spawn_recorder(launched, pid=900):
    class Alive:
        returncode = None

        def poll(self):
            return None

    def spawn(command, **kwargs):
        launched.append(command)
        process = Alive()
        process.pid = pid
        return process

    return spawn


def test_bridge_arguments_are_read_from_the_command_line_with_script_defaults(tmp_path):
    _fake_proc(tmp_path, 11, "python3", str(_manager.TELEMETRY_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--forward-port", "14600",
               "--additional-forward-port", "14620", "--additional-forward-port=14630", "--additional-forward-port", "14600", "--source-port", "14601")
    _fake_proc(tmp_path, 12, "python3", str(_manager.POSE_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--world", "default", "--rate", "10")
    assert _manager.bridge_arguments("telemetry", 11, tmp_path) == {"windows_host": "172.23.80.1", "forward_port": 14600, "source_port": 14601, "mirror_ports": [14620, 14630]}
    assert _manager.bridge_arguments("pose", 12, tmp_path) == {"windows_host": "172.23.80.1", "forward_port": 14610, "source_port": 14611, "mirror_ports": []}
    expected = {"forward_port": 14600, "source_port": 14601, "windows_host": "172.23.80.1"}
    mirrored = _manager.bridge_arguments("telemetry", 11, tmp_path)
    assert _manager.bridge_mismatches("telemetry", mirrored, expected) == []                           # mirrors are extra receivers, not a mismatch
    assert _manager.bridge_mismatches("telemetry", mirrored, {**expected, "forward_port": 14620}) == []   # a mirror target counts as "sends to"
    assert _manager.bridge_mismatches("telemetry", mirrored, {**expected, "forward_port": 14700}) == [
        "PX4 telemetry bridge sends to 14600 and 14620 and 14630, expected 14700"]
    adopted = _manager.ensure_bridge("telemetry", _manager.TELEMETRY_BRIDGE_SCRIPT, [], proc_root=tmp_path, expected=expected,
                                     spawn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("a mirrored bridge that sends to the right port is kept")))
    assert adopted == {"status": "running", "pid": 11, "adopted": True, "mirror_ports": [14620, 14630]}
    assert _manager.inspect_bridges(tmp_path)["telemetry"] == {"status": "running", "pid": 11, "mirror_ports": [14620, 14630]}
    assert _manager.bridge_mismatches("pose", _manager.bridge_arguments("pose", 12, tmp_path), {"forward_port": 14610, "source_port": 14611, "windows_host": "172.23.80.1"}) == []


def test_adopted_bridge_with_the_wrong_forward_port_is_restarted_with_the_expected_one(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(_manager.os, "getuid", lambda: 1000, raising=False)
    _fake_proc(tmp_path, 321, "python3", str(_manager.POSE_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--world", "default",
               "--forward-port", "14612", "--source-port", "14611")
    stopped, launched = [], []

    def terminate(pid, still_running, grace_s=3.0):
        stopped.append(pid)
        import shutil
        shutil.rmtree(tmp_path / str(pid))             # the process exits
        return not still_running()

    argv = ["--windows-host", "172.23.80.1", "--world", "default", "--forward-port", "14610", "--source-port", "14611"]
    result = _manager.ensure_bridge("pose", _manager.POSE_BRIDGE_SCRIPT, argv, proc_root=tmp_path, settle_s=0.2, spawn=_spawn_recorder(launched),
                                    expected={"forward_port": 14610, "source_port": 14611, "windows_host": "172.23.80.1"}, terminate=terminate)
    assert stopped == [321] and len(launched) == 1 and launched[0][2:] == argv
    assert result == {"status": "started", "pid": 900, "adopted": False, "restarted": True, "replaced": "Gazebo pose bridge sends to 14612, expected 14610"}


def test_matching_bridge_is_still_adopted_and_a_stale_windows_host_counts_as_a_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(_manager.os, "getuid", lambda: 1000, raising=False)
    _fake_proc(tmp_path, 400, "python3", str(_manager.TELEMETRY_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--forward-port", "14600", "--source-port", "14601")
    forbidden = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not touch a matching bridge"))
    expected = {"forward_port": 14600, "source_port": 14601, "windows_host": "172.23.80.1"}
    assert _manager.ensure_bridge("telemetry", _manager.TELEMETRY_BRIDGE_SCRIPT, [], proc_root=tmp_path, spawn=forbidden, expected=expected,
                                  terminate=forbidden) == {"status": "running", "pid": 400, "adopted": True}
    launched = []

    def terminate(pid, still_running, grace_s=3.0):
        import shutil
        shutil.rmtree(tmp_path / str(pid))
        return True

    moved = _manager.ensure_bridge("telemetry", _manager.TELEMETRY_BRIDGE_SCRIPT, [], proc_root=tmp_path, settle_s=0.2, spawn=_spawn_recorder(launched),
                                   expected={**expected, "windows_host": "172.29.0.1"}, terminate=terminate)
    assert moved["restarted"] is True and moved["replaced"] == "PX4 telemetry bridge sends to Windows host 172.23.80.1, expected 172.29.0.1"


def test_a_mismatched_bridge_that_cannot_be_restarted_says_exactly_what_is_wrong(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(_manager.os, "getuid", lambda: 1000, raising=False)
    _fake_proc(tmp_path, 321, "python3", str(_manager.POSE_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--forward-port", "14612")
    expected = {"forward_port": 14610, "source_port": 14611, "windows_host": "172.23.80.1"}
    forbidden = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start a second bridge beside one that is still running"))
    stuck = _manager.ensure_bridge("pose", _manager.POSE_BRIDGE_SCRIPT, [], proc_root=tmp_path, spawn=forbidden, expected=expected,
                                   terminate=lambda pid, still_running, grace_s=3.0: False)
    assert stuck["status"] == "failed" and stuck["pid"] == 321 and stuck["mismatch"] == ["Gazebo pose bridge sends to 14612, expected 14610"]
    assert stuck["detail"] == "Gazebo pose bridge sends to 14612, expected 14610. The running bridge (pid 321) could not be stopped, so it was not restarted."

    class Dead:
        pid = 778
        returncode = 1

        def poll(self):
            return 1

    def terminate(pid, still_running, grace_s=3.0):
        import shutil
        shutil.rmtree(tmp_path / str(pid))
        return True

    dead = _manager.ensure_bridge("pose", _manager.POSE_BRIDGE_SCRIPT, [], proc_root=tmp_path, settle_s=0.2, spawn=lambda *a, **k: Dead(),
                                  expected=expected, terminate=terminate)
    assert dead["status"] == "failed" and dead["detail"].startswith("Gazebo pose bridge sends to 14612, expected 14610. It was stopped but the replacement did not start: ")


def test_status_reports_a_mismatched_bridge_without_restarting_it(tmp_path):
    _fake_proc(tmp_path, 321, "python3", str(_manager.POSE_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--forward-port", "14612", "--source-port", "14611")
    _fake_proc(tmp_path, 322, "python3", str(_manager.TELEMETRY_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--forward-port", "14600", "--source-port", "14601")
    state = _manager.inspect_bridges(tmp_path)
    assert state["telemetry"] == {"status": "running", "pid": 322}
    assert state["pose"]["status"] == "mismatch" and state["pose"]["forward_port"] == 14612
    assert state["pose"]["detail"] == "Gazebo pose bridge sends to 14612, expected 14610. Activate and verify restarts it."
    assert (tmp_path / "321").exists()


def test_verification_table_names_the_bridge_problem_instead_of_an_unexplained_pending():
    from app.fleet_routes import build_activation_checks, classify_activation_failure
    rows = [{"aircraft_id": "px4-1", "verified": False, "px4_status": "connected", "gazebo_status": "not_seen"}]
    common = {"wsl_address": "172.23.85.230", "windows_host": "172.23.80.1", "gazebo_server": "running",
              "instances": [{"status": "process_present"}]}
    receivers, ips = {"px4": True, "gazebo": True}, {"px4": "172.23.85.230", "gazebo": "172.23.85.230"}
    wrong = "Gazebo pose bridge sends to 14612, expected 14610"
    stuck = {**common, "failure": f"pose bridge: {wrong}. The running bridge (pid 321) could not be stopped, so it was not restarted.", "failure_code": "bridge_start_failed",
             "bridges": {"telemetry": {"status": "running", "pid": 1, "adopted": True},
                         "pose": {"status": "failed", "pid": 321, "mismatch": [wrong], "detail": f"{wrong}. The running bridge (pid 321) could not be stopped, so it was not restarted."}}}
    checks = build_activation_checks(1, stuck, rows, receivers=receivers, configured_ips=ips)
    assert checks["pose_bridge"]["status"] == "failed" and wrong in checks["pose_bridge"]["detail"]
    assert checks["verification"]["status"] == "blocked" and checks["verification"]["detail"].startswith("0 of 1 UAV(s) have PX4 telemetry and a Gazebo pose. Cause: " + wrong)
    status, code, message = classify_activation_failure(1, stuck, checks, final=True)
    assert (status, code) == (502, "bridge_start_failed") and wrong in message

    seen = {**common, "bridges": {"telemetry": {"status": "running", "pid": 1}, "pose": {"status": "mismatch", "pid": 321, "mismatch": [wrong], "detail": f"{wrong}. Activate and verify restarts it."}}}
    checks = build_activation_checks(1, seen, rows, receivers=receivers, configured_ips=ips)
    assert checks["verification"]["status"] == "blocked" and wrong in checks["verification"]["detail"]
    assert classify_activation_failure(1, seen, checks, final=False) == (502, "bridge_port_mismatch", f"{wrong}. Activate and verify restarts it.")

    fixed = {**common, "bridges": {"telemetry": {"status": "running", "pid": 1, "adopted": True},
                                   "pose": {"status": "started", "pid": 900, "adopted": False, "restarted": True, "replaced": wrong}}}
    checks = build_activation_checks(1, fixed, rows, receivers=receivers, configured_ips=ips)
    assert checks["pose_bridge"] == {"status": "started", "detail": f"Bridge restarted by the manager because it was misconfigured: {wrong}."}
    assert checks["verification"]["status"] == "pending" and "Cause" not in checks["verification"]["detail"]      # genuinely waiting for the first pose
    assert classify_activation_failure(1, fixed, checks, final=False) is None


def test_pose_bridge_can_mirror_so_a_trial_backend_does_not_take_the_stream_away():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pose_bridge_mirror", Path(__file__).parents[1] / "tools" / "gazebo_wsl_pose_bridge.py")
    pose = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pose)
    args = pose.build_parser().parse_args(["--windows-host", "172.23.80.1", "--additional-forward-port", "14612", "--additional-forward-port", "14610"])
    assert args.forward_port == 14610 and args.additional_forward_ports == [14612, 14610]
    assert pose.forward_targets("172.23.80.1", args.forward_port, args.additional_forward_ports) == (("172.23.80.1", 14610), ("172.23.80.1", 14612))
    assert pose.forward_targets("172.23.80.1", 14610) == (("172.23.80.1", 14610),)


def test_mirror_ports_are_shown_in_the_bridge_check_without_blocking_verification():
    from app.fleet_routes import build_activation_checks
    rows = [{"aircraft_id": "px4-1", "verified": True, "px4_status": "connected", "gazebo_status": "connected"}]
    result = {"wsl_address": "172.23.85.230", "windows_host": "172.23.80.1", "gazebo_server": "running", "instances": [{"status": "process_present"}],
              "bridges": {"telemetry": {"status": "running", "pid": 1, "adopted": True, "mirror_ports": [14620]}, "pose": {"status": "running", "pid": 2, "adopted": True}}}
    checks = build_activation_checks(1, result, rows, receivers={"px4": True, "gazebo": True}, configured_ips={"px4": "172.23.85.230", "gazebo": "172.23.85.230"})
    assert checks["telemetry_bridge"] == {"status": "running", "detail": "Bridge process already running (adopted). It also mirrors to 14620 (another receiver)."}
    assert checks["verification"]["status"] == "ok"


# --- the telemetry bridge must accept every PX4 instance slot (it used to reject aircraft 6 and above) ----------------------------------

def test_the_telemetry_bridge_ports_are_read_and_an_old_five_instance_bridge_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(_manager.os, "getuid", lambda: 1000, raising=False)
    wanted = tuple(range(18570, 18570 + _manager.FLEET_CAPACITY))
    assert len(wanted) == _manager.FLEET_CAPACITY and 18575 in wanted                       # aircraft 6 (instance 5) sends from 18575
    _fake_proc(tmp_path, 40, "python3", str(_manager.TELEMETRY_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--forward-port", "14600", "--source-port", "14601")
    assert _manager.bridge_px4_ports(40, tmp_path) == _manager.OLD_BRIDGE_GCS_PORTS           # no explicit list: the old default, instances 1-5 only
    _fake_proc(tmp_path, 41, "python3", str(_manager.TELEMETRY_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--px4-source-port", "18570", "--px4-source-port=18571")
    assert _manager.bridge_px4_ports(41, tmp_path) == frozenset({18570, 18571})
    stopped, launched = [], []

    def terminate(pid, still_running, grace_s=3.0):
        stopped.append(pid)
        import shutil
        shutil.rmtree(tmp_path / str(pid))
        return not still_running()

    import shutil
    shutil.rmtree(tmp_path / "41")
    expected = {"forward_port": 14600, "source_port": 14601, "windows_host": "172.23.80.1", "px4_ports": wanted}
    argv = ["--windows-host", "172.23.80.1", "--forward-port", "14600", "--source-port", "14601", *[item for port in wanted for item in ("--px4-source-port", str(port))]]
    result = _manager.ensure_bridge("telemetry", _manager.TELEMETRY_BRIDGE_SCRIPT, argv, proc_root=tmp_path, settle_s=0.2, spawn=_spawn_recorder(launched), expected=expected, terminate=terminate)
    assert stopped == [40] and result["restarted"] is True and "accepts only PX4 instances 1-5" in result["replaced"]
    assert launched[0][2:] == argv and launched[0].count("--px4-source-port") == len(wanted)
    # a bridge that already accepts every slot is kept
    _fake_proc(tmp_path, 42, "python3", str(_manager.TELEMETRY_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1", "--forward-port", "14600", "--source-port", "14601",
               *[item for port in wanted for item in ("--px4-source-port", str(port))])
    kept = _manager.ensure_bridge("telemetry", _manager.TELEMETRY_BRIDGE_SCRIPT, argv, proc_root=tmp_path, expected=expected,
                                  spawn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("a bridge that accepts every instance is kept")))
    assert kept["status"] == "running" and kept["pid"] == 42
