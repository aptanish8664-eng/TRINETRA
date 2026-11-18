# Future realism study (Part 9): study only, nothing here is implemented

Status: **proposed / not built / not tested.** This document estimates feasibility, the safest approach, the effort and the claim each step would allow. It is software-only throughout: every step stays inside PX4 SITL and Gazebo on this machine. The PX4 facts below are from my knowledge of PX4 and were **not re-verified against the installed PX4 build in this run**; the first task of either step would be to check them there.

## What exists today (so the gap is clear)

* "Link lost" in TRINETRA is an **observation at the backend**: the telemetry store sees no datagrams for a while (`stale` after 3 s, `disconnected` after 10 s). PX4 itself is never told its data link is gone, so PX4's own failsafe does not run.
* GPS faults are applied to the **threat monitor's software copy** of the GPS stream (Attack Lab, Part 3). PX4's EKF and the aircraft never see them; the aircraft keeps flying on its true position, and the estimator velocity used as the inertial reference stays truthful (loophole 4 and 5 in `DETECTOR_LOOPHOLES.md`).

## 1. Real data-link loss

**Idea.** Let PX4 really lose its ground-control link in simulation so its own data-link-loss failsafe fires, and compare PX4's reaction with what TRINETRA reports.

**How PX4 behaves (to verify).** PX4 raises a data-link-loss failsafe when the GCS heartbeat is missing for `COM_DL_LOSS_T` seconds after a GCS was connected, and then does what `NAV_DLL_ACT` says (for example Hold or Return). The failsafe state is visible in the heartbeat custom mode and in STATUSTEXT messages.

**Safest approach.**
1. Add one **fixed, validated action** to the existing WSL fleet manager (`gcs_blackout`, instance list, duration 1-60 s): the UDP bridge for the GCS port drops its forwarding in both directions for that time and then resumes. The onboard executor link (a different port) stays up so TRINETRA can still observe and abort.
2. No shell access through the API, no arbitrary ports or durations: the same pattern as `start`/`progress` in Part F.
3. Show it in Mission Control as an explicit, confirmed "Simulate a ground-link loss (20 s)" action for a flying aircraft, audited like the other responses.
4. Record PX4's own failsafe state next to TRINETRA's "link lost" so both can be compared in the run record.

**Effort.** About 2 to 4 days: bridge change and its tests (1 day), manager action and API (1 day), UI and run record (1 day), live verification with real flights (1 day).

**Risks.** The executor and the backend share some assumptions about heartbeats; the simulated home must not move; a failsafe "Return" ends the run, which the continuity watcher must report correctly.

**Claim it would allow.** "In simulation, when the GCS link is cut for N seconds, PX4's failsafe reacted as configured and TRINETRA's link-lost reporting agreed with it (within X seconds)." It would **not** allow any statement about real radio links, range, interference or recovery of real hardware.

## 2. Spoofing injected into the simulator and the estimator

**Idea.** Corrupt the GPS **inside PX4's navigation input** so the EKF really follows it. Then the aircraft's true path changes, the estimator velocity is no longer an independent reference, and the detector is tested against the case it is actually meant for (loophole 4 and 5).

**Options (cheapest and safest first).**

| Option | What it is | Fidelity | Effort | Main risk |
|---|---|---|---|---|
| A. PX4's own failure injection | `failure gps ...` (needs `SYS_FAILURE_EN=1`, SITL only): switch the GPS off, stuck, garbage, wrong, slow, delayed, intermittent | real EKF reaction, but only a few fault shapes; no consistent offset or drift | about 2 days | does not cover the Attack Lab types (offset, drift, jump); useful as a first check of the idea |
| B. Modify the GPS between Gazebo and PX4 | in the PX4 `gz_bridge` (or a small Gazebo-transport relay on the NavSat topic) apply the shared `attacks.inject` offset/drift/jump/noise before the data becomes `sensor_gps` | the EKF follows the spoofed GPS exactly like a real spoof would drive it in an ideal receiver model; all six Attack Lab types | about 1 to 2 weeks including validation | patching PX4's source or topic remapping can break the build; the schedule must be applied by wall-clock or simulator time consistently |
| C. Spoof the MAVLink HIL GPS message | feed a hardware-in-the-loop style message from a proxy | simple but PX4 is then no longer running its Gazebo-GPS path | about 1 week | different code path from the one the missions use today |

**Safest approach.** Option B behind a fixed configuration: the attack comes from the same validated schema as the Attack Lab (types, ranges, hidden schedule), set only through the existing experiment runner, never through a shell or free parameters. Keep the aircraft inside a simulator geofence (a spoofed aircraft really flies off course). Keep ground truth out of detector inputs exactly as today.

**What changes for the detector.** The estimator velocity would follow the spoof, so the residual shrinks for slow drift and the detector must rely on other evidence (innovation test ratios, GPS-vs-IMU consistency from independent sensors). The training data and the evaluation must be regenerated with this behaviour (Part 7 steps 2-4) or the numbers will not carry over.

**Effort and order.** A first (2 days, to prove that the EKF reacts), then B (1 to 2 weeks), then the Part 7 retraining and a fresh before/after validation (a separate run). Total about 3 to 4 weeks of work for B plus the retraining.

**Claim it would allow.** "Against a simulated GPS fault that really enters PX4's EKF in SITL, the detector's false-alarm rate and detection latency were X and Y with a stated confidence bound, and the mission impact (true path deviation) was Z." It would **not** allow claims about real receivers, real RF spoofing, multi-constellation behaviour, real attacker adaptation or real flight hardware; the error model is still the project's own.

## Recommendation

1. Do Part 7 steps 2-4 first (data and features), because both options change what the detector sees.
2. Do option A as a two-day check, then the data-link-loss action (cheap, independent).
3. Do option B only after the retraining plan is agreed, and keep the "monitor-copy" injection as the fast mode so older results stay comparable.
