# Multi-aircraft execution (Part 5)

Software-only: every aircraft is a PX4 SITL instance in one Gazebo world. Status words: implemented / tested (unit) / verified live (real SITL flights, throwaway backend).

## 1. Audit before the change

| Piece | State before | Evidence |
|---|---|---|
| Plans with many aircraft, per-aircraft roles and routes | implemented (Part 2) | planning tests |
| Starting N aircraft in the simulator and verifying telemetry/pose | implemented (Part F) | capacity table in docs/FLEET_CAPACITY.md |
| Telemetry, battery and map for several aircraft | implemented: the cards, battery list and map already list every aircraft | Part F screenshots |
| Threat monitor for several aircraft (per-aircraft windows, peer comparison, roles from the plan) | implemented | `compute_live`, test_live_threat |
| Starting a flight | **single aircraft only**: the pre-start check refused any plan with more than one aircraft ("multi-aircraft execution not available yet") | old test `test_two_aircraft_plans...` |
| Per-aircraft run, pause, resume, abort, land | implemented per run (one run per aircraft) but the page showed only the newest run | `executions/latest` |
| Responses that involve another aircraft | disabled placeholders | Part 4 |

## 2. What was built

* **Start**: `GET /api/mission-control/executions/preflight` now returns `fleet` (one entry per aircraft: flies / can_start / blocking). `POST /api/mission-control/executions` starts **every flying aircraft** (primary first, then decoys); standby aircraft stay on the ground. All flying aircraft must pass their own pre-start checks or nothing is started. If a decoy fails to start after the primary has started, the primary keeps flying and the answer lists `not_started` with the reason. One run per aircraft; the single-aircraft answer shape is unchanged (a multi start adds `executions`).
* **Per-aircraft runs**: `GET /api/mission-control/executions/fleet?deployment_id=` lists each aircraft's newest run with its own controls (a pause of one never touches another); `executions/latest` returns the primary's run. Mission Control shows an "Aircraft runs" block per aircraft with PAUSE / RESUME / RETURN / LAND HERE.
* **Responses** (the Part 4 block): *Hand over to a standby aircraft* checks everything first (the 40 % battery rule, the standby's own pre-start checks, up to 500 m from its route start), then sends the threatened aircraft back to launch and starts the standby on **its own planned route** (it does not continue where the other stopped) and makes it the primary; *Switch primary* swaps the roles of the primary and a flying decoy (monitor roles only; no command). Both are confirmed, audited with the live score, tier and evidence, and stored as research decisions. Role changes are in memory only (reset by a simulation restart or a backend restart); the plan is never edited.
* **Switching to a smaller mission**: after a full restart the telemetry and pose stores forget aircraft that are not part of the new simulation (they used to stay listed as "link lost in flight").
* Not built: *Reassign decoy*; a handover that continues the threatened aircraft's route; roles that survive a backend restart.

## 3. Verified live (real PX4 SITL, 2 to 5 aircraft)

| Check | Result |
|---|---|
| (a) 2 aircraft, own telemetry | both shown with their own altitude, battery, armed state and mission progress |
| (b) primary + decoy flying together from one Start | both armed and climbed together; routes of 285 s and 204 s; each with its own threat state and its own run; pausing the decoy left the primary flying, resuming it let the decoy continue (browser clicks at 1914x826 and 1440x900); a standby stayed on the ground |
| Primary, decoy and standby (3 aircraft) | the start listed 2 flying and 1 on the ground |
| (c) Hand over to standby (px4-1 attacked, High/Critical alert) | px4-1 sent back to launch (run ended), px4-3 started its own route and showed as the new primary, px4-1's card showed role standby; recorded and audited. The first attempt was refused honestly: the standby sat about 100 m from its own route start and the old 50 m rule blocked it (now 500 m for a handover). Clicked at 1914x826 only |
| (c) Switch primary with the flying decoy | px4-2 became the primary and px4-1 a decoy on the threat cards, no command sent (1914x826; the 1440x900 repeat could not offer the button again because the role had already been switched, as designed) |
| (d) 3 flying (primary + 2 decoys) | all three flew together, each `executing` with its own progress |
| (d) 5 flying (primary + 4 decoys) | all five armed, climbed to 30 m and flew; restart at their start took 44-52 s |
| (d) switch from the 5-aircraft mission to a 2-aircraft mission | restart at the new start in 20-24 s, "2 of 2 ready", only px4-1 and px4-2 listed afterwards (before the fix px4-3 to px4-5 stayed as "link lost in flight") |

Screenshots: `docs/screenshots/p5-*` (per-aircraft runs, decoy paused) and `p5r-*` (handover, switch).

## 4. Limits and observations (honest)

* During these flights the baseline (no attack) assessment reached **Medium** several times for the primary and once for a decoy, so a Medium alert alone is not proof of an attack (Part 7 input).
* A handover is a replacement flight, not a seamless continuation: the standby flies its own route from its own start.
* The fake PX4 peer in the unit tests models system 1 only, so the second aircraft's flight is checked as started, not flown, in the unit tests; the live flights above cover the real behaviour.
* Only GENERAL accounts were used in the live checks.
* 6 or more aircraft flying together were not flown (5 was the largest); the measured reliable size for starting is 6 (docs/FLEET_CAPACITY.md).


## 5. Mission continuity after a response (Part 6)

Software-only. `backend/app/research/continuity.py` follows what the simulated aircraft really do after *Hand over to a standby aircraft* or *Switch primary* (checked every 2 s from the stored runs, synced from the executor like the Mission Control page, and the live telemetry):

| Check | Passes when |
|---|---|
| New primary flying its route | its run reaches a flying state within 90 s (handover) |
| Previous aircraft safe | after a handover: on the ground and disarmed (checked in telemetry, 120 s allowed after its run ended); after a switch: still flying its own route |
| Mission progressing | the new primary's route progress grows (a stall of 120 s is reported) or its run completes |

Outcome: **completed** (the new primary finished its route and the previous aircraft is confirmed safe), **degraded** (the mission went on but something is not confirmed: previous aircraft not confirmed safe, the new primary's flight ended early with its progress, or the watch time ran out), **failed** (the new primary never flew, its run failed or its link was lost). Saved in the audit trail (`reconfiguration_outcome`) and added to the stored experiment run record of the flight (`reconfigurations`, written even when the record already exists). `GET /api/research/reconfigurations` lists them; Mission Control shows them under the threat monitor header and the Attack Lab run record shows them.

Verified live (real PX4 SITL): px4-1 attacked (Critical), handover to px4-3 at +38 s; checks turned true one after another (px4-3 flying within 8 s, previous aircraft on the ground and disarmed at 40 s, progress rising), and the outcome was **completed** at +153 s when px4-3 reached 100 % of its route; the outcome was stored on the run record. The first live attempt reported **failed** because the watcher read the stored run state without asking the executor (it stayed "connecting"); it now syncs the run like the page does. Not verified live: a degraded or failed outcome (those paths are unit-tested only), the outcome of a *Switch primary* (unit-tested; the live switch was only checked for the role change), single-aircraft continuity (Part 5 was completed, so the single-aircraft version was not built).
