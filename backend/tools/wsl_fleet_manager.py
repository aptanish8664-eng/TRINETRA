"""Allowlisted WSL-side PX4 SITL fleet lifecycle helper for TRINETRA.

This process starts/stops simulation servers only. It never sends flight commands.
The Windows API invokes this fixed script with validated numeric/instance args.

Simulation home: PX4's own startup script (`px4-rc.gzsim`) sets the Gazebo world origin through the
`/world/<name>/set_spherical_coordinates` service when PX4_HOME_LAT, PX4_HOME_LON and PX4_HOME_ALT are all set.
This helper only sets those three environment variables (range-checked numbers) for an instance it starts; the
PX4 source tree and the world file are never edited. Without them the stock world origin is used.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any
import uuid

DEFAULT_PX4_ROOT = Path("/home/apt8055/uav-sim/PX4-Autopilot")
# Technical ceiling of instance slots this tool addresses. How many aircraft this machine RUNS is a policy set and enforced in the backend (config.SIM_MAX_AIRCRAFT).
FLEET_CAPACITY = 16
MODEL = "gz_x500"


def _proc_info(pid_dir: Path, px4_binary: Path) -> dict[str, Any] | None:
    try:
        executable = Path(os.readlink(pid_dir / "exe")).resolve()
        if executable != px4_binary.resolve():
            return None
        environment = (pid_dir / "environ").read_bytes().split(b"\0")
        env = dict(item.decode("utf-8", "ignore").split("=", 1) for item in environment if b"=" in item)
        if env.get("PX4_SIM_MODEL") != MODEL:
            return None
        args = (pid_dir / "cmdline").read_bytes().decode("utf-8", "ignore").split("\0")
        args = [item for item in args if item]
        instance = 0
        if "-i" in args:
            index = args.index("-i")
            if index + 1 >= len(args) or not args[index + 1].isdigit():
                return None
            instance = int(args[index + 1])
        return {"pid": int(pid_dir.name), "instance": instance}
    except (OSError, ValueError, PermissionError):
        return None


def discover_px4_instances(px4_root: Path, proc_root: Path = Path("/proc")) -> dict[int, int]:
    """Return instance->PID for PX4 processes from this checkout running gz_x500."""
    binary = px4_root / "build/px4_sitl_default/bin/px4"
    found: dict[int, int] = {}
    if not binary.is_file():
        return found
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        info = _proc_info(entry, binary)
        if info and 0 <= info["instance"] < FLEET_CAPACITY:
            found.setdefault(info["instance"], info["pid"])
    return found


HOME_ENV = ("PX4_HOME_LAT", "PX4_HOME_LON", "PX4_HOME_ALT")
HOME_ALTITUDE_RANGE_M = (-500.0, 9000.0)


def validate_home(latitude: Any, longitude: Any, altitude_m: Any) -> dict[str, float]:
    """Range-checked simulation home. Anything else is rejected before a process is touched."""
    values = (latitude, longitude, altitude_m)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("home must be three finite numbers (latitude, longitude, altitude)")
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise ValueError("home latitude/longitude is out of range")
    if not HOME_ALTITUDE_RANGE_M[0] <= altitude_m <= HOME_ALTITUDE_RANGE_M[1]:
        raise ValueError("home altitude is out of range")
    return {"latitude": round(float(latitude), 7), "longitude": round(float(longitude), 7),
            "altitude_m": round(float(altitude_m), 2)}


def home_environment(home: dict[str, float]) -> dict[str, str]:
    """The three PX4 variables as fixed-format decimal strings (no exponent, no user text)."""
    return {"PX4_HOME_LAT": f"{home['latitude']:.7f}", "PX4_HOME_LON": f"{home['longitude']:.7f}",
            "PX4_HOME_ALT": f"{home['altitude_m']:.2f}"}


def instance_home(pid: int, proc_root: Path = Path("/proc")) -> dict[str, float] | None:
    """The home a running PX4 instance was started with (from its environment), or None for the stock world origin."""
    try:
        environment = (proc_root / str(pid) / "environ").read_bytes().split(b"\0")
        env = dict(item.decode("utf-8", "ignore").split("=", 1) for item in environment if b"=" in item)
        return validate_home(*(float(env[name]) for name in HOME_ENV))
    except (OSError, KeyError, ValueError):
        return None


def same_home(a: dict[str, float] | None, b: dict[str, float] | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return (abs(a["latitude"] - b["latitude"]) < 1e-6 and abs(a["longitude"] - b["longitude"]) < 1e-6
            and abs(a["altitude_m"] - b["altitude_m"]) < 0.01)


# --- long-range simulation profile -------------------------------------------------------------------------------------
# PX4's startup script (px4-rc via rcS) runs `param set NAME VALUE` for every environment variable PX4_PARAM_<NAME>, so a
# profile needs no edit of the PX4 checkout. Only these parameters can ever be set, each range-checked; `off` writes PX4's own
# defaults back (the instance's parameter file persists between starts, so simply not setting them would keep old values).
# SITL only: this runs only for PX4 processes this manager starts.
PROFILE_PARAMS: dict[str, tuple[float, float, float]] = {   # name: (PX4 default, minimum, maximum accepted here)
    "MPC_XY_VEL_MAX": (12.0, 1.0, 150.0),       # horizontal velocity limit in auto modes (PX4's own yaml maximum is 20)
    "MPC_XY_CRUISE": (5.0, 1.0, 150.0),         # default mission cruise speed (a mission DO_CHANGE_SPEED overrides it)
    "MPC_ACC_HOR": (3.0, 1.0, 30.0),            # horizontal acceleration in auto modes
    "MPC_ACC_HOR_MAX": (5.0, 1.0, 30.0),        # maximum horizontal acceleration
    "MPC_TILTMAX_AIR": (45.0, 20.0, 85.0),      # maximum tilt in the air
    "MPC_JERK_AUTO": (4.0, 1.0, 80.0),          # jerk in auto modes
    "COM_FLT_TIME_MAX": (-1.0, -1.0, 100000.0),  # maximum flight time failsafe (-1 = disabled)
    "GF_MAX_HOR_DIST": (0.0, 0.0, 100000.0),    # geofence horizontal distance (0 = disabled)
    "GF_MAX_VER_DIST": (0.0, 0.0, 100000.0),    # geofence vertical distance (0 = disabled)
    "SIM_BAT_DRAIN": (60.0, 1.0, 1000000.0),    # seconds to drain the simulated battery from 100% to its minimum
    "SIM_BAT_MIN_PCT": (50.0, 5.0, 100.0),      # the simulated battery's floor
    "NAV_ACC_RAD": (10.0, 1.0, 200.0),          # mission waypoint acceptance radius
}
PROFILE_BATTERY_DRAIN_S = 18000.0   # 5 h to the floor: visibly draining over a long route, never reaching a low-battery level
PROFILE_BATTERY_FLOOR_PCT = 50.0    # far above PX4's low/critical/emergency battery thresholds


def validate_profile(enabled: Any, speed_cap: Any = None, accel: Any = None, tilt: Any = None, ground_extent: Any = None) -> dict[str, Any]:
    """Range-checked profile request; raises ValueError for anything else."""
    if not isinstance(enabled, bool):
        raise ValueError("long-range profile must be on or off")
    values = {}
    for name, value, low, high in (("speed_cap_mps", speed_cap, 1.0, 150.0), ("accel_mps2", accel, 1.0, 30.0), ("tilt_deg", tilt, 20.0, 85.0),
                                   ("ground_extent_m", ground_extent, 1000.0, 20_000_000.0)):
        if value is None:
            values[name] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{name} must be a number from {low:g} to {high:g}")
        values[name] = round(float(value), 1)
    return {"enabled": enabled, **values}


def profile_environment(profile: dict[str, Any]) -> dict[str, str]:
    """PX4_PARAM_* variables for one instance: the long-range values when enabled, PX4's defaults when not."""
    cap = profile.get("speed_cap_mps")
    if profile["enabled"]:
        wanted = {
            "MPC_XY_VEL_MAX": cap or 50.0, "MPC_XY_CRUISE": min(cap or 50.0, 50.0),
            "MPC_ACC_HOR": profile.get("accel_mps2") or 10.0, "MPC_ACC_HOR_MAX": profile.get("accel_mps2") or 10.0,
            "MPC_TILTMAX_AIR": profile.get("tilt_deg") or 60.0, "MPC_JERK_AUTO": 8.0,
            "COM_FLT_TIME_MAX": -1.0, "GF_MAX_HOR_DIST": 0.0, "GF_MAX_VER_DIST": 0.0,
            "SIM_BAT_DRAIN": PROFILE_BATTERY_DRAIN_S, "SIM_BAT_MIN_PCT": PROFILE_BATTERY_FLOOR_PCT, "NAV_ACC_RAD": 25.0,
        }
    else:
        wanted = {name: spec[0] for name, spec in PROFILE_PARAMS.items()}
    environment = {}
    for name, value in wanted.items():
        default, low, high = PROFILE_PARAMS[name]
        if not low <= value <= high:
            raise ValueError(f"{name}={value:g} is outside the allowed range {low:g} to {high:g}")
        environment[f"PX4_PARAM_{name}"] = f"{value:.3f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))
    return environment


def instance_profile(pid: int, proc_root: Path = Path("/proc")) -> dict[str, Any] | None:
    """The profile a running instance was started with (from its environment), or None if the manager set none."""
    try:
        raw = (proc_root / str(pid) / "environ").read_bytes().split(b"\0")
    except OSError:
        return None
    env = dict(item.decode("utf-8", "ignore").split("=", 1) for item in raw if b"=" in item)
    values = {name[len("PX4_PARAM_"):]: env[name] for name in env if name.startswith("PX4_PARAM_") and name[len("PX4_PARAM_"):] in PROFILE_PARAMS}
    if not values:
        return None
    try:
        numbers = {name: float(value) for name, value in values.items()}
    except ValueError:
        return None
    active = numbers.get("SIM_BAT_DRAIN") == PROFILE_BATTERY_DRAIN_S  # the profile's battery setting is only ever written when it is on
    return {"active": bool(active), "speed_cap_mps": numbers.get("MPC_XY_VEL_MAX"), "accel_mps2": numbers.get("MPC_ACC_HOR"),
            "tilt_deg": numbers.get("MPC_TILTMAX_AIR"), "flight_time_failsafe": "disabled" if numbers.get("COM_FLT_TIME_MAX") == -1.0 else numbers.get("COM_FLT_TIME_MAX"),
            "geofence_distance": "disabled" if numbers.get("GF_MAX_HOR_DIST") == 0.0 else numbers.get("GF_MAX_HOR_DIST"),
            "battery_drain_s": numbers.get("SIM_BAT_DRAIN"), "battery_floor_pct": numbers.get("SIM_BAT_MIN_PCT"), "acceptance_radius_m": numbers.get("NAV_ACC_RAD")}


def same_profile(running: dict[str, Any] | None, requested: dict[str, Any] | None) -> bool:
    if requested is None:
        return True
    expected = profile_environment(requested)
    wanted = {name[len("PX4_PARAM_"):]: float(value) for name, value in expected.items()}
    if running is None:
        return False
    mapping = {"MPC_XY_VEL_MAX": "speed_cap_mps", "MPC_ACC_HOR": "accel_mps2", "MPC_TILTMAX_AIR": "tilt_deg", "SIM_BAT_DRAIN": "battery_drain_s"}
    return all(abs((running.get(key) or 0.0) - wanted[name]) < 1e-6 for name, key in mapping.items())


# --- runtime Gazebo world (a wider ground) -----------------------------------------------------------------------------
# The stock `default` world's ground is a `<plane>` collision. Measured on this checkout: an aircraft flown 3 km from the origin
# has no ground under it (a landing there descended to -65 m in Gazebo and never touched down), and enlarging the plane's
# `<size>` to 50 km or 4,000 km changed nothing, so the plane's size is not honoured by the physics engine. The profile therefore
# starts Gazebo ITSELF from a runtime copy of the world whose ground collision is an explicit wide static box with its top at
# z = 0; PX4 then attaches to the running world exactly as it does when Gazebo was started by hand (px4-rc.gzsim: "gazebo already
# running world"). The PX4 checkout and its world files are not touched.
WORLD_NAME = "default"
GROUND_COLLISION_PATTERN = re.compile(r'(<collision name="collision">)\s*<geometry>\s*<plane>\s*<normal>0 0 1</normal>\s*<size>1 1</size>\s*</plane>\s*</geometry>')
GROUND_VISUAL_SIZE = "<size>500 500</size>"
VISUAL_EXTENT_M = 20000.0
GROUND_THICKNESS_M = 100.0


def world_runtime_dir(create: bool = True) -> Path:
    path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"trinetra-gz-world-{getattr(os, 'getuid', lambda: 0)()}"
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def build_runtime_world(px4_root: Path, extent_m: float, target_dir: Path | None = None) -> Path:
    """Write the runtime world with a wide ground collision. Refuses to guess if the stock file is not as expected."""
    if isinstance(extent_m, bool) or not isinstance(extent_m, (int, float)) or not math.isfinite(extent_m) or not 1000.0 <= extent_m <= 20_000_000.0:
        raise ValueError("ground extent must be a number from 1000 to 20000000 metres")
    source = px4_root / "Tools/simulation/gz/worlds" / f"{WORLD_NAME}.sdf"
    text = source.read_text(encoding="utf-8")
    if len(GROUND_COLLISION_PATTERN.findall(text)) != 1 or text.count(GROUND_VISUAL_SIZE) != 1 or f'<world name="{WORLD_NAME}">' not in text:
        raise RuntimeError("the PX4 default world does not have the expected ground plane, so it was not widened")
    box = (f"\\1<pose>0 0 {-GROUND_THICKNESS_M / 2:g} 0 0 0</pose><geometry><box><size>{extent_m:.0f} {extent_m:.0f} {GROUND_THICKNESS_M:g}</size></box></geometry>")
    text = GROUND_COLLISION_PATTERN.sub(box, text).replace(GROUND_VISUAL_SIZE, f"<size>{VISUAL_EXTENT_M:.0f} {VISUAL_EXTENT_M:.0f}</size>")
    directory = target_dir or world_runtime_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / f"{WORLD_NAME}.sdf"
    target.write_text(text, encoding="utf-8")
    (directory / "world.json").write_text(json.dumps({"ground_extent_m": float(extent_m)}), encoding="utf-8")
    return target


def runtime_world_extent(proc_root: Path = Path("/proc")) -> float | None:
    """The ground extent of the running Gazebo server if it was started from the runtime world, else None."""
    for pid in discover_gazebo_processes_any(proc_root):
        words = " ".join(_cmdline(proc_root / str(pid))).split()
        if "-s" in words and any(word.startswith(str(world_runtime_dir(create=False))) for word in words):
            try:
                return float(json.loads((world_runtime_dir() / "world.json").read_text(encoding="utf-8"))["ground_extent_m"])
            except (OSError, ValueError, KeyError):
                return None
    return None


def discover_gazebo_processes_any(proc_root: Path = Path("/proc")) -> list[int]:
    found = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if entry.name.isdigit():
            words = " ".join(_cmdline(entry)).split()
            if any(Path(item).name == "gz" for item in words[:2]) and "sim" in words and "-s" in words:
                found.append(int(entry.name))
    return found


def start_gazebo_with_world(px4_root: Path, world: Path, *, headless: bool = False, proc_root: Path = Path("/proc"), timeout_s: float = 30.0,
                            spawn=subprocess.Popen) -> dict[str, Any]:
    """Start the Gazebo server (and the GUI) from the runtime world, with the environment px4-rc.gzsim would use."""
    gz_env = px4_root / "build/px4_sitl_default/gz_env.sh"
    if not gz_env.is_file():
        return {"status": "failed", "detail": "gz_env.sh of the PX4 build is missing"}
    env = os.environ.copy()
    env["GZ_IP"] = "127.0.0.1"
    log = _log_path("gazebo-runtime")
    script = '. "$1"; exec gz sim --verbose=1 -r -s "$2"'
    with log.open("ab") as handle:
        server = spawn(["sh", "-c", script, "sh", str(gz_env), str(world)], env=env, stdin=subprocess.DEVNULL, stdout=handle,
                       stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if server.poll() is not None:
            return {"status": "failed", "detail": _last_log_line(log) or f"gz exited with code {server.returncode}"}
        if discover_gazebo_processes(px4_root, proc_root, any_world=True)["server"]:
            break
        time.sleep(0.3)
    if not headless:
        with log.open("ab") as handle:
            spawn(["sh", "-c", '. "$1"; exec gz sim -g', "sh", str(gz_env)], env=env, stdin=subprocess.DEVNULL, stdout=handle,
                  stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(2.0)
    return {"status": "started", "pid": server.pid, "world": str(world)}


TOOLS_DIR = Path(__file__).resolve().parent
TELEMETRY_BRIDGE_SCRIPT = TOOLS_DIR / "px4_wsl_udp_bridge.py"
POSE_BRIDGE_SCRIPT = TOOLS_DIR / "gazebo_wsl_pose_bridge.py"
DEFAULT_PORTS = {"telemetry_forward": 14600, "telemetry_source": 14601,
                 "pose_forward": 14610, "pose_source": 14611}


def windows_host_address(route_file: Path = Path("/proc/net/route")) -> str | None:
    """Return the WSL default-gateway (Windows host) IPv4 address from the kernel route table."""
    try:
        lines = route_file.read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "00000000" and int(fields[3], 16) & 2:
            try:
                return str(ipaddress.IPv4Address(int.from_bytes(bytes.fromhex(fields[2]), "little")))
            except ValueError:
                return None
    return None


def wsl_source_address(host: str | None) -> str | None:
    """The local address the kernel would use toward the Windows host (UDP connect sends nothing)."""
    if not host:
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host, 9))
            return probe.getsockname()[0]
    except OSError:
        return None


def _cmdline(entry: Path) -> list[str]:
    try:
        return [item for item in (entry / "cmdline").read_bytes().decode("utf-8", "ignore").split("\0") if item]
    except OSError:
        return []


def discover_script_process(script: Path, proc_root: Path = Path("/proc")) -> int | None:
    """PID of a running python process executing exactly this script, if any."""
    target = script.resolve()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        args = _cmdline(entry)
        if len(args) >= 2 and "python" in Path(args[0]).name:
            try:
                if Path(args[1]).resolve() == target:
                    return int(entry.name)
            except OSError:
                continue
    return None


def gazebo_server_running(proc_root: Path = Path("/proc")) -> bool:
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        # Ruby-launched `gz` rewrites its process title, so argv may arrive as one space-joined token.
        words = " ".join(_cmdline(entry)).split()
        if any(Path(item).name == "gz" for item in words[:2]) and "sim" in words and "-s" in words:
            return True
    return False


def discover_gazebo_processes(px4_root: Path, proc_root: Path = Path("/proc"), any_world: bool = False) -> dict[str, list[int]]:
    """PIDs of the Gazebo server started from this PX4 checkout's worlds (or the runtime world), and of `gz sim -g` GUI clients."""
    worlds = str((px4_root / "Tools/simulation/gz/worlds").resolve())
    runtime = str(world_runtime_dir(create=False))
    found: dict[str, list[int]] = {"server": [], "gui": []}
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        words = " ".join(_cmdline(entry)).split()
        if not (any(Path(item).name == "gz" for item in words[:2]) and "sim" in words):
            continue
        if "-s" in words and any(item.startswith(worlds) or item.startswith(runtime) for item in words):
            found["server"].append(int(entry.name))
        elif "-g" in words and "-s" not in words:
            found["gui"].append(int(entry.name))
    return found


def _terminate(pid: int, still_running, *, grace_s: float = 5.0) -> bool:
    """SIGTERM, then SIGKILL after the grace period. Returns True once `still_running()` is False."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and still_running():
        time.sleep(0.1)
    if still_running():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and still_running():
            time.sleep(0.1)
    return not still_running()


def _last_home_path() -> Path:
    return Path(os.environ.get("TRINETRA_STATE_DIR", str(Path.home() / ".trinetra"))) / "last_home.json"


def save_last_home(home: dict[str, float] | None) -> None:
    """Persist the home the simulation was last restarted at (read back by `status`; no database, no migration)."""
    try:
        path = _last_home_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"home": home, "saved_at": time.time()}), encoding="utf-8")
    except OSError:
        pass


def load_last_home() -> dict[str, Any] | None:
    try:
        value = json.loads(_last_home_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def stop_gazebo(px4_root: Path, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    """Stop this checkout's Gazebo server and its GUI clients so the next PX4 start creates a fresh world."""
    found = discover_gazebo_processes(px4_root, proc_root)
    stopped, failed = [], []
    for kind in ("gui", "server"):
        for pid in found[kind]:
            alive = lambda pid=pid, kind=kind: pid in discover_gazebo_processes(px4_root, proc_root)[kind]
            (stopped if _terminate(pid, alive) else failed).append(pid)
    return {"stopped": stopped, "failed": failed}


def _log_path(name: str) -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"trinetra-px4-fleet-{os.getuid()}-{name}.log"


def _last_log_line(path: Path) -> str:
    try:
        lines = [line for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
        return lines[-1][:200] if lines else ""
    except OSError:
        return ""


BRIDGE_TITLES = {"telemetry": "PX4 telemetry bridge", "pose": "Gazebo pose bridge"}
# What each bridge script uses when an option is omitted from its command line.
BRIDGE_ARGUMENT_DEFAULTS = {"telemetry": {"forward_port": 14600, "source_port": 14601},
                            "pose": {"forward_port": 14610, "source_port": 14611}}


def bridge_arguments(name: str, pid: int, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    """Where a running bridge sends, read from its own command line."""
    args = _cmdline(proc_root / str(pid))
    found: dict[str, Any] = {"windows_host": None, **BRIDGE_ARGUMENT_DEFAULTS[name], "mirror_ports": []}
    options = {"--windows-host": "windows_host", "--forward-port": "forward_port", "--source-port": "source_port",
               "--additional-forward-port": "mirror_ports"}
    index = 2
    while index < len(args):
        flag, _, inline = args[index].partition("=")
        key = options.get(flag)
        if key is None:
            index += 1
            continue
        value = inline if inline else (args[index + 1] if index + 1 < len(args) else "")
        index += 1 if inline else 2
        if key == "windows_host":
            found[key] = value
            continue
        try:
            port: int | None = int(value)
        except ValueError:
            port = None            # unreadable: it cannot equal the expected port, so it is reported
        if key == "mirror_ports":
            if port is not None:
                found[key].append(port)
        else:
            found[key] = port
    found["mirror_ports"] = sorted({port for port in found["mirror_ports"] if port != found["forward_port"]})
    return found


PX4_GCS_PORTS = tuple(range(18570, 18570 + FLEET_CAPACITY))      # PX4 instance N sends its GCS telemetry from 18570 + N; the bridge must accept every instance slot
OLD_BRIDGE_GCS_PORTS = frozenset(range(18570, 18575))              # what a bridge started without --px4-source-port accepted before: instances 1-5 only


def bridge_px4_ports(pid: int, proc_root: Path = Path("/proc")) -> frozenset[int]:
    """The PX4 source ports a running telemetry bridge accepts (from its command line; none given = the old default of five instances)."""
    args = _cmdline(proc_root / str(pid))
    ports = set()
    for index, item in enumerate(args):
        flag, _, inline = item.partition("=")
        if flag == "--px4-source-port":
            value = inline if inline else (args[index + 1] if index + 1 < len(args) else "")
            if value.isdigit():
                ports.add(int(value))
    return frozenset(ports) if ports else OLD_BRIDGE_GCS_PORTS


def bridge_mismatches(name: str, actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Plain statements of how a running bridge differs from the one the backend expects (empty when it matches)."""
    title = BRIDGE_TITLES[name]
    problems = []
    # A mirror port is a deliberate extra receiver (a trial backend): it is reported, but only a bridge that does not
    # send to the expected port at all is a mismatch.
    if expected["forward_port"] not in {actual["forward_port"], *actual["mirror_ports"]}:
        sends_to = " and ".join(str(port) for port in [actual["forward_port"], *actual["mirror_ports"]])
        problems.append(f"{title} sends to {sends_to}, expected {expected['forward_port']}")
    if actual["source_port"] != expected["source_port"]:
        problems.append(f"{title} sends from source port {actual['source_port']}, expected {expected['source_port']}")
    if expected.get("windows_host") and actual["windows_host"] != expected["windows_host"]:
        problems.append(f"{title} sends to Windows host {actual['windows_host']}, expected {expected['windows_host']}")
    return problems


def process_start_ticks(pid: int, proc_root: Path = Path("/proc")) -> int | None:
    """When a process started, in clock ticks since boot (field 22 of /proc/<pid>/stat). None when it cannot be read."""
    try:
        text = (proc_root / str(pid) / "stat").read_text(encoding="ascii", errors="replace")
        return int(text.rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def ensure_bridge(name: str, script: Path, argv: list[str], *, env: dict[str, str] | None = None,
                  proc_root: Path = Path("/proc"), settle_s: float = 1.5,
                  spawn=subprocess.Popen, expected: dict[str, Any] | None = None, terminate=None,
                  must_start_after: int | None = None) -> dict[str, Any]:
    """Adopt a running bridge for this script when it sends where it should, otherwise replace it; start one if none runs.

    `expected` holds the forward port, source port and Windows host the backend listens for. A running bridge that
    differs (left over from a trial with other ports, or from before the Windows host address changed) is stopped and
    started again. If that is not possible the result says exactly what is wrong.
    """
    pid = discover_script_process(script, proc_root)
    replaced = None
    if pid is not None:
        actual = bridge_arguments(name, pid, proc_root)
        problems = bridge_mismatches(name, actual, expected) if expected else []
        if expected and expected.get("px4_ports") and name == "telemetry":
            accepted = bridge_px4_ports(pid, proc_root)
            if not set(expected["px4_ports"]) <= accepted:
                problems.append(f"{BRIDGE_TITLES[name]} accepts only PX4 instances 1-{len(accepted)}, so aircraft above that are rejected; it needs 1-{len(expected['px4_ports'])}")
        if must_start_after is not None:
            started = process_start_ticks(pid, proc_root)
            if started is not None and started < must_start_after:
                problems.append(f"{BRIDGE_TITLES[name]} started before the current Gazebo server, so it no longer receives its poses")
        if not problems:
            adopted: dict[str, Any] = {"status": "running", "pid": pid, "adopted": True}
            if expected and actual["mirror_ports"]:
                adopted["mirror_ports"] = actual["mirror_ports"]      # informational: it also feeds another receiver
            return adopted
        replaced = "; ".join(problems)
        stopped = (terminate or _terminate)(pid, lambda: (proc_root / str(pid)).exists(), grace_s=3.0)
        if not stopped:
            return {"status": "failed", "pid": pid, "mismatch": problems,
                    "detail": f"{replaced}. The running bridge (pid {pid}) could not be stopped, so it was not restarted."}
    if not script.is_file():
        return {"status": "failed", "detail": "bridge script is missing"}
    log = _log_path(f"{name}-bridge")
    prefix = f"{replaced}. It was stopped but the replacement did not start: " if replaced else ""
    try:
        with log.open("ab") as handle:
            process = spawn([sys.executable, str(script), *argv], env=env, stdin=subprocess.DEVNULL,
                            stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        return {"status": "failed", "detail": f"{prefix}bridge could not be launched ({type(exc).__name__})"}
    deadline = time.monotonic() + settle_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return {"status": "failed", "detail": prefix + (_last_log_line(log) or f"bridge exited with code {process.returncode}")}
        time.sleep(0.1)
    started: dict[str, Any] = {"status": "started", "pid": process.pid, "adopted": False}
    if replaced:
        started.update(restarted=True, replaced=replaced)
    return started


def inspect_bridges(proc_root: Path = Path("/proc"), ports: dict[str, int] | None = None) -> dict[str, dict[str, Any]]:
    """Read-only bridge state. A running bridge that sends somewhere unexpected is reported as `mismatch` with the reason."""
    wanted = {**DEFAULT_PORTS, **(ports or {})}
    host = windows_host_address()
    result = {}
    for name, script in (("telemetry", TELEMETRY_BRIDGE_SCRIPT), ("pose", POSE_BRIDGE_SCRIPT)):
        pid = discover_script_process(script, proc_root)
        if not pid:
            result[name] = {"status": "not_running"}
            continue
        actual = bridge_arguments(name, pid, proc_root)
        problems = bridge_mismatches(name, actual, {"forward_port": wanted[f"{name}_forward"], "source_port": wanted[f"{name}_source"],
                                                    "windows_host": host})
        if problems:
            result[name] = {"status": "mismatch", "pid": pid, "mismatch": problems, "forward_port": actual["forward_port"],
                            "source_port": actual["source_port"], "mirror_ports": actual["mirror_ports"],
                            "detail": "; ".join(problems) + ". Activate and verify restarts it."}
        else:
            result[name] = {"status": "running", "pid": pid}
            extra = [port for port in [actual["forward_port"], *actual["mirror_ports"]] if port != wanted[f"{name}_forward"]]
            if extra:
                result[name]["mirror_ports"] = extra                 # informational: it also feeds another receiver
    return result


def ensure_bridges(existing: dict[int, int], ports: dict[str, int], *,
                   proc_root: Path = Path("/proc")) -> dict[str, dict[str, Any]]:
    host = windows_host_address()
    if host is None:
        gone = {"status": "failed", "detail": "the Windows host address could not be determined from the WSL route table"}
        return {"telemetry": dict(gone), "pose": dict(gone)}
    env = os.environ.copy()
    for pid in existing.values():  # reuse the running simulation's Gazebo transport address, if it set one
        try:
            for item in (proc_root / str(pid) / "environ").read_bytes().split(b"\0"):
                if item.startswith(b"GZ_IP="):
                    env["GZ_IP"] = item.split(b"=", 1)[1].decode("ascii", "ignore")
        except OSError:
            pass
        break
    telemetry = ensure_bridge("telemetry", TELEMETRY_BRIDGE_SCRIPT, [
        "--windows-host", host, "--forward-port", str(ports["telemetry_forward"]),
        "--source-port", str(ports["telemetry_source"]), *[item for port in PX4_GCS_PORTS for item in ("--px4-source-port", str(port))]], env=env, proc_root=proc_root,
        expected={"forward_port": ports["telemetry_forward"], "source_port": ports["telemetry_source"], "windows_host": host, "px4_ports": PX4_GCS_PORTS})
    servers = [ticks for ticks in (process_start_ticks(item, proc_root) for item in discover_gazebo_processes_any(proc_root)) if ticks is not None]
    pose = ensure_bridge("pose", POSE_BRIDGE_SCRIPT, [
        "--windows-host", host, "--world", "default", "--forward-port", str(ports["pose_forward"]),
        "--source-port", str(ports["pose_source"])], env=env, proc_root=proc_root,
        expected={"forward_port": ports["pose_forward"], "source_port": ports["pose_source"], "windows_host": host},
        must_start_after=max(servers) if servers else None)      # a pose bridge older than the Gazebo server keeps a dead subscription
    return {"telemetry": telemetry, "pose": pose}


def _state_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"trinetra-px4-fleet-{os.getuid()}.json"


def _load_owned(path: Path) -> dict[int, int]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {int(key): int(pid) for key, pid in raw.items() if str(key).isdigit() and int(pid) > 0}
    except (OSError, ValueError, TypeError):
        return {}


def _save_owned(path: Path, owned: dict[int, int]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({str(key): pid for key, pid in sorted(owned.items())}), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def resource_preflight(requested_count: int, existing_count: int, *,
                       available_memory_kib: int, cpu_count: int) -> tuple[bool, str | None]:
    missing = max(0, requested_count - existing_count)
    if missing == 0:
        return True, None
    if cpu_count < 2:
        return False, "at least two logical CPUs are required to start PX4/Gazebo SITL"
    # Reserve 1 GiB for the shared Gazebo/runtime and 512 MiB per new SITL.
    required_kib = 1024 * 1024 + missing * 512 * 1024
    if available_memory_kib < required_kib:
        return False, f"insufficient available memory for {missing} additional SITL instance(s)"
    return True, None


def _available_memory_kib() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    raise RuntimeError("Linux MemAvailable resource data is unavailable")


def _spawn(px4_root: Path, instance: int, home: dict[str, float] | None = None, profile: dict[str, Any] | None = None) -> subprocess.Popen:
    build = px4_root / "build/px4_sitl_default"
    binary = build / "bin/px4"
    rootfs = build / "etc"
    if not binary.is_file() or not rootfs.is_dir():
        raise RuntimeError("the PX4 SITL build or rootfs is missing")
    instance_dir = build / f"instance_{instance}"
    instance_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "PX4_SIM_MODEL": MODEL,
        "PX4_GZ_WORLD": "default",
        "PX4_GZ_MODEL_POSE": f"0,{instance * 3.0},0,0,0,0",
        "GZ_IP": "127.0.0.1",
    })
    for name in HOME_ENV:  # never inherit a home from the caller's shell; it is explicit or absent
        env.pop(name, None)
    if home is not None:
        env.update(home_environment(home))
    for name in [name for name in env if name.startswith("PX4_PARAM_")]:
        env.pop(name)  # never inherit parameter overrides from the caller's shell
    if profile is not None:
        env.update(profile_environment(profile))
    if instance > 0:
        env["PX4_GZ_NO_FOLLOW"] = "1"
    command = [str(binary), "-i", str(instance), "-d", str(rootfs)]
    with (instance_dir / "trinetra-fleet.log").open("ab") as log:
        return subprocess.Popen(command, cwd=instance_dir, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def _environment_report(proc_root: Path = Path("/proc")) -> dict[str, Any]:
    host = windows_host_address()
    return {"wsl_address": wsl_source_address(host), "windows_host": host,
            "gazebo_server": "running" if gazebo_server_running(proc_root) else "not_running"}


def activate_fleet(count: int, *, px4_root: Path = DEFAULT_PX4_ROOT,
                   proc_root: Path = Path("/proc"), timeout_s: float = 18.0,
                   ports: dict[str, int] | None = None,
                   home: dict[str, float] | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Adopt running instances and start the missing ones. `home` applies only to instances started here:
    a running instance is never restarted, and a different home is reported as `home_mismatch`."""
    if not 1 <= count <= FLEET_CAPACITY:
        raise ValueError(f"requested count must be between 1 and {FLEET_CAPACITY}")
    if home is not None:
        home = validate_home(home["latitude"], home["longitude"], home["altitude_m"])
    root = px4_root.resolve()
    existing = discover_px4_instances(root, proc_root)
    gazebo_note: dict[str, Any] | None = None
    if (profile is not None and profile["enabled"] and profile.get("ground_extent_m") and not discover_gazebo_processes(root, proc_root)["server"]
            and sum(instance in existing for instance in range(count)) < count):
        try:
            gazebo_note = start_gazebo_with_world(root, build_runtime_world(root, profile["ground_extent_m"]), headless=True, proc_root=proc_root)   # server only: the GUI is opened by hand (gz sim -g)
        except (OSError, RuntimeError, ValueError) as exc:
            gazebo_note = {"status": "failed", "detail": str(exc)}
    enough_resources, resource_error = resource_preflight(
        count, sum(instance in existing for instance in range(count)),
        available_memory_kib=_available_memory_kib(),
        cpu_count=os.cpu_count() or 0,
    )
    if not enough_resources:
        return {"requested_count": count, "process_count": 0, "instances": [],
                "failure": resource_error, "failure_code": "insufficient_resources",
                "bridges": inspect_bridges(proc_root), **_environment_report(proc_root),
                "manager": "px4_wsl_fleet_v1"}
    owned_path = _state_path()
    owned = _load_owned(owned_path)
    results = []
    failure = None
    for instance in range(count):
        if instance in existing:
            results.append({"instance": instance, "system_id": instance + 1,
                            "aircraft_id": f"px4-{instance + 1}", "gazebo_model": f"x500_{instance}",
                            "pid": existing[instance], "status": "process_present",
                            "home": instance_home(existing[instance], proc_root), "profile": instance_profile(existing[instance], proc_root)})
            continue
        try:
            process = _spawn(root, instance, home, profile)
            deadline = time.monotonic() + timeout_s
            pid = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                discovered = discover_px4_instances(root, proc_root)
                pid = discovered.get(instance)
                if pid is not None:
                    break
                time.sleep(0.25)
            if pid is None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                raise RuntimeError(f"PX4 instance {instance} did not remain running after startup")
            owned[instance] = pid
            _save_owned(owned_path, owned)
            results.append({"instance": instance, "system_id": instance + 1,
                            "aircraft_id": f"px4-{instance + 1}", "gazebo_model": f"x500_{instance}",
                            "pid": pid, "status": "process_started", "home": home,
                            "profile": instance_profile(pid, proc_root)})
        except (OSError, RuntimeError) as exc:
            failure = str(exc)
            break
    failure_code = "sitl_start_failed" if failure else None
    if failure:
        bridges = inspect_bridges(proc_root, ports)  # never start bridges for a partially failed activation
    else:
        bridges = ensure_bridges(discover_px4_instances(root, proc_root) or existing,
                                 {**DEFAULT_PORTS, **(ports or {})}, proc_root=proc_root)
        failed = [f"{name} bridge: {item.get('detail', 'failed')}" for name, item in bridges.items()
                  if item["status"] == "failed"]
        if failed:
            failure, failure_code = "; ".join(failed), "bridge_start_failed"
    mismatch = [item["aircraft_id"] for item in results
                if home is not None and item["status"] == "process_present" and not same_home(item.get("home"), home)]
    return {"requested_count": count, "process_count": len(results),
            "instances": results, "failure": failure, "failure_code": failure_code,
            "home_requested": home, "home_mismatch": mismatch,
            "profile_requested": profile, "ground_extent_m": runtime_world_extent(proc_root), "gazebo_world": gazebo_note,
            "profile_mismatch": [item["aircraft_id"] for item in results
                                                               if profile is not None and item["status"] == "process_present" and not same_profile(item.get("profile"), profile)],
            "bridges": bridges, **_environment_report(proc_root), "manager": "px4_wsl_fleet_v1"}


def relocate_fleet(count: int, home: dict[str, float] | None, *, px4_root: Path = DEFAULT_PX4_ROOT,
                   proc_root: Path = Path("/proc"), ports: dict[str, int] | None = None,
                   profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stop the managed PX4 instances and this checkout's Gazebo, then start `count` instances at `home`.

    `home=None` restores the stock world origin. Instances this manager did not start are never stopped: the
    request is refused before anything is touched. No flight command is sent at any point. If a step fails the
    simulation is simply stopped or partly started; `activate` recovers it.
    """
    if not 1 <= count <= FLEET_CAPACITY:
        raise ValueError(f"requested count must be between 1 and {FLEET_CAPACITY}")
    if home is not None:
        home = validate_home(home["latitude"], home["longitude"], home["altitude_m"])
    root = px4_root.resolve()
    existing = discover_px4_instances(root, proc_root)
    owned_path = _state_path()
    owned = _load_owned(owned_path)
    report: dict[str, Any] = {"requested_count": count, "home_requested": home, "relocated": False,
                              "stopped_instances": [], "gazebo_stopped": [], "manager": "px4_wsl_fleet_v1"}

    def refuse(code: str, message: str) -> dict[str, Any]:
        return {**report, "process_count": 0, "instances": [], "failure": message, "failure_code": code,
                "bridges": inspect_bridges(proc_root), **_environment_report(proc_root)}

    foreign = sorted(instance for instance, pid in existing.items() if owned.get(instance) != pid)
    if foreign:
        return refuse("instance_not_managed", (
            f"PX4 instance(s) {', '.join(str(item) for item in foreign)} were not started by the TRINETRA fleet manager, "
            "so they were left running. Stop them in WSL, then try again."))
    for instance, pid in sorted(existing.items()):
        alive = lambda instance=instance, pid=pid: discover_px4_instances(root, proc_root).get(instance) == pid
        if not _terminate(pid, alive):
            _save_owned(owned_path, owned)
            return refuse("stop_failed", f"PX4 instance {instance} could not be stopped; nothing was restarted.")
        owned.pop(instance, None)
        report["stopped_instances"].append(instance)
    _save_owned(owned_path, owned)
    gazebo = stop_gazebo(root, proc_root)
    report["gazebo_stopped"] = gazebo["stopped"]
    if gazebo["failed"]:
        return refuse("stop_failed", "The Gazebo server could not be stopped; PX4 is stopped and nothing was restarted. "
                                     "Activate the fleet to start it again.")
    time.sleep(1.0)  # let the Gazebo transport endpoints disappear before PX4 looks for a running world
    result = activate_fleet(max(count, len(existing)), px4_root=px4_root, proc_root=proc_root, ports=ports, home=home, profile=profile)
    if not result.get("failure"):
        save_last_home(home)
    return {**result, "relocated": not result.get("failure"), "stopped_instances": report["stopped_instances"],
            "gazebo_stopped": report["gazebo_stopped"], "last_home": load_last_home()}


def stop_managed(instances: list[int], *, px4_root: Path = DEFAULT_PX4_ROOT,
                 proc_root: Path = Path("/proc")) -> dict[str, Any]:
    if not instances or len(set(instances)) != len(instances) or any(i < 0 or i >= FLEET_CAPACITY for i in instances):
        raise ValueError("stop requires unique managed instance numbers within configured capacity")
    path = _state_path()
    owned = _load_owned(path)
    discovered = discover_px4_instances(px4_root.resolve(), proc_root)
    stopped, errors = [], []
    for instance in instances:
        pid = owned.get(instance)
        if pid is None or discovered.get(instance) != pid:
            errors.append({"instance": instance, "reason": "not_owned_or_process_identity_changed"})
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and discover_px4_instances(px4_root.resolve(), proc_root).get(instance) == pid:
                time.sleep(0.1)
            if discover_px4_instances(px4_root.resolve(), proc_root).get(instance) == pid:
                os.kill(pid, signal.SIGKILL)
            owned.pop(instance, None)
            stopped.append(instance)
        except OSError as exc:
            errors.append({"instance": instance, "reason": type(exc).__name__})
    _save_owned(path, owned)
    return {"stopped_instances": stopped, "errors": errors, "manager": "px4_wsl_fleet_v1"}


# --- staged start (Part F) ---------------------------------------------------------------------------------------------------
# Why: starting Gazebo with its GUI and every PX4 instance at once makes them fight for the same CPU while the world is still loading. On a busy host the
# virtual machine gets about half its CPUs, Gazebo's real-time factor falls from 0.99 to 0.4 and the aircraft need minutes instead of seconds; a fixed
# 90 s timer then gave up while the aircraft were still coming up (docs/FLEET_CAPACITY.md has the measurements).
# What this does instead: (1) stop PX4 and Gazebo and WAIT until the processes are really gone; (2) start the Gazebo SERVER only (no GUI) and wait until the
# world answers; (3) start the PX4 instances in small batches and wait for each aircraft to be really ready (startup script done, then a valid global
# position on its onboard MAVLink link) before the next batch; (4) wait for PROGRESS, not for a fixed time: an aircraft fails only when nothing about it
# changed for STALL_S, or after PER_AIRCRAFT_MAX_S. The supervisor runs detached, writes a progress file after every change and the backend polls it.
# SITL only. The only MAVLink traffic is a HEARTBEAT from a companion-computer id on each aircraft's own onboard link; no command is ever sent.
STALL_S = 45.0                # nothing about an aircraft or Gazebo changed for this long: it failed
PER_AIRCRAFT_MAX_S = 180.0    # one aircraft may never take longer than this from its spawn
GAZEBO_MAX_S = 100.0
STOP_MAX_S = 30.0
DEFAULT_BATCH = 2             # PX4 instances started together; chosen from the measurements in docs/FLEET_CAPACITY.md
SCRIPT_DONE = b"Startup script returned successfully"
ONBOARD_SOURCE = (253, 191)   # sender system / component of the readiness heartbeat (a companion computer; the executor uses 255/190)


def _progress_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"trinetra-px4-fleet-{os.getuid()}-start-progress.json"


class StartProgress:
    """The progress file the backend polls: written atomically after every change, `last_progress_at` moves only when something really changed."""

    def __init__(self, path: Path, request_id: str | None, count: int, targets: list[int], stop_first: bool, clock=time.time):
        self.path, self.clock = path, clock
        now = clock()
        self.state: dict[str, Any] = {
            "request_id": request_id, "pid": os.getpid(), "phase": "queued", "started_at": now, "updated_at": now, "last_progress_at": now, "count": count, "targets": targets,
            "stop_first": stop_first, "stop": {"state": "pending" if stop_first else "skipped", "detail": ""}, "gazebo": {"state": "pending", "detail": ""},
            "bridges": {"state": "pending", "detail": ""}, "aircraft": {str(i): {"aircraft_id": f"px4-{i + 1}", "state": "pending", "detail": "", "since": now} for i in range(count)},
            "timings": {}, "finished": False, "failure": None, "result": None, "manager": "px4_wsl_fleet_v1"}

    def write(self) -> None:
        self.state["updated_at"] = self.clock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            pass

    def mark(self, **fields: Any) -> None:
        self.state.update(fields)
        self.state["last_progress_at"] = self.clock()
        self.write()

    def part(self, name: str, state: str, detail: str = "") -> None:
        self.state[name] = {"state": state, "detail": detail}
        self.state["last_progress_at"] = self.clock()
        self.write()

    def aircraft(self, instance: int, state: str, detail: str = "", *, progress: bool = True) -> None:
        item = self.state["aircraft"][str(instance)]
        changed = item["state"] != state
        item.update(state=state, detail=detail)
        if changed:
            item["since"] = self.clock()
            self.state["timings"][f"px4-{instance + 1}:{state}"] = round(self.clock() - self.state["started_at"], 1)
        if progress or changed:
            self.state["last_progress_at"] = self.clock()
        self.write()

    def timing(self, name: str) -> None:
        self.state["timings"].setdefault(name, round(self.clock() - self.state["started_at"], 1))


def read_start_progress(path: Path | None = None, now: float | None = None) -> dict[str, Any]:
    """What `progress` answers: the file, plus its ages measured on the WSL clock and whether the supervisor is still alive."""
    path = path or _progress_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"present": False, "manager": "px4_wsl_fleet_v1"}
    now = time.time() if now is None else now
    alive = False
    try:
        os.kill(int(state.get("pid", 0)), 0)
        alive = True
    except (OSError, ValueError, TypeError):
        pass
    if not state.get("finished") and not alive and state.get("phase") != "queued":
        state = {**state, "finished": True, "failure": state.get("failure") or {"code": "supervisor_died", "message": "The start-up supervisor stopped before it finished."}}
    return {**state, "present": True, "alive": alive, "age_s": round(now - float(state.get("updated_at", now)), 1),
            "stalled_s": round(now - float(state.get("last_progress_at", now)), 1), "elapsed_s": round(now - float(state.get("started_at", now)), 1)}


def wait_processes_gone(root: Path, proc_root: Path = Path("/proc"), *, max_s: float = STOP_MAX_S, sleep=time.sleep, clock=time.monotonic) -> dict[str, Any]:
    """Block until no PX4 instance and no Gazebo process of this checkout exists any more (killing what ignores SIGTERM). Reports what was left."""
    started = clock()
    escalated = False
    while True:
        left_px4 = discover_px4_instances(root, proc_root)
        left_gz = discover_gazebo_processes(root, proc_root, any_world=True)
        left_gazebo = left_gz["server"] + left_gz["gui"]
        if not left_px4 and not left_gazebo:
            return {"gone": True, "seconds": round(clock() - started, 1), "left": {}}
        if clock() - started > max_s:
            return {"gone": False, "seconds": round(clock() - started, 1), "left": {"px4": sorted(left_px4), "gazebo": left_gazebo}}
        if not escalated and clock() - started > max(5.0, max_s / 3):
            escalated = True
            for pid in [*left_px4.values(), *left_gazebo]:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        sleep(0.3)


def gazebo_world_answers(timeout_s: float = 6.0) -> bool:
    """True once the Gazebo world publishes its statistics (the server finished loading the world)."""
    env = dict(os.environ, GZ_IP="127.0.0.1")
    try:
        out = subprocess.run(["gz", "topic", "-e", "-t", f"/world/{WORLD_NAME}/stats", "-n", "1"], capture_output=True, text=True, timeout=timeout_s, env=env, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "sim_time" in out


class OnboardProbe:
    """Readiness of ONE aircraft read from its own onboard MAVLink link (127.0.0.1:14540+i): a heartbeat is sent so PX4 starts streaming, then position and
    estimator status are read. Passive otherwise: no command, no parameter, nothing that changes the aircraft. Closes its socket as soon as it is done."""

    def __init__(self, instance: int):
        from pymavlink import mavutil          # also used by the mission executor; absent -> the caller falls back to the log
        self.mavutil = mavutil
        self.connection = mavutil.mavlink_connection(f"udpin:127.0.0.1:{14540 + instance}", source_system=ONBOARD_SOURCE[0], source_component=ONBOARD_SOURCE[1], dialect="common")
        self.last_heartbeat = 0.0
        self.heartbeat = False
        self.position = False
        self.flags = 0

    def poll(self, clock=time.monotonic) -> tuple[bool, bool]:
        """Returns (heartbeat seen, global position valid). Also keeps the estimator flags for progress detection."""
        if clock() - self.last_heartbeat >= 1.0:
            self.last_heartbeat = clock()
            self.connection.mav.heartbeat_send(self.mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER, self.mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
        deadline = clock() + 0.2
        while clock() < deadline:
            message = self.connection.recv_match(blocking=False)
            if message is None:
                break
            kind = message.get_type()
            if kind == "HEARTBEAT" and message.get_srcSystem() != ONBOARD_SOURCE[0]:
                self.heartbeat = True
            elif kind == "GLOBAL_POSITION_INT" and (message.lat or message.lon):
                self.position = True
            elif kind == "ESTIMATOR_STATUS":
                self.flags = int(message.flags)
        return self.heartbeat, self.position

    def close(self) -> None:
        try:
            self.connection.close()
        except OSError:
            pass


class StagedStart:
    """The whole staged start as one object whose every outside effect is a replaceable callable, so the order of the steps and the waiting rules can be tested."""

    def __init__(self, root: Path, count: int, targets: list[int], progress: StartProgress, *, home: dict[str, float] | None = None, profile: dict[str, Any] | None = None,
                 ports: dict[str, int] | None = None, stop_first: bool = False, batch: int = DEFAULT_BATCH, proc_root: Path = Path("/proc"), clock=time.monotonic, sleep=time.sleep,
                 spawn=None, start_gazebo=None, gazebo_ready=None, probe_factory=None, ensure_bridges_fn=None, log_size=None, read_log=None, stop_all=None, record_owner=None):
        self.root, self.count, self.targets, self.progress = root, count, targets, progress
        self.home, self.profile, self.ports, self.stop_first, self.batch = home, profile, ports, stop_first, max(1, batch)
        self.proc_root, self.clock, self.sleep = proc_root, clock, sleep
        self.spawn = spawn or (lambda instance: _spawn(root, instance, home, profile))
        self.start_gazebo = start_gazebo or self._start_gazebo
        self.gazebo_ready = gazebo_ready or gazebo_world_answers
        self.probe_factory = probe_factory or OnboardProbe
        self.ensure_bridges = ensure_bridges_fn or (lambda: ensure_bridges({}, {**DEFAULT_PORTS, **(ports or {})}, proc_root=proc_root))
        self.read_log = read_log or self._read_log
        self.log_size = log_size or self._log_size
        self.stop_all = stop_all or self._stop_all
        self.record_owner = record_owner or self._record_owner

    # -- helpers with real effects (replaced in tests) --
    def _log_file(self, instance: int) -> Path:
        return self.root / "build/px4_sitl_default" / f"instance_{instance}" / "trinetra-fleet.log"

    def _log_size(self, instance: int) -> int:
        try:
            return self._log_file(instance).stat().st_size
        except OSError:
            return 0

    def _read_log(self, instance: int, offset: int) -> bytes:
        try:
            with self._log_file(instance).open("rb") as handle:
                handle.seek(offset)
                return handle.read(400_000)
        except OSError:
            return b""

    def _start_gazebo(self) -> dict[str, Any]:
        extent = self.profile.get("ground_extent_m") if self.profile and self.profile.get("enabled") else None
        world = build_runtime_world(self.root, extent) if extent else self.root / "Tools/simulation/gz/worlds/default.sdf"
        return start_gazebo_with_world(self.root, world, headless=True, proc_root=self.proc_root, timeout_s=30.0)

    def _record_owner(self, instance: int) -> None:
        """The PX4 instance this supervisor just started becomes one the manager owns (a later restart may stop it; a foreign instance is never touched)."""
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            pid = discover_px4_instances(self.root, self.proc_root).get(instance)
            if pid is not None:
                owned = _load_owned(_state_path())
                owned[instance] = pid
                _save_owned(_state_path(), owned)
                return
            time.sleep(0.1)

    def _stop_all(self) -> dict[str, Any]:
        existing = discover_px4_instances(self.root, self.proc_root)
        owned_path = _state_path()
        owned = _load_owned(owned_path)
        foreign = sorted(instance for instance, pid in existing.items() if owned.get(instance) != pid)
        if foreign:
            return {"refused": True, "foreign": foreign}
        for instance, pid in sorted(existing.items()):
            _terminate(pid, lambda instance=instance, pid=pid: discover_px4_instances(self.root, self.proc_root).get(instance) == pid)
            owned.pop(instance, None)
        _save_owned(owned_path, owned)
        stop_gazebo(self.root, self.proc_root)
        return {"refused": False, "stopped": sorted(existing)}

    # -- the steps --
    def run(self) -> dict[str, Any]:
        progress = self.progress
        if self.stop_first:
            progress.mark(phase="stopping")
            progress.part("stop", "running", "Stopping PX4 and Gazebo.")
            stopped = self.stop_all()
            if stopped.get("refused"):
                names = ", ".join(f"px4-{item + 1}" for item in stopped["foreign"])
                return self.finish("instance_not_managed", f"{names} was not started by the TRINETRA fleet manager, so it was left running. Stop it in WSL, then try again.")
            gone = wait_processes_gone(self.root, self.proc_root, sleep=self.sleep, clock=self.clock)
            if not gone["gone"]:
                progress.part("stop", "failed", f"Still running after {gone['seconds']:g} s: {gone['left']}")
                return self.finish("stop_failed", f"PX4 or Gazebo could not be stopped (still running: {gone['left']}); nothing was restarted.")
            progress.part("stop", "ok", f"PX4 and Gazebo are gone ({gone['seconds']:g} s).")
            progress.timing("stopped")
        running = discover_px4_instances(self.root, self.proc_root)
        for instance in range(self.count):                         # a retry of some aircraft: the ones that are not its targets and already run stay as they are
            if instance not in self.targets and instance in running:
                progress.aircraft(instance, "ready", "Already running (kept).")
        need_start = [i for i in self.targets if i not in running]
        # 1. Gazebo: server only, no GUI; wait until the world answers
        if need_start:
            if not self.ensure_gazebo():
                return self.finish("gazebo_failed", self.progress.state["gazebo"]["detail"] or "Gazebo did not become ready.")
        else:
            progress.part("gazebo", "adopted", "Every requested aircraft is already running.")
        # 2. bridges first, so telemetry flows the moment an aircraft is up
        progress.mark(phase="bridges")
        bridges = self.ensure_bridges()
        failed = [f"{name} bridge: {item.get('detail', 'failed')}" for name, item in bridges.items() if item.get("status") == "failed"]
        progress.part("bridges", "failed" if failed else "ok", "; ".join(failed) or "Telemetry and pose bridges are running.")
        if failed:
            return self.finish("bridge_start_failed", "; ".join(failed), bridges=bridges)
        # 3. aircraft, in small batches, each batch waited for until its aircraft are really ready
        progress.mark(phase="aircraft")
        for instance in self.targets:
            if instance in running:
                progress.aircraft(instance, "ready", "Already running (adopted).")
        for start in range(0, len(need_start), self.batch):
            self.run_batch(need_start[start:start + self.batch])
        return self.finish(None, None, bridges=bridges)

    def ensure_gazebo(self) -> bool:
        progress = self.progress
        progress.mark(phase="gazebo")
        if discover_gazebo_processes(self.root, self.proc_root, any_world=True)["server"] or gazebo_server_running(self.proc_root):
            progress.part("gazebo", "adopted", "Gazebo is already running; its world is checked.")
        else:
            progress.part("gazebo", "starting", "Starting the Gazebo server (no GUI).")
            try:
                note = self.start_gazebo()
            except (OSError, RuntimeError, ValueError) as error:
                progress.part("gazebo", "failed", str(error))
                return False
            if note.get("status") == "failed":
                progress.part("gazebo", "failed", str(note.get("detail", "failed")))
                return False
        started = last = self.clock()
        while True:
            if self.gazebo_ready():
                progress.part("gazebo", "ready", f"The world answers ({self.clock() - started:.0f} s).")
                progress.timing("gazebo_ready")
                return True
            alive = bool(discover_gazebo_processes(self.root, self.proc_root, any_world=True)["server"] or gazebo_server_running(self.proc_root))
            if not alive:
                progress.part("gazebo", "failed", "The Gazebo server stopped while loading the world.")
                return False
            if self.clock() - started > GAZEBO_MAX_S:
                progress.part("gazebo", "failed", f"The world did not answer within {GAZEBO_MAX_S:g} s.")
                return False
            progress.state["gazebo"]["detail"] = f"Waiting for the world ({self.clock() - started:.0f} s)."
            progress.write()
            self.sleep(1.0)

    def run_batch(self, batch: list[int]) -> None:
        progress = self.progress
        spawned: dict[int, dict[str, Any]] = {}
        for instance in batch:
            offset = self.log_size(instance)
            try:
                self.spawn(instance)
            except (OSError, RuntimeError) as error:
                progress.aircraft(instance, "failed", f"PX4 instance could not be started: {error}")
                continue
            self.record_owner(instance)
            spawned[instance] = {"offset": offset, "since": self.clock(), "script": False, "probe": None, "last_change": self.clock(), "size": offset, "flags": 0, "heartbeat": False}
            progress.aircraft(instance, "booting", "PX4 is starting.")
        while spawned:
            for instance in list(spawned):
                item = spawned[instance]
                now = self.clock()
                if instance not in discover_px4_instances(self.root, self.proc_root):
                    if now - item["since"] > 1.0:
                        progress.aircraft(instance, "failed", "PX4 exited: " + (self._last_line(instance) or "no message in its log"))
                        self.close(item); spawned.pop(instance); continue
                size = self.log_size(instance)
                if size != item["size"]:
                    item["size"], item["last_change"] = size, now
                    progress.state["last_progress_at"] = progress.clock()
                if not item["script"]:
                    if SCRIPT_DONE in self.read_log(instance, item["offset"]):
                        item["script"], item["last_change"] = True, now
                        progress.aircraft(instance, "sensors", "PX4 started; waiting for a valid position.")
                else:
                    ready = self.check_ready(instance, item)
                    if ready:
                        progress.aircraft(instance, "ready", "Valid global position on the onboard link.")
                        self.close(item); spawned.pop(instance); continue
                if now - item["last_change"] > STALL_S:
                    progress.aircraft(instance, "failed", f"Nothing changed for {STALL_S:g} s while {'waiting for a valid position' if item['script'] else 'PX4 was starting'}.")
                    self.close(item); spawned.pop(instance); continue
                if now - item["since"] > PER_AIRCRAFT_MAX_S:
                    progress.aircraft(instance, "failed", f"Not ready {PER_AIRCRAFT_MAX_S:g} s after it was started.")
                    self.close(item); spawned.pop(instance); continue
                waited = int(now - item["since"])
                progress.aircraft(instance, progress.state["aircraft"][str(instance)]["state"], f"{progress.state['aircraft'][str(instance)]['detail'].split(' (')[0]} ({waited} s)", progress=False)
            if spawned:
                self.sleep(0.5)

    def check_ready(self, instance: int, item: dict[str, Any]) -> bool:
        if item["probe"] is None:
            try:
                item["probe"] = self.probe_factory(instance)
            except (ImportError, OSError):
                item["probe"] = False                                   # no pymavlink or the port is taken (an executor runs): fall back to the log
        if item["probe"] is False:
            return self.clock() - item["last_change"] > 12.0 and self.clock() - item["since"] > 25.0
        heartbeat, position = item["probe"].poll(self.clock)
        if heartbeat and not item["heartbeat"]:
            item["heartbeat"], item["last_change"] = True, self.clock()
        if getattr(item["probe"], "flags", 0) != item["flags"]:
            item["flags"], item["last_change"] = item["probe"].flags, self.clock()   # the estimator is aligning: that is progress
        return position

    def close(self, item: dict[str, Any]) -> None:
        if item.get("probe"):
            item["probe"].close()

    def _last_line(self, instance: int) -> str:
        lines = [line for line in self.read_log(instance, max(0, self.log_size(instance) - 4000)).decode("utf-8", "ignore").splitlines() if line.strip()]
        return lines[-1][:160] if lines else ""

    def finish(self, code: str | None, message: str | None, bridges: dict[str, Any] | None = None) -> dict[str, Any]:
        progress = self.progress
        states = {i: progress.state["aircraft"][str(i)] for i in self.targets}
        ready = [i for i, item in states.items() if item["state"] == "ready"]
        missing = [i for i, item in states.items() if item["state"] != "ready"]
        if code is None and missing:
            code = "partial" if ready else "no_aircraft_ready"
            message = (f"{len(ready)} of {len(self.targets)} aircraft are ready; " if ready else "No aircraft became ready; ") + "waiting for " + ", ".join(f"px4-{i + 1}" for i in missing) + " failed or stalled."
        if ready and self.home is not None and code in (None, "partial"):
            save_last_home(self.home)
        result = {"requested_count": self.count, "process_count": len(ready), "instances": [{"instance": i, "system_id": i + 1, "aircraft_id": f"px4-{i + 1}", "gazebo_model": f"x500_{i}", "status": states[i]["state"],
                                                                                               "detail": states[i]["detail"]} for i in self.targets],
                  "ready_count": len(ready), "missing": [f"px4-{i + 1}" for i in missing], "failure": message if code and code != "partial" else None, "failure_code": code if code and code != "partial" else None,
                  "partial": code == "partial", "bridges": bridges or {}, "home_requested": self.home, "last_home": load_last_home(), "timings": dict(progress.state["timings"])}
        progress.mark(phase="done" if not code else ("partial" if code == "partial" else "failed"), finished=True, result=result,
                      failure=None if code in (None,) else {"code": code, "message": message})
        return result


def start_fleet(count: int, *, instances: list[int] | None, home: dict[str, float] | None, profile: dict[str, Any] | None, ports: dict[str, int] | None, stop_first: bool,
                batch: int, request_id: str | None, px4_root: Path = DEFAULT_PX4_ROOT, wait: bool = False) -> dict[str, Any]:
    """`start`: validate, refuse a second supervisor, write the first progress state and launch the detached supervisor. Returns at once with the progress."""
    if not 1 <= count <= FLEET_CAPACITY:
        raise ValueError(f"requested count must be between 1 and {FLEET_CAPACITY}")
    targets = sorted(set(instances)) if instances else list(range(count))
    if any(item < 0 or item >= count for item in targets):
        raise ValueError("instances must lie within the requested count")
    if home is not None:
        home = validate_home(home["latitude"], home["longitude"], home["altitude_m"])
    current = read_start_progress()
    if current.get("present") and not current.get("finished") and current.get("alive"):
        return {**current, "failure": "A start is already running.", "failure_code": "start_in_progress"}
    path = _progress_path()
    progress = StartProgress(path, request_id, count, targets, stop_first)
    progress.state["pid"] = 0
    progress.write()
    command = [sys.executable, str(Path(__file__).resolve()), "supervise", "--count", str(count), "--batch", str(batch), "--px4-root", str(px4_root)]
    if instances:
        command += ["--instances", *[str(item) for item in targets]]
    if stop_first:
        command.append("--stop-first")
    if request_id:
        command += ["--request-id", request_id]
    if home is not None:
        command += [f"--home-lat={home['latitude']}", f"--home-lon={home['longitude']}", f"--home-alt={home['altitude_m']}"]
    if profile is not None:
        command += ["--long-range-profile", "on" if profile["enabled"] else "off"]
        for key, option in (("speed_cap_mps", "--speed-cap"), ("accel_mps2", "--accel-cap"), ("tilt_deg", "--tilt-max"), ("ground_extent_m", "--ground-extent")):
            if profile.get(key) is not None:
                command.append(f"{option}={profile[key]}")
    for key, option in (("telemetry_forward", "--telemetry-forward-port"), ("telemetry_source", "--telemetry-source-port"), ("pose_forward", "--pose-forward-port"), ("pose_source", "--pose-source-port")):
        if (ports or {}).get(key) is not None:
            command += [option, str(ports[key])]
    log = _log_path("start-supervisor")
    if wait:
        subprocess.run(command, check=False, stdin=subprocess.DEVNULL)
    else:
        with log.open("ab") as handle:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(0.3)
    return {**read_start_progress(), "started": True, "manager": "px4_wsl_fleet_v1"}


def supervise(args: Any, home: dict[str, float] | None, profile: dict[str, Any] | None, ports: dict[str, int]) -> int:
    """The detached supervisor process: one at a time (a file lock), runs the staged start and leaves the result in the progress file."""
    import fcntl
    lock = open(_progress_path().with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 3
    targets = sorted(set(args.instances)) if args.instances else list(range(args.count))
    progress = StartProgress(_progress_path(), args.request_id, args.count, targets, bool(args.stop_first))
    progress.write()
    try:
        StagedStart(args.px4_root.resolve(), args.count, targets, progress, home=home, profile=profile, ports=ports, stop_first=bool(args.stop_first), batch=args.batch or DEFAULT_BATCH).run()
    except Exception as error:      # a bug must leave a readable failure, never a progress file that stays "running"
        progress.mark(phase="failed", finished=True, failure={"code": "supervisor_error", "message": f"{type(error).__name__}: {error}"})
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "activate", "stop", "relocate", "start", "progress", "supervise"))
    parser.add_argument("--count", type=int)
    parser.add_argument("--instances", type=int, nargs="*")
    parser.add_argument("--request-id")
    parser.add_argument("--stop-first", action="store_true", help="start/supervise: stop PX4 and Gazebo first (a restart) and wait until they are gone")
    parser.add_argument("--batch", type=int, choices=range(1, 17), metavar="N", help="start/supervise: PX4 instances started together (default %d)" % DEFAULT_BATCH)
    for option in ("telemetry-forward-port", "telemetry-source-port", "pose-forward-port", "pose-source-port"):
        parser.add_argument(f"--{option}", type=int, choices=range(1024, 65536), metavar="PORT")
    parser.add_argument("--home-lat", type=float)
    parser.add_argument("--home-lon", type=float)
    parser.add_argument("--home-alt", type=float)
    parser.add_argument("--long-range-profile", choices=("on", "off"), help="apply (on) or restore (off) the long-range SITL parameters on instances this manager starts")
    parser.add_argument("--speed-cap", type=float)
    parser.add_argument("--accel-cap", type=float)
    parser.add_argument("--tilt-max", type=float)
    parser.add_argument("--ground-extent", type=float, help="metres of ground collision in the runtime Gazebo world (profile on only)")
    parser.add_argument("--default-home", action="store_true",
                        help="relocate only: restart at the stock world origin instead of a given home")
    parser.add_argument("--px4-root", type=Path, default=Path(os.environ.get("TRINETRA_PX4_ROOT", str(DEFAULT_PX4_ROOT))))
    args = parser.parse_args()
    try:
        if args.request_id is not None:
            uuid.UUID(args.request_id)
        given = [value for value in (args.home_lat, args.home_lon, args.home_alt) if value is not None]
        if given and len(given) != 3:
            raise ValueError("--home-lat, --home-lon and --home-alt must be given together")
        home = validate_home(args.home_lat, args.home_lon, args.home_alt) if given else None
        profile = None
        if args.long_range_profile is not None:
            profile = validate_profile(args.long_range_profile == "on", args.speed_cap, args.accel_cap, args.tilt_max, args.ground_extent)
        elif any(value is not None for value in (args.speed_cap, args.accel_cap, args.tilt_max, args.ground_extent)):
            raise ValueError("--speed-cap, --accel-cap, --tilt-max and --ground-extent need --long-range-profile")
        if (profile is not None) and args.action not in {"activate", "relocate", "start", "supervise"}:
            raise ValueError("a profile is only accepted by activate and relocate")
        if (home is not None or args.default_home) and args.action not in {"activate", "relocate", "start", "supervise"}:
            raise ValueError("a home is only accepted by activate and relocate")
        # The backend's own receiver ports (when given): activation starts the bridges on them and status compares against them.
        ports = {key: value for key, value in {
            "telemetry_forward": args.telemetry_forward_port, "telemetry_source": args.telemetry_source_port,
            "pose_forward": args.pose_forward_port, "pose_source": args.pose_source_port,
        }.items() if value is not None}
        if args.action == "progress":
            result = read_start_progress()
        elif args.action in {"start", "supervise"}:
            if args.count is None:
                raise ValueError(f"{args.action} requires --count")
            if args.action == "start":
                result = start_fleet(args.count, instances=args.instances, home=home, profile=profile, ports=ports, stop_first=bool(args.stop_first), batch=args.batch or DEFAULT_BATCH,
                                     request_id=args.request_id, px4_root=args.px4_root)
            else:
                return supervise(args, home, profile, ports)
        elif args.action in {"activate", "relocate"}:
            if args.count is None:
                raise ValueError(f"{args.action} requires --count")
            if args.action == "relocate":
                if (home is None) != args.default_home:
                    raise ValueError("relocate requires either a home or --default-home")
                result = relocate_fleet(args.count, home, px4_root=args.px4_root, ports=ports, profile=profile)
            elif args.default_home:
                raise ValueError("--default-home is only accepted by relocate")
            else:
                result = activate_fleet(args.count, px4_root=args.px4_root, ports=ports, home=home, profile=profile)
        elif args.action == "stop":
            result = stop_managed(args.instances or [], px4_root=args.px4_root)
        else:
            found = discover_px4_instances(args.px4_root.resolve())
            owned = _load_owned(_state_path())
            result = {"ground_extent_m": runtime_world_extent(), "instances": [{"instance": key, "system_id": key + 1,
                                     "aircraft_id": f"px4-{key + 1}", "gazebo_model": f"x500_{key}",
                                     "pid": pid, "status": "process_present", "home": instance_home(pid),
                                     "profile": instance_profile(pid), "managed": owned.get(key) == pid}
                                    for key, pid in sorted(found.items())],
                      "bridges": inspect_bridges(ports=ports), **_environment_report(),
                      "manager": "px4_wsl_fleet_v1"}
        if args.request_id is not None:
            result["request_id"] = args.request_id
        print(json.dumps(result, sort_keys=True))
        return 0 if not result.get("failure") else 2
    except (OSError, RuntimeError, ValueError) as exc:
        response = {"failure": str(exc), "manager": "px4_wsl_fleet_v1"}
        if args.request_id is not None:
            response["request_id"] = args.request_id
        print(json.dumps(response, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
