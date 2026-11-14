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
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any
import uuid

DEFAULT_PX4_ROOT = Path("/home/apt8055/uav-sim/PX4-Autopilot")
FLEET_CAPACITY = 5
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


def discover_gazebo_processes(px4_root: Path, proc_root: Path = Path("/proc")) -> dict[str, list[int]]:
    """PIDs of the Gazebo server started from this PX4 checkout's worlds, and of `gz sim -g` GUI clients."""
    worlds = str((px4_root / "Tools/simulation/gz/worlds").resolve())
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
        if "-s" in words and any(item.startswith(worlds) for item in words):
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


def ensure_bridge(name: str, script: Path, argv: list[str], *, env: dict[str, str] | None = None,
                  proc_root: Path = Path("/proc"), settle_s: float = 1.5,
                  spawn=subprocess.Popen) -> dict[str, Any]:
    """Adopt a running bridge for this script, or start exactly one fixed bridge process."""
    pid = discover_script_process(script, proc_root)
    if pid is not None:
        return {"status": "running", "pid": pid, "adopted": True}
    if not script.is_file():
        return {"status": "failed", "detail": "bridge script is missing"}
    log = _log_path(f"{name}-bridge")
    try:
        with log.open("ab") as handle:
            process = spawn([sys.executable, str(script), *argv], env=env, stdin=subprocess.DEVNULL,
                            stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        return {"status": "failed", "detail": f"bridge could not be launched ({type(exc).__name__})"}
    deadline = time.monotonic() + settle_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return {"status": "failed", "detail": _last_log_line(log) or f"bridge exited with code {process.returncode}"}
        time.sleep(0.1)
    return {"status": "started", "pid": process.pid, "adopted": False}


def inspect_bridges(proc_root: Path = Path("/proc")) -> dict[str, dict[str, Any]]:
    result = {}
    for name, script in (("telemetry", TELEMETRY_BRIDGE_SCRIPT), ("pose", POSE_BRIDGE_SCRIPT)):
        pid = discover_script_process(script, proc_root)
        result[name] = {"status": "running", "pid": pid} if pid else {"status": "not_running"}
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
        "--source-port", str(ports["telemetry_source"])], env=env, proc_root=proc_root)
    pose = ensure_bridge("pose", POSE_BRIDGE_SCRIPT, [
        "--windows-host", host, "--world", "default", "--forward-port", str(ports["pose_forward"]),
        "--source-port", str(ports["pose_source"])], env=env, proc_root=proc_root)
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


def _spawn(px4_root: Path, instance: int, home: dict[str, float] | None = None) -> subprocess.Popen:
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
                   home: dict[str, float] | None = None) -> dict[str, Any]:
    """Adopt running instances and start the missing ones. `home` applies only to instances started here:
    a running instance is never restarted, and a different home is reported as `home_mismatch`."""
    if not 1 <= count <= FLEET_CAPACITY:
        raise ValueError(f"requested count must be between 1 and {FLEET_CAPACITY}")
    if home is not None:
        home = validate_home(home["latitude"], home["longitude"], home["altitude_m"])
    root = px4_root.resolve()
    existing = discover_px4_instances(root, proc_root)
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
                            "home": instance_home(existing[instance], proc_root)})
            continue
        try:
            process = _spawn(root, instance, home)
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
                            "pid": pid, "status": "process_started", "home": home})
        except (OSError, RuntimeError) as exc:
            failure = str(exc)
            break
    failure_code = "sitl_start_failed" if failure else None
    if failure:
        bridges = inspect_bridges(proc_root)  # never start bridges for a partially failed activation
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
            "bridges": bridges, **_environment_report(proc_root), "manager": "px4_wsl_fleet_v1"}


def relocate_fleet(count: int, home: dict[str, float] | None, *, px4_root: Path = DEFAULT_PX4_ROOT,
                   proc_root: Path = Path("/proc"), ports: dict[str, int] | None = None) -> dict[str, Any]:
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
    result = activate_fleet(max(count, len(existing)), px4_root=px4_root, proc_root=proc_root, ports=ports, home=home)
    return {**result, "relocated": not result.get("failure"), "stopped_instances": report["stopped_instances"],
            "gazebo_stopped": report["gazebo_stopped"]}


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "activate", "stop", "relocate"))
    parser.add_argument("--count", type=int)
    parser.add_argument("--instances", type=int, nargs="*")
    parser.add_argument("--request-id")
    for option in ("telemetry-forward-port", "telemetry-source-port", "pose-forward-port", "pose-source-port"):
        parser.add_argument(f"--{option}", type=int, choices=range(1024, 65536), metavar="PORT")
    parser.add_argument("--home-lat", type=float)
    parser.add_argument("--home-lon", type=float)
    parser.add_argument("--home-alt", type=float)
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
        if (home is not None or args.default_home) and args.action not in {"activate", "relocate"}:
            raise ValueError("a home is only accepted by activate and relocate")
        if args.action in {"activate", "relocate"}:
            if args.count is None:
                raise ValueError(f"{args.action} requires --count")
            ports = {key: value for key, value in {
                "telemetry_forward": args.telemetry_forward_port, "telemetry_source": args.telemetry_source_port,
                "pose_forward": args.pose_forward_port, "pose_source": args.pose_source_port,
            }.items() if value is not None}
            if args.action == "relocate":
                if (home is None) != args.default_home:
                    raise ValueError("relocate requires either a home or --default-home")
                result = relocate_fleet(args.count, home, px4_root=args.px4_root, ports=ports)
            elif args.default_home:
                raise ValueError("--default-home is only accepted by relocate")
            else:
                result = activate_fleet(args.count, px4_root=args.px4_root, ports=ports, home=home)
        elif args.action == "stop":
            result = stop_managed(args.instances or [], px4_root=args.px4_root)
        else:
            found = discover_px4_instances(args.px4_root.resolve())
            owned = _load_owned(_state_path())
            result = {"instances": [{"instance": key, "system_id": key + 1,
                                     "aircraft_id": f"px4-{key + 1}", "gazebo_model": f"x500_{key}",
                                     "pid": pid, "status": "process_present", "home": instance_home(pid),
                                     "managed": owned.get(key) == pid}
                                    for key, pid in sorted(found.items())],
                      "bridges": inspect_bridges(), **_environment_report(),
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
