# Run 18 final report (Job 1 backend slowdown, Job 2 detector, Job 3 frontend)

Written 2026-11-18. Software-only simulator; the user's backend :8000, frontend :5173 and `backend/trinetra.db` were not touched (a throwaway backend :8003 with its own database was used).

## Status

| Job | Status |
|---|---|
| 1 Backend slowdown | **done, with gaps** (below) |
| 2 Detector accuracy | **done, budget NOT demonstrated** (below) |
| 3 Frontend design FE-A to FE-D | **done, with gaps** (below) |

## Job 1: causes, before and after (details: `docs/BACKEND_SLOWDOWN.md`)

* "Only one of five aircraft": (1) a stale Gazebo pose bridge older than the Gazebo server kept a dead subscription, so 0 of 6 aircraft counted as verified; fixed by comparing process start times and replacing it (tests; live: Activate for 6 aircraft went from "0 of 6" to 200). (2) A restart of a 1-aircraft mission stops the other 4 by design; the confirmation now says so.
* Page polling with 6 aircraft idle: 1002 KB/s -> 6.9 KB/s over HTTP (trails left out of polls, gzip); the telemetry poll answered in 59-135 ms instead of 1.1-1.7 s; streams 10 -> 4 Hz; backend CPU 0.9 -> 0.73 cores (0.47 with no page open).
* Browser-driven 6-aircraft restart: 3 of 3 succeeded (74, 71, 73 s). Waits lowered back to 120 s / 120 s / 150 s.
* Not done: 1- and 3-aircraft rows, per-aircraft summary endpoint, SSE, `/api/scenarios` summary (still about 85 KB gzipped, about 1 s), separate UDP receiver thread. One backend worker kept on purpose (in-memory telemetry, threat state, runs and the continuity watcher).

## Job 2: causes, aircraft-hours, before and after (details: `docs/DETECTOR_LOOPHOLES.md`, `backend/docs/ML_PIPELINE.md`)

* Cause: acceleration and braking at path corners (alerting rows 2.62 m/s2 vs 0.23 quiet; the synthetic flights had constant speed); not timing jitter (rates and gaps equal in alerting and quiet rows); speed outside 3-10 m/s partly.
* Collected **55 clean live flights = 1.51 aircraft-hours** (5 aircraft at once, 4-16 m/s, survey/spiral/patrol), split by batch: train 25 flights, validation 15, **held-out test 15 = 0.422 h**. A recorder bug lost the first collection; everything was flown again.
* Changes (data and features): live clean flights (weight 4), turn-rate and acceleration features (37 inputs), speeds 3-17 m/s in the synthetic data, tiers re-derived on validation.
* Held-out live clean flights: **54.4 -> 0 alert episodes per aircraft-hour** (0 events in 0.422 h: upper bound 7.1 per hour; **the 1-per-hour budget is not demonstrated**, about 3 aircraft-hours with no alert are needed). Validation live flights still had 4 episodes in 0.398 h (10 per hour), all in the 8 m/s spiral batch.
* Synthetic test: recall 0.657 -> 0.594, flights detected 0.848 -> 0.748, false alarms 0.26 -> 0.00 per hour, p90 latency 29.6 -> 7.0 s (a real loss of sensitivity).
* Live attacks (28 runs, monitor-level injection, both models scored on the same recorded flights): all detected by both; drift latency 4.5-5.6 s -> 10.8-11.2 s; velocity inconsistency highest tier High -> Medium; 3 runs per cell so a 100 % rate has a 95 % lower bound of only about 0.44.
* Not proven: real spoofing (the estimator velocity stays truthful), other conditions, spirals at other speeds, 16 m/s in test.
* Exports re-run: `D:\TRINETRA\exports\trinetra_dataset_2026-11-18.xlsx` (173.5 MB) and `trinetra_dataset_preview_2026-11-18.xlsx` (3.5 MB). Old model files saved in `D:\TRINETRA_scratch\artifacts_before`; new dataset in `backend/research_artifacts/dataset_v3`.

## Job 3: frontend (details: `docs/FRONTEND_DESIGN.md`)

Tokens, Card and grid; Login rebuilt as one centred card on a self-playing sky (no scroll, 36 s seamless drone loop, fly-away and shake); Overview descent (parallax layers, original SVG quadcopter, altitude readout 120 to 0 m, self-drawing flight path, landing pad, day/night, Lite visuals, fleet strip); Ctrl+K palette, skeletons, toasts, focus rings; Attack Lab cards aligned. CPU (headless Edge, software rendering): Overview 26 % of a core with visuals, 5.6 % in Lite; Login 27 % vs 3.5 %. Screenshots at 1914x826, 1440x900 and 900x900 are in `docs/screenshots/` (`feb-*`, `fec-*`, `fe-*`, `fea2-*`). Not done: audit of Scenario Lab, Fleet, Analytics, History, Research; Quick Mission's duplicated title; contrast measurement; GPU measurement; cursor tilt.

## Test counts and build (foreground, final)

* Backend: **689 passed, 1 skipped** (a-g 176, h-m 239, n-z 274 + 1 skipped).
* Frontend: `tsc` clean; **32 files, 351 tests passed**; production build OK (51 s).

## Files changed (main)

Backend: `app/main.py`, `fleet_routes.py`, `restart_routes.py`, `mission_execution/restart.py`, `research_routes.py` (recorder), `research/threat_data.py`, `threat_models.py`, `live_dataset.py` (new), `tools/wsl_fleet_manager.py`, `tools/export_dataset.py`, tests (`test_staged_start.py`, `test_api.py`, `test_live_dataset.py` new), `research_artifacts/` (model, metrics, card, `dataset_v3`). Frontend: `src/visuals/*` (new), `design-tokens.css`, `design-fixes.css`, `App.tsx`, `AccessPortal.tsx`, `AttackLab.tsx`, `RunRecords.tsx`, `RestartDialog.tsx`, `Px4MissionControl.tsx`, `CommandPalette.tsx`, `Toasts.tsx`, `services/api.ts`, tests. Docs: `BACKEND_SLOWDOWN.md`, `FRONTEND_DESIGN.md`, `DETECTOR_LOOPHOLES.md`, `ML_PIPELINE.md`, API contract, progress file, run log. Nothing was committed.

## Not verified

The detector budget; real-spoofing detection; idle-versus-loaded timing for the false alarms; 1- and 3-aircraft polling costs; GPU rendering cost; the pages not audited; `trinetra_doctor.py --start` (unit-tested only); responses from an OPERATOR account in the browser.

## Cleanup state

The throwaway backend :8003 (recording to `D:\TRINETRA_scratch\attack_flights`), Vite :5175 and a 5-aircraft simulation are still running; the WSL bridges send to the throwaway ports (14620/14612), not 14600/14610. Before using the normal stack press Activate and verify there (it replaces a mismatching bridge), or stop the simulation. Do not run two backends against one simulator.
