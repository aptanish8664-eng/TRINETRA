# Fleet start-up reliability and capacity (Part F)

Status words: **measured** = a real run on this machine (numbers from saved probe files in the run's scratch folder, not in Git);
**projection** = arithmetic from measured numbers, **not tested**; **proposed** = not built.

Machine: Windows 11 host, Ubuntu in WSL2 (disk on `D:\wsl\Ubuntu`), PX4 SITL x500 + one headless Gazebo server. Free memory in WSL stayed near 6.7 GiB and swap was never used in any run.

## 1. Why starting several aircraft was unreliable

| Evidence (measured) | Meaning |
|---|---|
| Under host CPU pressure (a CPU burner on the Windows side) the Gazebo real-time factor (RTF) fell from 1.0 to 0.41–0.47; the VM then gets about half of its CPUs. | The simulator itself was not "broken": it was starved. |
| The old start launched Gazebo (with its GUI), the bridges and **all** PX4 instances at once and then waited a **fixed 90 s**. With 5 aircraft and the burner at 8 threads: **0 of 2 runs** reached "ready". | The fixed timer expired while everything was still booting. |
| Same load with the staged start: **1 of 1 run ready after 196 s** and another after **298 s** (RTF about 0.5). | Waiting for progress instead of a clock makes a slow machine slow, not failed. |
| With no burner the old start needed 18–24 s (5 aircraft) and the staged start 38–43 s (batches of 2). | The staged start is slower on an idle machine (about 20 s) and reliable on a busy one. |
| Aircraft 6 and above never delivered telemetry in the first 6-aircraft runs (0 of 3). The telemetry bridge only accepted PX4 source ports 18570–18574. | A silent allowlist bug. Now 18570–18585, and an older bridge is replaced automatically. After the fix, 6 aircraft: 3 of 3. |

What changed (implemented, tested):

1. **Stop until the processes are really gone** (not for a fixed time).
2. **Headless Gazebo server first**, and wait until the world statistics topic answers.
3. Bridges, then **PX4 in batches of 2** (`TRINETRA_FLEET_START_BATCH`). A batch is waited for until each aircraft shows the startup-script line **and** a valid `GLOBAL_POSITION_INT` on its own onboard MAVLink link.
4. **Progress-based waiting**: an aircraft fails only after 45 s without any change, or 180 s in total for that aircraft. Gazebo gets up to 100 s.
5. A progress file (atomic JSON, one writer under `flock`) is written by a detached supervisor in WSL; the backend job polls it every second and shows one row per aircraft (pending, booting, sensors, ready, failed).
6. **Partial result**: if some aircraft are ready and some are not, the job finishes as `partial` ("Only 2 of 3 aircraft are ready (px4-1, px4-2); px4-3 did not start.") with two choices: *Retry the missing ones* (starts only the missing instances, does not stop the others) and *Continue with the ready aircraft*. Steps that could not run say NOT RUN.
7. The backend verification window is 180 s and counts "signs of life"; a job is only called stuck after 240 s without any change.

## 2. Capacity table (measured, 3 runs per size, idle host, staged start)

| Aircraft | Runs ready | Ready time s (min / avg / max) | Gazebo RTF (min / avg) | Peak load1 | Peak CPU % | Lowest free memory MiB | WSL swap |
|---|---|---|---|---|---|---|---|
| 1 | 3/3 | 16.2 / 16.8 / 17.5 | 1.00 / 1.00 | 7.7 | 38 | 6876 | 0 |
| 2 | 3/3 | 17.0 / 17.2 / 17.4 | 1.00 / 1.00 | 2.9 | 45 | 6820 | 0 |
| 3 | 3/3 | 29.6 / 30.5 / 31.2 | 1.00 / 1.00 | 4.9 | 48 | 6805 | 0 |
| 4 | 3/3 | 31.1 / 32.5 / 34.6 | 0.96 / 0.99 | 6.6 | 65 | 6771 | 0 |
| 5 | 3/3 | 44.9 / 45.1 / 45.4 | 0.99 / 1.00 | 9.2 | 61 | 6760 | 0 |
| 6 (after the bridge-port fix) | 3/3 | 46.9 / 47.9 / 48.7 | 0.99 / 0.99 | 13.0 | 71 | 6678 | 0 |
| 7 | 1/3 | 80.6 (only success) | 0.84 / 0.91 | 14.9 | 72 | 6647 | 0 |

(The first 6-aircraft series, before the port fix, was 0/3; 7 aircraft was 1/3, so 7 is **not** reliable.)

**Decision:** the measured reliable size is **6**. `TRINETRA_SIM_MAX_AIRCRAFT` defaults to 6 (`SIM_MAX_AIRCRAFT_DEFAULT` in `backend/app/config.py`). Only activating, starting and deploying-with-aircraft are limited by it; a mission plan can have any number of aircraft and can still be deployed as a plan only.

Per aircraft (measured, between 1 and 6): about 5–9 s more start time, about 35–40 MiB more PX4 memory, about 2–3 more load units. Memory is not the limit here; CPU time is.

Flight-speed check (measured, 1 vs 6 aircraft flying the same route): 282 s vs 314 s, so 6 aircraft fly about 11 % slower in wall-clock time.

## 3. The browser and backend are a second bottleneck (measured, not fixed)

With Mission Control open, API calls took 2–8 s and each telemetry poll was about 2 MB (3000-point trails per aircraft). With the page closed the same endpoint answered in about 200 ms. UI-driven restarts therefore need long waits (hence the 180 s verification window with signs of life). Reducing trail length in the polled payload is the obvious fix; **not done** in this part.

One 1440x900 browser run (3 aircraft, partial scenario) ended with "0 of 3 telemetry" although two aircraft were ready, while a repeat of the same run succeeded and telemetry answered in 200 ms right afterwards. I believe this is the same backend saturation; I did **not** prove it. *Retry* recovers it.

## 4. Browser checks of the dialog (real clicks, Playwright/msedge)

| Check | 1914x826 | 1440x900 |
|---|---|---|
| Slow start, 3 aircraft: live "n of 3 ready", per-aircraft rows, nothing shown as failed, all DONE at the end | passed (~99 s) | passed (~72 s) |
| Partial result: one PX4 killed while booting, names ready/missing aircraft, both buttons, *Retry the missing ones* ends with 3 of 3 ready | passed | passed on the second attempt (first attempt hit the saturation failure above) |
| Dialog fits the viewport | yes | yes |

At 1440x900 the ready aircraft were verified, so the later steps showed DONE instead of NOT RUN; at 1914x826 the unverified ones showed NOT RUN. Both are honest views of what actually ran.

Screenshots: `docs/screenshots/pf-slow-*`, `pf-partial-*`.

## 5. Study: 12 aircraft (projection, not tested)

Extrapolating the measured trend (load1 about +2–3 per aircraft, RTF 1.0 at 6 and 0.91 at 7, start time about +8 s per aircraft, all on an idle host):

* 12 aircraft would need roughly 100–130 s to start and run Gazebo at an RTF clearly below 1 (my estimate 0.4–0.6), so every flight would be 2x slower or worse; MAVLink timing and the detectors would see different timing than at 1.0. Memory (the PX4 processes used about 450 MiB in total at 6 aircraft) would still fit, so the limit is CPU.
* No run above 7 was made.

| Option | What it is | Effort | Trade-off |
|---|---|---|---|
| A. Give WSL more CPUs | Change `.wslconfig` (not touched here) | minutes, needs the user | Helps all sizes; the host also needs the CPUs |
| B. Slower, honest time | Run 12 at RTF 0.5 and say so | none | Flights take twice as long; timing-based results not comparable |
| C. Lightweight aircraft (no camera, simplified sensors, lower rate) | Fewer Gazebo sensors per aircraft | days; changes what is simulated | Cheaper, but a different vehicle model than the 1–6 aircraft case |
| D. Two simulation hosts | Split fleet over two WSL instances/machines | weeks | Real scale, needs sync and networking |
| E. Cut the UI payload | Short trails in the poll, delta updates | 1–2 days | Fixes the page-open slowdown regardless of fleet size |

**Recommendation:** keep 6 as the reliable default; do E first (it helps every size and is small), then A if 8–10 aircraft are wanted on this machine. Do not build C until a study shows A and E are not enough. **No lightweight mode has been built.**

## 6. Not verified

* Sizes 8+ (never run). The 12-aircraft numbers are projections.
* Behaviour on other machines with fewer cores.
* Whether the intermittent "0 of 3 telemetry" case is only backend saturation.

## 7. Run 19 update

* The start guard uses `TRINETRA_SIM_MAX_AIRCRAFT` (default 6) and the free memory of the host (`GET /api/fleet/capacity`). Planning is not limited.
* With Mission Control open in a browser the host collapsed (free Windows memory 119-346 MiB, API answers 3-76 s) before the fixes. Causes found: deep copies of large Gazebo snapshots (44.6 % of backend CPU), per-byte MAVLink parsing, and a WSL call per preflight. Fixes: snapshots without trails (240.5 ms to 0.17 ms, 1.1 MB to 1.4 KB), receiver thread with a bounded queue, cached fleet status (4 s), per-aircraft summary endpoint, polling paused for hidden tabs.
* Three cold starts of the same 5-aircraft deployment: all connected at 37.7, 37.2 and 38.2 s with fresh telemetry.
* Not done: a clean "after" table of host CPU/memory with the browser open (the machine had about 3.6 GB free at idle), a 3-aircraft comparison, and lazy standby start (not justified by the numbers: the 2 standby aircraft cost about 45 % of a core in total).
