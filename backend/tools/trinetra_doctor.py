"""TRINETRA health check: one command, a green/red list, and for every red item the exact fix.

    python backend/tools/trinetra_doctor.py                 # check only (read-only)
    python backend/tools/trinetra_doctor.py --start         # also start the backend and the frontend when they are not running
    python backend/tools/trinetra_doctor.py --api http://127.0.0.1:8000 --web http://127.0.0.1:5173

Software-only. It reads state and, with --start, launches two fixed local commands (the backend with uvicorn and the frontend dev server). It
never sends a command to PX4, never uses credentials and never opens `backend/.env` (it only checks that the file exists). Exit code 0 when
nothing is red, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
DISTRO = "Ubuntu"
PX4_BINARY = "/home/apt8055/uav-sim/PX4-Autopilot/build/px4_sitl_default/bin/px4"
MIN_FREE_GB_C = 1.5
MIN_FREE_GB_D = 5.0
GREEN, RED, YELLOW = "OK  ", "FAIL", "WARN"


@dataclass
class Result:
    name: str
    status: str                 # OK / FAIL / WARN
    detail: str
    fix: str = ""


@dataclass
class Doctor:
    api: str
    web: str
    results: list[Result] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, fix: str = "", warn: bool = False) -> bool:
        self.results.append(Result(name, GREEN if ok else (YELLOW if warn else RED), detail, "" if ok else fix))
        return ok


def run(command: list[str], timeout: float = 15.0) -> tuple[int, str]:
    """Runs one fixed command with a hard timeout. Returns (exit code, output); (-1, reason) when it could not run."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"no answer within {timeout:g} s"
    except OSError as error:
        return -1, str(error)


def http_json(url: str, timeout: float = 4.0) -> tuple[Optional[int], object]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            try:
                return response.status, json.loads(body or b"null")
            except ValueError:
                return response.status, body[:200]
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, None


def port_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def check_machine(d: Doctor) -> None:
    free_c = shutil.disk_usage("C:\\").free / 1e9 if os.name == "nt" else shutil.disk_usage("/").free / 1e9
    d.add("Free space on C:", free_c >= MIN_FREE_GB_C, f"{free_c:.1f} GB free (needs {MIN_FREE_GB_C:g} GB; the WSL disk and temp files can grow)",
          "Free space on C: (Disk Cleanup, empty %TEMP%). Do not start a simulator run below 1.5 GB.")
    if os.name == "nt" and Path("D:\\").exists():
        free_d = shutil.disk_usage("D:\\").free / 1e9
        d.add("Free space on D:", free_d >= MIN_FREE_GB_D, f"{free_d:.1f} GB free (needs {MIN_FREE_GB_D:g} GB)", "Free space on D: (exports, WSL disk and scratch files live there).", warn=True)
    d.add("Project virtual environment", VENV_PYTHON.exists(), str(VENV_PYTHON),
          r"Create it: py -3.9 -m venv .venv  then  .venv\Scripts\python.exe -m pip install -r backend\requirements.txt")
    d.add("Backend settings file", (BACKEND / ".env").exists(), "backend/.env exists (its content is never read by this tool)",
          "Create backend/.env from the example in docs (database URL, allowed origins, UDP ports). Never paste secrets into chat or the frontend.", warn=True)
    d.add("Frontend dependencies", (FRONTEND / "node_modules").exists(), "frontend/node_modules exists", "cd frontend && npm.cmd install")
    code, out = run([str(VENV_PYTHON), "-c", "import fastapi, pymavlink, argon2, xlsxwriter, numpy; print('ok')"], 30) if VENV_PYTHON.exists() else (-1, "no venv")
    d.add("Python packages", code == 0 and "ok" in out, "fastapi, pymavlink, argon2, xlsxwriter, numpy import" if code == 0 else out.strip()[-200:],
          r".venv\Scripts\python.exe -m pip install -r backend\requirements.txt")


def check_wsl(d: Doctor) -> bool:
    code, out = run(["wsl.exe", "-d", DISTRO, "--exec", "echo", "wsl-ok"], 25)
    answered = code == 0 and "wsl-ok" in out
    d.add(f"WSL ({DISTRO}) answers", answered, "answered" if answered else out.strip()[:160] or "no answer",
          f"Open a terminal and run: wsl -d {DISTRO}   (if it hangs: wsl --shutdown, then try again). Do not edit .wslconfig without asking.")
    if not answered:
        return False
    code, out = run(["wsl.exe", "-d", DISTRO, "--exec", "sh", "-c", f"test -x {PX4_BINARY} && echo px4-built; ls /mnt/d/TRINETRA/backend/tools/wsl_fleet_manager.py"], 25)
    d.add("PX4 SITL build and TRINETRA tools in WSL", "px4-built" in out and "wsl_fleet_manager.py" in out, "PX4 binary and wsl_fleet_manager.py found" if "px4-built" in out else out.strip()[:160],
          f"The PX4 build is missing: build PX4-Autopilot in WSL (make px4_sitl_default) or fix the path {PX4_BINARY}; D: must be mounted at /mnt/d.")
    return True


def check_processes(d: Doctor, wsl_ok: bool) -> None:
    if not wsl_ok:
        return
    code, out = run(["wsl.exe", "-d", DISTRO, "--exec", "sh", "-c", "ps -eo args | grep -E 'bin/px4 -i|gz sim|px4_wsl_udp_bridge|gazebo_wsl_pose_bridge' | grep -v grep | cut -c1-150"], 25)
    lines = [line for line in out.splitlines() if line.strip()]
    px4 = [line for line in lines if "bin/px4 -i" in line]
    gazebo = [line for line in lines if "gz sim" in line]
    bridges = [line for line in lines if "bridge" in line]
    d.add("PX4 instances running", bool(px4), f"{len(px4)} PX4 instance(s)" if px4 else "none running",
          "In Mission Control press ACTIVATE AND VERIFY (Scenario Lab) or RESTART AT MISSION START. It starts PX4 and Gazebo in small groups.", warn=True)
    d.add("Gazebo server running", bool(gazebo), "a Gazebo server is running" if gazebo else "not running", "Started together with the PX4 instances (see above).", warn=True)
    d.add("Telemetry and pose bridges running", len(bridges) >= 2, f"{len(bridges)} bridge process(es)" if bridges else "none running",
          "Started by Activate and verify / Restart at mission start; the Fleet page shows their ports.", warn=True)
    for line in bridges:
        if "px4_wsl_udp_bridge" in line and "--forward-port" in line:
            port = line.split("--forward-port")[1].split()[0]
            d.add("PX4 bridge forwards to", True, f"UDP {port} (the backend's PX4 receiver must be bound to this port)")



MANAGER = "/mnt/d/TRINETRA/backend/tools/wsl_fleet_manager.py"
PX4_ROOT = "/home/apt8055/uav-sim/PX4-Autopilot"


def manager_json(args: list[str], timeout: float = 40.0) -> Optional[dict]:
    """Runs the fixed WSL fleet manager with fixed arguments and parses its JSON answer (None when it cannot run)."""
    code, out = run(["wsl.exe", "-d", DISTRO, "--exec", "python3", MANAGER, *args, "--px4-root", PX4_ROOT], timeout)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return None


def check_stale(d: Doctor, wsl_ok: bool, clean: bool) -> None:
    """Reports TRINETRA processes alive with nothing driving them; --clean stops only those (provably TRINETRA's own: the manager checks each one)."""
    if not wsl_ok:
        return
    found = manager_json(["inventory"])
    if found is None:
        d.add("Stale TRINETRA processes", False, "the fleet manager could not be read", "Run: wsl -d Ubuntu python3 /mnt/d/TRINETRA/backend/tools/wsl_fleet_manager.py inventory", warn=True)
        return
    stale = found.get("stale", [])
    if not stale:
        d.add("Stale TRINETRA processes", True, "none (every running simulation process belongs to a complete simulation, or nothing is running)")
        return
    if not clean:
        d.add("Stale TRINETRA processes", False, "alive with nothing driving them: " + ", ".join(stale),
              "Run this tool with --clean (it stops only these TRINETRA processes), or press RESTART AT MISSION START (it clears them first).", warn=True)
        return
    result = manager_json(["clean", "--clean-mode", "stale"])
    stopped, left = (result or {}).get("stopped", []), (result or {}).get("left_running", [])
    d.add("Stale TRINETRA processes", bool(result) and not left, ("stopped: " + ", ".join(stopped) if stopped else "nothing stopped") + (("; still running: " + ", ".join(left)) if left else ""),
          "Stop the remaining processes by hand in WSL (kill <pid>) after checking they are TRINETRA's.")


def windows_memory_gb() -> float:
    """Total physical memory of this computer in GB (0 when it cannot be read)."""
    if os.name != "nt":
        return 0.0
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong), ("pt", ctypes.c_ulonglong),
                    ("pa", ctypes.c_ulonglong), ("vt", ctypes.c_ulonglong), ("va", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
    status = Status(); status.length = ctypes.sizeof(Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.total / 2**30


def recommended_wslconfig(total_gb: float, logical_cpus: int, aircraft: int = 6) -> str:
    """A .wslconfig sized to this computer, leaving enough for Windows. Measured on the reference laptop (15.4 GB, 8 threads): 5-6 aircraft use about 2.6 GB
    of WSL memory and 2-4 threads (docs/FLEET_CAPACITY.md); the rest of the machine's memory is what Windows and the browser need."""
    memory = max(3, min(int(total_gb * 0.30), 8))              # WSL peaked near 2.6 GB with 5 aircraft; 30 % of the machine, 3 to 8 GB
    processors = max(2, min(logical_cpus - 2, 6))              # leave two threads for Windows
    swap = 2
    return ("[wsl2]\n" f"memory={memory}GB\n" f"processors={processors}\n" f"swap={swap}GB\n" "\n[experimental]\nautoMemoryReclaim=gradual\n\n")


def wslconfig_path() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".wslconfig"


def check_wslconfig(d: Doctor, apply: bool) -> None:
    """Prints the recommended .wslconfig. --apply-wslconfig writes it only when none exists, or after copying the old one to .wslconfig.bak (never silently:
    it says what it wrote and that `wsl --shutdown` is needed afterwards)."""
    total = windows_memory_gb()
    cpus = os.cpu_count() or 4
    path = wslconfig_path()
    current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
    recommended = recommended_wslconfig(total, cpus) if total else None
    if recommended is None:
        d.add("WSL resource limits (.wslconfig)", False, "total memory could not be read", "Run this tool on Windows.", warn=True)
        return
    d.add("WSL resource limits (.wslconfig)", current is not None, f"{path} {'exists' if current is not None else 'does not exist: WSL takes up to half of the memory and every CPU'} (this computer: {total:.1f} GB, {cpus} threads)",
          "Run this tool with --apply-wslconfig (it writes the recommended file below; then run: wsl --shutdown).", warn=True)
    print("\nRecommended .wslconfig for this computer:\n" + "".join("    " + line + "\n" for line in recommended.splitlines()))
    if apply:
        if current is not None and current.strip() == recommended.strip():
            print(f"  {path} already has exactly this content; nothing written.")
            return
        if current is not None:
            backup = path.with_name(".wslconfig.bak")
            if backup.exists():
                backup = path.with_name(f".wslconfig.bak.{time.strftime('%Y%m%d-%H%M%S')}")
            backup.write_text(current, encoding="utf-8")
            print(f"  the existing file was copied to {backup}")
        path.write_text(recommended, encoding="utf-8")
        print(f"  WROTE {path}. It takes effect after:  wsl --shutdown   (close terminals and the simulation first).")


def check_backend(d: Doctor) -> bool:
    status, body = http_json(d.api + "/api/health")
    up = status == 200
    d.add("Backend answers", up, f"{d.api} is healthy" if up else f"no answer from {d.api}",
          r"Start it: cd backend ; ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000   (or run this tool with --start)")
    if not up:
        return False
    components = (body or {}).get("components", {}) if isinstance(body, dict) else {}
    database = components.get("database", {}).get("status")
    d.add("Database", database == "healthy", f"status {database}", "Check DATABASE_URL in backend/.env and that backend/trinetra.db exists; never delete or overwrite the database file.")
    px4 = components.get("px4", {}).get("status")
    d.add("PX4 telemetry arriving at the backend", px4 == "receiving", f"status {px4}; vehicles: {components.get('px4', {}).get('vehicle_count')}",
          "Start the simulator (ACTIVATE AND VERIFY). If PX4 is running but nothing arrives, the WSL bridge forwards to a different UDP port than the backend listens on: compare "
          "'PX4 bridge forwards to' above with TRINETRA_PX4_UDP_BIND_PORT, then press ACTIVATE AND VERIFY again (it replaces a mismatching bridge).", warn=True)
    gazebo = components.get("gazebo", {}).get("status")
    d.add("Gazebo poses arriving at the backend", gazebo == "receiving", f"status {gazebo}", "Same as above for the Gazebo pose bridge (UDP 14610 by default).", warn=True)
    artifact = BACKEND / "research_artifacts" / "model.joblib"
    d.add("Threat model file", artifact.exists(), f"{artifact.name} found ({artifact.stat().st_size // 1024} KB)" if artifact.exists() else "model.joblib is missing",
          "The live threat monitor needs backend/research_artifacts/model.joblib; train it with: cd backend ; ../.venv/Scripts/python.exe -m app.research.threat_models", warn=True)
    return True


def check_frontend(d: Doctor) -> None:
    status, _ = http_json(d.web)
    d.add("Frontend answers", status == 200, f"{d.web} is up" if status == 200 else f"no answer from {d.web}",
          "Start it: cd frontend ; npm.cmd run dev   (or run this tool with --start)")


def start_backend_and_frontend(d: Doctor) -> None:
    """Launches the two local servers with fixed commands (never anything the caller supplies)."""
    log_dir = ROOT / "exports" / "doctor_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    host_api, port_api = d.api.split("//")[-1].split(":")[0], int(d.api.rsplit(":", 1)[-1])
    if not port_listening(host_api, port_api):
        subprocess.Popen([str(VENV_PYTHON), "-m", "uvicorn", "app.main:app", "--host", host_api, "--port", str(port_api)], cwd=BACKEND,
                         stdout=(log_dir / "backend.out").open("ab"), stderr=(log_dir / "backend.err").open("ab"), creationflags=flags)
        print(f"  started the backend on {d.api} (log: {log_dir})")
    host_web, port_web = d.web.split("//")[-1].split(":")[0], int(d.web.rsplit(":", 1)[-1])
    if not port_listening(host_web, port_web):
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if npm:
            subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND, stdout=(log_dir / "frontend.out").open("ab"), stderr=(log_dir / "frontend.err").open("ab"), creationflags=flags)
            print(f"  started the frontend on {d.web} (log: {log_dir})")
        else:
            print("  npm was not found: install Node.js, then run: cd frontend ; npm.cmd run dev")
    for _ in range(40):
        if http_json(d.api + "/api/health")[0] == 200 and http_json(d.web)[0] == 200:
            break
        time.sleep(1.5)


def report(d: Doctor) -> int:
    width = max(len(item.name) for item in d.results)
    for item in d.results:
        print(f"[{item.status}] {item.name.ljust(width)}  {item.detail}")
        if item.fix:
            print(f"       -> fix: {item.fix}")
    red = [item for item in d.results if item.status == RED]
    warn = [item for item in d.results if item.status == YELLOW]
    print()
    if not red and not warn:
        print("Everything is green. Next: open the web page, sign in, deploy a mission, ACTIVATE AND VERIFY, then START IN SIMULATION (docs/HOW_TO_START_A_RUN.md).")
    elif not red:
        print(f"No red items, {len(warn)} warning(s). The simulator may simply not be started yet: open Mission Control and press ACTIVATE AND VERIFY or RESTART AT MISSION START.")
    else:
        print(f"{len(red)} red item(s). Fix them in the order shown, then run this tool again.")
    return 1 if red else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TRINETRA health check (read-only unless --start is given).")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="backend URL")
    parser.add_argument("--web", default="http://127.0.0.1:5173", help="frontend URL")
    parser.add_argument("--start", action="store_true", help="start the backend and the frontend if they are not running (fixed local commands)")
    parser.add_argument("--no-wsl", action="store_true", help="skip the WSL checks (they take a few seconds)")
    parser.add_argument("--clean", action="store_true", help="stop stale TRINETRA simulation processes (only ones the fleet manager can prove are TRINETRA's own)")
    parser.add_argument("--apply-wslconfig", action="store_true", help="write the recommended .wslconfig (only if none exists, or after backing the old one up to .wslconfig.bak); needs wsl --shutdown afterwards")
    args = parser.parse_args(argv)
    d = Doctor(args.api.rstrip("/"), args.web.rstrip("/"))
    print(f"TRINETRA doctor - {time.strftime('%Y-%m-%d %H:%M:%S')} - software-only simulator checks\n")
    check_machine(d)
    if args.start:
        start_backend_and_frontend(d)
    wsl_ok = False if args.no_wsl else check_wsl(d)
    check_processes(d, wsl_ok)
    check_stale(d, wsl_ok, args.clean)
    check_wslconfig(d, args.apply_wslconfig)
    check_backend(d)
    check_frontend(d)
    return report(d)


if __name__ == "__main__":
    sys.exit(main())
