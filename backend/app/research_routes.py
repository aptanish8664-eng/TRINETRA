"""Read-only threat assessment API (offline replay today; live PX4 residual inputs do not exist yet).

Nothing here executes anything: /assess returns recommendations, /decisions records the operator's choice.
Requests and features are guarded against ground-truth fields (labels, attack schedule). Replay ground truth stays
on the server and is revealed only by the evaluation endpoint after a replay is marked finished.
"""
from __future__ import annotations

import json
import os
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .attack_lab_routes import load_pinned_plan
from .auth_routes import require_csrf, require_permission
from .authorization import Permission, has_permission
from .persistence.database import add_audit_event, get_attack_experiment, list_attack_experiments, list_research_decisions, record_research_decision
from .research import threat_engine as engine
from .research.attacks import AttackConfig, GroundTruthLeak, assert_no_ground_truth_deep
from .research.threat_data import DT, FEATURE_COLUMNS, FEATURE_COLUMNS_LONG, HZ, compute_features, residual_series, simulate_aircraft
from .research.threat_models import ARTIFACT_DIR, RECOVERY_S, ThreatModel
from .mission_planning.geometry import to_enu
from .security import AuthContext

router = APIRouter()
model_dir: Path = Path(os.environ.get("TRINETRA_THREAT_MODEL_DIR", str(ARTIFACT_DIR)))
_loaded: dict[str, Any] = {"dir": None, "model": None, "error": None}
REPLAY_STORE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
MAX_REPLAYS = 8
MAX_REPLAY_S = 900.0
EVAL_STRIDE_S = 2.0
EVAL_WINDOW_S = 120.0
HOLD_S = 30.0  # alert hold: the strongest evidence of the last 30 s stays visible (the windowed residual only sees changes)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def get_model() -> Optional[ThreatModel]:
    if _loaded["dir"] != str(model_dir):
        _loaded.update(dir=str(model_dir), model=None, error=None)
        try:
            _loaded["model"] = ThreatModel(model_dir)
        except Exception as error:  # missing or tampered artifact: report, never guess
            _loaded["error"] = f"{type(error).__name__}: {error}"
    return _loaded["model"]


class Sample(StrictModel):
    t: float
    gps_position: list[float] = Field(min_length=3, max_length=3)
    gps_velocity: list[float] = Field(min_length=3, max_length=3)
    imu_velocity: list[float] = Field(min_length=3, max_length=3)


class Peer(StrictModel):
    aircraft_id: str = Field(max_length=16)
    samples: list[Sample] = Field(min_length=5, max_length=600)


class Candidate(StrictModel):
    aircraft_id: str = Field(max_length=16)
    role: str = Field(max_length=16)
    battery_pct: float = Field(ge=0, le=100)
    distance_to_task_m: float = Field(default=0.0, ge=0)
    workload: float = Field(default=0.0, ge=0, le=1)


class AssessRequest(StrictModel):
    aircraft_id: str = Field(pattern=r"^px4-[1-9][0-9]{0,2}$")
    role: Literal["primary", "standby", "decoy"]
    battery_pct: float = Field(ge=0, le=100)
    remaining_fraction: float = Field(default=1.0, ge=0, le=1)
    samples: list[Sample] = Field(min_length=5, max_length=600)
    peers: list[Peer] = Field(default_factory=list, max_length=4)
    handover_candidates: list[Candidate] = Field(default_factory=list, max_length=5)
    min_battery_pct: float = Field(default=engine.DEFAULT_MIN_BATTERY_PCT, ge=0, le=100)
    candidate_roles: list[Literal["standby", "decoy"]] = Field(default_factory=lambda: ["standby", "decoy"], min_length=1, max_length=2)


def _arrays(samples: list[Sample]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (np.array([s.t for s in samples], dtype=float), np.array([s.gps_position for s in samples], dtype=float),
            np.array([s.gps_velocity for s in samples], dtype=float), np.array([s.imu_velocity for s in samples], dtype=float))


def assess_arrays(model: ThreatModel, t, gps_position, gps_velocity, imu_velocity, role: str, battery_pct: float, remaining_fraction: float,
                  peer_res10: Optional[np.ndarray], candidates: list[dict], min_battery_pct: float, candidate_roles: list[str],
                  peer_res30: Optional[np.ndarray] = None) -> dict[str, Any]:
    """Pure assessment of the LAST sample of a window from sensor arrays only."""
    features = compute_features(t, gps_position, gps_velocity, imu_velocity, peer_res10, 1 + (0 if peer_res10 is None else 1), peer_res30,
                                 include_long=any(name in FEATURE_COLUMNS_LONG for name in model.feature_columns))
    all_confidence = model.confidence(features)
    recent = np.flatnonzero(t >= t[-1] - HOLD_S)
    pick = int(recent[len(recent) - 1 - int(np.argmax(all_confidence[recent][::-1]))])  # latest of the strongest
    row = features.iloc[[pick]]
    confidence = float(all_confidence[pick])
    values = {name: float(row[name].iloc[0]) for name in FEATURE_COLUMNS}
    sev = float(engine.severity(values["res_pos_w10"], values["res_rate"], values["res_vel"]))
    impact = float(engine.mission_impact(role, battery_pct, remaining_fraction))
    score = float(engine.threat_score(confidence, sev, impact))
    tier = model.tiers.tier(score)
    ranked_candidates = engine.handover_candidates(candidates, min_battery_pct, candidate_roles)
    return {
        "anomaly_confidence": round(confidence, 4), "severity": round(sev, 4), "mission_impact": round(impact, 4),
        "threat_score": round(score, 1), "tier": tier, "threat_score_is_probability": False, "threat_score_formula": engine.THREAT_SCORE_FORMULA,
        "evidence": engine.evidence_items(values, model.reference), "handover_candidates": ranked_candidates,
        "actions": engine.rank_actions(tier, confidence, role, battery_pct, ranked_candidates, min_battery_pct),
        "t": float(t[-1]), "model_id": model.model_id, "evidence_from_s": float(t[pick]), "held": pick != len(t) - 1, "hold_s": HOLD_S,
    }


def _reject_ground_truth(payload: Any) -> None:
    try:
        assert_no_ground_truth_deep(payload, "request")
    except GroundTruthLeak as error:
        raise HTTPException(status_code=422, detail={"code": "ground_truth_rejected", "message": str(error)}) from error


@router.get("/api/research/model-info")
def model_info(_context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    model = get_model()
    if model is None:
        return {"available": False, "reason": _loaded["error"] or "No trained model artifact found. Run: python -m app.research.threat_models",
                "formula": engine.THREAT_SCORE_FORMULA}
    metrics = model.metrics
    selected = metrics["models"][metrics["selected_model"]]
    return {
        "available": True, "model_id": model.model_id, "trained_at": metrics["trained_at"], "selected_model": metrics["selected_model"],
        "tiers": metrics["tiers"]["thresholds"], "formula": engine.THREAT_SCORE_FORMULA, "feature_columns": metrics["feature_columns"],
        "test_metrics": {k: v for k, v in (selected.get("test") or {}).items() if not k.startswith("by_")}, "dataset": metrics["dataset"],
        "calibration": {"method": metrics["calibration"]["method"], "brier_test_calibrated": metrics["calibration"].get("brier_test_calibrated")},
        "limits": metrics["limits"], "live_residual_inputs_available": False,
        "min_battery_pct_default": engine.DEFAULT_MIN_BATTERY_PCT, "sample_hz": HZ,
    }


@router.post("/api/research/assess")
def assess(body: dict = Body(...), _context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
           _csrf: AuthContext = Depends(require_csrf)) -> dict:
    _reject_ground_truth(body)
    try:
        request = AssessRequest.model_validate(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "assess_invalid", "message": "The assessment window is invalid.",
                                                      "issues": [{"field": ".".join(str(p) for p in item["loc"]), "message": item["msg"]} for item in error.errors()]}) from error
    model = get_model()
    if model is None:
        raise HTTPException(status_code=503, detail={"code": "model_unavailable", "message": "No trained threat model is available. Run: python -m app.research.threat_models"})
    t, gp, gv, iv = _arrays(request.samples)
    if np.any(np.diff(t) <= 0):
        raise HTTPException(status_code=422, detail={"code": "assess_invalid", "message": "Sample times must be strictly increasing."})
    peer_res = peer_res30 = None
    peer_series, peer_series30 = [], []
    for peer in request.peers:
        pt, pgp, _pgv, piv = _arrays(peer.samples)
        if len(pt) >= len(t):
            peer_series.append(residual_series(pt[-len(t):], pgp[-len(t):], piv[-len(t):], 10.0))
            peer_series30.append(residual_series(pt[-len(t):], pgp[-len(t):], piv[-len(t):], 30.0))
    if peer_series:
        peer_res, peer_res30 = np.median(np.vstack(peer_series), axis=0), np.median(np.vstack(peer_series30), axis=0)
    result = assess_arrays(model, t, gp, gv, iv, request.role, request.battery_pct, request.remaining_fraction, peer_res,
                           [item.model_dump() for item in request.handover_candidates], request.min_battery_pct, list(request.candidate_roles), peer_res30)
    result["aircraft_id"] = request.aircraft_id
    return result


class DecisionRequest(StrictModel):
    aircraft_id: str = Field(pattern=r"^px4-[1-9][0-9]{0,2}$")
    action: Literal["continue_monitor", "increase_monitoring", "dead_reckoning_fallback", "isolate_aircraft", "handover_to_standby", "return_to_launch"]
    handover_candidate: Optional[str] = Field(default=None, pattern=r"^px4-[1-9][0-9]{0,2}$")
    source: Literal["live_telemetry", "offline_replay"]
    source_name: Optional[str] = Field(default=None, max_length=120)
    tier: Optional[Literal["Low", "Medium", "High", "Critical"]] = None
    threat_score: Optional[float] = Field(default=None, ge=0, le=100)


@router.post("/api/research/decisions", status_code=201)
def record_decision(request: Request, body: dict = Body(...), context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
                    _csrf: AuthContext = Depends(require_csrf)) -> dict:
    """Records the operator's choice. It does not command any aircraft."""
    _reject_ground_truth(body)
    try:
        decision = DecisionRequest.model_validate(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "decision_invalid", "message": "The decision is invalid.",
                                                      "issues": [{"field": ".".join(str(p) for p in item["loc"]), "message": item["msg"]} for item in error.errors()]}) from error
    if decision.action == "handover_to_standby" and not decision.handover_candidate:
        raise HTTPException(status_code=422, detail={"code": "decision_invalid", "message": "Choose a handover candidate aircraft.", "issues": [{"field": "handover_candidate", "message": "required for handover"}]})
    if decision.action != "handover_to_standby" and decision.handover_candidate:
        raise HTTPException(status_code=422, detail={"code": "decision_invalid", "message": "A handover candidate is only valid for a handover.", "issues": [{"field": "handover_candidate", "message": "not allowed"}]})
    saved = record_research_decision(context.user.id, context.user.username, decision.aircraft_id, decision.action, decision.handover_candidate,
                                     decision.source, decision.source_name, decision.tier, decision.threat_score)
    add_audit_event(action="research_decision_recorded", outcome="success", reason="Operator choice recorded; no command was sent.",
                    request_id=getattr(request.state, "request_id", "request-unavailable"), actor_id=context.user.id, actor_name=context.user.full_name,
                    target_type="research_decision", target_id=saved["id"], metadata={"aircraft_id": decision.aircraft_id, "action": decision.action, "source": decision.source})
    return saved


@router.get("/api/research/decisions")
def decisions(_context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    return {"decisions": list_research_decisions(50)}


# --- offline replays (synthetic telemetry over a pinned experiment) -----------------------------------------------

class ReplayRequest(StrictModel):
    experiment_id: str = Field(min_length=1, max_length=64)


def _route_path_enu(aircraft) -> tuple[np.ndarray, float]:
    route = aircraft.route
    origin = (aircraft.start_location.latitude, aircraft.start_location.longitude)
    points = [to_enu(origin, w.latitude, w.longitude)[:2] for w in route.waypoints]
    return np.array(points if len(points) > 1 else [(0.0, 0.0), (1.0, 1.0)]), float(aircraft.speed_mps or 5.0)


@router.post("/api/research/replays", status_code=201)
def create_replay(body: dict = Body(...), context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
                  _csrf: AuthContext = Depends(require_csrf)) -> dict:
    """Simulates telemetry for a pinned experiment. The response contains sensors only; the schedule stays server-side."""
    try:
        request = ReplayRequest.model_validate(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "replay_invalid", "message": "experiment_id is required."}) from error
    visible = {item["id"] for item in list_attack_experiments(context.user.id, include_all=has_permission(context.user.role, Permission.MISSION_READ_ALL))}
    experiment = get_attack_experiment(request.experiment_id) if request.experiment_id in visible else None
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    plan, _scenario, issues = load_pinned_plan(context, experiment["scenario_id"], experiment["scenario_version_id"])
    if plan is None:
        raise HTTPException(status_code=422, detail={"code": "replay_invalid", "message": issues[0]["message"]})
    seed = int(experiment["seed"])
    attacks = [AttackConfig.model_validate(item) for item in experiment["config"]["attacks"]]
    aircraft_payload, sims = [], {}
    duration = 0.0
    routed = [item for item in plan.aircraft if item.route and item.start_location]
    if not routed:
        raise HTTPException(status_code=422, detail={"code": "replay_invalid", "message": "The pinned plan has no generated routes to replay."})
    for index, item in enumerate(routed):
        path, speed = _route_path_enu(item)
        total_s = min(float(item.route.duration_s), MAX_REPLAY_S)
        rng = np.random.default_rng([seed, index, 77])
        sim = simulate_aircraft(rng, item.uav_id, item.role or "standby", path, [a for a in attacks if a.target_aircraft == item.uav_id],
                                seed + 101 * index, max(total_s, 20.0), speed=speed)
        sims[item.uav_id] = {"sim": sim, "reassignable": item.reassignable}
        duration = max(duration, sim.total_s)
        aircraft_payload.append({
            "aircraft_id": item.uav_id, "role": item.role, "reassignable": item.reassignable,
            "t": [round(float(v), 2) for v in sim.t],
            "gps_position": np.round(sim.gps.position, 3).tolist(), "gps_velocity": np.round(sim.gps.velocity, 3).tolist(),
            "imu_velocity": np.round(sim.imu_velocity, 3).tolist(), "battery_pct": np.round(sim.battery_pct, 2).tolist(),
        })
    replay_id = uuid.uuid4().hex
    REPLAY_STORE[replay_id] = {"user_id": context.user.id, "name": experiment["name"], "sims": sims, "attacks": experiment["config"]["attacks"],
                               "finished": False, "min_battery_pct": experiment["config"]["handover"]["min_battery_pct"],
                               "candidate_roles": experiment["config"]["handover"]["candidate_roles"]}
    while len(REPLAY_STORE) > MAX_REPLAYS:
        REPLAY_STORE.popitem(last=False)
    return {"replay_id": replay_id, "experiment_name": experiment["name"], "experiment_id": experiment["id"], "hz": HZ, "duration_s": round(duration, 1),
            "aircraft": aircraft_payload, "min_battery_pct": experiment["config"]["handover"]["min_battery_pct"],
            "candidate_roles": experiment["config"]["handover"]["candidate_roles"], "source": "offline_replay"}


def _replay(replay_id: str, context: AuthContext) -> dict[str, Any]:
    replay = REPLAY_STORE.get(replay_id)
    if replay is None or replay["user_id"] != context.user.id:
        raise HTTPException(status_code=404, detail="Replay not found.")
    return replay


@router.post("/api/research/replays/{replay_id}/finish")
def finish_replay(replay_id: str, context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)), _csrf: AuthContext = Depends(require_csrf)) -> dict:
    _replay(replay_id, context)["finished"] = True
    return {"finished": True}


@router.get("/api/research/replays/{replay_id}/evaluation")
def replay_evaluation(replay_id: str, context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    """Evaluation summary. Reveals the attack schedule, so it is refused until the replay is marked finished."""
    replay = _replay(replay_id, context)
    if not replay["finished"]:
        raise HTTPException(status_code=409, detail={"code": "replay_not_finished", "message": "The attack schedule is revealed only after the replay ends."})
    model = get_model()
    if model is None:
        raise HTTPException(status_code=503, detail={"code": "model_unavailable", "message": "No trained threat model is available."})
    results = []
    for aircraft_id, entry in replay["sims"].items():
        sim = entry["sim"]
        window = 600  # the whole history up to the API limit: the features use the flight's own early-flight scale
        stride = max(1, int(EVAL_STRIDE_S * HZ))
        tiers, times = [], []
        for end in range(stride, len(sim.t) + 1, stride):
            start = max(0, end - window)
            result = assess_arrays(model, sim.t[start:end], sim.gps.position[start:end], sim.gps.velocity[start:end], sim.imu_velocity[start:end],
                                   sim.role, float(sim.battery_pct[end - 1]), 1.0 - float(sim.t[end - 1]) / max(sim.total_s, 1.0), None, [], replay["min_battery_pct"], replay["candidate_roles"])
            tiers.append(result["tier"])
            times.append(float(sim.t[end - 1]))
        alert = [tier != "Low" for tier in tiers]
        sustained_alert = [index >= 1 and alert[index] and alert[index - 1] for index in range(len(alert))]
        attacked = sim.truth.is_attacked
        start_t = float(sim.t[int(np.argmax(attacked))]) if attacked.any() else None
        first = next((times[i] for i, flag in enumerate(sustained_alert) if flag and (start_t is None or times[i] >= start_t)), None)
        onsets = [i for i, flag in enumerate(sustained_alert) if flag and (i == 0 or not sustained_alert[i - 1])]
        # Un-attacked time within RECOVERY_S after an attack ends is neither normal flight nor a false alarm (the residual window still sees the GPS return).
        last_attacked = float(sim.t[np.flatnonzero(attacked)[-1]]) if attacked.any() else None
        recovering = lambda moment: last_attacked is not None and last_attacked < moment <= last_attacked + RECOVERY_S
        attacked_at = lambda moment: bool(attacked[min(int(round(moment / DT)), len(attacked) - 1)])
        false_alarms = sum(1 for i in onsets if not attacked_at(times[i]) and not recovering(times[i]))
        recovery_samples = int(sum(1 for moment in sim.t if recovering(float(moment)) and not attacked_at(float(moment))))
        clean_hours = max(float(((~attacked).sum() - recovery_samples) * DT) / 3600.0, 1e-9)
        error = np.linalg.norm((sim.gps.position - sim.true_position)[:, :2], axis=1)
        results.append({
            "aircraft_id": aircraft_id, "role": sim.role, "attacked": bool(attacked.any()),
            "attack_start_s": start_t, "detection_latency_s": round(first - start_t, 1) if (first is not None and start_t is not None) else None,
            "detected": first is not None if attacked.any() else None, "false_alarms": false_alarms,
            "false_alarms_per_hour": round(false_alarms / clean_hours, 2), "max_gps_position_error_m": round(float(error.max()), 1),
            "mean_gps_position_error_m": round(float(error.mean()), 1), "max_tier": max(tiers, key=engine.TIERS.index) if tiers else "Low",
            "mission_completion_pct": None, "handover_time_s": None,
        })
    return {"experiment_name": replay["name"], "attacks": replay["attacks"], "aircraft": results,
            "not_measured": {"mission_completion_pct": "needs the experiment runner", "handover_time_s": "no handover is executed in offline replay"}}
