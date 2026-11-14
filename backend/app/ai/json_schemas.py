"""Responses API JSON Schemas kept explicit to avoid runtime schema generation."""


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _array(items: dict, minimum: int = 0, maximum: int = 40) -> dict:
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


_string = {"type": "string"}
_nullable_string = {"type": ["string", "null"]}
_integer = {"type": "integer"}

GEOGRAPHIC_MISSION_JSON_SCHEMA = _object({
    "summary": _string,
    "objective_suggestions": _array(_object({
        "objective": {"type": "string", "enum": [
            "area_survey", "search_rescue", "perimeter_monitoring", "inspection",
            "disaster_assessment", "agricultural_survey", "mapping_survey",
            "target_tracking", "environmental_monitoring", "custom_mission",
        ]},
        "rationale": _string,
    }, ["objective", "rationale"]), 0, 5),
    "assignments": _array(_object({
        "uav_id": _string,
        "role": {"type": "string", "enum": [
            "area_survey", "perimeter_monitoring", "inspection",
            "search_observation", "communication_relay",
        ]},
        "task": _string,
        "zone_id": _nullable_string,
    }, ["uav_id", "role", "task", "zone_id"]), 0, 20),
    "missing_information": _array(_string, 0, 12),
    "constraints": _array(_string, 0, 12),
    "warnings": _array(_string, 0, 12),
}, ["summary", "objective_suggestions", "assignments", "missing_information", "constraints", "warnings"])

PROPOSAL_JSON_SCHEMA = _object({
    "mission_objectives": _array(_string, 1, 8),
    "required_information": _array(_string, 0, 12),
    "proposed_tasks": _array(_object({
        "description": _string,
        "task_reference": _nullable_string,
        "assigned_uav_id": _nullable_string,
        "waypoint_reference": _nullable_string,
        "priority": {"type": "integer", "minimum": 1, "maximum": 3},
    }, ["description", "task_reference", "assigned_uav_id", "waypoint_reference", "priority"]), 0, 40),
    "uav_role_suggestions": _array(_object({
        "role": {"type": "string", "enum": ["general", "search", "monitor", "relay", "reserve"]},
        "count": {"type": "integer", "minimum": 0, "maximum": 12},
        "rationale": _string,
    }, ["role", "count", "rationale"]), 0, 12),
    "waypoint_references": _array(_string, 0, 20),
    "assumptions": _array(_string, 0, 12),
    "constraints": _array(_string, 0, 12),
    "assessment_explanations": _array(_string, 1, 8),
    "clarification_questions": _array(_string, 0, 12),
}, ["mission_objectives", "required_information", "proposed_tasks", "uav_role_suggestions",
    "waypoint_references", "assumptions", "constraints", "assessment_explanations", "clarification_questions"])

ASSESSMENT_JSON_SCHEMA = _object({
    "summary": _string,
    "findings": _array(_object({
        "explanation": _string,
        "evidence_references": _array(_string, 0, 30),
    }, ["explanation", "evidence_references"]), 0, 12),
    "suggested_responses": _array({
        "type": "string", "enum": ["continue_monitoring", "pause_simulation", "stop_simulation"],
    }, 0, 3),
}, ["summary", "findings", "suggested_responses"])

SCENARIO_ASSISTANT_JSON_SCHEMA = _object({
    "reply": _string,
    "actions": _array(_object({
        "op": {"type": "string", "enum": [
            "set_objective", "set_max_duration", "set_mission_text", "set_constraint",
            "set_required_uav_count", "set_assignment_field",
        ]},
        "objective": _nullable_string,
        "minutes": {"type": ["integer", "null"]},
        "count": {"type": ["integer", "null"]},
        "uav_index": {"type": ["integer", "null"]},
        "field": _nullable_string,
        "text_value": _nullable_string,
        "number_value": {"type": ["number", "null"]},
    }, ["op", "objective", "minutes", "count", "uav_index", "field", "text_value", "number_value"]), 0, 5),
}, ["reply", "actions"])
