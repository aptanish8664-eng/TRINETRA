# Conversational Mission Planner

Status: partially implemented and mock-tested (2026-11-07). The optional OpenAI provider is not live-tested because `OPENAI_API_KEY` is not configured.

## Current flow

1. An authenticated user with `ai.propose` starts `POST /api/ai/conversations`, supplying a message and the operator-selected `OperatorSimulationConfig`.
2. The backend validates the simulator configuration, redacts common direct identifiers/secrets, rejects geographic coordinate text, reserves per-user AI usage, and sends the bounded conversation plus abstract simulator context to the existing OpenAI Responses adapter using the strict `MissionProposalContent` JSON schema.
3. The backend validates the structured result against the existing simulator configuration and proposal rules. It also checks explicitly stated numeric/spelled fleet and task counts, minute/hour durations, spoofing type, spoof target, and labeled response policy against the operator-selected config; direct corrections can replace earlier conflicting values, while unresolved conflicts keep the conversation in `collecting`. No run or scenario is created.
4. The owner sends follow-up turns to `POST /api/ai/conversations/{id}/turn`. `GET /api/ai/conversations/{id}` resumes that owner's conversation. Records expire after seven days, accept at most eight user turns, and enforce the configured cumulative input limit. Provider calls are bounded by the existing timeout, rate and output-token budgets.
5. When deterministic validation finds no clarification requirement, the backend creates an advisory mission proposal in the existing General review workflow and marks the conversation `ready_for_review`.
6. A user with `scenario.create` can POST `/api/ai/conversations/{id}/scenario` after reviewing the proposal. This idempotently creates a draft/version using the operator-selected simulator configuration plus advisory objectives, task notes, roles, and constraints; it does not approve or execute a run. The frontend then opens Scenario Lab, where the saved draft and intent can be selected and edited through existing versioning controls.

The React client retains only the current conversation ID in `sessionStorage`, scoped by user ID. Message text and proposal content are reloaded from the authenticated backend. Planner UI state is reset and the component remounted when the signed-in user or role changes. Backend ownership is authoritative; the browser key is not an authorization token.

## Trust, privacy, and limits

- AI output is advisory. Existing Pydantic schema validation, deterministic proposal checks, independent General review, self-approval prevention, and separate run start remain in force.
- The operator-selected simulator config is the only simulator input. The model cannot configure the simulation or start a run.
- Coordinate text is rejected because the current simulator has abstract coordinates and no geographic waypoint registry or authoritative origin.
- Common email, phone, credential assignments, bearer tokens, and OpenAI key patterns are redacted before provider submission and storage. This is a targeted filter, not a guarantee that arbitrary personal information is detected.
- Provider storage is disabled (`store=False`); requests use the existing server-side key, request timeout, quota reservation, strict JSON schema, and safe error handling.
- Scenario persistence stores executable abstract simulator config separately from advisory versioned mission intent. Intent includes objectives, proposed task notes/references/priorities, role suggestions, area/environment notes, and constraints. Geographic area notes are descriptive only; they are not used for route generation or simulation execution. The first objective supplies the draft title.
- The provider's interpretation of missing information comes from the structured model response. Deterministic checks cover only explicit fleet/task counts, mission duration, spoofing type, spoof target, labeled response policy, labeled spoof start tick, labeled spoof duration in ticks/steps, and spoof magnitude explicitly stated in abstract/simulation units. They do not extract all mission fields or infer arbitrary phrasing; contradictions in other mission facts remain model-mediated. The user must verify the proposal and manually choose simulator settings.
- Deterministic conflict findings are prioritized ahead of provider-authored required-information text, so a full 12-entry provider list cannot hide a recognized configuration mismatch.
- The active local SQLite database was not written during this milestone. On next backend startup, SQLAlchemy `create_all` will add the new `ai_conversations` and `scenario_version_intent` tables; no existing table or row is rewritten by these additions. Legacy scenario versions without intent retain their config-only hash.

## Verification

- Focused AI/scenario routes (`test_api.py` + `test_ai_routes.py`): 41 passed in 63.93 s; the additional reference validation check later passed in a targeted 3-test run.
- Focused planner conflict tests: 3 passed; full backend suite: 57 passed using the isolated pytest database after a regression case for conflict-list saturation.
- Frontend: 16 tests passed across six files; TypeScript and Vite production build passed. Three existing jsdom scroll warnings remain.
- Live OpenAI, browser-level flows, concurrent transfer load, and migration against the populated active database have not been tested.

## Next work

1. Keep broad natural-language contradictions under clarification and add tests before widening the narrow phrase parser.
2. Add geographic area, initial position, and route structures only after an authoritative frame/origin is defined and validated.
3. When a server-side provider key is intentionally configured, run a controlled live-provider smoke test without logging prompts or secrets.
4. Before calling a planner field deterministic, retain mismatch, correction, ambiguity, and maximum-schema-list regression tests.
