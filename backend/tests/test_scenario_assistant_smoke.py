r"""Manual smoke test against the LIVE OpenAI provider. Skipped unless TRINETRA_LIVE_AI_SMOKE=1.

Run once, from D:\TRINETRA\backend, with the real backend/.env key configured:

    $env:TRINETRA_LIVE_AI_SMOKE = "1"
    ..\.venv\Scripts\python.exe -m pytest -q tests/test_scenario_assistant_smoke.py -s

It makes one small provider request (billable), prints only the reply text and the proposed actions, and
never prints the API key.
"""
from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("TRINETRA_LIVE_AI_SMOKE") != "1", reason="set TRINETRA_LIVE_AI_SMOKE=1 to call the live provider")


def test_live_provider_returns_validated_reply_and_only_allowlisted_actions():
    from app.ai.provider import get_ai_provider
    from app.ai.scenario_assistant import clean_request, sanitize_actions
    from app.ai.schemas import ScenarioAssistantRequest

    provider = get_ai_provider()
    assert provider.available, "OPENAI_API_KEY is not configured for the backend"
    request = ScenarioAssistantRequest.model_validate({
        "draft_id": "live-smoke", "message": "Please set the altitude of UAV 1 to 25 metres and tell me what else is missing.",
        "history": [], "snapshot": {
            "section": "B", "title": "Smoke test", "objectives": ["Search and Rescue"],
            "description": "Search the shoreline.", "area_kind": "circle", "required_uav_count": 1, "activated_uav_count": 1,
            "fleet_verified": True,
            "validation_items": ["Every selected aircraft needs a generated route and a confirmed mission."],
            "deploy_blockers": ["Save the draft first: deployment uses the saved immutable version."],
            "aircraft": [{"index": 1, "uav_id": "px4-1", "confirmed": False, "role": "primary",
                          "task_type": "expanding_square_search", "missing": ["Altitude (m)", "Speed (m/s)"]}],
        },
    })
    payload, _ = clean_request(request)
    result = asyncio.run(provider.scenario_assistant(payload))
    actions, dropped = sanitize_actions(result.parsed.actions, 1, {1})
    print("\nREPLY:", result.parsed.reply)
    print("ACTIONS:", actions, "DROPPED:", dropped)
    assert result.parsed.reply.strip()
    assert all(item["op"] in {"set_objective", "set_mission_duration", "set_mission_text", "set_required_uav_count", "set_assignment_field"} for item in actions)
