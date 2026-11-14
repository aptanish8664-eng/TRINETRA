# AI Mission Planner and Assessment

## Configure a backend-only API key

The OpenAI key belongs to the backend process. Never send it in chat, put it in frontend code, browser storage, a command line, or a committed file.

1. Sign in to the [OpenAI Platform](https://platform.openai.com/) and confirm that the organization/project has API access and billing configured.
2. Create a **new** project API key for TRINETRA in the platform's [API keys page](https://platform.openai.com/api-keys). Do not reuse a key shared in chat or another public channel.
3. Open `D:\TRINETRA\backend\.env` in a text editor. This ignored file is prepared with an empty `OPENAI_API_KEY` and the configured model:

   ```powershell
   notepad D:\TRINETRA\backend\.env
   ```

4. Replace the empty value after `OPENAI_API_KEY=` with the newly generated key, without quotation marks. Keep `OPENAI_MODEL=gpt-4.1-mini` unchanged unless you select another verified model supporting Responses and Structured Outputs. Keep the key out of chat, source code, frontend files, browser storage, logs, and commits.
5. Restart the backend from `D:\TRINETRA\backend`:

   ```powershell
   ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

6. Sign in, open **Mission Planner**, and confirm its AI status reports configured. To verify a real request, submit a non-sensitive test mission description and confirm a structured advisory response appears. `/api/ai/status` only confirms that a key is configured; it does not make a paid health-check request.

The backend loads `backend/.env` for `OPENAI_API_KEY` and `OPENAI_MODEL`; non-empty backend values override the repository-root `.env` fallback, while process environment variables take precedence. Empty template values do not erase another configured value. `backend/.env.example` contains placeholders only and is safe to share. In production, use the deployment secret manager instead of a file, set the frontend's exact deployed origin in `TRINETRA_ALLOWED_ORIGINS`, and set `TRINETRA_SESSION_COOKIE_SECURE=true` behind HTTPS. Never define `OPENAI_API_KEY` with a `VITE_` prefix.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | empty | Backend secret. Empty disables AI and leaves manual planning available. |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Responses API model. Select a model that supports Responses and Structured Outputs. |
| `TRINETRA_AI_TIMEOUT_SECONDS` | `20` | Provider timeout, bounded to 3–90 seconds. SDK retries are disabled. |
| `TRINETRA_AI_MAX_INPUT_CHARS` | `6000` | Maximum mission-description characters sent for planning; bounded to 200–20,000. |
| `TRINETRA_AI_MAX_OUTPUT_TOKENS` | `1800` | Per-call output cap and usage reservation; bounded to 128–6,000. |
| `TRINETRA_AI_REQUESTS_PER_HOUR` | `20` | Per-operator request limit, shared through the local SQLite usage table; bounded to 1–500. |
| `TRINETRA_AI_DAILY_OUTPUT_TOKEN_LIMIT` | `20000` | Per-operator daily output-token budget; bounded to 128–1,000,000. Set this at or above the per-call output cap to permit a request. |
| `TRINETRA_MAX_HTTP_BODY_BYTES` | `65536` | Maximum request-body size when a Content-Length is supplied; bounded to 1 KB–1 MB. |

The usage limiter reserves the configured maximum output tokens before each request, then records actual usage returned by the provider. Provider failures consume a request slot but release the token reservation. Usage rows older than 90 days are deleted as new requests are reserved. These application limits do not replace project-level billing or platform rate limits.

## API and approval flow

- `GET /api/ai/status` requires an authenticated session. It reveals provider, model, key-configured status, and the operator's remaining local quotas.
- `POST /api/ai/proposals` requires an approved session with the backend `ai.propose` permission (available to both application roles) and CSRF validation. It accepts the description and current simulator settings. It does not create a run.
- `GET /api/ai/proposals/{proposal_id}` returns the owner's proposal; `GENERAL` users can view proposals through their global mission scope.
- `GET /api/ai/proposals/pending` lists proposals for `GENERAL` review. Approval and rejection require a reviewer other than the requester. `POST /api/ai/proposals/{proposal_id}/approve` requires the explicit `{"confirmation":"approve"}` body and creates a **ready** run owned by the requester. `POST /api/ai/proposals/{proposal_id}/reject` requires a reason. Proposals expire after seven days; decisions and expirations are audited.
- `POST /api/ai/assessments/{run_id}` requires an authenticated session, CSRF validation, and run ownership. It returns an AI explanation alongside backend-validated evidence and permitted response suggestions. Suggestions never execute automatically.

The existing manual workflow remains available in **Scenario Lab**, with explicit per-aircraft abstract GPS fault settings, versioned advisory mission intent, and a direct link to the shared geographic Mission Planner for individual PX4 geography. Mission Planner starts with the conversation; map and manual geographic controls open on demand and retain the current editable mission. Existing `POST /api/simulations` request/response payloads remain unchanged. `OPERATOR` and `GENERAL` permissions are enforced by separate backend routes; restricted command review is reserved for a different `GENERAL` user. Approval creates a ready run; starting is a separate Mission Control action.

## Validation and geographic limits

The model receives only the operator's mission text after common email/phone and credential-pattern redaction plus the selected abstract simulator configuration and valid generated UAV/task IDs. A request containing coordinate pairs is rejected before reaching the provider. The structured result has no coordinate field; generated coordinate-like text is rejected. The backend validates task references, UAV IDs, assignment capacity, role counts, and simulator settings. A proposal with an unknown waypoint reference is stripped of that reference, marked as needing clarification, and cannot be approved. Redaction is pattern-based and cannot identify every possible secret, so do not paste secrets into mission text.

This repository has no geographic waypoint registry. The simulator uses seeded abstract task markers and generates positions itself. Suggested tasks and roles are advisory and never configure the engine. For approved proposals, only the operator-selected, deterministically validated simulator settings reach the simulator. This is a software research demonstrator, not a route planner, flight controller, or source of safety guarantees.

Mission Assessment sends only reported GPS, validated anomaly/threat fields, task IDs, progress, and selected event metadata. It does not send hidden truth positions, hidden scenario attack labels, user identity, session credentials, or raw event reasons. The backend rejects evidence references outside the supplied telemetry and filters response suggestions through the user's role permissions and simulation state. Numeric threat scores, completion metrics, and task counts shown in the result are taken from deterministic backend state; the model cannot supply those values.

## Privacy, billing, and model choice

The adapter uses the official OpenAI Python SDK and Responses API with Structured Outputs, `store=false`, a bounded timeout, no SDK retries, a strict JSON Schema followed by Pydantic validation. The configured model must support the Responses API and Structured Outputs; the default `gpt-4.1-mini` supports both according to its [model page](https://developers.openai.com/api/docs/models/gpt-4.1-mini). Choose another supported model through `OPENAI_MODEL` after reviewing current [model guidance](https://developers.openai.com/api/docs/models) and [API pricing](https://developers.openai.com/api/docs/pricing). API usage is metered by model and tokens, and platform billing must be configured separately from this application.

The application does not persist the original prompt or put it in audit metadata. It stores the structured proposal so the operator can review and approve it. Audit records store operator ID, model, response ID, input character/token counts, output tokens, duration, status, and proposal/run IDs—not prompt contents, API keys, passwords, session cookies, or CSRF values. Protect the local database and backups because reviewed proposal text is persisted there.

OpenAI's official data-control documentation says API data is not used for model training by default, abuse-monitoring logs may retain content for up to 30 days, and Responses application-state retention depends on `store`; this adapter sets `store=false`. Review the current [data controls](https://developers.openai.com/api/docs/guides/your-data) and your organization's account settings before sending sensitive mission descriptions. Do not enter personal, classified, or operationally sensitive details.

## Test and availability behavior

The backend tests inject a mocked provider or use the official SDK against an in-memory HTTP transport; they do not require a key or make billable API calls. If no key is configured, the protected status endpoint reports AI unavailable and AI endpoints return a safe configuration message. Provider rate limits return HTTP 429; exhausted project credits/billing return HTTP 402; invalid credentials return a safe HTTP 503 configuration message. These responses never include the provider's raw error body or key. The existing manual scenario planner continues to work.

The service has not been validated against a real provider account in this checkout. OpenAPI schema generation currently fails in the repository's Python 3.9/Pydantic 2.11 environment, including for an empty FastAPI app. Structured Outputs uses an explicit JSON Schema to avoid that unrelated local Pydantic schema-generation failure; API `/docs` generation remains an environment limitation to address separately.

## Scenario Lab assistant (read-only help plus proposed actions)

- **Deterministic first.** In `frontend/src/components/ScenarioWorkspace.tsx` existing commands run first, then the read-only intents in `assistantHelp.ts` (readiness, missing fields, explanations, summaries). Readiness always comes from the shared `computeDeployBlockers`/`computeMissionMissing` in `scenarioPlanning.ts`, the same validation the Review section uses. These answers never change state, call an API, or open a proposal.
- **Model second.** Only messages no rule understood go to `POST /api/ai/scenario-assistant` (authenticated, `ai.propose`, CSRF, per-user hourly/daily limits shared with the other AI routes). The body is `{draft_id, message<=500, history<=12 (last 6 used), snapshot}`; the snapshot is a compact, coordinate-free view of the form (booleans for selected map points). All of it is treated as untrusted data; text with coordinates, e-mail, phone or credential patterns is redacted or rejected before it reaches the provider. The model name, timeout and output-token cap come from `OPENAI_MODEL`, `TRINETRA_AI_TIMEOUT_SECONDS` and `TRINETRA_AI_MAX_OUTPUT_TOKENS`; the existing `OpenAIResponsesProvider` client is reused.
- **Response.** `{reply, actions[], source: "ai", dropped_actions}`. `actions` use a fixed allowlist (`set_objective`, `set_mission_duration`, `set_mission_text`, `set_required_uav_count`, `set_assignment_field` for role/task/objective/destination action/data/completion/expected result/return completion/altitude/speed/action duration). Unknown, out-of-range, location, unsafe or coordinate-bearing actions are dropped server-side and validated again in the browser. Proposed actions only become a pending proposal; the form, fleet and PX4/Gazebo change only after the operator presses CONFIRM, through the same controlled state as manual edits, and sequential aircraft configuration still applies.
- **Failures.** Missing key, provider error, timeout, rate limit or malformed output return a structured `{detail: {code, message}}`; the chat shows "AI is unavailable: ..." with the deterministic help text. No AI reply is ever fabricated.
- **Smoke test.** `backend/tests/test_scenario_assistant_smoke.py` is skipped unless `TRINETRA_LIVE_AI_SMOKE=1`; it makes one live provider request and prints only the reply and validated actions.

## Scenario Lab assistant: conversation lifetime and selectable options

- **Deliberate rule change.** The Scenario Lab assistant conversation is held in component memory only, one conversation per draft. It is never written to `sessionStorage`/`localStorage`, so it is empty after a page refresh and after any login. It is kept while the panel is collapsed and reopened and while another tab (for example Mission Control) is shown, because the workspace stays mounted. Switching drafts shows that draft's in-memory conversation; a new draft starts empty. Logout clears every conversation, pending proposal, busy flag and unsent input. This replaces the earlier rule that a refresh restored the conversation from per-user session storage; leftover `trinetra-scenario-chat:*` and `trinetra-active-draft:*` browser keys are deleted on load and never read.
- **Server side.** `POST /api/ai/scenario-assistant` stores no message text: it records only usage counters and an audit event (sizes, token counts, model, action counts). Separately, the Mission Planner's `/api/ai/conversations` records (`ai_conversations`) and scenario versions remain stored as before; the Scenario Lab UI does not load them.
- **Selectable options.** The assistant shows clickable options that depend on the current section and selected aircraft (always: ready?, missing?, summarize, what can you do; A: explain, set objective, duration, UAV count; B: explain a field, missing/summary for UAV n, set altitude/speed/action duration, role, task; C (review): explain Section C, why can't I deploy, explain approval, explain validation, explain Attack Lab, what does each preset do). An option only submits the message a user could have typed, through the same single handler; it never calls the AI endpoint. Options needing a value open an inline picker whose choices come from the form constants (objectives, roles, fleet size 1-5, numeric ranges) and create the normal pending proposal; nothing changes before CONFIRM. Options are disabled while a proposal is pending or the assistant is busy, and work with the AI provider unconfigured.
- **Behaviour change.** Typed "set mission duration" and "change the mission objective" commands now also become pending proposals (previously applied immediately), so every consequential assistant change needs CONFIRM.


## Scenario Lab assistant on the schema-2 mission plan

- Help text, missing-field lists, explanations, option chips and model actions all derive from the shared specs in `frontend/src/components/missionPlan.ts` (task types and their parameters, aircraft and mission fields, roles, return modes, payload categories). `backend/tests/test_mission_planning.py` checks those lists stay in step with the backend.
- Allowlisted actions (`POST /api/ai/scenario-assistant`): `set_objective`, `set_max_duration`, `set_mission_text` (description/notes), `set_constraint` (max distance/altitude), `set_required_uav_count`, and `set_assignment_field` for role, priority, task type, return mode, altitude, speed, loiter duration and the numeric/enum task parameters (a parameter applies only to the aircraft's current task type; task types are limited by the selected objectives). Locations, areas, routes, deployment and approval are never proposable. The relief-supply task is described only as a simulated, logged event.
- Typed commands: "set altitude to 30", "set task type of UAV 1 to coverage survey", "set max altitude to 80", "set max duration to 20 minutes", "I need 2 UAVs".

## Attack Lab help (2026-11-13)

The rule-based Scenario Lab assistant explains the Attack Lab, attack types, presets, seed and start time, and the ground-truth rule from the same shared text as the Attack Lab tab. Chips: "Explain Attack Lab", "What does each preset do". Typed form-changing commands still go through CONFIRM; the assistant never configures attacks. Tested with unit tests only; no live AI calls were made and the UI is not browser-verified.

## Three sections and the deploy help (2026-11-14; unit-tested, not browser-verified, no live AI call made)

- Scenario Lab has three sections: A Mission, B Individual UAV configuration, C Review and validation. The assistant's section ids are `A`, `B`, `C` (`POST /api/ai/scenario-assistant` accepts only these in `snapshot.section`). A typed "Section D" is answered with "There is no Section D any more: review and validation is Section C."
- Section C help says the next action is always shown beneath Save (a General deploys to Mission Control; an Operator submits for review and deploys once that exact version is approved), that the reasons are listed beside it when it is unavailable, and ends with the one line "GPS attack experiments are designed in the Attack Lab after saving." It carries no attack details.
- New rule-based topics: "deploy" (what Deploy to Mission Control needs), "simulation home" (mission start, the 1 km notice and the confirmed "Move simulation to mission start" restart that commands no flight), "attack fields" (start, duration, magnitude, direction, seed), "attack response" (the operator decides; the handover battery minimum is an eligibility setting only).
- Preset help comes from the shared `PRESET_SPECS`: Baseline (no attack), Slow drift, Rapid jump, Strong offset, Noise burst, and Drift on two aircraft (needs a second aircraft). It states that presets define the attack only. The words "scripted" and "benchmark" no longer appear.
- The system prompt rule about faults now says attack experiments are designed in the Attack Lab tab and are never configured from the form.

## Save status, deploy button and endurance wording (2026-11-14; unit-tested, no live AI call)

- Next to the Save button Scenario Lab shows "Saved v1" or "Unsaved changes". Beneath it only the deploy button is shown (General "Deploy to Mission Control", Operator "Submit for review") with the fleet verification row; the reasons list appears only while the button is disabled. The assistant's Section C and deploy topics describe exactly this.
- The review shows, per aircraft, the estimated duration and length next to the aircraft limit (default 20 min, 15 km) and a blocking message when it is exceeded. The assistant reads the same checklist, so "is everything ready?" lists an over-long route as a blocker.
