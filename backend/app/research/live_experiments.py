"""Live experiment runner (Part 3): runs the "implemented" Attack Lab experiments of the deployed mission while it flies.

Software-only. The simulated GPS fault is applied to the *monitor's software copy* of the GPS stream (the aligned window the live
threat monitor reads), with the same attack functions the offline dataset and the replay use (`attacks.inject`). PX4, its EKF and
the aircraft never see it and nothing is sent to any aircraft.

Rules kept here:
* the schedule is hidden: no live endpoint returns it, only a count of armed experiments;
* experiments run in step with the flight: every implemented experiment pinned to the deployed version is armed when the run starts;
  overlapping attack windows on one aircraft are refused when Implement is switched on, so attacks run one after the other;
* a run record is written per experiment when the flight run ends (completed, aborted, landed, restarted, failed), using
  `save_experiment_result`; the record is the only place the schedule appears, and only after the run;
* what is measured is what the monitor really showed (score and tier after the injection); nothing is scored from simulator truth.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import numpy as np

from ..persistence.database import (
    experiment_results_available, get_mission_execution, list_attack_experiments, list_research_decisions, save_experiment_result,
    update_experiment_result_payload,
)
from .attacks import AttackConfig, GpsStream, inject
from .threat_data import HZ

LOGGER = logging.getLogger("trinetra.live_experiments")
IMPLEMENTED = "implemented"
REACTION_TIERS = ("Medium", "High", "Critical")      # the monitor "reacted" at Medium or above (Low is the quiet state)
ALERT_TIERS = ("High", "Critical")
HOLD_S = 30.0                                        # the monitor holds the strongest evidence of the last 30 s on screen
FALSE_ALARM_GAP_S = 5.0                              # reactions closer together than this are one episode
MAX_STORED_POINTS = 600                              # timeline points kept per aircraft in a run record
SAMPLE_EVERY_S = 1.0
NOISE_TABLE_S = 4000
_NOOP = {"type": "offset", "start_s": 86_400.0, "duration_s": 1.0, "magnitude": 5.0}

_lock = threading.RLock()
_runs: dict[str, "Run"] = {}
_reconfigurations: dict[str, list[dict[str, Any]]] = {}      # execution id -> mission-continuity outcomes (Part 6)
_result_ids: dict[str, list[str]] = {}                    # execution id -> stored run-record ids
_ticker: dict[str, Any] = {"thread": None}


@dataclass
class ArmedExperiment:
    id: str
    name: str
    owner_user_id: str
    seed: int
    config_hash: str
    attacks: list[AttackConfig]
    noise: dict[int, np.ndarray] = field(default_factory=dict)
    anchors: dict[int, np.ndarray] = field(default_factory=dict)       # freeze: the position the receiver stopped on


@dataclass
class Run:
    execution_id: str
    deployment_id: str
    scenario_version_id: str
    context: Any
    experiments: list[ArmedExperiment]
    started_at: datetime
    started_mono: float = field(default_factory=time.monotonic)
    samples: dict[str, list[tuple[float, float, str, bool]]] = field(default_factory=dict)     # aircraft -> (t, score, tier, attacked)
    reached_t: dict[str, float] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)
    closed: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def armed_experiments(scenario_version_id: str) -> list[ArmedExperiment]:
    """Implemented experiments pinned to exactly this scenario version, oldest first."""
    found = []
    for item in reversed(list_attack_experiments("", include_all=True)):
        if item["status"] != IMPLEMENTED or item["scenario_version_id"] != scenario_version_id:
            continue
        attacks = [AttackConfig.model_validate(attack) for attack in item["config"].get("attacks", [])]
        if attacks:
            found.append(ArmedExperiment(item["id"], item["name"], item["owner_user_id"], int(item["seed"]), item["config_hash"], attacks))
    return found


def armed_count(scenario_version_id: Optional[str]) -> int:
    return len(armed_experiments(scenario_version_id)) if scenario_version_id else 0


def overlap_conflict(experiment_id: str, owner_user_id: str, scenario_version_id: str, attacks: list[dict]) -> Optional[str]:
    """None, or a sentence naming the already implemented experiment whose attack window overlaps one of these (same aircraft)."""
    del owner_user_id
    mine = [AttackConfig.model_validate(item) for item in attacks]
    for other in armed_experiments(scenario_version_id):
        if other.id == experiment_id:
            continue
        for first in mine:
            for second in other.attacks:
                if first.target_aircraft == second.target_aircraft and first.start_s < second.end_s + HOLD_S and second.start_s < first.end_s + HOLD_S:
                    return (f"This overlaps in time with the implemented experiment “{other.name}” on {first.target_aircraft}. "
                            f"Experiments run one after the other, so switch that one off or move one of them (leave {HOLD_S:g} s between the attacks).")
    return None


def status_for_viewer(scenario_version_id: Optional[str]) -> dict[str, Any]:
    """What Mission Control may know: how many are armed and whether a run is being measured. Never the schedule."""
    with _lock:
        active = [run for run in _runs.values() if not run.closed]
    return {"armed": armed_count(scenario_version_id), "run_active": bool(active), "experiments_in_run": sum(len(run.experiments) for run in active),
            "note": "The attack schedule stays hidden until the run has ended."}


# --- arming and the background ticker -------------------------------------------------------------------------

def arm_run(execution: dict[str, Any], context: Any, roles: Optional[dict[str, str]] = None) -> int:
    """Called when a flight run starts. Returns how many experiments are armed for it."""
    experiments = armed_experiments(execution["scenario_version_id"])
    if not experiments:
        return 0
    run = Run(execution["id"], execution["deployment_id"], execution["scenario_version_id"], context, experiments, _now(), roles=dict(roles or {}))
    with _lock:
        _runs[run.execution_id] = run
        _start_ticker()
    return len(experiments)


def _start_ticker() -> None:
    thread = _ticker.get("thread")
    if thread is not None and thread.is_alive():
        return
    thread = threading.Thread(target=_tick_loop, name="live-experiment-runner", daemon=True)
    _ticker["thread"] = thread
    thread.start()


def _tick_loop() -> None:
    from .. import research_routes      # late import: research_routes imports this module
    while True:
        with _lock:
            runs = [run for run in _runs.values() if not run.closed]
        if not runs:
            return
        for run in runs:
            try:
                research_routes.live_payload(run.context)       # computes (and so observes) at most once per interval
                execution = get_mission_execution(run.execution_id)
                if execution is None or execution.get("finished_at"):
                    close_run(run.execution_id, (execution or {}).get("reason") or (execution or {}).get("state") or "ended")
            except Exception:      # the runner must never take the monitor down
                LOGGER.exception("live experiment tick failed")
        time.sleep(max(0.5, float(research_routes.LIVE_ASSESSMENT_INTERVAL_S)))


# --- injection into the monitor's software copy ---------------------------------------------------------------

def _noise_table(experiment: ArmedExperiment, index: int) -> np.ndarray:
    table = experiment.noise.get(index)
    if table is None:
        table = np.random.default_rng([experiment.seed, index, 29]).normal(0.0, 1.0, size=(NOISE_TABLE_S * int(HZ), 3))
        experiment.noise[index] = table
    return table


def apply_to_window(aircraft_id: str, t: np.ndarray, position: np.ndarray, velocity: np.ndarray) -> tuple[np.ndarray, np.ndarray, bool]:
    """Returns the (position, velocity, attack_active_at_last_sample) the monitor should read. Copies; the input is untouched."""
    with _lock:
        runs = [run for run in _runs.values() if not run.closed]
    if not runs:
        return position, velocity, False
    position, velocity = position.copy(), velocity.copy()
    active_last = False
    for run in runs:
        run.reached_t[aircraft_id] = max(run.reached_t.get(aircraft_id, 0.0), float(t[-1]))
        for experiment in run.experiments:
            stateless = [attack if (attack.target_aircraft == aircraft_id and attack.type in ("offset", "jump", "drift", "velocity_inconsistency"))
                         else AttackConfig(target_aircraft=attack.target_aircraft, **_NOOP) for attack in experiment.attacks]
            if any(attack.target_aircraft == aircraft_id and attack.type in ("offset", "jump", "drift", "velocity_inconsistency") for attack in experiment.attacks):
                out = inject(GpsStream(t, position, velocity), stateless, experiment.seed).stream
                position, velocity = out.position, out.velocity
            for index, attack in enumerate(experiment.attacks):
                if attack.target_aircraft != aircraft_id:
                    continue
                mask = (t >= attack.start_s) & (t < attack.end_s)
                if not mask.any():
                    continue
                if attack.type == "freeze":
                    anchor = experiment.anchors.get(index)
                    if anchor is None:
                        anchor = position[int(np.argmax(mask))].copy()
                        experiment.anchors[index] = anchor
                    position[mask] = anchor
                    velocity[mask] = 0.0
                elif attack.type == "noise":
                    table = _noise_table(experiment, index)
                    steps = np.clip(np.round(t[mask] * HZ).astype(int), 0, len(table) - 1)
                    noise = table[steps] * attack.magnitude
                    noise[:, 2] = 0.0
                    position[mask] += noise
            active_last = active_last or any(attack.target_aircraft == aircraft_id and attack.start_s <= float(t[-1]) < attack.end_s for attack in experiment.attacks)
    return position, velocity, active_last


def observe(aircraft_id: str, t_last: float, result: dict[str, Any], attacked: bool) -> None:
    with _lock:
        runs = [run for run in _runs.values() if not run.closed]
        for run in runs:
            series = run.samples.setdefault(aircraft_id, [])
            if series and t_last - series[-1][0] < SAMPLE_EVERY_S:
                continue
            series.append((round(float(t_last), 1), float(result["threat_score"]), str(result["tier"]), bool(attacked)))


# --- the run record -------------------------------------------------------------------------------------------

def _episodes(points: list[tuple[float, float, str, bool]], inside: Callable[[float], bool]) -> int:
    count, last = 0, None
    for t, _score, tier, _attacked in points:
        if tier in REACTION_TIERS and not inside(t):
            if last is None or t - last > FALSE_ALARM_GAP_S:
                count += 1
            last = t
    return count


def _downsample(points: list[tuple[float, float, str, bool]]) -> list[dict[str, Any]]:
    step = max(1, int(np.ceil(len(points) / MAX_STORED_POINTS)))
    return [{"t": t, "score": score, "tier": tier, "attacked": attacked} for t, score, tier, attacked in points[::step]]


def build_record(run: Run, experiment: ArmedExperiment, reason: str, ended_at: datetime) -> dict[str, Any]:
    attacks, caveats = [], []
    windows: dict[str, list[tuple[float, float]]] = {}
    for other in run.experiments:      # every attack of the flight excuses a reaction, not only this experiment's own (they run one after the other)
        for attack in other.attacks:
            windows.setdefault(attack.target_aircraft or "", []).append((attack.start_s, attack.end_s + HOLD_S))
    for attack in experiment.attacks:
        aircraft = attack.target_aircraft or ""
        points = run.samples.get(aircraft, [])
        reached = run.reached_t.get(aircraft, 0.0)
        item: dict[str, Any] = {"target_aircraft": aircraft, "type": attack.type, "start_s": attack.start_s, "duration_s": attack.duration_s,
                                "magnitude": attack.magnitude, "direction_deg": attack.direction_deg,
                                "reached": reached >= attack.start_s, "fully_run": reached >= attack.end_s}
        inside = [p for p in points if attack.start_s <= p[0] <= attack.end_s + HOLD_S]
        before = [p for p in points if attack.start_s - 5.0 <= p[0] < attack.start_s]
        tier_before = before[-1][2] if before else "Low"
        rank = ("Low", "Medium", "High", "Critical").index
        # a monitor that was already alarming when the attack began only "reacts" by escalating to a higher tier
        reactions = [p for p in inside if rank(p[2]) > rank(tier_before) and p[2] in REACTION_TIERS]
        alerts = [p for p in inside if p[2] in ALERT_TIERS and rank(p[2]) > rank(tier_before)]
        item.update(first_reaction_s=reactions[0][0] if reactions else None, first_reaction_tier=reactions[0][2] if reactions else None,
                    latency_s=round(reactions[0][0] - attack.start_s, 1) if reactions else None,
                    first_alert_s=alerts[0][0] if alerts else None, first_alert_latency_s=round(alerts[0][0] - attack.start_s, 1) if alerts else None,
                    peak_score=max((p[1] for p in inside), default=None), peak_tier=max((p[2] for p in inside), key=("Low", "Medium", "High", "Critical").index, default=None),
                    detected=bool(reactions), samples_in_window=len(inside), tier_before_attack=tier_before)
        item["outcome"] = ("not_reached" if not item["reached"] else "detected" if reactions
                           else "unclear" if tier_before in REACTION_TIERS else "missed")
        attacks.append(item)
    aircraft_ids = sorted(set(run.samples) | {attack.target_aircraft for attack in experiment.attacks if attack.target_aircraft})
    false_alarms, series = 0, {}
    for aircraft in aircraft_ids:
        points = run.samples.get(aircraft, [])
        spans = windows.get(aircraft, [])
        false_alarms += _episodes(points, lambda t, spans=spans: any(start <= t <= end for start, end in spans))
        series[aircraft] = _downsample(points)
    reached_attacks = [item for item in attacks if item["reached"]]
    outcome = ("no_attack" if not experiment.attacks else "not_reached" if not reached_attacks
               else "detected" if all(item["outcome"] == "detected" for item in reached_attacks)
               else "unclear" if any(item["outcome"] == "unclear" for item in reached_attacks) and not any(item["outcome"] == "missed" for item in reached_attacks)
               else "missed")
    if any(not item["fully_run"] and item["reached"] for item in attacks):
        caveats.append("The flight ended before at least one attack finished, so its measurements cover only part of it.")
    if any(not item["reached"] for item in attacks):
        caveats.append("The flight ended before at least one attack started: that attack never ran and is not counted as missed.")
    if any(item["outcome"] == "unclear" for item in attacks):
        caveats.append("The monitor was already at Medium or higher when an attack began and did not escalate, so a reaction to that attack cannot be told apart from the earlier alarm.")
    if len(run.experiments) > 1:
        caveats.append("Several experiments ran in this flight one after the other: reactions inside any of their attack windows are not counted as false alarms of the others.")
    if not any(run.samples.values()):
        caveats.append("The monitor produced no assessment during this run (it needs about 10 s of flight data), so nothing could be measured.")
    caveats.append("One flight is one sample: the latencies are measurements from this run, not a performance guarantee. The monitor holds the strongest "
                   f"evidence of the last {HOLD_S:g} s, and latency is measured from the scheduled start of the attack to the first Medium-or-higher assessment.")
    caveats.append("The attack is applied to the monitor's software copy of the GPS stream; the aircraft kept flying on its real position.")
    decisions = []
    for decision in list_research_decisions(200):
        stamp = datetime.fromisoformat(decision["created_at"].replace("Z", "+00:00"))
        if run.started_at <= stamp <= ended_at:
            decisions.append({"at": decision["created_at"], "after_run_start_s": round((stamp - run.started_at).total_seconds(), 1), "aircraft_id": decision["aircraft_id"],
                              "action": decision["action"], "tier": decision["tier"], "threat_score": decision["threat_score"], "by": decision["username"]})
    return {"kind": "live_run", "execution_id": run.execution_id, "deployment_id": run.deployment_id,
            "started_at": run.started_at.isoformat().replace("+00:00", "Z"), "ended_at": ended_at.isoformat().replace("+00:00", "Z"),
            "run_seconds": round((ended_at - run.started_at).total_seconds(), 1), "end_reason": reason, "seed": experiment.seed, "config_hash": experiment.config_hash,
            "roles": run.roles, "attacks": attacks, "false_alarms": false_alarms, "outcome": outcome, "operator_responses": decisions,
            "timeline": series, "assessments_recorded": sum(len(p) for p in run.samples.values()), "caveats": caveats,
            "reconfigurations": list(_reconfigurations.get(run.execution_id, []))}


def close_run(execution_id: str, reason: str) -> list[dict[str, Any]]:
    """Writes one run record per armed experiment. Safe to call twice; the second call does nothing."""
    with _lock:
        run = _runs.get(execution_id)
        if run is None or run.closed:
            return []
        run.closed = True
    ended_at = _now()
    saved = []
    for experiment in run.experiments:
        try:
            if experiment_results_available():
                saved.append(save_experiment_result(experiment.id, build_record(run, experiment, reason, ended_at)))
                _result_ids.setdefault(execution_id, []).append(saved[-1]["id"])
        except Exception:
            LOGGER.exception("could not store the run record of %s", experiment.id)
    with _lock:
        _runs.pop(execution_id, None)
    return saved


def attach_reconfiguration(execution_id: Optional[str], view: dict[str, Any]) -> None:
    """Adds a mission-continuity outcome to the run record of the flight it happened in (now, or when the record is written)."""
    if not execution_id:
        return
    with _lock:
        _reconfigurations.setdefault(execution_id, []).append(view)
        ids, current = list(_result_ids.get(execution_id, [])), list(_reconfigurations[execution_id])
    for result_id in ids:
        update_experiment_result_payload(result_id, {"reconfigurations": current})


def close_all(reason: str) -> None:
    with _lock:
        ids = list(_runs)
    for execution_id in ids:
        close_run(execution_id, reason)


def reset_for_tests() -> None:
    with _lock:
        _runs.clear()
        _reconfigurations.clear()
        _result_ids.clear()
