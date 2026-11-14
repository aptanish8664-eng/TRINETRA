from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI as SDKAsyncOpenAI
from sqlalchemy import update

from app.ai.provider import (
    AIProviderConfigurationError,
    AIProviderFailure,
    AIProviderQuotaExceeded,
    AIProviderRateLimited,
    ProviderResult,
    get_ai_provider,
)
from app.ai.schemas import GeographicMissionProposalContent, MissionProposalContent
from app.main import app
from app.persistence.database import MissionProposalRecord, SessionLocal, utcnow_naive


def valid_proposal(**updates):
    proposal = {
        "mission_objectives": ["Run an abstract repeatable telemetry scenario."],
        "required_information": [],
        "proposed_tasks": [
            {"description": "Inspect task one", "task_reference": "T-01", "assigned_uav_id": "UAV-01", "waypoint_reference": None, "priority": 1},
            {"description": "Inspect task two", "task_reference": "T-02", "assigned_uav_id": "UAV-01", "waypoint_reference": None, "priority": 2},
        ],
        "uav_role_suggestions": [{"role": "general", "count": 4, "rationale": "Roles are advisory only."}],
        "waypoint_references": [],
        "assumptions": ["The seeded abstract simulator is intended."],
        "constraints": [],
        "assessment_explanations": ["Review deterministic telemetry and task progress."],
        "clarification_questions": [],
    }
    proposal.update(updates)
    return proposal


def proposal_payload(description="Run a clean abstract simulator scenario with a four-aircraft fleet."):
    return {
        "description": description,
        "operator_config": {
            "uav_count": 4, "task_count": 10, "seed": 21, "duration": 180,
            "spoofing_type": "none", "target_uav": "UAV-02", "spoof_start": 15,
            "spoof_duration": 35, "spoof_magnitude": 3.0, "response_policy": "adaptive",
            "detector_threshold": None,
        },
    }


class MockProvider:
    available = True

    def __init__(self, proposal=None, assessment=None, error=None):
        self.proposal = proposal if proposal is not None else valid_proposal()
        self.assessment = assessment
        self.error = error
        self.proposal_calls = 0
        self.assessment_calls = 0
        self.assessment_input = None
        self.description = None
        self.conversation_messages = None
        self.geographic_context = None
        self.geographic_plan = GeographicMissionProposalContent.model_validate({
            "summary": "Distribute the requested advisory tasks among the listed aircraft.",
            "assignments": [{
                "uav_id": "px4-7", "role": "area_survey",
                "task": "Survey the named zone", "zone_id": "zone-1",
            }],
            "missing_information": [], "constraints": [], "warnings": [],
        })

    async def propose_mission(self, description, context):
        self.proposal_calls += 1
        self.description = description
        if self.error:
            raise self.error
        return ProviderResult(self.proposal, "mock-response-proposal", 35, 42)

    async def continue_mission_conversation(self, messages, context):
        self.proposal_calls += 1
        self.conversation_messages = messages
        if self.error:
            raise self.error
        return ProviderResult(self.proposal, "mock-response-conversation", 35, 42)

    async def propose_geographic_mission(self, description, context):
        self.description = description
        self.geographic_context = context
        if self.error:
            raise self.error
        return ProviderResult(self.geographic_plan, "mock-response-geographic", 35, 42)

    async def assess_mission(self, evidence):
        self.assessment_calls += 1
        self.assessment_input = evidence
        if self.error:
            raise self.error
        content = self.assessment or {
            "summary": "The supplied telemetry shows a run ready for review.",
            "findings": [{
                "explanation": "Initial aircraft telemetry is available for review.",
                "evidence_references": ["UAV-01", "E-0001"],
            }],
            "suggested_responses": ["continue_monitoring", "pause_simulation", "stop_simulation"],
        }
        return ProviderResult(content, "mock-response-assessment", 40, 30)


@pytest.fixture
def mock_ai():
    provider = MockProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(get_ai_provider, None)


def test_proposal_requires_auth_and_calls_provider_only_after_authorization(mock_ai):
    guest = TestClient(app)
    assert guest.post("/api/ai/proposals", json=proposal_payload()).status_code == 401
    assert mock_ai.proposal_calls == 0


def test_geographic_ai_plan_uses_only_current_aircraft_ids_and_omits_coordinates(operator_client, mock_ai, monkeypatch):
    from app import main

    class TelemetryStore:
        def snapshot(self):
            return {"vehicles": [{
                "id": "px4-7", "status": "connected",
                "position": {"valid": True},
            }]}

    monkeypatch.setattr(main, "px4_udp_transport", object())
    monkeypatch.setattr(main, "px4_telemetry_store", TelemetryStore())
    operator, _ = operator_client
    response = operator.post("/api/ai/geographic-mission-plan", json={
        "objective": "area_survey",
        "description": "Survey the northern section and monitor its edge.",
        "zones": [{"id": "zone-1", "name": "Northern section"}],
    })

    assert response.status_code == 200, response.text
    assert response.json()["assignments"][0]["uav_id"] == "px4-7"
    assert mock_ai.geographic_context["available_aircraft_ids"] == ["px4-7"]
    assert mock_ai.geographic_context["zones"] == [{"id": "zone-1", "name": "Northern section"}]
    assert "latitude" not in str(mock_ai.geographic_context).lower()
    assert "longitude" not in str(mock_ai.geographic_context).lower()


def test_geographic_ai_plan_rejects_coordinates_and_provider_aircraft_invention(operator_client, mock_ai, monkeypatch):
    from app import main

    class TelemetryStore:
        def snapshot(self):
            return {"vehicles": [{
                "id": "px4-7", "status": "connected",
                "position": {"valid": True},
            }]}

    monkeypatch.setattr(main, "px4_udp_transport", object())
    monkeypatch.setattr(main, "px4_telemetry_store", TelemetryStore())
    operator, _ = operator_client
    coordinate_request = operator.post("/api/ai/geographic-mission-plan", json={
        "objective": "area_survey",
        "description": "Survey 37.7749, -122.4194.",
        "zones": [],
    })
    assert coordinate_request.status_code == 422

    mock_ai.geographic_plan = GeographicMissionProposalContent.model_validate({
        "summary": "Assign the mission.",
        "assignments": [{
            "uav_id": "px4-99", "role": "area_survey",
            "task": "Survey the zone", "zone_id": None,
        }],
        "missing_information": [], "constraints": [], "warnings": [],
    })
    invented_aircraft = operator.post("/api/ai/geographic-mission-plan", json={
        "objective": "area_survey",
        "description": "Survey the named zone.",
        "zones": [],
    })
    assert invented_aircraft.status_code == 502

    mock_ai.geographic_plan = GeographicMissionProposalContent.model_validate({
        "summary": "Start at 37.7749, -122.4194.",
        "assignments": [],
        "missing_information": [], "constraints": [], "warnings": [],
    })
    fabricated_coordinates = operator.post("/api/ai/geographic-mission-plan", json={
        "objective": "area_survey",
        "description": "Survey the named zone.",
        "zones": [],
    })
    assert fabricated_coordinates.status_code == 502


def test_general_independently_approves_operator_proposal(general_client, operator_client, mock_ai):
    general, _ = general_client
    operator, user = operator_client
    initial_runs = {run["id"] for run in operator.get("/api/history").json()["runs"]}
    submitted = operator.post("/api/ai/proposals", json=proposal_payload())
    assert submitted.status_code == 200
    proposal = submitted.json()
    assert proposal["status"] == "pending_review"
    assert proposal["ready_for_review"] is True
    assert proposal["proposal"]["waypoint_references"] == []
    assert "ground truth" not in str(mock_ai.assessment_input).lower()
    assert {run["id"] for run in operator.get("/api/history").json()["runs"]} == initial_runs

    approval_path = f"/api/ai/proposals/{proposal['proposal_id']}/approve"
    assert operator.post(approval_path, json={"confirmation": "approve"}).status_code == 403
    assert operator.get("/api/ai/proposals/pending").status_code == 403
    approval = general.post(approval_path, json={"confirmation": "approve"})
    assert approval.status_code == 200
    run = approval.json()
    assert run["status"] == "approved"
    assert run["started"] is False
    assert run["state"]["status"] == "ready"
    assert operator.get(f"/api/simulations/{run['run_id']}").status_code == 200
    assert operator.post(f"/api/simulations/{run['run_id']}/start").status_code == 200
    assert operator.get(f"/api/ai/proposals/{proposal['proposal_id']}").json()["status"] == "approved"
    assert general.post(approval_path, json={"confirmation": "approve"}).status_code == 409
    events = general.get("/api/general/audit?limit=100").json()["events"]
    assert any(
        event["event_type"] == "ai_mission_proposal" and event["actor_user_id"] == user.id
        for event in events
    )
    assert any(
        event["event_type"] == "ai_proposal_approved" and event["target_id"] == proposal["proposal_id"]
        for event in events
    )
    assert "run a clean abstract" not in str(events).lower()


def test_general_rejection_requires_reason_and_is_independent(general_client, operator_client, mock_ai):
    general, _ = general_client
    operator, _ = operator_client
    proposal = operator.post("/api/ai/proposals", json=proposal_payload()).json()
    path = f"/api/ai/proposals/{proposal['proposal_id']}/reject"
    assert operator.post(path, json={"reason": "self rejection"}).status_code == 403
    assert general.post(path, json={"reason": "   "}).status_code == 422
    rejected = general.post(path, json={"reason": "The proposal needs another review."})
    assert rejected.status_code == 200
    stored = operator.get(f"/api/ai/proposals/{proposal['proposal_id']}").json()
    assert stored["status"] == "rejected"
    assert stored["decision_reason"] == "The proposal needs another review."
    assert general.post(
        f"/api/ai/proposals/{proposal['proposal_id']}/approve",
        json={"confirmation": "approve"},
    ).status_code == 409


def test_expired_ai_proposal_cannot_be_approved_and_is_audited(general_client, operator_client, mock_ai):
    general, _ = general_client
    operator, _ = operator_client
    proposal = operator.post("/api/ai/proposals", json=proposal_payload()).json()
    with SessionLocal() as db:
        db.execute(update(MissionProposalRecord).where(
            MissionProposalRecord.id == proposal["proposal_id"],
        ).values(expires_at=utcnow_naive() - timedelta(seconds=1)))
        db.commit()
    expired = operator.get(f"/api/ai/proposals/{proposal['proposal_id']}")
    assert expired.status_code == 200
    assert expired.json()["status"] == "expired"
    assert expired.json()["decision_reason"] == "Approval request expired after seven days."
    events = general.get("/api/general/audit?event_type=ai_proposal_expired").json()["events"]
    assert len(events) == 1


def test_missing_waypoint_registry_forces_clarification(mock_ai, operator_client):
    operator, _ = operator_client
    mock_ai.proposal = valid_proposal(
        waypoint_references=["North Ridge"],
        proposed_tasks=[{
            "description": "Inspect a location", "task_reference": "T-01",
            "assigned_uav_id": "UAV-01", "waypoint_reference": "North Ridge",
            "priority": 1,
        }],
    )
    response = operator.post(
        "/api/ai/proposals",
        json=proposal_payload("Plan an abstract survey of North Ridge."),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_clarification"
    assert body["ready_for_review"] is False
    assert body["proposal"]["waypoint_references"] == []
    assert body["proposal"]["proposed_tasks"][0]["waypoint_reference"] is None
    assert body["validation_issues"] == ["waypoints_omitted_registry_unavailable"]


@pytest.mark.parametrize("proposal", [
    {"mission_objectives": ["Missing required structured fields"]},
    valid_proposal(proposed_tasks=[{
        "description": "Invalid assignment", "task_reference": "T-01",
        "assigned_uav_id": "UAV-99", "waypoint_reference": None, "priority": 1,
    }]),
    valid_proposal(mission_objectives=["Use coordinates 37.7749, -122.4194"]),
    valid_proposal(uav_role_suggestions=[{"role": "search", "count": 5, "rationale": "Too many roles."}]),
])
def test_malformed_or_invalid_proposals_are_rejected(operator_client, mock_ai, proposal):
    operator, _ = operator_client
    initial_runs = {run["id"] for run in operator.get("/api/history").json()["runs"]}
    mock_ai.proposal = proposal
    response = operator.post("/api/ai/proposals", json=proposal_payload())
    assert response.status_code == 502
    assert {run["id"] for run in operator.get("/api/history").json()["runs"]} == initial_runs


@pytest.mark.parametrize("description", [
    "Fly to 37.77, -122.42 and inspect.",
    "Patrol coordinates: 19, 72.",
    "Use 19° N, 72° E as the waypoint.",
])
def test_coordinate_text_is_rejected_before_provider(operator_client, mock_ai, description):
    operator, _ = operator_client
    response = operator.post("/api/ai/proposals", json=proposal_payload(description))
    assert response.status_code == 422
    assert mock_ai.proposal_calls == 0


def test_prompt_redacts_common_secrets_before_provider(operator_client, mock_ai):
    operator, _ = operator_client
    response = operator.post("/api/ai/proposals", json=proposal_payload(
        "Plan abstract tasks. Contact me at test@example.org; key sk-proj-12345678901234567890"
    ))
    assert response.status_code == 200
    assert "test@example.org" not in mock_ai.description
    assert "sk-proj-12345678901234567890" not in mock_ai.description
    assert "[email omitted]" in mock_ai.description
    assert "[phone omitted]" in mock_ai.description


def test_manual_planning_still_works_when_ai_is_unavailable(operator_client, mock_ai):
    operator, _ = operator_client
    mock_ai.available = False
    manual = operator.post("/api/simulations", json={"spoofing_type": "none", "duration": 8})
    assert manual.status_code == 200
    assert manual.json()["state"]["status"] == "ready"
    assert operator.post(f"/api/simulations/{manual.json()['id']}/start").status_code == 200
    assert operator.post("/api/ai/proposals", json=proposal_payload()).status_code == 503


def test_official_responses_adapter_sends_strict_schema_without_network(monkeypatch):
    from app.ai import provider as provider_module

    calls = {}
    async def handler(request):
        calls["request"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "id": "mock-sdk-response", "object": "response", "created_at": 1,
            "status": "completed", "model": "gpt-4.1-mini", "output": [{
                "id": "msg_mock", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(valid_proposal()), "annotations": []}],
            }],
            "usage": {"input_tokens": 17, "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens": 23, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 40},
        })

    class SDKClientFactory:
        def __call__(self, **kwargs):
            calls["client"] = kwargs
            transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return SDKAsyncOpenAI(**kwargs, http_client=transport_client)

    monkeypatch.setattr(openai, "AsyncOpenAI", SDKClientFactory())
    monkeypatch.setattr(provider_module, "OPENAI_API_KEY", "test-only-key")
    provider = provider_module.OpenAIResponsesProvider()
    async def exercise_adapter():
        proposal_result = await provider.propose_mission("abstract simulator request", {"waypoints": []})
        conversation_result = await provider.continue_mission_conversation(
            [{"role": "user", "content": "Plan an abstract task."}], {"waypoints": []},
        )
        return proposal_result, conversation_result

    result, conversation_result = asyncio.run(exercise_adapter())
    assert isinstance(result.parsed, MissionProposalContent)
    assert isinstance(conversation_result.parsed, MissionProposalContent)
    assert result.output_tokens == 23
    assert calls["client"]["max_retries"] == 0
    assert calls["request"]["store"] is False
    assert calls["request"]["text"]["format"]["type"] == "json_schema"
    assert calls["request"]["text"]["format"]["strict"] is True
    assert "additionalProperties" in calls["request"]["text"]["format"]["schema"]


@pytest.mark.parametrize(("sdk_error", "status_code", "error_code", "expected_error"), [
    (openai.RateLimitError, 429, "rate_limit_exceeded", AIProviderRateLimited),
    (openai.RateLimitError, 429, "insufficient_quota", AIProviderQuotaExceeded),
    (openai.AuthenticationError, 401, "invalid_api_key", AIProviderConfigurationError),
])
def test_official_responses_adapter_sanitizes_provider_failures(monkeypatch, sdk_error, status_code, error_code, expected_error):
    from app.ai import provider as provider_module
    from app.ai.json_schemas import PROPOSAL_JSON_SCHEMA

    response = httpx.Response(status_code,
                              request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    error_type = "authentication_error" if status_code == 401 else "rate_limit_error"
    body = {"error": {"code": error_code, "type": error_type}}
    error = sdk_error("private provider response", response=response, body=body)

    class FailedResponses:
        async def create(self, **kwargs):
            raise error

    class FakeClient:
        responses = FailedResponses()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: FakeClient())
    monkeypatch.setattr(provider_module, "OPENAI_API_KEY", "test-only-key")
    provider = provider_module.OpenAIResponsesProvider()

    async def request():
        return await provider._parse(
            MissionProposalContent, PROPOSAL_JSON_SCHEMA, "instructions", {}, "test proposal",
        )

    with pytest.raises(expected_error) as raised:
        asyncio.run(request())
    assert "private provider response" not in str(raised.value)


def test_missing_provider_configuration_and_provider_failure_are_safe(operator_client, mock_ai):
    operator, _ = operator_client
    mock_ai.available = False
    status = operator.get("/api/ai/status").json()
    assert status["available"] is False
    assert "API_KEY" in status["reason"]
    assert operator.post("/api/ai/proposals", json=proposal_payload()).status_code == 503
    mock_ai.available = True
    mock_ai.error = AIProviderFailure("private exception content")
    response = operator.post("/api/ai/proposals", json=proposal_payload())
    assert response.status_code == 503
    assert "private exception content" not in response.text


@pytest.mark.parametrize(("provider_error", "status_code", "message_fragment"), [
    (AIProviderRateLimited("request"), 429, "rate limit"),
    (AIProviderQuotaExceeded("request"), 402, "insufficient API credits"),
    (AIProviderConfigurationError("request"), 503, "rejected the configured API key"),
])
def test_openai_account_errors_are_safe_and_actionable(operator_client, mock_ai, provider_error, status_code, message_fragment):
    operator, _ = operator_client
    mock_ai.error = provider_error

    response = operator.post("/api/ai/proposals", json=proposal_payload())

    assert response.status_code == status_code
    assert message_fragment in response.json()["detail"]
    assert "request" not in response.text


def test_ai_rate_limit_is_per_user(operator_client, mock_ai, monkeypatch):
    from app.ai import routes

    operator, _ = operator_client
    monkeypatch.setattr(routes, "AI_REQUESTS_PER_HOUR", 1)
    assert operator.post("/api/ai/proposals", json=proposal_payload()).status_code == 200
    assert operator.post("/api/ai/proposals", json=proposal_payload()).status_code == 429
    assert mock_ai.proposal_calls == 1


def test_assessment_receives_only_reported_telemetry(operator_client, mock_ai):
    operator, _ = operator_client
    run = operator.post("/api/simulations", json={"spoofing_type": "sudden", "seed": 9}).json()
    response = operator.post(f"/api/ai/assessments/{run['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["validated_evidence"]["reported_telemetry"][0]["reported_gps"]
    assert "position" not in data["validated_evidence"]["reported_telemetry"][0]
    assert "config" not in data["validated_evidence"]
    assert "spoofing_type" not in str(mock_ai.assessment_input)
    assert "position" not in str(mock_ai.assessment_input)
    assert "stop_simulation" not in data["assessment"]["suggested_responses"]
    assert data["simulation_status"] == "ready"

    mock_ai.assessment = {
        "summary": "A fabricated event is cited.",
        "findings": [{"explanation": "Unsupported claim.", "evidence_references": ["E-FAKE"]}],
        "suggested_responses": [],
    }
    assert operator.post(f"/api/ai/assessments/{run['id']}").status_code == 502


def test_description_length_is_enforced(operator_client, mock_ai, monkeypatch):
    from app.ai import routes

    operator, _ = operator_client
    monkeypatch.setattr(routes, "AI_MAX_INPUT_CHARS", 20)
    assert operator.post("/api/ai/proposals", json=proposal_payload()).status_code == 413


def test_ai_conversation_persists_clarification_and_transfers_validated_config(operator_client, mock_ai):
    operator, _ = operator_client
    mock_ai.proposal = valid_proposal(
        required_information=["Clarify whether this is an abstract simulation."],
        clarification_questions=["Should this remain an abstract simulator scenario?"],
    )
    initial = operator.post("/api/ai/conversations", json={
        "message": "Draft a repeatable abstract test.",
        "operator_config": proposal_payload()["operator_config"],
    })
    assert initial.status_code == 201, initial.text
    state = initial.json()
    assert state["status"] == "collecting"
    assert state["proposal_id"] is None
    assert state["messages"][-1]["content"] == "To continue, please clarify: Should this remain an abstract simulator scenario?"

    mock_ai.proposal = valid_proposal()
    completed = operator.post(
        f"/api/ai/conversations/{state['conversation_id']}/turn",
        json={"message": "Yes, keep it abstract and software-only."},
    )
    assert completed.status_code == 200, completed.text
    ready = completed.json()
    assert ready["status"] == "ready_for_review"
    assert ready["proposal_id"]
    assert len(ready["messages"]) == 4
    assert mock_ai.conversation_messages[-1]["content"] == "Yes, keep it abstract and software-only."

    restored = operator.get(f"/api/ai/conversations/{state['conversation_id']}")
    assert restored.status_code == 200
    assert restored.json()["proposal_id"] == ready["proposal_id"]
    saved = operator.post(f"/api/ai/conversations/{state['conversation_id']}/scenario")
    assert saved.status_code == 200, saved.text
    assert saved.json()["versions"][0]["config"] == ready["operator_config"]
    assert saved.json()["versions"][0]["intent"]["mission_objectives"] == ready["proposal"]["mission_objectives"]
    assert saved.json()["versions"][0]["intent"]["proposed_tasks"][0]["description"] == ready["proposal"]["proposed_tasks"][0]["description"]
    repeated = operator.post(f"/api/ai/conversations/{state['conversation_id']}/scenario")
    assert repeated.status_code == 200
    assert repeated.json()["id"] == saved.json()["id"]


def test_ai_conversation_updates_the_selected_scenario_without_creating_a_duplicate(operator_client, mock_ai):
    operator, _ = operator_client
    config = proposal_payload()["operator_config"]
    intent = {
        "mission_objectives": ["Existing manual objective"],
        "proposed_tasks": [], "uav_role_suggestions": [],
        "operating_area_notes": "Existing abstract area note",
        "environment_notes": "", "constraints": ["Keep this constraint"],
        "geographic_mission": None,
        "uav_fault_assignments": [
            {"uav_id": "UAV-01", "fault_type": "none", "spoof_start": 0, "spoof_duration": 1, "spoof_magnitude": 0.0},
            {"uav_id": "UAV-02", "fault_type": "none", "spoof_start": 0, "spoof_duration": 1, "spoof_magnitude": 0.0},
        ],
    }
    created = operator.post("/api/scenarios", json={
        "title": "Shared draft", "config": config, "intent": intent,
    })
    assert created.status_code == 201, created.text
    scenario_id = created.json()["id"]

    started = operator.post("/api/ai/conversations", json={
        "message": "Plan a repeatable abstract simulator scenario.",
        "operator_config": config,
    })
    assert started.status_code == 201
    conversation_id = started.json()["conversation_id"]
    saved = operator.post(
        f"/api/ai/conversations/{conversation_id}/scenario",
        json={"scenario_id": scenario_id},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["id"] == scenario_id
    assert len(saved.json()["versions"]) == 2
    latest = saved.json()["versions"][0]
    assert {key: latest["config"][key] for key in config} == config
    assert latest["intent"]["operating_area_notes"] == "Existing abstract area note"
    assert latest["intent"]["constraints"][0] == "Keep this constraint"
    assert any("unverified advisory text" in item for item in latest["intent"]["constraints"])
    assert latest["intent"]["uav_fault_assignments"] == intent["uav_fault_assignments"]

    repeated = operator.post(
        f"/api/ai/conversations/{conversation_id}/scenario",
        json={"scenario_id": scenario_id},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == scenario_id
    assert len(repeated.json()["versions"]) == 2


def test_conversation_rejects_coordinates_and_protects_owner_scope(operator_client, mock_ai):
    operator, _ = operator_client
    payload = {
        "message": "Fly at 37.7749, -122.4194",
        "operator_config": proposal_payload()["operator_config"],
    }
    assert operator.post("/api/ai/conversations", json=payload).status_code == 422
    assert mock_ai.proposal_calls == 0
    started = operator.post("/api/ai/conversations", json={
        "message": "Plan abstract simulator tasks.",
        "operator_config": proposal_payload()["operator_config"],
    })
    assert started.status_code == 201
    conversation_id = started.json()["conversation_id"]
    guest = TestClient(app)
    assert guest.get(f"/api/ai/conversations/{conversation_id}").status_code == 401
    assert guest.post(f"/api/ai/conversations/{conversation_id}/scenario").status_code == 401


def test_conversation_detects_config_mismatch_and_accepts_explicit_correction(operator_client, mock_ai):
    operator, _ = operator_client
    started = operator.post("/api/ai/conversations", json={
        "message": "Plan five UAVs for 30 minutes.",
        "operator_config": proposal_payload()["operator_config"],
    })
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["status"] == "collecting"
    assert any("operator-selected simulator configuration has 4" in item for item in body["proposal"]["required_information"])
    assert any("operator-selected duration is 180 ticks" in item for item in body["proposal"]["required_information"])
    assert body["proposal_id"] is None

    corrected = operator.post(
        f"/api/ai/conversations/{body['conversation_id']}/turn",
        json={"message": "Correction: use four UAVs for three minutes."},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "ready_for_review"
    assert corrected.json()["proposal_id"]


def test_conversation_detects_attack_profile_mismatches_and_accepts_correction(operator_client, mock_ai):
    operator, _ = operator_client
    mock_ai.proposal = valid_proposal(required_information=["More information {0}".format(index) for index in range(12)])
    selected_config = {**proposal_payload()["operator_config"], "spoofing_type": "sudden"}
    started = operator.post("/api/ai/conversations", json={
        "message": "Use gradual GPS spoofing targeting UAV-03 with response policy set to monitor; spoof start tick 20, spoof duration 40 ticks, spoof magnitude 4 abstract units.",
        "operator_config": selected_config,
    })
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["status"] == "collecting"
    required = body["proposal"]["required_information"]
    assert any("operator-selected simulator configuration uses sudden" in item for item in required)
    assert any("operator-selected simulator configuration targets UAV-02" in item for item in required)
    assert any("operator-selected simulator configuration uses adaptive" in item for item in required)
    assert any("spoof start tick 20" in item for item in required)
    assert any("spoof duration in ticks 40" in item for item in required)
    assert any("spoof magnitude 4 abstract units" in item for item in required)

    mock_ai.proposal = valid_proposal()
    corrected = operator.post(
        f"/api/ai/conversations/{body['conversation_id']}/turn",
        json={"message": "Correction: use sudden GPS spoofing targeting UAV-02 with response policy set to adaptive; spoof start tick 15, spoof duration 35 ticks, spoof magnitude 3 abstract units."},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "ready_for_review"
    assert corrected.json()["proposal_id"]


def test_conversation_recognizes_velocity_inconsistency_fault_profile(operator_client, mock_ai):
    operator, _ = operator_client
    mock_ai.proposal = valid_proposal()
    selected_config = {**proposal_payload()["operator_config"], "spoofing_type": "freeze"}
    started = operator.post("/api/ai/conversations", json={
        "message": "Apply a velocity inconsistency GPS fault to UAV-02.",
        "operator_config": selected_config,
    })
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["status"] == "collecting"
    assert any("operator-selected simulator configuration uses freeze" in item
               for item in body["proposal"]["required_information"])

    corrected = operator.post(
        f"/api/ai/conversations/{body['conversation_id']}/turn",
        json={"message": "Correction: use the freeze GPS fault."},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "ready_for_review"


def test_conversation_does_not_guess_between_conflicting_counts(operator_client, mock_ai):
    operator, _ = operator_client
    response = operator.post("/api/ai/conversations", json={
        "message": "Use five or six UAVs.",
        "operator_config": proposal_payload()["operator_config"],
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "collecting"
    assert body["proposal_id"] is None
    assert any("conflicting UAV counts" in item for item in body["proposal"]["required_information"])
