"""Attack Lab: simulated GPS-attack experiments pinned to an exact schema-2 scenario version.

An experiment DEFINES simulated, software-only attacks. Nothing is executed here: there is no experiment runner yet,
nothing is sent to PX4 or Gazebo, and attack details are never returned by Mission Control or Scenario Lab endpoints.
GENERAL accounts create/edit; OPERATOR accounts are read-only (assumption recorded in docs/OVERNIGHT_RUN_LOG.md).

Presets define the ATTACK ONLY. An attack never changes an aircraft's role and no preset scripts a response:
isolation and handover are the operator's decisions in the Threat Monitor. The stored `response_policy` field is
kept for compatibility and is always `operator_decides`.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .auth_routes import require_csrf, require_permission
from .authorization import Permission, has_permission
from .persistence.database import (
    add_audit_event, delete_attack_experiment, deployment_times_for_versions, get_attack_experiment, get_scenario,
    latest_mission_deployment, list_attack_experiments, list_scenarios, save_attack_experiment,
)
from .research.attacks import ATTACK_SPECS, ATTACK_LAB_NAME, AttackConfig, attack_limits
from .schemas import ScenarioIntent
from .security import AuthContext

router = APIRouter()
MAX_ATTACKS = 10
RESPONSE_POLICY = "operator_decides"  # the only response: the operator decides in the Threat Monitor
DEFAULT_MIN_BATTERY_PCT = 40.0
SEED_MAX = 2**31 - 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExperimentAttack(AttackConfig):
    target_aircraft: str = Field(max_length=16, pattern=r"^px4-[1-9][0-9]{0,2}$")  # required for experiments


class HandoverConfig(StrictModel):
    min_battery_pct: float = Field(default=DEFAULT_MIN_BATTERY_PCT, ge=0, le=100, allow_inf_nan=False)
    candidate_roles: list[Literal["standby", "decoy"]] = Field(default_factory=lambda: ["standby", "decoy"], min_length=1, max_length=2)


class Measurements(StrictModel):
    detection_latency: bool = True
    false_alarms: bool = True
    position_error: bool = True
    mission_completion: bool = True
    handover_time: bool = True


class ExperimentConfig(StrictModel):
    attacks: list[ExperimentAttack] = Field(default_factory=list, max_length=MAX_ATTACKS)
    # Kept for stored-record compatibility; any earlier value is read as operator_decides. Nothing scripts a response.
    response_policy: Literal["operator_decides"] = RESPONSE_POLICY
    # Eligibility settings for the operator's later handover choice; they never trigger a handover.
    handover: HandoverConfig = Field(default_factory=HandoverConfig)
    measurements: Measurements = Field(default_factory=Measurements)
    allow_overlap: bool = False

    @field_validator("response_policy", mode="before")
    @classmethod
    def fixed_policy(cls, _value: Any) -> str:
        return RESPONSE_POLICY


class ExperimentRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    scenario_id: str = Field(min_length=1, max_length=64)
    scenario_version_id: str = Field(min_length=1, max_length=64)
    preset_id: Optional[str] = Field(default=None, max_length=40)
    seed: int = Field(default=1, ge=0, le=SEED_MAX)
    config: ExperimentConfig = Field(default_factory=ExperimentConfig)
    status: Literal["draft", "ready"] = "draft"

    @field_validator("preset_id")
    @classmethod
    def known_preset(cls, value: Optional[str]) -> Optional[str]:
        preset = canonical_preset_id(value)
        if value is not None and preset is None:
            raise ValueError("Unknown preset")
        return preset

    @model_validator(mode="after")
    def trim_name(self) -> "ExperimentRequest":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("The experiment name cannot be blank")
        return self


class Issue(StrictModel):
    code: str
    message: str
    field: Optional[str] = None


# --- presets ---------------------------------------------------------------------------------------------------

# One line on what the attack does and one on why it is useful. `min_aircraft` is how many aircraft the ATTACK
# itself needs; a preset is kept out of the list (with `needs` as the reason) when the pinned plan has fewer.
PRESET_INFO: dict[str, dict[str, Any]] = {
    "baseline": {"label": "Baseline (no attack)", "min_aircraft": 1, "needs": "",
                 "does": "No attack: the GPS data is left untouched.",
                 "useful": "Shows how quiet the monitor stays and how many false alarms it raises."},
    "slow_drift": {"label": "Slow drift", "min_aircraft": 1, "needs": "",
                   "does": "The primary aircraft's reported position creeps away at 0.5 m/s for 40% of the route.",
                   "useful": "The hardest attack to notice: measures how long a gradual error stays hidden."},
    "rapid_jump": {"label": "Rapid jump", "min_aircraft": 1, "needs": "",
                   "does": "The primary aircraft's reported position leaps 150 m for 10 seconds.",
                   "useful": "The easiest attack to notice: measures the fastest detection time."},
    "strong_offset": {"label": "Strong offset", "min_aircraft": 1, "needs": "",
                      "does": "The primary aircraft's reported position is held 150 m away for 60% of the route.",
                      "useful": "A sustained, obvious error: measures detection and how the options offered to the operator hold up."},
    "noise_burst": {"label": "Noise burst", "min_aircraft": 1, "needs": "",
                    "does": "15 m of random scatter is added to the primary aircraft's reported position for 30% of the route.",
                    "useful": "Checks that jitter is told apart from a real displacement."},
    "paired_drift": {"label": "Drift on two aircraft", "min_aircraft": 2,
                     "needs": "It attacks two aircraft at once, so the plan needs a second aircraft.",
                     "does": "The same 0.5 m/s drift is applied to the primary and to a second aircraft at the same time.",
                     "useful": "The monitor compares aircraft with each other: measures detection when the comparison aircraft is attacked too."},
}
for _item in PRESET_INFO.values():
    _item["summary"] = f"{_item['does']} {_item['useful']}"
PRESET_IDS: tuple[str, ...] = tuple(PRESET_INFO)
# Preset ids stored before presets were renamed to describe the attack. Stored rows are not rewritten.
LEGACY_PRESET_IDS = {"mild_drift": "slow_drift", "rapid_spoof": "rapid_jump", "role_handover": "strong_offset",
                     "isolation_fallback": "slow_drift"}


def canonical_preset_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = LEGACY_PRESET_IDS.get(value, value)
    return value if value in PRESET_INFO else None


def preset_catalogue(aircraft_count: Optional[int] = None) -> dict[str, list[dict[str, Any]]]:
    """Presets offered for a plan with `aircraft_count` aircraft, and the ones kept out with the reason."""
    offered, hidden = [], []
    for key, item in PRESET_INFO.items():
        entry = {"id": key, "label": item["label"], "summary": item["summary"], "does": item["does"],
                 "useful": item["useful"], "min_aircraft": item["min_aircraft"]}
        if aircraft_count is not None and aircraft_count < item["min_aircraft"]:
            hidden.append({**entry, "reason": item["needs"]})
        else:
            offered.append(entry)
    return {"presets": offered, "hidden": hidden}


def _aircraft_info(plan) -> dict[str, dict[str, Any]]:
    return {item.uav_id: {"role": item.role, "reassignable": item.reassignable, "duration_s": item.route.duration_s if item.route else None}
            for item in plan.aircraft}


def build_preset(preset_id: str, plan, seed: int = 1) -> dict[str, Any]:
    """Preset defaults sized from the pinned plan's real route durations. Editable after loading."""
    info = _aircraft_info(plan)
    primary = next((uav for uav, item in info.items() if item["role"] == "primary"), next(iter(info), None))
    duration = float((info.get(primary) or {}).get("duration_s") or 0.0)

    def window(start_fraction: float, length_fraction: float, cap: float) -> tuple[float, float]:
        start = round(duration * start_fraction, 1)
        return start, round(max(1.0, min(cap, duration * length_fraction, duration - start)), 1)

    def attack(target: str, kind: str, start: float, length: float, magnitude: float) -> dict[str, Any]:
        assert kind in ATTACK_SPECS  # ranges are enforced by AttackConfig when the experiment is validated
        return {"target_aircraft": target, "type": kind, "start_s": start, "duration_s": length, "magnitude": magnitude, "direction_deg": None}

    attacks: list[dict[str, Any]] = []
    if preset_id != "baseline" and primary is not None and duration > 0:
        if preset_id == "slow_drift":
            attacks = [attack(primary, "drift", *window(0.3, 0.4, 600), 0.5)]
        elif preset_id == "rapid_jump":
            attacks = [attack(primary, "jump", *window(0.4, 0.1, 10), 150.0)]
        elif preset_id == "strong_offset":
            attacks = [attack(primary, "offset", *window(0.3, 0.6, 600), 150.0)]
        elif preset_id == "noise_burst":
            attacks = [attack(primary, "noise", *window(0.35, 0.3, 600), 15.0)]
        elif preset_id == "paired_drift":
            attacks = [attack(primary, "drift", *window(0.3, 0.4, 600), 0.5)]
            second = next((uav for uav in info if uav != primary and (info[uav]["duration_s"] or 0) > 0), None)
            if second is not None:
                duration = float(info[second]["duration_s"])
                attacks.append(attack(second, "drift", *window(0.3, 0.4, 600), 0.5))
    return {"preset_id": preset_id, "seed": seed, "config": {
        "attacks": attacks, "response_policy": RESPONSE_POLICY, "allow_overlap": False,
        "handover": {"min_battery_pct": DEFAULT_MIN_BATTERY_PCT, "candidate_roles": ["standby", "decoy"]},
        "measurements": Measurements().model_dump(),
    }}


# --- validation ------------------------------------------------------------------------------------------------

def _pydantic_issues(error: ValidationError) -> list[dict[str, Any]]:
    return [{"code": "invalid_field", "message": item["msg"].removeprefix("Value error, "),
             "field": ".".join(str(part) for part in item["loc"])} for item in error.errors()]


def load_pinned_plan(context: AuthContext, scenario_id: str, version_id: str) -> tuple[Any, dict, list[dict[str, Any]]]:
    """Returns (plan, scenario, issues). The version must exist, belong to the scenario and be schema 2."""
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    scenario = get_scenario(scenario_id, context.user.id, include_all=include_all)
    if scenario is None:
        return None, {}, [{"code": "scenario_not_found", "message": "The referenced scenario does not exist.", "field": "scenario_id"}]
    version = next((item for item in scenario["versions"] if item["id"] == version_id), None)
    if version is None:
        return None, scenario, [{"code": "version_not_found", "message": "The referenced scenario version does not exist.", "field": "scenario_version_id"}]
    intent = ScenarioIntent.model_validate(version["intent"])
    if intent.schema_version != 2 or intent.mission_plan is None:
        return None, scenario, [{"code": "version_not_schema_2", "message": "Attack Lab needs a schema-2 mission plan version.", "field": "scenario_version_id"}]
    return intent.mission_plan, scenario, []


def validate_experiment(request: ExperimentRequest, plan) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    info = _aircraft_info(plan)
    attacks = request.config.attacks
    for index, attack in enumerate(attacks):
        field = f"config.attacks.{index}"
        if attack.target_aircraft not in info:
            issues.append({"code": "target_not_in_plan", "message": f"{attack.target_aircraft} is not an aircraft of the referenced plan.", "field": f"{field}.target_aircraft"})
            continue
        duration = info[attack.target_aircraft]["duration_s"]
        if duration is None:
            issues.append({"code": "route_missing", "message": f"{attack.target_aircraft} has no generated route, so the attack window cannot be checked.", "field": f"{field}.start_s"})
        elif attack.end_s > duration:
            issues.append({"code": "attack_outside_route", "message": f"The attack ends at {attack.end_s:g} s but {attack.target_aircraft}'s route lasts about {duration:g} s.", "field": f"{field}.duration_s"})
    if not request.config.allow_overlap:
        by_target: dict[str, list[tuple[float, float, int]]] = {}
        for index, attack in enumerate(attacks):
            by_target.setdefault(attack.target_aircraft, []).append((attack.start_s, attack.end_s, index))
        for target, windows in by_target.items():
            windows.sort()
            for (s1, e1, i1), (s2, e2, i2) in zip(windows, windows[1:]):
                if s2 < e1:
                    issues.append({"code": "attacks_overlap", "message": f"Attacks {i1 + 1} and {i2 + 1} overlap on {target}; enable overlap explicitly to allow it.", "field": f"config.attacks.{i2}.start_s"})
    preset = request.preset_id
    if preset == "baseline" and attacks:
        issues.append({"code": "preset_baseline_has_attacks", "message": "The baseline preset has no attacks; remove them or clear the preset.", "field": "config.attacks"})
    if preset is not None and preset != "baseline" and not attacks:
        issues.append({"code": "preset_needs_attack", "message": f"The {PRESET_INFO[preset]['label']} preset needs at least one attack.", "field": "config.attacks"})
    if preset is not None and len(info) < PRESET_INFO[preset]["min_aircraft"]:
        issues.append({"code": "preset_needs_two_aircraft", "field": "preset_id",
                       "message": f"The {PRESET_INFO[preset]['label']} preset is not available for this plan. {PRESET_INFO[preset]['needs']}"})
    return issues


def _view(record: dict[str, Any], latest_version_by_scenario: dict[str, str]) -> dict[str, Any]:
    latest = latest_version_by_scenario.get(record["scenario_id"])
    return {**record, "preset_id": canonical_preset_id(record.get("preset_id")),
            "config": {**record["config"], "response_policy": RESPONSE_POLICY},
            "superseded": latest is not None and latest != record["scenario_version_id"], "executed": False,
            "note": "Defines a simulated, software-only attack. It is not executed until the experiment runner exists."}


def _latest_versions(context: AuthContext, scenario_ids: set[str]) -> dict[str, str]:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    result = {}
    for scenario_id in scenario_ids:
        scenario = get_scenario(scenario_id, context.user.id, include_all=include_all)
        if scenario and scenario["versions"]:
            result[scenario_id] = scenario["versions"][0]["id"]
    return result


def _require_general(context: AuthContext) -> None:
    if context.user.role != "GENERAL":
        raise HTTPException(status_code=403, detail={"code": "read_only_role", "message": "Operator accounts can view Attack Lab experiments but not create or edit them."})


def _parse(body: dict) -> ExperimentRequest:
    try:
        return ExperimentRequest.model_validate(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "experiment_invalid", "message": "The experiment is invalid.", "issues": _pydantic_issues(error)}) from error


def _checked(context: AuthContext, request: ExperimentRequest):
    plan, _scenario, issues = load_pinned_plan(context, request.scenario_id, request.scenario_version_id)
    if plan is not None:
        issues = validate_experiment(request, plan)
    if issues:
        raise HTTPException(status_code=422, detail={"code": "experiment_invalid", "message": issues[0]["message"], "issues": issues})
    return plan


# --- routes ----------------------------------------------------------------------------------------------------

@router.get("/api/attack-lab/limits")
def attack_lab_limits(_context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    return {**attack_limits(), "name": ATTACK_LAB_NAME, "max_attacks": MAX_ATTACKS, "seed": {"min": 0, "max": SEED_MAX},
            "response": {"policy": RESPONSE_POLICY, "note": "Presets and experiments define the attack only. Isolation and handover are the operator's decisions in the Threat Monitor."},
            "handover": {"min_battery_pct": DEFAULT_MIN_BATTERY_PCT, "roles": ["standby", "decoy"],
                         "label": "Handover eligibility: minimum battery (%)",
                         "note": "Used later, when the operator considers a handover: an aircraft below this battery level is not offered. It never triggers a handover."},
            "presets": preset_catalogue()["presets"], "executes": False}


def _area_summary(plan) -> str:
    area = plan.operating_area
    if area is None:
        return "no operating area"
    if area.kind == "circle":
        return f"circle, radius {area.radius_m:g} m" if area.radius_m is not None else "circle"
    return f"polygon, {len(area.vertices)} vertices"


@router.get("/api/attack-lab/missions")
def list_missions(context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    """Saved schema-2 scenarios and their versions for the mission picker. Carries no attack or experiment data."""
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    current = latest_mission_deployment(context.user.id, include_all=include_all)
    missions, legacy_only = [], 0
    for summary in list_scenarios(context.user.id, include_all=include_all):
        scenario = get_scenario(summary["id"], context.user.id, include_all=include_all)
        if scenario is None:
            continue
        deployed_at = deployment_times_for_versions({item["id"] for item in scenario["versions"]})
        versions = []
        for index, version in enumerate(scenario["versions"]):
            intent = version.get("intent") or {}
            if intent.get("schema_version") != 2 or intent.get("mission_plan") is None:
                continue
            try:
                plan = ScenarioIntent.model_validate(intent).mission_plan
            except ValidationError:
                continue
            aircraft = [{"uav_id": uav, **item} for uav, item in _aircraft_info(plan).items()]
            durations = [item["duration_s"] for item in aircraft if item["duration_s"]]
            versions.append({
                "id": version["id"], "version_number": version["version_number"], "config_hash": version["config_hash"],
                "created_at": version["created_at"], "latest": index == 0, "superseded": index != 0,
                "deployed": version["id"] in deployed_at, "deployed_at": deployed_at.get(version["id"]),
                "in_mission_control": bool(current and current["scenario_version_id"] == version["id"]),
                "aircraft": aircraft, "estimated_duration_s": max(durations) if durations else None,
                "area_summary": _area_summary(plan), "objectives": list(plan.objectives),
                "presets": preset_catalogue(len(aircraft)),
            })
        if versions:
            missions.append({"scenario_id": scenario["id"], "title": scenario["title"], "versions": versions})
        else:
            legacy_only += 1
    return {"missions": missions, "legacy_only_count": legacy_only}


@router.get("/api/attack-lab/experiments")
def list_experiments(context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    records = list_attack_experiments(context.user.id, include_all=has_permission(context.user.role, Permission.MISSION_READ_ALL))
    latest = _latest_versions(context, {item["scenario_id"] for item in records})
    return {"experiments": [_view(item, latest) for item in records]}


@router.get("/api/attack-lab/experiments/{experiment_id}")
def read_experiment(experiment_id: str, context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    record = get_attack_experiment(experiment_id)
    visible = {item["id"] for item in list_attack_experiments(context.user.id, include_all=has_permission(context.user.role, Permission.MISSION_READ_ALL))}
    if record is None or experiment_id not in visible:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    return _view(record, _latest_versions(context, {record["scenario_id"]}))


@router.post("/api/attack-lab/validate")
def validate_endpoint(body: dict = Body(...), context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    try:
        request = ExperimentRequest.model_validate(body)
    except ValidationError as error:
        return {"valid": False, "issues": _pydantic_issues(error)}
    plan, _scenario, issues = load_pinned_plan(context, request.scenario_id, request.scenario_version_id)
    if plan is not None:
        issues = validate_experiment(request, plan)
    return {"valid": not issues, "issues": issues}


@router.post("/api/attack-lab/presets/{preset_id}/apply")
def apply_preset(preset_id: str, body: dict = Body(...), context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    preset_id = canonical_preset_id(preset_id) or ""
    if preset_id not in PRESET_IDS:
        raise HTTPException(status_code=404, detail={"code": "unknown_preset", "message": "Unknown preset."})
    plan, _scenario, issues = load_pinned_plan(context, str(body.get("scenario_id", "")), str(body.get("scenario_version_id", "")))
    if plan is None:
        raise HTTPException(status_code=422, detail={"code": "experiment_invalid", "message": issues[0]["message"], "issues": issues})
    if len(plan.aircraft) < PRESET_INFO[preset_id]["min_aircraft"]:
        issue = {"code": "preset_needs_two_aircraft", "field": "preset_id",
                 "message": f"The {PRESET_INFO[preset_id]['label']} preset is not available for this plan. {PRESET_INFO[preset_id]['needs']}"}
        raise HTTPException(status_code=422, detail={"code": "experiment_invalid", "message": issue["message"], "issues": [issue]})
    seed = body.get("seed", 1)
    return build_preset(preset_id, plan, int(seed) if isinstance(seed, int) and 0 <= seed <= SEED_MAX else 1)


@router.post("/api/attack-lab/experiments", status_code=201)
def create_experiment(request: Request, body: dict = Body(...), context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
                      _csrf: AuthContext = Depends(require_csrf)) -> dict:
    _require_general(context)
    parsed = _parse(body)
    _checked(context, parsed)
    saved = save_attack_experiment(context.user.id, parsed.name, parsed.scenario_id, parsed.scenario_version_id, parsed.preset_id,
                                   parsed.seed, parsed.status, parsed.config.model_dump(mode="json"))
    add_audit_event(action="attack_experiment_saved", outcome="success", reason="Simulated, software-only attack experiment defined; nothing executed.",
                    request_id=getattr(request.state, "request_id", "request-unavailable"), actor_id=context.user.id, actor_name=context.user.full_name,
                    target_type="attack_experiment", target_id=saved["id"], metadata={"preset_id": parsed.preset_id, "status": parsed.status, "attacks": len(parsed.config.attacks)})
    return _view(saved, _latest_versions(context, {saved["scenario_id"]}))


@router.put("/api/attack-lab/experiments/{experiment_id}")
def update_experiment(experiment_id: str, request: Request, body: dict = Body(...), context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
                      _csrf: AuthContext = Depends(require_csrf)) -> dict:
    _require_general(context)
    existing = get_attack_experiment(experiment_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    if existing["status"] != "draft":
        raise HTTPException(status_code=409, detail={"code": "not_draft", "message": "Only draft experiments can be edited."})
    parsed = _parse(body)
    _checked(context, parsed)
    saved = save_attack_experiment(existing["owner_user_id"], parsed.name, parsed.scenario_id, parsed.scenario_version_id, parsed.preset_id,
                                   parsed.seed, parsed.status, parsed.config.model_dump(mode="json"), experiment_id=experiment_id)
    return _view(saved, _latest_versions(context, {saved["scenario_id"]}))


@router.delete("/api/attack-lab/experiments/{experiment_id}", status_code=204, response_class=Response)
def remove_experiment(experiment_id: str, context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
                      _csrf: AuthContext = Depends(require_csrf)) -> Response:
    _require_general(context)
    existing = get_attack_experiment(experiment_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    if existing["status"] != "draft":
        raise HTTPException(status_code=409, detail={"code": "not_draft", "message": "Only draft experiments can be deleted."})
    delete_attack_experiment(experiment_id)
    return Response(status_code=204)
