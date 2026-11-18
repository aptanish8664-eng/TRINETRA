"""Restart the managed PX4/Gazebo simulation at a mission start, in any aircraft state (also used for the one-click mission switch).

SITL only. Steps, each reported with a status so the UI can show a verification table:
  managed      only PX4 instances the fleet manager started are touched; a foreign instance is refused BEFORE anything is stopped
  executor     stop the mission executor / landing processes and end the active run (final state "aborted", the reason says why)
  stop_sim     stop the managed PX4 and this checkout's Gazebo and wait until the processes are really gone
  start_sim    start the Gazebo server (no GUI) and wait until its world answers, then the PX4 instances in small batches, each waited for until it has a valid
               position (the fleet manager's staged start; home persisted as the last known home). The job follows its progress and gives up only when nothing
               changes for STALL_S or START_BUDGET_S is used up, never because of one fixed timer
  bridges      the WSL telemetry and pose bridges point at the right ports
  telemetry    PX4 telemetry is flowing
  gazebo_pose  Gazebo model poses are flowing
  position     the aircraft reports a position at the mission start
  orphans      no stray executor, PX4 or Gazebo processes of this checkout remain (what was killed is reported)

Only instances the fleet manager started are stopped (the manager refuses foreign ones and says what to do). No flight command is sent.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..persistence.database import active_mission_execution, update_mission_execution
from ..security import AuthContext
from ..telemetry.fleet_manager import FLEET_CAPACITY

REASONS = {"restart": "simulation restarted", "switch": "simulation restarted for a new mission"}
STEP_DEFS = [
    ("managed", "PX4 started by TRINETRA"), ("executor", "Stop the flight run"), ("stop_sim", "Stop PX4 and Gazebo"), ("start_sim", "Start PX4 and Gazebo at the mission start"),
    ("bridges", "WSL bridges"), ("telemetry", "PX4 telemetry"), ("gazebo_pose", "Gazebo pose"), ("position", "Position at the mission start"),
    ("orphans", "No stray processes"),
]
Progress = Callable[[str, str, str], None]
AircraftProgress = Callable[[list[dict[str, Any]]], None]
POLL_S = 1.0                      # how often the job reads the manager's progress
STALL_S = 75.0                    # nothing changed in the whole start (manager progress and verified aircraft) for this long: it failed
VERIFY_STALL_S = 120.0            # after the manager finished: no sign of life from any further aircraft for this long -> give up on the rest (see fleet_routes.RELOCATION_VERIFY_TIMEOUT_S)
MAX_TOTAL_BASE_S, MAX_TOTAL_PER_AIRCRAFT_S = 120.0, 45.0     # overall cap = base + per aircraft (measured; docs/FLEET_CAPACITY.md)
MANAGER_CODES = {"gazebo_failed": "sitl_start_failed", "no_aircraft_ready": "sitl_start_failed", "supervisor_error": "manager_failed", "supervisor_died": "manager_failed",
                 "start_in_progress": "manager_failed", "manager_unreachable": "manager_failed"}


def start_budget_s(requested: int) -> float:
    return MAX_TOTAL_BASE_S + MAX_TOTAL_PER_AIRCRAFT_S * requested


def aircraft_rows(requested: int, manager: dict[str, Any], rows: list[dict[str, Any]], gave_up: bool = False) -> list[dict[str, Any]]:
    """One row per aircraft for the dialog: starting | telemetry_ok | pose_ok | ready | failed, with the plain reason."""
    by_system = {row["system_id"]: row for row in rows}
    out = []
    for i in range(requested):
        item = (manager.get("aircraft") or {}).get(str(i), {})
        row = by_system.get(i + 1, {})
        telemetry, pose = row.get("px4_status") == "connected", row.get("gazebo_status") == "connected"
        mstate = item.get("state", "pending")
        if mstate == "failed":
            state, detail = "failed", item.get("detail") or "PX4 did not start."
        elif mstate == "ready" and telemetry and pose:
            state, detail = "ready", "PX4 telemetry and Gazebo pose are flowing."
        elif gave_up and mstate != "pending":
            state, detail = "failed", ("No PX4 telemetry arrived." if not telemetry else "No Gazebo pose arrived.") + " Nothing changed for a while, so it was given up."
        elif mstate == "ready" and telemetry:
            state, detail = "telemetry_ok", "PX4 telemetry is flowing; waiting for the Gazebo pose."
        elif mstate == "ready" and pose:
            state, detail = "pose_ok", "Gazebo pose is flowing; waiting for PX4 telemetry."
        elif mstate == "ready":
            state, detail = "starting", "Ready in PX4; waiting for telemetry and the Gazebo pose."
        else:
            state, detail = "starting", {"pending": "Waiting for its turn.", "booting": "PX4 is starting.", "sensors": "PX4 started; waiting for a valid position."}.get(mstate, item.get("detail", "") or "Starting.")
        out.append({"aircraft_id": f"px4-{i + 1}", "state": state, "detail": detail, "manager_state": mstate, "telemetry": telemetry, "pose": pose})
    return out


def summary_line(rows: list[dict[str, Any]]) -> str:
    ready = [row for row in rows if row["state"] == "ready"]
    waiting = [row["aircraft_id"] for row in rows if row["state"] not in ("ready", "failed")]
    failed = [row["aircraft_id"] for row in rows if row["state"] == "failed"]
    text = f"{len(ready)} of {len(rows)} ready"
    if waiting:
        text += ", waiting for " + " and ".join(waiting) if len(waiting) <= 2 else f", waiting for {len(waiting)} more"
    if failed:
        text += ("; failed: " + ", ".join(failed))
    return text


def follow_staged_start(fr: Any, requested: int, home: dict[str, Any], correlation_id: str, *, instances: Optional[list[int]], stop_first: bool, step: Progress,
                        on_aircraft: Optional[AircraftProgress], clock=time.monotonic, sleep=time.sleep) -> dict[str, Any]:
    """Launch the manager's staged start and follow its progress file until it finishes. Returns {"ok", "state", "answer"}."""
    started = clock()
    from .. import config

    answer = fr.invoke_fleet_manager("start", count=requested, instances=instances, request_id=correlation_id, home=home, stop_first=stop_first, batch=config.FLEET_START_BATCH, timeout_seconds=30)
    if isinstance(answer.get("failure"), str) and answer["failure"]:          # the launch itself failed (wsl.exe, a second start, bad arguments); a progress-file failure is a dict
        return {"ok": False, "state": {}, "answer": answer}
    last_reply, state = clock(), {}
    if stop_first:
        step("stop_sim", "running", "Stopping PX4 and Gazebo.")
    while True:
        reply = fr.invoke_fleet_manager("progress", timeout_seconds=20)
        if (isinstance(reply.get("failure"), str) and reply["failure"]) or not reply.get("present"):      # a string = the call failed; a dict = the start itself failed (handled below)
            if clock() - last_reply > 45.0:
                return {"ok": False, "state": state, "answer": {"failure": "The start-up progress could not be read for 45 s.", "failure_code": "manager_unreachable"}}
            sleep(POLL_S)
            continue
        last_reply, state = clock(), reply
        stop = reply.get("stop") or {}
        if stop_first:
            step("stop_sim", {"ok": "ok", "failed": "failed"}.get(stop.get("state"), "running"), stop.get("detail") or "Stopping PX4 and Gazebo.")
        aircraft = reply.get("aircraft") or {}
        targets = reply.get("targets") or list(range(requested))
        ready = [key for key in aircraft if aircraft[key]["state"] == "ready" and int(key) in targets]
        gazebo = reply.get("gazebo") or {}
        phase = reply.get("phase")
        if phase != "stopping":
            step("start_sim", "running", f"Gazebo: {str(gazebo.get('detail') or gazebo.get('state')).rstrip('.')}. {len(ready)} of {len(targets)} aircraft ready.")
        if on_aircraft:
            on_aircraft(aircraft_rows(requested, reply, fr._verified_fleet(requested)[1]))        # the manager's state plus what the backend already receives
        if reply.get("finished"):
            return {"ok": True, "state": reply, "answer": {}}
        if clock() - started > start_budget_s(requested):
            return {"ok": False, "state": reply, "answer": {"failure": f"The start did not finish within {start_budget_s(requested):.0f} s.", "failure_code": "start_timeout"}}
        if float(reply.get("stalled_s") or 0.0) > STALL_S:
            return {"ok": False, "state": reply, "answer": {"failure": f"Nothing changed for {STALL_S:g} s while starting.", "failure_code": "start_stalled"}}
        sleep(POLL_S)


def legacy_process_result(state: dict[str, Any], answer: dict[str, Any], requested: int) -> dict[str, Any]:
    """The staged result in the shape the activation checks understand (instances present, bridges, failure code)."""
    result = state.get("result") or {}
    aircraft = state.get("aircraft") or {}
    instances = [{"instance": int(k), "system_id": int(k) + 1, "aircraft_id": v.get("aircraft_id"), "status": "process_started" if v["state"] in ("booting", "sensors", "ready") else v["state"]}
                 for k, v in sorted(aircraft.items(), key=lambda kv: int(kv[0]))]
    failure = (answer or {}).get("failure") or result.get("failure")
    code = (answer or {}).get("failure_code") or result.get("failure_code")
    return {"instances": instances, "failure": failure, "failure_code": MANAGER_CODES.get(code, code), "bridges": result.get("bridges") or {"telemetry": {"status": "running"}, "pose": {"status": "running"}},
            "gazebo_server": "running" if (state.get("gazebo") or {}).get("state") in ("ready", "adopted") else "unknown", "wsl_address": None, "windows_host": None, "timings": state.get("timings")}


def new_steps() -> list[dict[str, str]]:
    return [{"key": key, "label": label, "status": "pending", "detail": ""} for key, label in STEP_DEFS]


def _failed(step: str, status: int, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "failed_step": step, "failure": (status, code, message), **extra}


def end_active_runs(purpose: str, context: Optional[AuthContext]) -> tuple[list[str], dict[str, Any]]:
    """Read the runs' real state, stop the executor processes, then end every active run record with the reason.

    Returns (ended aircraft ids, the cleanup answer). The state before is read from the executor BEFORE it is stopped, so the event is accurate.
    """
    from .. import execution_routes

    active = []
    for number in range(1, FLEET_CAPACITY + 1):
        aircraft_id = f"px4-{number}"
        running = active_mission_execution(aircraft_id)
        if running is not None:
            running = execution_routes.sync_execution(running, force=True)
            if running["active"]:
                active.append((aircraft_id, running))
    cleanup = execution_routes.invoke_mission_executor("cleanup", instance=0, timeout_seconds=25)
    if cleanup.get("failure"):
        return [], cleanup
    ended = []
    reason = REASONS[purpose]
    for aircraft_id, running in active:
        execution_routes._backend_event(running["id"], "simulation_restarted", f"The run on {aircraft_id} ended: {reason}. The executor was stopped and PX4 and Gazebo are restarted.", context,
                                        {"state_before": running["state"], "reason": reason})
        update_mission_execution(running["id"], state="aborted", reason=reason, finished=True)
        ended.append(aircraft_id)
    return ended, cleanup


def restart_simulation(plan: Any, home: dict[str, Any], requested: int, *, correlation_id: str, purpose: str = "restart",
                       context: Optional[AuthContext] = None, progress: Optional[Progress] = None, on_aircraft: Optional[AircraftProgress] = None,
                       instances: Optional[list[int]] = None, stop_first: bool = True) -> dict[str, Any]:
    """Run the whole restart. Returns {"ok": True, ...} or {"ok": False, "failed_step", "failure": (status, code, message), ...}. Never raises for a step failure."""
    from .. import main as app_main
    from ..config import GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP, PX4_UDP_ALLOWED_SOURCE_IP
    from .. import execution_routes, fleet_routes as fr
    from ..research_routes import reset_live_threat

    _check, _fleet_snapshot, _home_check = fr._check, fr._fleet_snapshot, fr._home_check
    build_activation_checks, classify_activation_failure = fr.build_activation_checks, fr.classify_activation_failure

    def step(key: str, status: str, detail: str = "") -> None:
        if progress:
            progress(key, status, detail)

    started = time.monotonic()
    receivers = {"px4": app_main.px4_udp_transport is not None, "gazebo": app_main.gazebo_pose_udp_transport is not None}
    configured_ips = {"px4_telemetry": PX4_UDP_ALLOWED_SOURCE_IP, "gazebo_pose": GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP}
    if not all(receivers.values()):
        step("managed", "failed", "The PX4 telemetry and Gazebo pose UDP receivers must both be enabled and bound before the simulation is restarted.")
        return _failed("managed", 503, "receivers_unavailable", "The PX4 telemetry and Gazebo pose UDP receivers must both be enabled and bound before the simulation is restarted.",
                       checks={"receivers": _check("unavailable", "A backend UDP receiver is not bound.", px4=receivers["px4"], gazebo=receivers["gazebo"])})

    # 0. nothing is stopped unless every running PX4 instance was started by the TRINETRA fleet manager
    step("managed", "running")
    survey = fr.invoke_fleet_manager("status", timeout_seconds=12)
    foreign = sorted(int(item.get("system_id") or int(item.get("instance", 0)) + 1) for item in (survey.get("instances") or []) if isinstance(item, dict) and item.get("managed") is False)
    if foreign:
        names = ", ".join(f"px4-{number}" for number in foreign)
        message = (f"{names} {'was' if len(foreign) == 1 else 'were'} not started by the TRINETRA fleet manager, so it is not stopped or restarted and nothing was changed. "
                   "Stop that PX4 process in WSL (close the terminal that runs it, or kill the px4 process), then press Retry.")
        step("managed", "failed", message)
        return _failed("managed", 409, "instance_not_managed", message)
    step("managed", "ok", "Every running PX4 instance was started by the TRINETRA fleet manager." if survey.get("instances") else "No PX4 instance is running yet.")

    # 1. the run and its executor
    step("executor", "running")
    ended, cleanup = end_active_runs(purpose, context)
    killed = list(cleanup.get("killed") or [])
    if cleanup.get("failure"):
        detail = f"The mission executor could not be reached to stop it: {cleanup['failure']}."
        step("executor", "failed", detail)
        return _failed("executor", 503, "executor_unreachable", detail, ended=ended)
    step("executor", "ok", (f"Ended the run on {', '.join(ended)} (reason: {REASONS[purpose]}). " if ended else "No flight run was active. ")
         + (f"Stopped {len(killed)} executor process(es)." if killed else "No executor process was running."))

    # 2-3. PX4 and Gazebo: the staged start (stop until gone, Gazebo server first, PX4 in small batches, each waited for until ready)
    step("stop_sim", "running" if stop_first else "ok", "" if stop_first else "Only the missing aircraft are started; nothing is stopped.")
    followed = follow_staged_start(fr, requested, home, correlation_id, instances=instances, stop_first=stop_first, step=step, on_aircraft=on_aircraft)
    state, answer = followed["state"], followed["answer"]
    manager_state = state                           # `state` is reused below for the aircraft's flight state
    process_result = legacy_process_result(state, answer, requested)
    stopped_ids = [int(k) for k in (state.get("aircraft") or {})] if stop_first else []
    stop_state = (state.get("stop") or {}).get("state")
    code = process_result.get("failure_code")
    if stop_first and (stop_state == "failed" or code in {"stop_failed", "instance_not_managed"} or (answer.get("failure") and not state)):
        message = str(process_result.get("failure") or (state.get("stop") or {}).get("detail") or "The simulation could not be stopped.")
        step("stop_sim", "failed", message)
        return _failed("stop_sim", 409 if code == "instance_not_managed" else 502, code or "manager_failed", message, ended=ended, simulation_stopped=False)
    step("stop_sim", "ok", (state.get("stop") or {}).get("detail") or ("Nothing was running." if stop_first else "Nothing was stopped."))
    manager_partial = bool((state.get("result") or {}).get("partial"))
    manager_failure = followed["ok"] is False or (state.get("failure") and not manager_partial)
    rows_now = fr._verified_fleet(requested)[1]
    if manager_failure:
        message = str(process_result.get("failure") or (state.get("failure") or {}).get("message") or "The simulation could not be started.")
        failed_rows = aircraft_rows(requested, state, rows_now, gave_up=True)
        if on_aircraft:
            on_aircraft(failed_rows)
        checks = build_activation_checks(requested, process_result, rows_now, receivers=receivers, configured_ips=configured_ips)
        problem = classify_activation_failure(requested, process_result, checks, final=False) or (502, code or "manager_failed", message)
        step("start_sim", "failed", problem[2])
        return _failed("start_sim", *problem, ended=ended, checks=checks, simulation_stopped=True, aircraft=failed_rows)
    checks = build_activation_checks(requested, process_result, rows_now, receivers=receivers, configured_ips=configured_ips)
    ready_by_manager = [int(k) for k, v in (state.get("aircraft") or {}).items() if v["state"] == "ready"]
    step("start_sim", "ok", f"Gazebo is ready and {len(ready_by_manager)} of {len(state.get('targets') or [])} aircraft started"
         + (f" (px4-{', px4-'.join(str(i + 1) for i in sorted(set(state.get('targets') or []) - set(ready_by_manager)))} did not)." if manager_partial else ".")
         + f" Home {home['latitude']:.5f}, {home['longitude']:.5f} saved as the last known home.")

    # 4-7. verification: the aircraft the manager reports ready must show PX4 telemetry and a Gazebo pose; waited for as long as the count keeps growing
    step("bridges", "running"); step("telemetry", "running"); step("gazebo_pose", "running"); step("position", "running")
    checks["simulation_home"] = _check("pending", "Waiting for PX4 to report a position at the mission start.", requested=home)
    # a retry of only the missing aircraft keeps expecting the ones that were already up
    expected_ids = set(ready_by_manager) | (set(range(requested)) - set(state.get("targets") or range(requested)))
    expected = len(expected_ids)
    window = float(fr.RELOCATION_VERIFY_TIMEOUT_S)
    verify_started = last_change = time.monotonic()
    best = -1
    gave_up = False
    while True:
        verified, rows = fr._verified_fleet(requested)
        checks["simulation_home"] = _home_check(plan, home)
        fresh = build_activation_checks(requested, process_result, rows, receivers=receivers, configured_ips=configured_ips)
        bridge_bad = [fresh[name]["detail"] for name in ("telemetry_bridge", "pose_bridge") if fresh[name]["status"] in {"mismatch", "failed", "not_running"}]
        step("bridges", "failed" if bridge_bad else "ok", " ".join(bridge_bad) or "Telemetry and pose bridges are running at the right ports.")
        current = aircraft_rows(requested, state, rows, gave_up=gave_up)
        if on_aircraft:
            on_aircraft(current)
        live = [row for row in current if row["state"] == "ready"]
        px4_up = all(row["px4_status"] == "connected" for row in rows if row["system_id"] - 1 in expected_ids) and len(rows) >= requested
        pose_up = all(row["gazebo_status"] == "connected" for row in rows if row["system_id"] - 1 in expected_ids) and len(rows) >= requested
        step("telemetry", "ok" if px4_up else "running", "PX4 telemetry is flowing." if px4_up else f"Waiting for PX4 telemetry ({summary_line(current)}).")
        step("gazebo_pose", "ok" if pose_up else "running", "Gazebo model pose is flowing." if pose_up else f"Waiting for the Gazebo pose ({summary_line(current)}).")
        position = checks["simulation_home"]
        step("position", "ok" if position["status"] == "ok" else "running", position["detail"])
        # Progress = more signs of life than before: an aircraft that is verified, or whose PX4 data has started to arrive (even stale), or whose Gazebo pose has.
        # On a busy host the backend receives telemetry late; that is not a stall as long as something keeps getting better.
        signal = len(live) * 3 + sum(1 for row in rows if row["px4_status"] in ("connected", "stale")) + sum(1 for row in rows if row["gazebo_status"] == "connected")
        if signal > best:
            best, last_change = signal, time.monotonic()
        if len(verified) >= expected and expected > 0 and position["status"] == "ok":
            checks = {**fresh, "simulation_home": position}
            break
        if time.monotonic() - last_change >= window or bridge_bad:
            gave_up = True
            checks = {**fresh, "simulation_home": position}
            final_rows = aircraft_rows(requested, state, rows, gave_up=True)
            if on_aircraft:
                on_aircraft(final_rows)
            usable = [row for row in final_rows if row["state"] == "ready"]
            failed_key = "bridges" if bridge_bad else "telemetry" if not px4_up else "gazebo_pose" if not pose_up else "position"
            if usable and not bridge_bad and requested > 1:
                missing = [row["aircraft_id"] for row in final_rows if row["state"] != "ready"]
                message = (f"Only {len(usable)} of {requested} aircraft are ready ({', '.join(row['aircraft_id'] for row in usable)}); "
                           f"{', '.join(missing)} did not produce both PX4 telemetry and a Gazebo pose in time.")
                step(failed_key, "failed", message)
                return {"ok": False, "partial": True, "failed_step": failed_key, "failure": (200, "partial_fleet", message), "ended": ended, "checks": checks, "simulation_stopped": True,
                        "aircraft": final_rows, "ready": [row["aircraft_id"] for row in usable], "missing": missing, "mission_start": home}
            problem = classify_activation_failure(requested, process_result, checks, final=True)
            if problem is None:
                problem = (502, "home_not_applied", "PX4 and Gazebo restarted, but the reported position is not at the mission start: " + position["detail"])
            step(failed_key, "failed", problem[2])
            return _failed(failed_key, *problem, ended=ended, checks=checks, simulation_stopped=True, aircraft=final_rows)
        time.sleep(0.5)

    # 8. stray processes
    step("orphans", "running")
    second = execution_routes.invoke_mission_executor("cleanup", instance=0, timeout_seconds=25)
    late = list(second.get("killed") or [])
    killed.extend(late)
    detail = ("Stopped stray process(es): " + ", ".join(f"{item.get('kind')} pid {item.get('pid')}" for item in killed) + ".") if killed else \
        "No stray executor, PX4 or Gazebo processes of this checkout remain."
    step("orphans", "failed" if second.get("failure") else "ok", f"Could not check: {second['failure']}" if second.get("failure") else detail)
    reset_live_threat()
    if stop_first and not instances:     # a full restart: aircraft of the earlier, larger simulation are gone and must not linger as "link lost"
        try:
            app_main.px4_telemetry_store.forget_except({f"px4-{index + 1}" for index in range(requested)})
            app_main.gazebo_pose_store.forget_except({f"x500_{index}" for index in range(requested)})
        except Exception:
            pass
    vehicles, _models = _fleet_snapshot()
    state = next((item for item in vehicles if item.get("id") == f"px4-{1}"), None)
    if manager_partial:
        # Some aircraft never came up: the ones that did are verified and usable, the others are named. The job ends "partial" (retry the missing ones, or continue).
        final_rows = aircraft_rows(requested, manager_state, rows)
        if on_aircraft:
            on_aircraft(final_rows)
        usable = [row["aircraft_id"] for row in final_rows if row["state"] == "ready"]
        missing = [row["aircraft_id"] for row in final_rows if row["state"] != "ready"]
        message = f"Only {len(usable)} of {requested} aircraft are ready ({', '.join(usable)}); {', '.join(missing)} did not start."
        step("start_sim", "failed", message)
        return {"ok": False, "partial": True, "failed_step": "start_sim", "failure": (200, "partial_fleet", message), "ended": ended, "checks": checks, "simulation_stopped": True,
                "aircraft": final_rows, "ready": usable, "missing": missing, "mission_start": home, "ended_executions": ended, "killed_processes": killed}
    return {"ok": True, "mission_start": home, "activated_uav_count": len(verified), "vehicles": verified, "instances": rows, "checks": checks,
            "max_distance_to_start_m": checks["simulation_home"].get("max_distance_m"), "ended_executions": ended, "killed_processes": killed,
            "armed": (state or {}).get("armed"), "elapsed_s": round(time.monotonic() - started, 1), "stopped_instances": stopped_ids,
            "commands_sent_to_aircraft": False}
