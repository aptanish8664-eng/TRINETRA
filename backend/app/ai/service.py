from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from ..simulation.engine import SimulationConfig
from .schemas import MissionProposalContent, OperatorSimulationConfig


_COORDINATE_PAIR = re.compile(r"(?<![A-Za-z0-9])[-+]?\d{1,2}\.\d+\s*°?\s*[NnSs]?\s*[,/]\s*[-+]?\d{1,3}\.\d+\s*°?\s*[EeWw]?(?![A-Za-z0-9])")
_LABELLED_COORDINATE = re.compile(r"\b(?:lat(?:itude)?|lon(?:gitude)?|coordinates?)\b.{0,35}[-+]?\d{1,3}(?:\.\d+)?", re.IGNORECASE)
_DMS_COORDINATE = re.compile(r"\b\d{1,2}\s*°\s*[NS]\s*[,/]\s*\d{1,3}\s*°\s*[EW]\b", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
_SECRET_ASSIGNMENT = re.compile(r"\b(?:password|passphrase|api[_ -]?key|session[_ -]?token|access[_ -]?token|secret|private[_ -]?key|encryption[_ -]?key)\b\s*(?:[:=]|\bis\b|\bequals\b)\s*[^\s,;]+", re.IGNORECASE)
_BEARER_SECRET = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
_OPENAI_KEY = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b")
_NUMERIC_METRIC = re.compile(r"\b\d+(?:\.\d+)?\b")
_EVIDENCE_ID = re.compile(r"\b(?:UAV|T|E)-\d+\b")
_COUNT_TOKEN = r"(?P<count>\d{1,2}|twelve|eleven|ten|nine|eight|seven|six|five|four|three|two|one)"
_COUNT_ALTERNATE = r"(?:\s+(?:or|and)\s+(?P<alternate>\d{1,2}|twelve|eleven|ten|nine|eight|seven|six|five|four|three|two|one))?"
_UAV_COUNT = re.compile(rf"\b{_COUNT_TOKEN}{_COUNT_ALTERNATE}[\s-]+(?:uavs?|drones?|aircraft|vehicles)\b", re.IGNORECASE)
_TASK_COUNT = re.compile(rf"\b{_COUNT_TOKEN}{_COUNT_ALTERNATE}[\s-]+tasks?\b", re.IGNORECASE)
_DURATION_COUNT = re.compile(rf"\b{_COUNT_TOKEN}[\s-]+(?:minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE)
_SPOOF_TYPE = re.compile(
    r"\b(?:(?P<leading>sudden|gradual|intermittent|jump|freeze|noise|degradation|velocity[_ -]inconsistency)\s+(?:gps\s+)?(?:spoof(?:ing)?|fault)|"
    r"(?:spoof(?:ing)?|gps\s+fault)\s+(?:type|mode)\s*(?:is|=|:)\s*(?P<labelled>none|sudden|gradual|intermittent|jump|freeze|noise|degradation|velocity[_ -]inconsistency))\b",
    re.IGNORECASE,
)
_NO_SPOOF = re.compile(r"\b(?:no|without)\s+(?:gps\s+)?(?:spoof(?:ing)?|fault)\b", re.IGNORECASE)
_TARGET_UAV = re.compile(
    r"\b(?:target(?:ing)?|spoof(?:ing)?(?:\s+target)?)\s*(?:is|=|:)?\s*UAV[- ](?P<index>\d{1,2})\b|"
    r"\bUAV[- ](?P<tail>\d{1,2})\s+as\s+(?:the\s+)?(?:spoof(?:ing)?\s+)?target\b",
    re.IGNORECASE,
)
_RESPONSE_POLICY = re.compile(
    r"\b(?:response\s+(?:policy|mode)\s*(?:is|=|:|set\s+to)\s*(?P<labelled>adaptive|monitor|baseline)|"
    r"use\s+(?P<leading>adaptive|monitor|baseline)\s+(?:response|policy))\b",
    re.IGNORECASE,
)
_ATTACK_START_TICK = re.compile(
    r"\b(?:gps\s+)?spoof(?:ing)?\s+(?:start(?:s|ing)?|begins?)\s*(?:at\s*)?(?:tick\s*)?(?:is\s*|=|:)?(?P<value>\d+)\b",
    re.IGNORECASE,
)
_ATTACK_DURATION_TICKS = re.compile(
    r"\b(?:gps\s+)?spoof(?:ing)?\s+(?:duration(?:\s*(?:is|=|:))?|lasts?(?:\s+for)?)\s*"
    r"(?P<value>\d+)\s*(?:ticks?|steps?)\b",
    re.IGNORECASE,
)
_ATTACK_MAGNITUDE = re.compile(
    r"\b(?:gps\s+)?spoof(?:ing)?\s+(?:magnitude|offset(?:\s+magnitude)?)\s*(?:is|=|:|of)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?:abstract|simulation)\s+units?\b",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _count_values(pattern: re.Pattern, text: str) -> set[int]:
    values: set[int] = set()
    for match in pattern.finditer(text):
        for field in ("count", "alternate"):
            token = match.group(field)
            if token:
                values.add(int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()])
    return values


def _resolved_explicit_values(messages: list[str], extractor) -> set[str]:
    requested = set().union(*(extractor(text) for text in messages)) if messages else set()
    if messages:
        latest = extractor(messages[-1])
        if len(requested) > 1 and len(latest) == 1 and re.search(
            r"\b(?:correction|actually|instead|change to|make it|use)\b", messages[-1], re.IGNORECASE,
        ):
            return latest
    return requested


class ProposalValidationError(Exception):
    pass


def conversation_config_conflicts(messages: list[dict[str, str]], config: OperatorSimulationConfig) -> list[str]:
    """Find explicit supported settings that conflict with operator-selected config."""
    user_messages = [item["content"] for item in messages if item.get("role") == "user"]
    correction = re.compile(r"\b(?:correction|actually|instead|change to|make it|use)\b", re.IGNORECASE)
    checks = (
        ("uav_count", _UAV_COUNT, config.uav_count, "UAVs"),
        ("task_count", _TASK_COUNT, config.task_count, "tasks"),
    )
    conflicts: list[str] = []
    for _field, pattern, configured, label in checks:
        requested = set().union(*(_count_values(pattern, text) for text in user_messages)) if user_messages else set()
        last_message = user_messages[-1] if user_messages else ""
        latest_values = _count_values(pattern, last_message)
        if len(requested) > 1 and len(latest_values) == 1 and correction.search(last_message):
            requested = latest_values
        if len(requested) > 1:
            values = ", ".join(str(value) for value in sorted(requested))
            noun = "UAV" if label == "UAVs" else "task"
            conflicts.append(f"Your request contains conflicting {noun} counts ({values}); clarify one value.")
        elif requested and next(iter(requested)) != configured:
            value = next(iter(requested))
            conflicts.append(
                f"Your request specifies {value} {label}, while the operator-selected simulator configuration has {configured}. "
                "Update the selected configuration or clarify the request before continuing."
            )

    def requested_durations(text: str) -> set[int]:
        values: set[int] = set()
        for match in _DURATION_COUNT.finditer(text):
            token = match.group("count")
            value = int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]
            unit = match.group(0).lower()
            is_hour = bool(re.search(r"\b(?:hours?|hrs?)\b", unit))
            values.add(value * (3600 if is_hour else 60))
        return values

    durations = set().union(*(requested_durations(text) for text in user_messages)) if user_messages else set()
    latest_durations = requested_durations(user_messages[-1]) if user_messages else set()
    if len(durations) > 1 and len(latest_durations) == 1 and correction.search(user_messages[-1]):
        durations = latest_durations
    if len(durations) > 1:
        values = ", ".join(str(value // 60) + " minutes" for value in sorted(durations))
        conflicts.append(f"You gave conflicting mission durations ({values}); clarify one value.")
    elif durations and next(iter(durations)) != config.duration:
        requested_ticks = next(iter(durations))
        conflicts.append(
            f"Your request specifies {requested_ticks} seconds ({requested_ticks} simulator ticks at the fixed one-second step), "
            f"while the operator-selected duration is {config.duration} ticks. Update the selected configuration or clarify the request."
        )

    def setting_values(pattern: re.Pattern, text: str, fields: tuple[str, ...]) -> set[str]:
        values = set()
        for match in pattern.finditer(text):
            value = next((match.group(field) for field in fields if match.group(field)), None)
            if value is not None:
                values.add(value.lower().replace(" ", "_").replace("-", "_"))
        return values

    def spoof_types(text: str) -> set[str]:
        values = setting_values(_SPOOF_TYPE, text, ("leading", "labelled"))
        if _NO_SPOOF.search(text):
            values.add("none")
        return values

    requested_spoof_types = _resolved_explicit_values(user_messages, spoof_types)
    if len(requested_spoof_types) > 1:
        values = ", ".join(sorted(requested_spoof_types))
        conflicts.append(f"Your request contains conflicting GPS fault profiles ({values}); clarify one value.")
    elif requested_spoof_types and next(iter(requested_spoof_types)) != config.spoofing_type:
        requested = next(iter(requested_spoof_types))
        conflicts.append(
            f"Your request specifies the {requested} GPS fault profile, while the operator-selected simulator configuration uses "
            f"{config.spoofing_type}. Update the selected configuration or clarify the request."
        )

    def target_ids(text: str) -> set[str]:
        values = set()
        for match in _TARGET_UAV.finditer(text):
            index = match.group("index") or match.group("tail")
            values.add("UAV-{0:02d}".format(int(index)))
        return values

    requested_targets = _resolved_explicit_values(user_messages, target_ids)
    if len(requested_targets) > 1:
        values = ", ".join(sorted(requested_targets))
        conflicts.append(f"Your request contains conflicting GPS spoofing targets ({values}); clarify one value.")
    elif requested_targets:
        requested = next(iter(requested_targets))
        valid_uavs = {"UAV-{0:02d}".format(index) for index in range(1, config.uav_count + 1)}
        if requested not in valid_uavs:
            conflicts.append(
                f"Your request targets {requested}, which is outside the operator-selected fleet of {config.uav_count} UAVs. "
                "Update the selected configuration or clarify the target."
            )
        elif requested != config.target_uav:
            conflicts.append(
                f"Your request targets {requested} for GPS spoofing, while the operator-selected simulator configuration "
                f"targets {config.target_uav}. Update the selected configuration or clarify the target."
            )

    def response_policies(text: str) -> set[str]:
        return setting_values(_RESPONSE_POLICY, text, ("leading", "labelled"))

    requested_policies = _resolved_explicit_values(user_messages, response_policies)
    if len(requested_policies) > 1:
        values = ", ".join(sorted(requested_policies))
        conflicts.append(f"Your request contains conflicting response policies ({values}); clarify one value.")
    elif requested_policies and next(iter(requested_policies)) != config.response_policy:
        requested = next(iter(requested_policies))
        conflicts.append(
            f"Your request specifies the {requested} response policy, while the operator-selected simulator configuration uses "
            f"{config.response_policy}. Update the selected configuration or clarify the request."
        )

    def numeric_values(pattern: re.Pattern, text: str, floating_point: bool = False) -> set[str]:
        if floating_point:
            return {str(float(match.group("value"))) for match in pattern.finditer(text)}
        return {str(int(match.group("value"))) for match in pattern.finditer(text)}

    explicit_timing = (
        (_ATTACK_START_TICK, config.spoof_start, "spoof start tick"),
        (_ATTACK_DURATION_TICKS, config.spoof_duration, "spoof duration in ticks"),
    )
    for pattern, configured, label in explicit_timing:
        extract = lambda text, pattern=pattern: numeric_values(pattern, text)
        values = _resolved_explicit_values(user_messages, extract)
        if len(values) > 1:
            conflicts.append(f"Your request contains conflicting {label} values ({', '.join(sorted(values))}); clarify one value.")
        elif values and int(next(iter(values))) != configured:
            requested = next(iter(values))
            conflicts.append(
                f"Your request specifies {label} {requested}, while the operator-selected simulator configuration uses "
                f"{configured}. Update the selected configuration or clarify the request."
            )

    magnitude_extract = lambda text: numeric_values(_ATTACK_MAGNITUDE, text, floating_point=True)
    requested_magnitudes = _resolved_explicit_values(user_messages, magnitude_extract)
    if len(requested_magnitudes) > 1:
        values = ", ".join(sorted(requested_magnitudes, key=float))
        conflicts.append(f"Your request contains conflicting spoof magnitudes ({values} abstract units); clarify one value.")
    elif requested_magnitudes and float(next(iter(requested_magnitudes))) != config.spoof_magnitude:
        requested = float(next(iter(requested_magnitudes)))
        conflicts.append(
            f"Your request specifies spoof magnitude {requested:g} abstract units, while the operator-selected simulator "
            f"configuration uses {config.spoof_magnitude:g}. Update the selected configuration or clarify the request."
        )
    return conflicts


def redact_operator_text(text: str) -> tuple[str, bool]:
    """Keep exact coordinates and common direct identifiers out of provider input."""
    if _COORDINATE_PAIR.search(text) or _LABELLED_COORDINATE.search(text) or _DMS_COORDINATE.search(text):
        return "", True
    redacted = _EMAIL.sub("[email omitted]", text)
    redacted = _PHONE.sub("[phone omitted]", redacted)
    redacted = _SECRET_ASSIGNMENT.sub("[credential omitted]", redacted)
    redacted = _BEARER_SECRET.sub("[credential omitted]", redacted)
    redacted = _OPENAI_KEY.sub("[credential omitted]", redacted)
    return redacted, False


def _content_strings(content: MissionProposalContent) -> list[str]:
    values = content.model_dump(mode="python")
    output: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            output.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    collect(values)
    return output


def validate_mission_proposal(
    raw: Any, operator_config: OperatorSimulationConfig,
) -> tuple[MissionProposalContent, list[str]]:
    try:
        content = MissionProposalContent.model_validate(raw)
    except ValidationError as exc:
        raise ProposalValidationError("structured_fields_invalid") from exc

    if any(_COORDINATE_PAIR.search(text) or _LABELLED_COORDINATE.search(text) or _DMS_COORDINATE.search(text)
           for text in _content_strings(content)):
        raise ProposalValidationError("coordinate_output_rejected")

    sim_config = SimulationConfig(**operator_config.model_dump())
    try:
        sim_config.validate()
    except ValueError as exc:
        raise ProposalValidationError("operator_simulation_config_invalid") from exc

    uav_ids = {"UAV-{0:02d}".format(i) for i in range(1, operator_config.uav_count + 1)}
    task_ids = {"T-{0:02d}".format(i) for i in range(1, operator_config.task_count + 1)}
    capacity = max(2, (operator_config.task_count + operator_config.uav_count - 1) // operator_config.uav_count)
    if len(content.proposed_tasks) > operator_config.task_count:
        raise ProposalValidationError("proposed_task_count_exceeds_operator_configuration")
    assignment_counts: dict[str, int] = {}
    seen_tasks: set[str] = set()

    for task in content.proposed_tasks:
        if task.task_reference is not None:
            if task.task_reference not in task_ids or task.task_reference in seen_tasks:
                raise ProposalValidationError("task_reference_invalid_or_duplicate")
            seen_tasks.add(task.task_reference)
        if task.assigned_uav_id is not None:
            if task.assigned_uav_id not in uav_ids or task.task_reference is None:
                raise ProposalValidationError("task_assignment_invalid")
            assignment_counts[task.assigned_uav_id] = assignment_counts.get(task.assigned_uav_id, 0) + 1
            if assignment_counts[task.assigned_uav_id] > capacity:
                raise ProposalValidationError("task_assignment_exceeds_capacity")

    if sum(role.count for role in content.uav_role_suggestions) > operator_config.uav_count:
        raise ProposalValidationError("uav_role_suggestion_exceeds_fleet")

    issues: list[str] = []
    questions = list(content.clarification_questions)
    required = list(content.required_information)
    if content.waypoint_references or any(task.waypoint_reference for task in content.proposed_tasks):
        # There is intentionally no waypoint registry in this simulator.
        content = content.model_copy(update={
            "waypoint_references": [],
            "proposed_tasks": [task.model_copy(update={"waypoint_reference": None}) for task in content.proposed_tasks],
        })
        required.insert(0, "No verified waypoint registry is available; geographic routes cannot be proposed or created.")
        questions.insert(0, "Can this be planned as an abstract simulator scenario, without geographic waypoints?")
        issues.append("waypoints_omitted_registry_unavailable")

    mandatory_constraints = [
        "All AI-generated narrative, place names, task details, and role suggestions are unverified advisory text.",
        "The simulator uses seeded abstract task markers; it does not contain geographic routes or verified waypoints.",
        "Suggested task assignments and UAV roles are advisory; the simulator deterministically assigns its abstract tasks.",
        "This proposal has not been safety-certified and must be reviewed by an authorized operator.",
    ]
    constraints = list(dict.fromkeys(mandatory_constraints + content.constraints))[:12]
    content = content.model_copy(update={
        "required_information": list(dict.fromkeys(required))[:12],
        "clarification_questions": list(dict.fromkeys(questions))[:12],
        "constraints": constraints,
    })
    return content, issues


def mission_proposal_status(content: MissionProposalContent) -> str:
    if content.required_information or content.clarification_questions:
        return "needs_clarification"
    return "pending_review"


def sanitize_assessment(raw: Any, evidence_ids: set[str], allowed_responses: set[str]):
    from .schemas import MissionAssessmentContent

    try:
        assessment = MissionAssessmentContent.model_validate(raw)
    except ValidationError as exc:
        raise ProposalValidationError("assessment_fields_invalid") from exc
    for finding in assessment.findings:
        if not finding.evidence_references or not set(finding.evidence_references).issubset(evidence_ids):
            raise ProposalValidationError("assessment_evidence_reference_invalid")
        for text in [finding.explanation]:
            without_ids = _EVIDENCE_ID.sub("", text)
            if _NUMERIC_METRIC.search(without_ids):
                raise ProposalValidationError("assessment_numeric_claim_rejected")
    if _NUMERIC_METRIC.search(_EVIDENCE_ID.sub("", assessment.summary)):
        raise ProposalValidationError("assessment_numeric_claim_rejected")
    return assessment.model_copy(update={
        "suggested_responses": [value for value in assessment.suggested_responses if value in allowed_responses],
    })

