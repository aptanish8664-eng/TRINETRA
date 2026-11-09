# Scenario Lab

Status: partially implemented and regression-tested (2026-11-07). Scenario drafts and approvals use the existing owner-scoped immutable version workflow. Simulator execution remains the deterministic abstract backend.

## Version contents

Each `ScenarioVersionRecord` keeps the executable `SimulationConfig` JSON separate from optional intent in `scenario_version_intent` (one row per version):

- Mission objectives.
- Proposed task descriptions, optional task/UAV references, and priority.
- Advisory UAV role suggestions and rationale.
- Operating-area notes and environment notes.
- Constraints.

Intent is planning context only. Task/UAV references are validated against the selected fleet/task counts, role totals cannot exceed fleet size, and geographic coordinate text is rejected. The simulator still uses only `SimulationConfig`; these notes do not control generated task locations, assignment policy, flight behavior, weather, or geographic boundaries.

The React Scenario Lab lets an authorized user edit objectives, task notes, role counts, non-geographic area notes, environment notes, and constraints. Saving any changed config or intent creates a new immutable version. Existing versions remain unchanged; existing approval requests stay bound to the version originally submitted. General reviewers see both the exact simulator config and advisory intent before deciding.

## Compatibility and integrity

- Existing `scenario_versions` columns and JSON are unchanged. SQLAlchemy creates the new auxiliary intent table on startup; no existing row is rewritten.
- A legacy version without an intent row reads as an empty intent and retains its prior configuration-only hash.
- When intent exists, the version hash covers both simulator config and intent. The approval execution check recalculates this combined hash, so tampered intent prevents execution.
- API clients omitting the optional `intent` field preserve the latest intent on update. Sending an explicit empty intent clears it by creating a new version when needed.
- Repeating an update with unchanged config and intent does not create another version.
- Deleting a draft removes associated intent through its version foreign-key cascade. Submitted scenarios retain the existing deletion protection.

## AI transfer

The conversational planner transfers validated objectives, task proposals, role suggestions, and constraints to a new Scenario Lab version, along with the operator-selected simulator config. The first objective is used as its title. The user can edit both parts before submitting that exact immutable version for General review. The AI does not approve or execute it.

## Unsupported fields

There is no geographic origin/frame, route/waypoint registry, altitude, payload, endurance, vehicle capability, or modeled environmental parameter. `operating_area_notes` and `environment_notes` are narrative only. Do not interpret them as a validated flight plan or execution input.

## Verification

- Tests cover intent-only version creation, old-client updates preserving intent, no-op updates, invalid fleet role totals/references, coordinate rejection, approval hash integrity, AI transfer, and idempotent draft creation. Frontend component tests cover editing/saving intent and loading/editing an existing version.
- Latest full backend result: 56 passed. Frontend: 13 tests passed and production build passed.
- The populated active SQLite database was not written or migrated in this milestone. Its next backend startup will create the new empty tables via `Base.metadata.create_all`; startup against the populated database has not yet been smoke-tested.
