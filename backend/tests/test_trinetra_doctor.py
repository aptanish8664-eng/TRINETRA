"""The health check tool: red items carry the exact fix, nothing is sent to an aircraft, the settings file is never read."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("trinetra_doctor", Path(__file__).resolve().parents[1] / "tools" / "trinetra_doctor.py")
doctor = importlib.util.module_from_spec(SPEC)
import sys  # noqa: E402
sys.modules["trinetra_doctor"] = doctor          # dataclasses look the module up by name
SPEC.loader.exec_module(doctor)


def make():
    return doctor.Doctor("http://127.0.0.1:8000", "http://127.0.0.1:5173")


def test_a_red_item_has_its_fix_and_the_exit_code_is_one(capsys):
    d = make()
    d.add("Backend answers", False, "no answer", "Start it with uvicorn")
    d.add("Frontend answers", True, "up", "never shown")
    assert doctor.report(d) == 1
    out = capsys.readouterr().out
    assert "[FAIL] Backend answers" in out and "-> fix: Start it with uvicorn" in out and "never shown" not in out and "1 red item" in out


def test_all_green_prints_the_next_step_and_exits_zero(capsys):
    d = make()
    d.add("A", True, "fine")
    assert doctor.report(d) == 0
    out = capsys.readouterr().out
    assert "Everything is green" in out and "docs/HOW_TO_START_A_RUN.md" in out


def test_warnings_do_not_fail_the_run(capsys):
    d = make()
    d.add("PX4 instances running", False, "none running", "Press ACTIVATE AND VERIFY", warn=True)
    assert doctor.report(d) == 0
    assert "[WARN]" in capsys.readouterr().out


def test_backend_check_reads_the_health_components(monkeypatch):
    d = make()
    health = {"status": "ready", "components": {"database": {"status": "healthy"}, "px4": {"status": "receiving", "vehicle_count": 2}, "gazebo": {"status": "receiving"}}}
    monkeypatch.setattr(doctor, "http_json", lambda url, timeout=4.0: (200, health))
    assert doctor.check_backend(d) is True
    states = {item.name: item.status for item in d.results}
    assert states["Backend answers"] == "OK  " and states["PX4 telemetry arriving at the backend"] == "OK  " and states["Database"] == "OK  "


def test_no_telemetry_is_a_warning_with_the_port_hint(monkeypatch):
    d = make()
    health = {"components": {"database": {"status": "healthy"}, "px4": {"status": "awaiting_data"}, "gazebo": {"status": "awaiting_data"}}}
    monkeypatch.setattr(doctor, "http_json", lambda url, timeout=4.0: (200, health))
    doctor.check_backend(d)
    item = next(item for item in d.results if item.name.startswith("PX4 telemetry"))
    assert item.status == "WARN" and "ACTIVATE AND VERIFY" in item.fix and "TRINETRA_PX4_UDP_BIND_PORT" in item.fix


def test_backend_down_is_red_and_stops_the_detail_checks(monkeypatch):
    d = make()
    monkeypatch.setattr(doctor, "http_json", lambda url, timeout=4.0: (None, None))
    assert doctor.check_backend(d) is False
    assert [item.status for item in d.results] == ["FAIL"] and "--start" in d.results[0].fix


def test_the_settings_file_is_only_checked_for_existence_never_read(monkeypatch, tmp_path):
    reads = []
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: reads.append(self.name) or original(self, *a, **k))
    original_open = Path.open
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: reads.append(self.name) or original_open(self, *a, **k))
    d = make()
    doctor.check_machine(d)
    assert ".env" not in reads
    assert any(item.name == "Backend settings file" for item in d.results)


def test_wsl_failure_is_red_with_the_fix(monkeypatch):
    d = make()
    monkeypatch.setattr(doctor, "run", lambda command, timeout=15.0: (-1, "no answer within 25 s"))
    assert doctor.check_wsl(d) is False
    assert d.results[0].status == "FAIL" and "wsl --shutdown" in d.results[0].fix and ".wslconfig" in d.results[0].fix


def test_start_mode_only_launches_fixed_commands(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "ROOT", tmp_path)           # the log files go to a temporary folder, not to exports
    launched = []
    monkeypatch.setattr(doctor.subprocess, "Popen", lambda command, **kw: launched.append(command))
    monkeypatch.setattr(doctor, "port_listening", lambda host, port: False)
    monkeypatch.setattr(doctor, "http_json", lambda url, timeout=4.0: (200, {}))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "npm.cmd")
    monkeypatch.setattr(doctor.time, "sleep", lambda s: None)
    doctor.start_backend_and_frontend(make())
    assert launched[0][1:4] == ["-m", "uvicorn", "app.main:app"] and launched[1][1:] == ["run", "dev"]
