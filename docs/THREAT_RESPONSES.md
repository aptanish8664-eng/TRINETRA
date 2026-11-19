# GPS Threat Assessment: response options (Part 4)

Software-only. Every action below acts on the **simulated** aircraft (PX4 SITL) through the same code as the matching Mission Control button. Nothing is sent to a real aircraft. The detector and the ranking in `threat_engine.py` were not changed.

## 1. Audit before the change (what the old buttons did)

| Button (ranked by the threat engine) | Intended action | Wired to a command? | Worked live? |
|---|---|---|---|
| Continue and monitor | accept the risk, carry on | No: `POST /api/research/decisions` only stored a row ("recorded, not executed") | The record was saved; no effect on the aircraft |
| Increase monitoring | raise the operator's attention | No: record only | Record only |
| Switch to dead-reckoning fallback | stop using GPS | No: record only (PX4 SITL has no such switch wired) | Record only |
| Isolate aircraft | stop trusting the aircraft / hold it | No: record only | Record only |
| Hand over to standby | give the task to another aircraft | No: record only (server checked the candidate's battery) | Record only |
| Return to launch | fly back | No: record only. The real "Abort (return to launch)" existed only as a separate Mission Control button | Record only |

(Audit made by reading `ThreatCard.tsx`, `research_routes.py` and `threat_engine.py` and by the existing tests; not re-run live before the change.)

## 2. After the change (implemented and live-verified)

`GET /api/research/responses?aircraft_id=` lists each response with `enabled` and the exact `reason` when disabled. `POST /api/research/responses` runs one (confirmation required for every command), records it with the server's own live score, tier and evidence, writes an audit event `threat_response`, and stores a research decision (`response_<action>`, source name `executed: ...`, `acknowledged: ...` or `refused: ...`). The live experiment run record lists these as operator responses.

| Response (button) | What it really does | Same code as | Live result (real PX4 SITL, browser clicks) |
|---|---|---|---|
| Acknowledge and continue | records the acknowledgement, sends no command | new (record only, by design) | passed at 1914x826 and 1440x900 (needs a live assessment: disabled while the monitor is warming up) |
| Hold (pause the mission) | pauses the mission, PX4 mode Hold | Mission Control Pause | passed at both sizes: state `paused`, mode `Hold` |
| Resume the mission | resumes the paused mission | Mission Control Resume | passed at both sizes: state `executing` again |
| Return to launch | aborts the run and flies back | Mission Control Abort (return to launch) | passed at 1914x826: state `returning`, PX4 mode `Return` |
| Land here | lands where it is and disarms | Mission Control Land here | passed at both sizes: `landing`, run ended with the aircraft on the ground |
| Restart the simulation at the mission start | opens the existing confirmation and progress dialog | Restart at mission start | passed at 1914x826: dialog opened, restart finished |
| Hand over to a standby aircraft | not available | none | disabled with the reason "Needs more than one active aircraft." (Part 5) |
| Increase monitoring, dead-reckoning fallback | removed from the live card | none | Not wired in the simulator; still ranked by the engine as a recommendation, and still offered (recorded only) on the offline replay |

The visible alert now appears for **High and Critical** (it was Critical only), with an "Alert sound" switch (default on, saved in the browser, a short tone only when a new High/Critical tier appears).

## 3. Findings and limits (honest)

* While the Mission Control page is open the backend is slow; the telemetry can look stale for a moment, and Land here was refused twice with "No telemetry from this aircraft" until two changes were made: the fleet snapshot used by Land here and by the options no longer builds the 3000-point trails (`include_trails=False`), and Land here looks again for up to 3 s before refusing for stale telemetry.
* After Hold and Resume the live monitor restarts its data window and shows "collecting data" for about 10 s, so Acknowledge is disabled for a few seconds. This is existing monitor behaviour (a gap restarts the window); its cause was not investigated.
* The refusal reasons come from the same checks as the Mission Control buttons; OPERATOR accounts need an approved version for Hold/Resume (role rule), as before.
* Not tested live: a response chosen from an OPERATOR account; Return to launch at 1440x900 (the controls are the same component).

## 4. Run 19: operator control (implemented; live results in the progress file)

* **Always available:** Pause, Resume, RTL, Land here, Restart, plus **Resume on track** (shows the waypoint it rejoins, asks to confirm), **Reassign** (candidates with role, state, battery, link and distance; choose what the original does: hold, return to launch, land or continue as decoy; the new aircraft is started first and verified before the original is touched), **Switch primary** and **Keep monitoring** (acknowledgement with a reminder probability).
* **Suggestions** are buttons and are never pressed for you. Thresholds are named constants in `backend/app/research/suggestions.py`: `SUGGEST_WATCH` 0.30, `SUGGEST_ACT` 0.60, `CONFIRMED_AT` 0.85, with plain explanations.
* **Attack story:** the threat panel keeps a timeline of what was seen, suggested and done; it is stored in the run record (`attack_story`) and exported on the "Attack story" sheet.
* **Limits:** roles, the story and acknowledgements are held in backend memory and are lost on a backend restart (the run record keeps the story once written). A rise of the probability past a reminder was shown live only with a lowered reminder (5 %); a natural rise past 60 % was not caught on that run. Degraded or failed continuity outcomes were not produced live.
