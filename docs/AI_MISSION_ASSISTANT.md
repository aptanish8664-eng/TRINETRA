# AI Mission Planner and Assessment

## Configure a backend-only API key

The OpenAI key belongs to the backend process. Never send it in chat, put it in frontend code, browser storage, a command line, or a committed file.

1. Sign in to the [OpenAI Platform](https://platform.openai.com/) and confirm that the organization/project has API access and billing configured.
2. Create a project API key for TRINETRA in the platform's [API keys page](https://platform.openai.com/api-keys). Copy it once into a local password manager or directly into the local `.env` editor.
3. From the repository root, create the ignored local file and open it:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

4. Set `OPENAI_API_KEY` in that local file. Keep it out of source code and commits. `.gitignore` ignores `.env` and `.env.*` while allowing the placeholder `.env.example`.
5. Configure the model and limits below, start the backend, sign in, and open **Mission Planner**. Its status endpoint reports whether the backend has a key configured. It does not make a paid health-check request.

The backend loads repository-root `.env` with `python-dotenv`; host environment variables take precedence. In production, use the deployment secret manager instead of a checked-in file, set the frontend's exact deployed origin in `TRINETRA_ALLOWED_ORIGINS`, and set `TRINETRA_SESSION_COOKIE_SECURE=true` behind HTTPS. Never define `OPENAI_API_KEY` with a `VITE_` prefix.

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

The existing manual workflow remains available in **Scenario Lab**. Existing `POST /api/simulations` request/response payloads remain unchanged. `OPERATOR` and `GENERAL` permissions are enforced by separate backend routes; restricted command review is reserved for a different `GENERAL` user. Approval creates a ready run; starting is a separate Mission Control action.

## Validation and geographic limits

The model receives only the operator's mission text after common email/phone and credential-pattern redaction plus the selected abstract simulator configuration and valid generated UAV/task IDs. A request containing coordinate pairs is rejected before reaching the provider. The structured result has no coordinate field; generated coordinate-like text is rejected. The backend validates task references, UAV IDs, assignment capacity, role counts, and simulator settings. A proposal with an unknown waypoint reference is stripped of that reference, marked as needing clarification, and cannot be approved. Redaction is pattern-based and cannot identify every possible secret, so do not paste secrets into mission text.

This repository has no geographic waypoint registry. The simulator uses seeded abstract task markers and generates positions itself. Suggested tasks and roles are advisory and never configure the engine. For approved proposals, only the operator-selected, deterministically validated simulator settings reach the simulator. This is a software research demonstrator, not a route planner, flight controller, or source of safety guarantees.

Mission Assessment sends only reported GPS, validated anomaly/threat fields, task IDs, progress, and selected event metadata. It does not send hidden truth positions, hidden scenario attack labels, user identity, session credentials, or raw event reasons. The backend rejects evidence references outside the supplied telemetry and filters response suggestions through the user's role permissions and simulation state. Numeric threat scores, completion metrics, and task counts shown in the result are taken from deterministic backend state; the model cannot supply those values.

## Privacy, billing, and model choice

The adapter uses the official OpenAI Python SDK and Responses API with Structured Outputs, `store=false`, a bounded timeout, no SDK retries, a strict JSON Schema followed by Pydantic validation. The configured model must support the Responses API and Structured Outputs; the default `gpt-4.1-mini` supports both according to its [model page](https://developers.openai.com/api/docs/models/gpt-4.1-mini). Choose another supported model through `OPENAI_MODEL` after reviewing current [model guidance](https://developers.openai.com/api/docs/models) and [API pricing](https://developers.openai.com/api/docs/pricing). API usage is metered by model and tokens, and platform billing must be configured separately from this application.

The application does not persist the original prompt or put it in audit metadata. It stores the structured proposal so the operator can review and approve it. Audit records store operator ID, model, response ID, input character/token counts, output tokens, duration, status, and proposal/run IDs—not prompt contents, API keys, passwords, session cookies, or CSRF values. Protect the local database and backups because reviewed proposal text is persisted there.

OpenAI's official data-control documentation says API data is not used for model training by default, abuse-monitoring logs may retain content for up to 30 days, and Responses application-state retention depends on `store`; this adapter sets `store=false`. Review the current [data controls](https://developers.openai.com/api/docs/guides/your-data) and your organization's account settings before sending sensitive mission descriptions. Do not enter personal, classified, or operationally sensitive details.

## Test and availability behavior

The backend tests inject a mocked provider; they do not require a key or make billable API calls. If no key is configured, the protected status endpoint reports AI unavailable and proposal/assessment endpoints return a safe 503. The existing manual scenario planner continues to work.

The service has not been validated against a real provider account in this checkout. OpenAPI schema generation currently fails in the repository's Python 3.9/Pydantic 2.11 environment, including for an empty FastAPI app. Structured Outputs uses an explicit JSON Schema to avoid that unrelated local Pydantic schema-generation failure; API `/docs` generation remains an environment limitation to address separately.
