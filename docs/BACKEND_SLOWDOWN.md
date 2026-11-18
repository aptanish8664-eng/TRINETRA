# Backend slowdown while Mission Control is open (Job 1)

Software-only. Measured with a headless Edge (software rendering, so browser CPU is overstated; the numbers below are the **backend** process and the bytes on the wire) against the throwaway backend with **6 aircraft running and idle on the ground**, page open for 40 s. Raw results: `D:\TRINETRA_scratch\probe\net_*.json` (not in Git).

## 0. Why Mission Control showed only one of the five aircraft (found, fixed)

Reproduced on this machine. Two causes, both real:

1. **A stale Gazebo pose bridge.** The pose bridge process had been running since the morning; Gazebo was restarted later, and the old bridge kept a dead subscription, so no pose ever arrived (`gazebo: disconnected`). With 6 PX4 running and telemetry arriving, **0 of 6 aircraft counted as verified** (a verified aircraft needs telemetry *and* a pose), so Activate failed with "0 of 6" and the page showed aircraft that were "not connected". Fix: the fleet manager now compares process start times (`/proc/<pid>/stat`) and replaces a pose bridge that started before the current Gazebo server (`ensure_bridge(..., must_start_after=...)`). Two unit tests. Live: Activate and verify for 6 aircraft went from "0 of 6" to 200.
2. **A restart of a smaller mission stops the other aircraft.** The log shows instances 1-4 exited at the same second and instance 0 restarted: a restart starts exactly the deployed mission's aircraft (here 1), so the four others were stopped, by design. The old backend kept them listed as "not connected" ghosts (fixed in Part 5 by dropping aircraft of the earlier simulation). The restart confirmation now says it: "This mission has 1 aircraft and 5 are running now. The restart starts exactly 1: the other 4 running aircraft are stopped and disappear from Mission Control." (frontend test).

Also seen: two backends (the user's :8000 and a throwaway :8003) can fight over the same bridges; each Activate replaces a bridge that sends to the other one's port. Do not run two backends against one simulator.

## 1. What the page polled (6 aircraft, idle, before)

| Endpoint | Per minute | Size each | Time each | KB/s |
|---|---|---|---|---|
| GET /api/telemetry/gazebo | 22.5 | 1939 KB | 1099 ms | 727 |
| GET /api/telemetry/px4 | 4.5 | 1956 KB | 1661 ms | 147 |
| GET /api/scenarios | 6 | 1197 KB | 3270 ms | 120 |
| GET /api/research/live | 46.5 | 5.5 KB | 292 ms | 4.2 |
| websocket streams (px4 + gazebo) | about 12.5 frames/s | 10 KB | - | 128 |

Total over HTTP **1002 KB/s**; backend CPU **0.9 cores**. Nothing was compressed. The 2 MB were the 3000-point flight trails of every aircraft, sent again on every poll although the page already receives trail deltas over the websocket.

## 2. Fixes (the cause, not longer timeouts)

* `GET /api/telemetry/px4` and `/gazebo` accept `trails=false`; the page's polls (and the Quick Mission / Scenario Lab polls) use it; only the first full frame carries trails.
* GZip for every JSON response of 1 KB or more (level 3).
* Telemetry and pose websocket streams send 4 times a second instead of 10: the stream builds its snapshot on the same event loop that receives the UDP telemetry, so 10 per second competed with ingestion.
* Fleet/telemetry snapshots used by Land here, the options and the pre-start checks no longer build trails (Part 4).
* The restart waits that I had lengthened (verify 180 s, stall 180 s, job stuck 240 s) went back down to 120 s, 120 s and 150 s: with the pose-bridge fix a browser-driven restart of 6 aircraft no longer needs them.

## 3. After (6 aircraft, idle, same method)

| Endpoint | Per minute | Size each | Time each |
|---|---|---|---|
| GET /api/telemetry/gazebo | 27 | 0.9 KB | 135 ms |
| GET /api/telemetry/px4 | 7.5 | 12 KB | 59 ms |
| GET /api/scenarios | 3 | 84.6 KB (gzip) | 986 ms |
| GET /api/research/live | 58.5 | 1.4 KB | 45 ms |

Total over HTTP **6.9 KB/s** (from 1002); websocket 4.9 MB in 40 s (from 5.1 MB, at a quarter of the frames per stream); backend CPU **0.73 cores**; with **no page open** the backend uses **0.47 cores**, so the page itself now costs about 0.26 cores and the rest is receiving UDP telemetry from 6 aircraft. During a 6-aircraft flight (measured 25 s after the start): 0.87 cores, 9.7 KB/s.

## 4. Browser-driven restart of 6 aircraft, three runs

| Run | Result | Time |
|---|---|---|
| 1 | all 6 READY, all steps DONE | 74 s |
| 2 | all 6 READY, all steps DONE | 71 s |
| 3 | all 6 READY, all steps DONE | 73 s |

(Before the pose-bridge fix an API-driven restart of 6 aircraft failed after 724 s with "4 of 6 ready".)

## 5. Not done / not measured (honest)

* The table was measured with 6 aircraft only; the 1- and 3-aircraft rows were not measured, so the claim that cost no longer grows much with the number of aircraft is **not demonstrated** (the trails, the big part, are gone, so it should be close to proportional to the small per-aircraft summary).
* No per-aircraft "summary versus detail" endpoint and no server-sent-events stream were built: the websocket streams already exist and are kept; polling remains the fallback.
* `GET /api/scenarios` is still about 85 KB gzipped (1.3 MB raw, 74 scenarios with their full latest version) and takes about 1 s; it is requested every few seconds while Mission Control is open and I did not find which component asks for it. It should return a summary (title, ids, counts) and let the detail endpoint serve the rest.
* One backend worker is kept on purpose: the telemetry store, the live threat state, the experiment runs and the continuity watcher live in process memory, so several workers would each see a part of the UDP stream and lose runs. Moving the UDP receiver into its own thread was not done.
* The page already pauses its polls when the tab is hidden; polls are not paused during a restart.
