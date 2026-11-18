# Run 17 final report (Parts 0 to 9 and the final checks)

Written 2026-11-17. Software-only simulator throughout (PX4 SITL and Gazebo in WSL, throwaway backend :8003 with its own database; the user's backend :8000, frontend :5173 and `backend/trinetra.db` were not touched). The frontend design work (FE-A to FE-D) is **not part of this report**; see the last section.

## 1. Per part: done / partly / not done

| Part | Status | What exists | Reused vs built |
|---|---|---|---|
| 0 Dataset export (xlsx) | **done** | `backend/tools/export_dataset.py`, `D:\TRINETRA\exports\trinetra_dataset_2026-11-16.xlsx` (181.3 MB, 248 s) | built (new tool, reuses the dataset code) |
| 1 Saved missions in Quick Mission | **done** | "Open a saved mission" picker | built on existing scenario API |
| 2 Any number of aircraft | **done** | planning (per-aircraft rows, decoy lanes), capacity message, plan-only deploy, Scenario Lab Section B, assistant limits | extended existing planner |
| 2B | **done** | preview workbook `exports\trinetra_dataset_preview_2026-11-17.xlsx` (3.5 MB) and the rest of 2B (see run log) | built |
| F Reliable multi-aircraft start | **done** | staged start, progress-based waiting, per-aircraft dialog, partial result with Retry/Continue, capacity measured 1-7, default 6, `docs/FLEET_CAPACITY.md` | rebuilt the start path; reused the fleet manager |
| 3 Implement switch and live runner | **done** | Implement switch, hidden schedule, monitor-level injection with the shared attack functions, run records (outcome, latency, false alarms, operator responses, timeline), xlsx export, "N experiments armed" | built; reused experiment storage and `attacks.inject` |
| 4 Threat response options | **done** | audit table, real Acknowledge / Hold / Resume / Return / Land here, Restart, audit with score/tier/evidence, High+Critical alert, sound switch | built; reused the Mission Control command code |
| 5 Multi-aircraft execution | **done** (limits below) | start of every flying aircraft, per-aircraft runs and controls, hand over, switch primary, 5-to-2 switch fix | built; reused per-aircraft runs and executor |
| 6 Mission continuity | **done** | watcher, outcomes completed/degraded/failed, stored on the run record | built |
| 7 Detector accuracy | **PARTLY**: step 1 only (loophole table and plan); **retraining and before/after NOT done** | `docs/DETECTOR_LOOPHOLES.md` | - |
| 8 Reliability items | **done** | far-home restart, OPERATOR Land here (live), `trinetra_doctor.py` + docs + assistant topic, flaky tests | built |
| 9 Future realism study | **done** (study only) | `docs/FUTURE_REALISM.md` | - |

## 2. Part F: cause and capacity

Cause (measured): host CPU starvation (Gazebo real-time factor 0.41-0.47 under load) plus everything started at once plus the Gazebo GUI plus a fixed 90 s timer. Old start with 5 aircraft under load: 0 of 2 runs ready; staged start under the same load: ready after 196 s and 298 s. A second defect: the telemetry bridge accepted only PX4 source ports 18570-18574, so aircraft 6 and above were silently dropped (now 18570-18585).

| Aircraft | Runs ready | Ready time s (min/avg/max) | RTF (min/avg) | Peak load1 | Lowest free MiB |
|---|---|---|---|---|---|
| 1 | 3/3 | 16.2 / 16.8 / 17.5 | 1.00 / 1.00 | 7.7 | 6876 |
| 2 | 3/3 | 17.0 / 17.2 / 17.4 | 1.00 / 1.00 | 2.9 | 6820 |
| 3 | 3/3 | 29.6 / 30.5 / 31.2 | 1.00 / 1.00 | 4.9 | 6805 |
| 4 | 3/3 | 31.1 / 32.5 / 34.6 | 0.96 / 0.99 | 6.6 | 6771 |
| 5 | 3/3 | 44.9 / 45.1 / 45.4 | 0.99 / 1.00 | 9.2 | 6760 |
| 6 | 3/3 (after the port fix) | 46.9 / 47.9 / 48.7 | 0.99 / 0.99 | 13.0 | 6678 |
| 7 | 1/3 | 80.6 (only success) | 0.84 / 0.91 | 14.9 | 6647 |

Capacity set to **6** (`TRINETRA_SIM_MAX_AIRCRAFT`). 12-aircraft study and recommendation (keep 6; cut the UI payload first, then more WSL CPUs if wanted; no lightweight mode built) are in `docs/FLEET_CAPACITY.md`; the 12-aircraft numbers are projections, not measurements.

## 3. Audit tables

Part 4 (before the change): all six response buttons only stored a "recorded, not executed" decision. After: Acknowledge (record only, by design), Hold, Resume, Return to launch, Land here (real commands through the Mission Control code), Restart (existing dialog), Hand over and Switch primary (Part 5). Full table with live results: `docs/THREAT_RESPONSES.md`.

Part 5 (before the change): telemetry, threat monitor, map and per-aircraft runs already handled several aircraft; starting a flight refused every plan with more than one aircraft; responses involving another aircraft were disabled placeholders. Full table: `docs/MULTI_AIRCRAFT.md`.

## 4. Part 7 loophole table (summary) and before/after

11 loopholes are in `docs/DETECTOR_LOOPHOLES.md` (speed range 3-10 m/s in training vs 12/16 m/s, no take-off/landing/hold/RTL/spiral phases, window-start assumption broken by a restart, GPS-aided inertial reference, monitor-level injection, synthetic-to-live false-alarm gap, alert hold tail, truth columns next to features, clipping handling, single-aircraft baseline only, weak hard cases). **Before/after: not available.** The only new measurement is the "before" at live: **2-3 Medium-or-higher baseline episodes per 7-minute 10 m/s flight (about 17-25 per hour)** against 0.26 per hour on the synthetic test set and the budget of 1 per hour. No retraining was run, so there is no "after"; the detector, model, metrics and model card are unchanged.

## 5. Live timings (real PX4 SITL, one machine)

* Restart at a mission start: 18-21 s for 1-2 aircraft; 32 s for 3; 44-52 s for 5; 6.7 km relocation 18 s.
* Staged start through the browser dialog: about 72-99 s for 3 aircraft; a retry of the one missing aircraft finishes the dialog (about 123 s total in the second run).
* Flights: 285 s planned routes took 426-435 s of wall time while Mission Control was open; the capacity-run comparison (282 s vs 314 s for 1 vs 6 aircraft) is in `docs/FLEET_CAPACITY.md`.
* Land here through the response API took 7.9 s to answer with the page open (the busy host slows every call; Hold and Resume were not timed); OPERATOR Land here 4.1 s, disarmed 43 s after the click; handover: threatened aircraft returning and standby flying within about 8 s of the response; continuity outcome "completed" at +153 s.
* Part 3: one experiment flight (jump 150 m) detected at once (Critical at the start of the attack in the first record); two experiments in one flight both detected.

## 6. Export paths

* `D:\TRINETRA\exports\trinetra_dataset_2026-11-16.xlsx` (181.3 MB)
* `D:\TRINETRA\exports\trinetra_dataset_preview_2026-11-17.xlsx` (3.5 MB)
* `D:\TRINETRA\exports\trinetra_run_records_2026-11-17.xlsx` (30 KB, from the Attack Lab button)
* `D:\TRINETRA\exports\doctor_logs\` (empty log files created by an earlier version of a doctor test; safe to delete, I did not delete them)

## 7. Tests and build (final checks, run in the foreground)

* Backend, split by file name: a-g **176 passed**; h-m **237 passed** (two tests pinned to the old behaviour were updated with the reason: the old px4-9 id limit and the old `multi_aircraft_not_available` code); n-z **272 passed, 1 skipped**. Total **685 passed, 1 skipped**.
* Frontend: `tsc --noEmit` clean; **30 files, 340 tests passed**; production build OK (47.8 s). The flaky tests are fixed by a 30 s vitest timeout and a longer first `waitFor` (4 repeated runs of the two historically flaky files and 3 full runs passed).

## 8. Files changed

84 tracked files modified and 180 new files (code, tests, docs and screenshots). Main new code: `backend/app/restart_routes.py`, `mission_execution/restart.py`, `response_routes.py`, `research/live_experiments.py`, `research/continuity.py`, `research/run_export.py`, `mission_planning/quick.py`, `tools/export_dataset.py`, `tools/trinetra_doctor.py`; frontend `RestartDialog`, `QuickMissionForm`, `RunRecords`, `FleetRuns`, `ContinuityPanel`, `ThreatCard`/`ThreatMonitor`; docs `FLEET_CAPACITY.md`, `THREAT_RESPONSES.md`, `MULTI_AIRCRAFT.md`, `DETECTOR_LOOPHOLES.md`, `HOW_TO_START_A_RUN.md`, `FUTURE_REALISM.md`. API changes are in `TRINETRA_API_CONTRACT.md`; milestone notes in `TRINETRA_IMPLEMENTATION_PROGRESS.md` and `docs/OVERNIGHT_RUN_LOG.md`. Nothing was committed.

## 9. Unverified or not done (honest list)

* Part 7 steps 2-4 (new training data, retraining, before/after with a confidence bound, model card, ML pipeline numbers, Part 0 re-export) were not done.
* Degraded and failed continuity outcomes and the outcome of a Switch primary were not produced live (unit-tested only).
* Responses from an OPERATOR account were not clicked in the browser (OPERATOR Land here was checked through the API).
* 6 or more aircraft flying together were not flown (5 was the largest); sizes above 7 were never started; the 12-aircraft figures are projections.
* The backend/UI throughput problem with Mission Control open (about 2 MB per telemetry poll) is documented but not fixed; one browser restart failed once with "0 of 3 telemetry" and I could not prove its cause.
* After Hold/Resume the live monitor restarts its window ("collecting data" for about 10 s): not investigated.
* The live false-alarm rate of the detector is unknown but clearly above the synthetic estimate (see section 4).
* `trinetra_doctor.py --start` was unit-tested but not run live (to avoid starting duplicate servers on the user's ports); the PX4 facts in `FUTURE_REALISM.md` were not re-verified against the installed build.
* Reassign decoy was not built; role changes made by a hand over or switch are in memory only.

## 10. Cleanup state

The throwaway stack is still running: backend :8003, Vite :5175 and a 2-aircraft SITL simulation, and the WSL bridges send to the throwaway backend's ports (14620/14612), **not** to the standard ports 14600/14610. Before using the normal stack press **Activate and verify** in the normal Mission Control (it replaces a mismatching bridge) or stop the simulation. The user's own backend :8000 and frontend :5173 were not changed.
