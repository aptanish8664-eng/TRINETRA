# TRINETRA AI and ML Audit

**Status:** source-inspected; research workflow is abstract-simulator-only; live provider and independent real telemetry validation are unverified.

## AI mission-planning provider

The AI module isolates an optional OpenAI Responses provider behind a protocol. The API uses strict structured schemas, validates proposals against operator-selected simulator configuration, redacts common direct identifier/secret patterns, rejects geographic-coordinate text, limits input/output/time/usage, persists owner-scoped conversations, and records usage/audit outcomes. Proposal output is advisory; it does not change simulator settings, approve a scenario, or execute a run. General review and explicit user transfer to Scenario Lab remain separate.

The API provider is unavailable when no backend key is configured; manual planning remains available. The project docs state that live OpenAI calls were not performed. Any AI-generated task notes and scenario intent are descriptive/advisory, not validated navigation or flight instructions. Deterministic contradiction checks cover a limited set of explicit statements; broader natural-language semantics remain model-mediated.

## Dataset and classifier

The dataset pipeline creates content-addressed, versioned samples from deterministic abstract runs, separates train/validation/test by seed, checks schema/label consistency and hashes, and excludes truth coordinates/attack labels from features. The optional scikit-learn decision-tree pipeline trains on train seeds, selects a threshold on validation data, tests on held-out seeds, serializes a validated JSON tree, and checks artifact integrity. Runtime model loading is optional; heuristic detection remains the default.

Project research documentation reports 72,900 rows over 30 abstract seeds, nine profiles, and an 18/6/6 seed split. It reports aggregate test F1 around 0.882 and explicitly notes the six-positive-sample jump profile, small seed count, uncalibrated confidence, and absence of field/PX4 validation. These are repository-reported historical artifact metrics, not recomputed in this audit and not operational performance claims.

The UI and experiment outputs disclose important limitations: precision/recall/F1 are run-level event-presence indicators rather than per-timestep metrics; there are no confidence intervals in a single run; model confidence is uncalibrated. A high abstract test score cannot establish robustness to real sensor noise, dynamics, environments, adversarial behavior, or distribution shift.

## Findings and recommendations

- Dataset/model tooling is implemented but not independently reproduced during this audit. The current backend research tests were run in a selected batch and the result is in the test report.
- Live OpenAI provider acceptance is blocked by unconfigured/unavailable credentials and was not attempted.
- Add broader seed groups, per-profile uncertainty, calibration, robustness/sensitivity tests, and an independent held-out simulator configuration before increasing research claims.
- Keep any future PX4 data evaluation separately sourced, permissioned, and explicitly SITL. Do not mix synthetic labels or hidden truth into operational inference inputs.
- Ensure optional AI keys remain backend-only; no key was inspected or written during this audit.
