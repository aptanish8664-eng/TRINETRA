from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from .schemas import MissionAssessmentContent, MissionProposalContent
from .json_schemas import ASSESSMENT_JSON_SCHEMA, PROPOSAL_JSON_SCHEMA
from ..config import AI_MAX_OUTPUT_TOKENS, AI_TIMEOUT_SECONDS, OPENAI_API_KEY, OPENAI_MODEL


T = TypeVar("T")


class AIProviderUnavailable(Exception):
    pass


class AIProviderFailure(Exception):
    pass


class AIProviderMalformedResponse(Exception):
    pass


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    parsed: T
    response_id: str
    input_tokens: int
    output_tokens: int


class MissionAIProvider(Protocol):
    @property
    def available(self) -> bool: ...

    async def propose_mission(self, description: str, context: dict[str, Any]) -> ProviderResult[MissionProposalContent]: ...

    async def continue_mission_conversation(self, messages: list[dict[str, str]], context: dict[str, Any]) -> ProviderResult[MissionProposalContent]: ...

    async def assess_mission(self, evidence: dict[str, Any]) -> ProviderResult[MissionAssessmentContent]: ...


class OpenAIResponsesProvider:
    """Isolated adapter for OpenAI's official Python SDK and Responses API."""

    @property
    def available(self) -> bool:
        return bool(OPENAI_API_KEY)

    async def propose_mission(self, description: str, context: dict[str, Any]) -> ProviderResult[MissionProposalContent]:
        instructions = (
            "Interpret the operator's mission request as a proposal for TRINETRA, a software-only simulator. "
            "Use only facts in the operator request and supplied simulator context. Never invent coordinates, "
            "geographic locations, verified waypoints, UAV capabilities, safety guarantees, or threat evidence. "
            "There is no geographic waypoint registry; set waypoint references to empty and ask for clarification "
            "if a geographic route or location is required. This simulator supports only abstract seeded task "
            "markers; its deterministic engine, not you, assigns actual tasks and positions. Keep suggested UAV "
            "roles advisory. Ask concise clarification questions for ambiguity or missing required information. "
            "Do not infer simulation settings; the separately supplied operator configuration is authoritative. "
            "Do not claim assessment findings exist before a simulation runs."
        )
        prompt = {"operator_request": description, "simulator_context": context}
        return await self._parse(
            MissionProposalContent, PROPOSAL_JSON_SCHEMA, instructions, prompt, "mission proposal",
        )

    async def continue_mission_conversation(self, messages: list[dict[str, str]], context: dict[str, Any]) -> ProviderResult[MissionProposalContent]:
        instructions = (
            "Continue the operator's multi-turn mission planning conversation for TRINETRA, a software-only abstract simulator. "
            "Use the complete conversation and supplied simulator context. Extract only facts explicitly supplied by the operator. "
            "Put unresolved mandatory information in required_information and ask concise, targeted questions in clarification_questions. "
            "When enough information exists, return an advisory structured proposal and leave both lists empty. "
            "Never invent locations, geographic coordinates, routes, UAV capabilities, safety claims, or simulator settings. "
            "There is no geographic waypoint registry; do not produce waypoint references. The operator-selected simulator configuration "
            "is authoritative and is not changed by this conversation. Do not claim the scenario has been saved or executed."
        )
        return await self._parse(
            MissionProposalContent, PROPOSAL_JSON_SCHEMA, instructions,
            {"conversation": messages, "simulator_context": context}, "mission conversation",
        )

    async def assess_mission(self, evidence: dict[str, Any]) -> ProviderResult[MissionAssessmentContent]:
        instructions = (
            "Explain only the supplied validated simulation telemetry. This evidence intentionally excludes "
            "hidden ground truth, scenario attack labels, and unreported coordinates. Never invent scores, "
            "events, task outcomes, capabilities, or safety guarantees. Reference only supplied evidence IDs. "
            "Do not include numeric values or metrics in explanatory prose; deterministic values are shown "
            "separately by the backend. Suggest only response labels explicitly supplied as permitted. "
            "Do not trigger a response."
        )
        return await self._parse(MissionAssessmentContent, ASSESSMENT_JSON_SCHEMA, instructions, evidence, "mission assessment")

    async def _parse(self, schema: type[T], json_schema: dict, instructions: str, payload: dict[str, Any], label: str) -> ProviderResult[T]:
        if not self.available:
            raise AIProviderUnavailable("AI provider is not configured")
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=OPENAI_API_KEY,
                timeout=AI_TIMEOUT_SECONDS,
                max_retries=0,
            )
            async with client:
                response = await client.responses.create(
                    model=OPENAI_MODEL,
                    instructions=instructions,
                    input=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}],
                    text={"format": {
                        "type": "json_schema", "name": label.replace(" ", "_"),
                        "strict": True, "schema": json_schema,
                    }},
                    max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                    store=False,
                )
            output_text = response.output_text
            if not output_text:
                raise AIProviderMalformedResponse("Structured output was empty or refused")
            try:
                parsed = schema.model_validate_json(output_text)
            except Exception as exc:
                raise AIProviderMalformedResponse("Structured output did not validate") from exc
            usage = response.usage
            return ProviderResult(
                parsed=parsed,
                response_id=str(response.id),
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            )
        except AIProviderMalformedResponse:
            raise
        except Exception as exc:
            # Provider exception details can contain request material; never bubble them to logs or clients.
            raise AIProviderFailure(label) from exc


def get_ai_provider() -> MissionAIProvider:
    return OpenAIResponsesProvider()

