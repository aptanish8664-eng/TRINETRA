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

## Start in simulation and the assistant (2026-11-14)

- The Scenario Lab assistant has no action for starting, pausing, resuming or aborting a run, and none may be added to the allowlist: its actions remain form edits only. Starting is done in Mission Control, in the Deployed mission panel, after an in-page confirmation.
- The text beneath the deploy button in Scenario Lab says that deploying arms or flies nothing and that starting is done in Mission Control; for an Operator it says Deploy and Start in simulation stay disabled until a General approves that exact version.
- The rule-based help has no dedicated "start in simulation" topic yet (not implemented). The existing "deploy" and "approval" topics are unchanged.
- No live AI call was made for this work.

## Limits update
Assistant speed bound is now 0.1-90 m/s; flight time up to 300 min and route length up to 1,000 km come from the backend limits endpoint. Area tasks are limited to 20 km; long legs need a waypoint route.


## Scenario Lab assistant on the schema-2 mission plan

- Help text, missing-field lists, explanations, option chips and model actions all derive from the shared specs in `frontend/src/components/missionPlan.ts` (task types and their parameters, aircraft and mission fields, roles, return modes, payload categories). `backend/tests/test_mission_planning.py` checks those lists stay in step with the backend.
- Allowlisted actions (`POST /api/ai/scenario-assistant`): `set_objective`, `set_max_duration`, `set_mission_text` (description/notes), `set_constraint` (max distance/altitude), `set_required_uav_count`, and `set_assignment_field` for role, priority, task type, return mode, altitude, speed, loiter duration and the numeric/enum task parameters (a parameter applies only to the aircraft's current task type; task types are limited by the selected objectives). Locations, areas, routes, deployment and approval are never proposable. The relief-supply task is described only as a simulated, logged event.
- Typed commands: "set altitude to 30", "set task type of UAV 1 to coverage survey", "set max altitude to 80", "set max duration to 20 minutes", "I need 2 UAVs".

## Quick mission and the assistant as the front door (2026-11-15; implemented, unit/API tested, click-tested in headless Edge against a throwaway backend and the real PX4 SITL aircraft)

**What it is.** The Scenario Lab opens with a **Quick mission** panel. The user picks a mission type, clicks one point on the map (or picks a search result, or presses "Use the aircraft's position") and, optionally, changes the size or the number of aircraft. The panel fills the SAME draft the manual form edits (Sections A and B, now behind an **Advanced** expander; saved versions open exactly as before) and, with exactly one aircraft and no errors, the route is generated and the aircraft mission confirmed automatically. The scenario name is suggested from the type and the place and stays editable. Nothing is saved, deployed or flown by filling the draft. **Save**, **Save and deploy to Mission Control** (a General; an Operator is told a General must approve the saved version first) and the later **Start in simulation** are separate button presses; Save and deploy never starts a flight.

**One place for the rules.** Every derived value lives in `backend/app/mission_planning/quick.py`, is served by `GET /api/mission-plans/limits` (`quick` block) and is computed by `POST /api/mission-plans/quick`; the frontend keeps no copy of a default or formula. `backend/tests/test_quick_mission.py` pins them.

| Rule | Value |
| --- | --- |
| Mission types | area survey (coverage survey), search spiral (expanding square), relief delivery (payload drop-off, simulated), perimeter patrol, waypoint route. No new generator; objectives are chosen from the type. |
| Area centre | the point the user chose (delivery and waypoint route: the destination). |
| Start | the chosen point for area missions, or the aircraft's position when "Use the aircraft's position" is the Where. For delivery and waypoint route the start is the aircraft's position. A start outside the area is a visible warning, never a block (the backend still validates; an aircraft far away is handled by the existing relocation notice and confirmation). |
| Datum / heading / return / loiter | datum = area centre; first leg heading 0 (north); return to start and land; loiter 0 s. |
| Size | one number, the area radius; default 300 m; allowed 50 m to 5 km (longer legs: use a waypoint route). |
| Speed | 8 m/s (`TRINETRA_QUICK_SPEED_MPS`, clipped to the planning limits). Reason shown in the panel: the threat-detector baseline was validated only at that speed. |
| Altitude | 50 m (`TRINETRA_QUICK_ALTITUDE_M`, clipped to the planning limits). |
| Track / line spacing | 0.6 x altitude, rounded to 5 m, at least 10 m (50 m gives 30 m). Widened in 25 % steps only when the pattern would exceed the waypoint limit, with a warning. |
| Spiral size | the spiral covers 2/3 of the area radius on each side of the datum so it fits inside the circle: the generator's `search_radius_m` (its longest leg) = 2 x 2/3 x radius (400 m for a 300 m area). |
| Relief delivery | drop altitude min(10 m, altitude), hover 10 s, payload "relief supplies"; the release is a simulated, logged event. |
| Roles | the first aircraft is primary, priority 1 (the priority field is not shown for a single aircraft); others are standby, priority 2. Never more aircraft than verified ones. |
| Limits (auto, overridable) | max distance = farthest route point from the start x 1.3 + 50 m, rounded up to 50 m; max altitude = max(1.5 x altitude, altitude + 30 m) capped at the configured maximum; max duration = 1.5 x the estimated flight time in whole minutes. Order: generate the route, derive the limits, then validate. Shown as "auto (value)"; a value the user types is enforced exactly and is never replaced (USE AUTO turns the derivation back on). |

Golden numbers (pinned in the tests, from the real generators at 12.97 N 77.59 E, 8 m/s, 50 m): search spiral 5857 m, 732 s (12.2 min), 30 waypoints, limits 450 m / 80 m / 18 min; area survey 10879 m, 1360 s, 43 waypoints, 450 / 80 / 34; perimeter patrol 2584 m, 323 s, 52 waypoints, 450 / 80 / 8; relief delivery from an aircraft 300 m away 740 m, 102.5 s, 5 waypoints, 450 / 80 / 3; waypoint route 697 m, 87 s, 4 waypoints, 450 / 80 / 2. The requested example "about 5.3 km and 11 minutes for a 300 m spiral with 30 m tracks" was not reproduced exactly: the whole route (climb, transit to the datum, the spiral, return and landing) is 5.86 km and 12.2 min.

**Relaxed rules (were convenience-only blocks).** A start outside the operating area now warns. When the user explicitly raises the maximum duration above the aircraft default (`max_flight_s`), a route longer than the endurance default is a warning (`endurance_time_raised`, `endurance_distance_raised`) instead of a blocking error; with no raised limit the endurance errors are unchanged. Invalid and unexecutable plans stay blocked: no route, altitude below the minimum, speed outside the range, waypoint cap, area patterns beyond 20 km, unverified aircraft.

**The assistant (the existing Scenario Lab assistant, not a second one).** Free text goes first to the deterministic, offline parser `POST /api/mission-plans/quick/parse` (no external AI, no API key; stateless: the client sends the state it got last time), after the older deterministic commands (set fields, activate fleet) and before the read-only help and the AI model. It understands mission type, size with units (m, metres, km, kilometres, "half a kilometre", "0.2 km"), drone count in digits and words, speed (m/s, km/h), altitude, and "here / the aircraft's position / the point I click / this area / a capitalised place name". It asks only for what is missing, one question at a time, with buttons for fixed choices ("Click the place on the map", "Use the aircraft's position", "Use 90 m/s", "Use the default 8 m/s", "Never mind"). A map click (or a picked search result) answers the where question. It then shows a one-screen summary (type, area, start, aircraft, speed, altitude, estimated time and distance, derived limits) and **CONFIRM** fills the draft through the same request builder and the same code path as the Quick panel, so the same inputs give the same draft (tested request for request and stored draft for stored draft).

What it can do: the five types above, with the sizes, counts, speeds and altitudes it is given. What it cannot do: follow or track a moving target, photograph or inspect, take off, land, arm, disarm or return (those are done in Mission Control), spray, several missions in one sentence. These are answered honestly ("Not supported: ...") with the closest supported type offered (for example a waypoint route). Conflicting values (a speed above the limit, an altitude above the limit, a size outside 50 m to 5 km, more drones than verified aircraft) are asked about, with the maximum or the default as buttons, never silently clamped. Sentences that only ask it to save, deploy, start, run, abort, pause, send, submit or approve something are refused ("run a survey here" is still a survey): **the assistant only fills the draft**. It never calls a save, deploy, approval or execution route (tested: only the parse and quick endpoints are used); saving and deploying remain the user's own clicks and the role rules are unchanged. The parser is a pure function with a fixed state shape so a language model could replace it later without changing the callers.

**Draft safety.** An unsaved Scenario Lab draft survives a refresh (browser `localStorage`, one entry per signed-in user and scenario, never sent to the server). "Restored your unsaved draft" appears with a **Discard** button; saving and signing out remove it; the browser asks before the page is left with unsaved changes. A saved version that was saved without a route says so when it is opened.

## Quick Mission tab and the assistant on both pages (2026-11-15; unit-tested, click-tested)

Quick Mission is now its own tab (`/quick-mission`) and Scenario Lab no longer contains the quick panel: Scenario Lab shows Sections A-C and a "Create a quick mission" link. The Scenario Assistant is mounted on BOTH pages (same component, same offline parser and endpoints). On Quick Mission, "search 300 m with one drone" then a click fills the quick form and the plan; in Scenario Lab the same sentence (confirmed with CONFIRM) fills Sections A-C through the same derivation, and a map click (on the one planning map) answers its where question. It still only fills the draft: it never saves, deploys, starts or aborts. What Quick Mission saves carries its quick source (type, place, size, aircraft count, speed, altitude, name) so the General can reopen it with "Edit in Quick Mission"; fine-tuning the plan in Scenario Lab drops that claim (rule in the API contract).

## Land here, Restart at mission start and the switch (Run 16)
The offline help (`assistantHelp.ts`) explains, in plain words and only as help (the assistant never presses these buttons): **Land here** lands the aircraft at its current position and then disarms it, in the air or armed on the ground, with or without a run; it is not a motor kill and not return to launch (Abort stays return to launch); the run ends as "aborted: landed in place". **Restart at mission start** is always available (idle, armed on the ground, flying): the current flight ends, PX4 and Gazebo restart at the mission start (about 20 s), the steps are shown with the failed step, its reason and a Retry. **Switch and restart at the new start** (in the "newer deployment is ready" banner) ends the flight, restarts at the NEW mission's start and shows the new deployment; an Operator can use it only for an approved version. Keywords: land here, land now, disarm, restart at mission start, switch and restart, newer deployment. Tests: `assistantHelp.test.ts`.

## Saved missions on the Quick Mission page (Run 17)
The assistant understands "which saved missions do I have" (lists names and versions) and "open the saved mission <name>" (fills the Quick Mission form from it, or says a Scenario Lab mission cannot be edited here; an exact name wins, a partial match must be unique). It only fills or lists: it never saves, deploys or starts anything, and an unsaved form is protected by the same warning as the picker. Tests: `ScenarioWorkspace.quick.test.tsx`.

## Any number of aircraft (Run 17, Part 2)
The numbers in the derivation list above changed: **roles** are UAV-1 primary and every other aircraft decoy until the user chooses (any aircraft may be primary, decoy or standby; at least one primary); a **decoy** flies a parallel lane 25 m, 50 m, 75 m ... beside the primary route (inside the area, both sides; more decoys than lanes reuse the lanes 5 m higher per layer); a **standby** stays on the ground; default starts are 4 m apart going north, a new column 4 m east after every 10 aircraft; per aircraft the user may override altitude, speed and start offset. Planning has no limit and never depends on running aircraft; how many can be simulated is `TRINETRA_SIM_MAX_AIRCRAFT`. The assistant understands "12 UAVs, 2 decoys" (two decoys, the rest standby), "3 drones and 1 standby", "make UAV-7 standby" (a draft edit on the Quick Mission form), asks "Did you mean N?" once above 50, and never saves, deploys or starts anything. Tests: `backend/tests/test_multi_aircraft_planning.py`, `ScenarioWorkspace.quick.test.tsx`, `quickMission.test.ts`.


### Health check topic (Part 8)

The assistant's read-only help has a `health check` topic (keywords: health check, doctor, something is not working, check my setup, simulator not starting). It explains `backend/tools/trinetra_doctor.py` (read-only; `--start` to start the backend and frontend), what it checks and that it never sends a command to an aircraft or reads `backend/.env`; it points to `docs/HOW_TO_START_A_RUN.md`. The assistant itself still only fills or edits drafts: it never runs the tool, saves, deploys or starts anything.
