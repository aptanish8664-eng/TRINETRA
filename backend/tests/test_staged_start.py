"""Part F: the staged start of several aircraft (stop until gone, Gazebo server first, PX4 in small batches, progress-based waiting, per-aircraft state).

The WSL manager's `StagedStart` is driven here with fakes for every outside effect (processes, Gazebo, the onboard MAVLink probe, the clock), so the ORDER of the steps
and the WAITING RULES are tested without WSL, PX4 or Gazebo. The live behaviour and timings are in docs/FLEET_CAPACITY.md and the run log.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("wsl_fleet_manager_staged", Path(__file__).parents[1] / "tools" / "wsl_fleet_manager.py")
assert _spec and _spec.loader
fm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fm)


class World:
    """A pretend simulation: a clock that sleep() advances, processes that appear when spawned, a Gazebo that answers after a delay, aircraft with a boot time."""

    def __init__(self, tmp_path, *, boot_s=6.0, position_s=5.0, gazebo_s=3.0):
        self.t = 0.0
        self.events = []
        self.running = {}                    # instance -> spawn time
        self.boot_s, self.position_s, self.gazebo_s = boot_s, position_s, gazebo_s
        self.gazebo_started = None
        self.never_ready, self.exits_at, self.log_text = set(), {}, {}
        self.progress_path = tmp_path / "progress.json"
        self.owners = []

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    # ---- effects ----
    def start_gazebo(self):
        self.gazebo_started = self.t
        self.events.append(("gazebo_start", self.t))
        return {"status": "started"}

    def gazebo_ready(self):
        return self.gazebo_started is not None and self.t - self.gazebo_started >= self.gazebo_s

    def spawn(self, instance):
        assert self.gazebo_ready(), "a PX4 instance was started before Gazebo was ready"
        self.running[instance] = self.t
        self.events.append(("spawn", instance, self.t))

    def log_size(self, instance):
        spawned = self.running.get(instance)
        return 0 if spawned is None else int(min(self.t - spawned, self.boot_s) * 100) + (50 if instance in self.never_ready and self.t - spawned > self.boot_s else 0)

    def read_log(self, instance, offset):
        spawned = self.running.get(instance)
        if spawned is not None and self.t - spawned >= self.boot_s:
            return b"INFO [px4] Startup script returned successfully\n"
        return self.log_text.get(instance, b"")

    def probe(self, instance):
        world = self

        class Probe:
            flags = 0

            def poll(self, clock=None):
                spawned = world.running.get(instance)
                if spawned is None or instance in world.never_ready:
                    return False, False
                since = world.t - spawned - world.boot_s
                return since >= 0.5, since >= world.position_s

            def close(self):
                pass
        return Probe()

    def discover(self, root, proc_root=None):
        return {i: 1000 + i for i, spawned in self.running.items() if self.exits_at.get(i, 10 ** 9) > self.t}


def run_staged(tmp_path, monkeypatch, world, count=5, batch=2, stop_first=False, targets=None, **extra):
    monkeypatch.setattr(fm, "discover_px4_instances", world.discover)
    monkeypatch.setattr(fm, "discover_gazebo_processes", lambda *a, **k: {"server": [1] if world.gazebo_started is not None else [], "gui": []})
    monkeypatch.setattr(fm, "gazebo_server_running", lambda *a, **k: world.gazebo_started is not None)
    monkeypatch.setattr(fm, "save_last_home", lambda home: world.events.append(("last_home", home)))
    monkeypatch.setattr(fm, "load_last_home", lambda: None)
    targets = list(range(count)) if targets is None else targets
    progress = fm.StartProgress(world.progress_path, "0" * 32, count, targets, stop_first, clock=world.clock)
    staged = fm.StagedStart(Path("/px4"), count, targets, progress, home={"latitude": 12.9, "longitude": 77.5, "altitude_m": 488.0}, stop_first=stop_first, batch=batch,
                            clock=world.clock, sleep=world.sleep, spawn=world.spawn, start_gazebo=world.start_gazebo, gazebo_ready=world.gazebo_ready,
                            probe_factory=world.probe, ensure_bridges_fn=lambda: world.events.append(("bridges", world.t)) or {"telemetry": {"status": "running"}, "pose": {"status": "running"}},
                            log_size=world.log_size, read_log=world.read_log, stop_all=lambda: world.events.append(("stop", world.t)) or {"refused": False},
                            record_owner=lambda instance: world.owners.append(instance), **extra)
    return staged.run(), progress


def kinds(world):
    return [event[0] for event in world.events]


def test_the_order_is_stop_then_gazebo_then_bridges_then_the_aircraft_in_batches(tmp_path, monkeypatch):
    world = World(tmp_path)
    monkeypatch.setattr(fm, "wait_processes_gone", lambda *a, **k: {"gone": True, "seconds": 0.4, "left": {}})
    result, progress = run_staged(tmp_path, monkeypatch, world, count=5, batch=2, stop_first=True)
    assert kinds(world)[:4] == ["stop", "gazebo_start", "bridges", "spawn"]
    spawns = [event for event in world.events if event[0] == "spawn"]
    assert [event[1] for event in spawns] == [0, 1, 2, 3, 4]
    # a batch of two starts together, and the next batch only after BOTH aircraft of the previous batch are ready (boot 6 s + position 5 s)
    assert spawns[0][2] == spawns[1][2] and spawns[2][2] == spawns[3][2] and spawns[2][2] >= spawns[0][2] + world.boot_s + world.position_s
    assert spawns[4][2] >= spawns[2][2] + world.boot_s + world.position_s
    assert result["ready_count"] == 5 and result["partial"] is False and result["failure"] is None
    assert progress.state["phase"] == "done" and progress.state["finished"] is True and progress.state["gazebo"]["state"] == "ready"
    assert world.owners == [0, 1, 2, 3, 4]                      # every instance it starts becomes one the manager owns (a later restart may stop it)
    assert ("last_home", {"latitude": 12.9, "longitude": 77.5, "altitude_m": 488.0}) in world.events


def test_a_pixhawk_instance_is_never_started_before_gazebo_answers(tmp_path, monkeypatch):
    world = World(tmp_path, gazebo_s=20.0)
    result, _ = run_staged(tmp_path, monkeypatch, world, count=2, batch=2)
    first_spawn = next(event for event in world.events if event[0] == "spawn")
    assert first_spawn[2] >= 20.0                               # World.spawn also asserts it: the aircraft waited for the world


def test_when_gazebo_never_answers_no_aircraft_is_started_and_the_reason_is_plain(tmp_path, monkeypatch):
    world = World(tmp_path, gazebo_s=10 ** 6)
    result, progress = run_staged(tmp_path, monkeypatch, world, count=3, batch=1)
    assert kinds(world).count("spawn") == 0
    assert result["failure_code"] == "gazebo_failed" and "did not answer" in result["failure"]
    assert progress.state["phase"] == "failed" and progress.state["finished"] is True
    assert all(item["state"] == "pending" for item in progress.state["aircraft"].values())     # never started: "pending", not "running"


def test_waiting_follows_progress_not_a_fixed_timer(tmp_path, monkeypatch):
    # one aircraft takes 130 s to boot, far beyond the 45 s stall window, but its log keeps growing: it is NOT failed
    world = World(tmp_path, boot_s=130.0, position_s=5.0)
    result, progress = run_staged(tmp_path, monkeypatch, world, count=1, batch=1)
    assert result["ready_count"] == 1 and result["failure"] is None
    assert progress.state["timings"]["px4-1:ready"] > fm.STALL_S


def test_an_aircraft_that_stops_changing_fails_after_the_stall_window_with_a_reason_and_the_others_continue(tmp_path, monkeypatch):
    world = World(tmp_path)
    world.never_ready = {2}                                     # px4-3 boots but never gets a position and its log stops changing
    world.boot_s = 6.0
    result, progress = run_staged(tmp_path, monkeypatch, world, count=4, batch=2)
    states = {key: item["state"] for key, item in progress.state["aircraft"].items()}
    assert states == {"0": "ready", "1": "ready", "2": "failed", "3": "ready"}
    assert result["partial"] is True and result["missing"] == ["px4-3"] and result["ready_count"] == 3 and result["failure"] is None
    assert "Nothing changed for 45 s" in progress.state["aircraft"]["2"]["detail"]
    assert progress.state["phase"] == "partial" and progress.state["failure"]["code"] == "partial"
    assert "3 of 4" in progress.state["failure"]["message"] and "px4-3" in progress.state["failure"]["message"]


def test_an_aircraft_whose_process_exits_fails_at_once_with_the_last_line_of_its_log(tmp_path, monkeypatch):
    world = World(tmp_path)
    world.exits_at = {1: 3.0}
    world.log_text = {1: b"ERROR [px4] something broke\n"}
    result, progress = run_staged(tmp_path, monkeypatch, world, count=2, batch=2)
    assert progress.state["aircraft"]["1"]["state"] == "failed" and "PX4 exited" in progress.state["aircraft"]["1"]["detail"]
    assert result["missing"] == ["px4-2"] and result["ready_count"] == 1


def test_only_the_missing_aircraft_are_started_by_a_retry_and_running_ones_are_adopted(tmp_path, monkeypatch):
    world = World(tmp_path)
    world.running = {0: 0.0, 1: 0.0}
    world.gazebo_started = -100.0
    result, progress = run_staged(tmp_path, monkeypatch, world, count=4, batch=2, targets=[2, 3])
    assert [event[1] for event in world.events if event[0] == "spawn"] == [2, 3]
    assert result["ready_count"] == 2 and progress.state["timings"].get("gazebo_ready") is not None
    assert [progress.state["aircraft"][key]["state"] for key in ("0", "1")] == ["ready", "ready"]       # the ones that were not asked for and run stay ready, not "pending"
    assert progress.state["aircraft"]["0"]["detail"] == "Already running (kept)."


def test_nothing_is_started_when_every_requested_aircraft_already_runs(tmp_path, monkeypatch):
    world = World(tmp_path)
    world.running = {0: 0.0, 1: 0.0}
    world.gazebo_started = -100.0
    result, progress = run_staged(tmp_path, monkeypatch, world, count=2, batch=2)
    assert kinds(world).count("spawn") == 0 and result["ready_count"] == 2
    assert progress.state["gazebo"]["state"] == "adopted" and all(item["detail"].startswith("Already running") for item in progress.state["aircraft"].values())


def test_a_foreign_instance_is_never_stopped_and_nothing_is_started(tmp_path, monkeypatch):
    world = World(tmp_path)
    monkeypatch.setattr(fm, "discover_px4_instances", world.discover)
    progress = fm.StartProgress(world.progress_path, None, 2, [0, 1], True, clock=world.clock)
    staged = fm.StagedStart(Path("/px4"), 2, [0, 1], progress, stop_first=True, clock=world.clock, sleep=world.sleep, stop_all=lambda: {"refused": True, "foreign": [0]},
                            spawn=world.spawn, start_gazebo=world.start_gazebo, gazebo_ready=world.gazebo_ready, ensure_bridges_fn=lambda: {}, record_owner=lambda i: None)
    result = staged.run()
    assert result["failure_code"] == "instance_not_managed" and "px4-1 was not started by the TRINETRA fleet manager" in result["failure"]
    assert kinds(world).count("spawn") == 0 and world.gazebo_started is None


# ---- the stop step really finishes -----------------------------------------------------------------------------------------------

def _fake_px4(proc, pid, instance):
    entry = proc / str(pid)
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "cmdline").write_bytes(b"/px4/build/px4_sitl_default/bin/px4\0-i\0" + str(instance).encode() + b"\0-d\0/px4/build/px4_sitl_default/etc\0")
    return entry


def test_the_stop_step_waits_until_the_processes_are_gone_and_says_what_is_left_otherwise(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    monkeypatch.setattr(fm, "discover_px4_instances", lambda root, proc_root=None: {i: p for i, p in {0: 10, 1: 11}.items() if (proc / str(p)).exists()})
    monkeypatch.setattr(fm, "discover_gazebo_processes", lambda *a, **k: {"server": [], "gui": []})
    monkeypatch.setattr(fm.os, "kill", lambda pid, sig: None)
    entries = [_fake_px4(proc, 10, 0), _fake_px4(proc, 11, 1)]
    clock = {"t": 0.0}

    def sleep(seconds):
        clock["t"] += seconds
        if clock["t"] > 2.0:                                    # the processes disappear 2 s later
            for entry in entries:
                if entry.exists():
                    (entry / "cmdline").unlink()
                    entry.rmdir()

    gone = fm.wait_processes_gone(Path("/px4"), proc, max_s=30, sleep=sleep, clock=lambda: clock["t"])
    assert gone["gone"] is True and 2.0 < gone["seconds"] < 5.0
    entries = [_fake_px4(proc, 10, 0)]
    clock["t"] = 0.0
    stuck = fm.wait_processes_gone(Path("/px4"), proc, max_s=4, sleep=lambda s: clock.__setitem__("t", clock["t"] + s), clock=lambda: clock["t"])
    assert stuck["gone"] is False and stuck["left"]["px4"] == [0]


# ---- the progress file ------------------------------------------------------------------------------------------------------------

def test_a_progress_file_whose_supervisor_died_is_reported_finished_and_failed(tmp_path):
    path = tmp_path / "p.json"
    progress = fm.StartProgress(path, None, 2, [0, 1], False)
    progress.mark(phase="aircraft")
    state = json.loads(path.read_text())
    state["pid"] = 2 ** 22 + 12345                              # no such process
    path.write_text(json.dumps(state))
    read = fm.read_start_progress(path)
    assert read["finished"] is True and read["failure"]["code"] == "supervisor_died" and read["alive"] is False
    assert fm.read_start_progress(tmp_path / "missing.json") == {"present": False, "manager": "px4_wsl_fleet_v1"}


def test_the_progress_ages_are_measured_on_the_manager_clock(tmp_path):
    path = tmp_path / "p.json"
    progress = fm.StartProgress(path, None, 1, [0], False, clock=lambda: 100.0)
    progress.mark(phase="gazebo")
    progress.state["last_progress_at"] = 100.0
    progress.write()
    read = fm.read_start_progress(path, now=130.0)
    assert read["stalled_s"] == 30.0 and read["elapsed_s"] == 30.0 and read["age_s"] == 30.0


# --- a pose bridge older than the Gazebo server is replaced ---------------------------------------------------------

def _fake_proc(root, pid, cmdline, start_ticks):
    folder = root / str(pid)
    folder.mkdir(parents=True)
    (folder / "cmdline").write_bytes(b"\0".join(item.encode() for item in cmdline) + b"\0")
    fields = ["S"] + ["0"] * 18 + [str(start_ticks)] + ["0"] * 5
    (folder / "stat").write_text(f"{pid} (python3) " + " ".join(fields), encoding="ascii")


def test_start_ticks_are_read_from_proc_stat(tmp_path):
    from tools import wsl_fleet_manager as manager
    _fake_proc(tmp_path, 7, ["python3", "x"], 4242)
    assert manager.process_start_ticks(7, tmp_path) == 4242 and manager.process_start_ticks(8, tmp_path) is None


def test_a_bridge_that_started_before_gazebo_is_stopped_and_replaced_but_a_newer_one_is_kept(tmp_path, monkeypatch):
    from tools import wsl_fleet_manager as manager
    monkeypatch.setattr(manager, "_log_path", lambda name: tmp_path / f"{name}.log")
    script = manager.POSE_BRIDGE_SCRIPT
    _fake_proc(tmp_path, 50, ["python3", str(script), "--forward-port", "14612"], 1000)
    stopped, spawned = [], []

    class Process:
        pid, returncode = 99, None
        def poll(self): return None
    kept = manager.ensure_bridge("pose", script, [], proc_root=tmp_path, must_start_after=900, spawn=lambda *a, **k: spawned.append(a) or Process(), terminate=lambda pid, running, grace_s=3.0: stopped.append(pid) or True)
    assert kept["adopted"] is True and not stopped and not spawned
    replaced = manager.ensure_bridge("pose", script, [], proc_root=tmp_path, must_start_after=2000, settle_s=0.2, spawn=lambda *a, **k: spawned.append(a) or Process(), terminate=lambda pid, running, grace_s=3.0: stopped.append(pid) or True)
    assert stopped == [50] and replaced["status"] == "started" and "before the current Gazebo server" in replaced["replaced"]
