"""Mission continuity after a multi-aircraft response (Part 6): did the reconfiguration really leave the mission flying and the aircraft safe?

Software-only. After "Hand over to a standby aircraft" or "Switch primary" a watcher follows what the simulated aircraft really do:

* the new primary must be flying its own route (its run reaches a flying state within `START_GRACE_S`);
* the previous aircraft must be safe: after a handover it is back on the ground and disarmed (it was sent back to launch); after a switch it keeps
  flying its own route;
* the mission must keep progressing (the new primary's route progress grows, or its run completes).

Outcome, saved on the audit trail and on the stored experiment run record of the flight when there is one:
`completed` (the new primary finished its route and the previous aircraft is confirmed safe), `degraded` (the mission went on but something is
not confirmed: the previous aircraft is not confirmed safe, the new primary's flight was ended early, or progress stalled), `failed` (the new
primary never flew or its run failed or lost its link). Only measured facts are used; nothing is assumed.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..persistence.database import add_audit_event, get_mission_execution

LOGGER = logging.getLogger("trinetra.continuity")
START_GRACE_S = 90.0           # the new primary must be flying its route this soon after the response
STALL_S = 120.0                # progress that does not grow for this long is reported as stalled
SAFE_WAIT_S = 120.0            # the previous aircraft has this long after its run ended to be on the ground and disarmed
MAX_WATCH_S = 45 * 60.0
POLL_S = 2.0
FLYING = {"executing", "pausing", "paused", "resuming"}
_lock = threading.RLock()
_items: dict[str, "Reconfiguration"] = {}
_thread: dict[str, Any] = {"t": None}
_vehicle_source = {"fn": None}
_sync_source = {"fn": None}


@dataclass
class Reconfiguration:
    id: str
    kind: str                      # "handover" | "switch_primary"
    previous: str                  # the aircraft that was the primary
    new: str                       # the aircraft that became the primary
    deployment_id: str
    previous_run: Optional[str]
    new_run: Optional[str]
    started_wall: str
    started_mono: float = field(default_factory=time.monotonic)
    status: str = "verifying"      # verifying | completed | degraded | failed
    checks: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    finished_wall: Optional[str] = None
    flying_seen: bool = False
    best_percent: float = 0.0
    progress_at: float = field(default_factory=time.monotonic)
    previous_ended_at: Optional[float] = None
    actor: str = ""

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "previous_primary": self.previous, "new_primary": self.new, "deployment_id": self.deployment_id,
                "previous_run": self.previous_run, "new_run": self.new_run, "started_at": self.started_wall, "finished_at": self.finished_wall,
                "status": self.status, "checks": dict(self.checks), "notes": list(self.notes), "simulation_only": True}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def set_vehicle_source(fn: Any) -> None:
    """Where to read a vehicle's armed state and altitude (the live telemetry snapshot); injected so tests can fake it."""
    _vehicle_source["fn"] = fn


def _vehicle(aircraft_id: str) -> Optional[dict[str, Any]]:
    fn = _vehicle_source["fn"]
    if fn is None:
        from ..fleet_routes import _fleet_snapshot
        fn = lambda: _fleet_snapshot()[0]            # noqa: E731
    return next((item for item in fn() if item.get("id") == aircraft_id), None)


def watch(kind: str, previous: str, new: str, deployment_id: str, previous_run: Optional[str], new_run: Optional[str], actor: str = "") -> Reconfiguration:
    item = Reconfiguration(uuid.uuid4().hex, kind, previous, new, deployment_id, previous_run, new_run, _now(), actor=actor)
    item.checks = {"new_primary_flying": None, "previous_aircraft_safe": None, "mission_progressing": None}
    with _lock:
        _items[item.id] = item
        if len(_items) > 100:
            for key in [key for key, value in _items.items() if value.status != "verifying"][:50]:
                _items.pop(key, None)
        thread = _thread["t"]
        if thread is None or not thread.is_alive():
            thread = threading.Thread(target=_loop, name="continuity-watch", daemon=True)
            _thread["t"] = thread
            thread.start()
    return item


def list_items() -> list[dict[str, Any]]:
    with _lock:
        return [item.view() for item in sorted(_items.values(), key=lambda value: value.started_wall, reverse=True)]


def _loop() -> None:
    while True:
        with _lock:
            active = [item for item in _items.values() if item.status == "verifying"]
        if not active:
            return
        for item in active:
            try:
                evaluate(item)
            except Exception:
                LOGGER.exception("continuity evaluation failed")
        time.sleep(POLL_S)


def set_sync(fn: Any) -> None:
    """How a stored run is brought up to date from the executor (default: the same `sync_execution` the Mission Control page uses)."""
    _sync_source["fn"] = fn


def _run(run_id: Optional[str]) -> Optional[dict[str, Any]]:
    run = get_mission_execution(run_id) if run_id else None
    if run is None or not run["active"]:
        return run
    fn = _sync_source["fn"]
    if fn is None:
        from ..execution_routes import sync_execution     # the stored state only moves when somebody asks the executor: do it here too
        fn = sync_execution
    return fn(run) or run


def evaluate(item: Reconfiguration, now: Optional[float] = None) -> str:
    """One look at the aircraft. Updates the checks and, when everything is decided, the final status. Returns the status."""
    now = time.monotonic() if now is None else now
    elapsed = now - item.started_mono
    new_run, old_run = _run(item.new_run), _run(item.previous_run)
    state = (new_run or {}).get("state")
    percent = float(((new_run or {}).get("progress") or {}).get("percent") or 0.0)
    if state in FLYING | {"returning", "landing", "failsafe"} or (new_run and not new_run["active"] and state == "completed"):
        item.flying_seen = True
    if item.flying_seen:
        item.checks["new_primary_flying"] = {"ok": True, "detail": f"{item.new} is flying its own route (run state {state})."}
    elif elapsed > START_GRACE_S:
        item.checks["new_primary_flying"] = {"ok": False, "detail": f"{item.new} was not flying its route {START_GRACE_S:.0f} s after the response (run state {state})."}
    if percent > item.best_percent + 0.5:
        item.best_percent, item.progress_at = percent, now
    if new_run is not None:
        stalled = state in ("executing",) and now - item.progress_at > STALL_S
        item.checks["mission_progressing"] = {"ok": not stalled and (percent > 0 or state == "completed"), "detail": f"{item.new} route progress {max(percent, item.best_percent):.0f}%" + (" (stalled)" if stalled else "")}
    # the previous aircraft
    if item.kind == "handover":
        vehicle = _vehicle(item.previous)
        ended = old_run is not None and not old_run["active"]
        if ended and item.previous_ended_at is None:
            item.previous_ended_at = now
        armed = (vehicle or {}).get("armed")
        altitude = ((vehicle or {}).get("position") or {}).get("relative_altitude_m")
        on_ground = armed is False and isinstance(altitude, (int, float)) and abs(altitude) < 2.0
        if on_ground:
            item.checks["previous_aircraft_safe"] = {"ok": True, "detail": f"{item.previous} is on the ground and disarmed."}
        elif ended and now - (item.previous_ended_at or now) > SAFE_WAIT_S:
            item.checks["previous_aircraft_safe"] = {"ok": False, "detail": f"{item.previous} is not confirmed on the ground and disarmed {SAFE_WAIT_S:.0f} s after its run ended."}
        elif old_run is not None and old_run["state"] == "failed":
            item.checks["previous_aircraft_safe"] = {"ok": False, "detail": f"The run of {item.previous} failed: {old_run['reason']}"}
    else:
        item.checks["previous_aircraft_safe"] = {"ok": old_run is None or old_run["state"] not in ("failed", "link_lost"), "detail": f"{item.previous} keeps flying its own route as a decoy (run state {(old_run or {}).get('state')})."}
    return _decide(item, new_run, now)


def _decide(item: Reconfiguration, new_run: Optional[dict[str, Any]], now: float) -> str:
    state = (new_run or {}).get("state")
    never_flew = item.checks.get("new_primary_flying") and item.checks["new_primary_flying"]["ok"] is False
    final: Optional[str] = None
    if never_flew or state in ("failed", "link_lost") or new_run is None:
        final = "failed"
        item.notes.append("The new primary never flew its route, or its run failed." if state != "link_lost" else "The new primary's link was lost.")
    elif new_run and not new_run["active"]:
        previous = item.checks.get("previous_aircraft_safe") or {}
        if state == "completed" and previous.get("ok") is True:
            final = "completed"
        elif state == "completed":
            final = "degraded"
            item.notes.append("The new primary finished its route, but the previous aircraft is not confirmed safe." if previous.get("ok") is not True else "")
        else:
            final = "degraded"
            item.notes.append(f"The new primary's flight ended early ({state}: {new_run.get('reason') or 'no reason given'}) at {item.best_percent:.0f}% of its route.")
    elif now - item.started_mono > MAX_WATCH_S:
        final = "degraded"
        item.notes.append("The new primary was still flying after the watch time ran out; the outcome could not be confirmed.")
    if final is None:
        return "verifying"
    item.notes = [text for text in item.notes if text]
    item.status, item.finished_wall = final, _now()
    _finish(item)
    return final


def _finish(item: Reconfiguration) -> None:
    try:
        add_audit_event(action="reconfiguration_outcome", outcome="success" if item.status == "completed" else "refused", reason=f"{item.kind}: {item.status}",
                        request_id=item.id, actor_name=item.actor or None, target_type="aircraft", target_id=item.new,
                        metadata={**item.view(), "simulation_only": True})
    except Exception:
        LOGGER.exception("could not audit the reconfiguration outcome")
    try:
        from . import live_experiments
        live_experiments.attach_reconfiguration(item.previous_run, item.view())
    except Exception:
        LOGGER.exception("could not attach the reconfiguration to the run record")


def reset_for_tests() -> None:
    with _lock:
        _items.clear()
    _vehicle_source["fn"] = None
    _sync_source["fn"] = None
