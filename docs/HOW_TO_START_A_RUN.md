# How to start a run (software-only simulator)

Everything here runs on your own computer, inside PX4 SITL and Gazebo (in WSL). Nothing is sent to a real aircraft.

## 0. Check the setup in one command

```
cd D:\TRINETRA
.venv\Scripts\python.exe backend\tools\trinetra_doctor.py
```

It prints a green/red list. Every red or yellow line has the exact fix under it. It is read-only, it never reads `backend/.env`, and it exits with 0 when nothing is red. Add `--start` to let it start the backend and the frontend for you (two fixed local commands; their logs go to `exports\doctor_logs`). Use `--api` and `--web` when your servers run on other ports (for example `--api http://127.0.0.1:8003 --web http://127.0.0.1:5175`), and `--no-wsl` to skip the WSL checks.

What it checks: free space on C: (below 1.5 GB do not start a simulator run) and D:, the virtual environment and Python packages, the frontend dependencies, whether WSL answers and the PX4 build and tools are there, whether PX4, Gazebo and the two bridges run (and which UDP port the PX4 bridge sends to), whether the backend is healthy, whether telemetry and Gazebo poses arrive at the backend, whether the threat model file exists, whether the web page answers.

## 1. Start a flight

1. Open the web page and sign in (General accounts start directly; Operator accounts need a General's approval of the exact version).
2. Plan a mission (Quick Mission or Scenario Lab) with any number of aircraft. A saved mission can be opened from the top of Quick Mission.
3. In Scenario Lab press **Activate and verify** (starts the aircraft in small groups and waits for each one) and then **Deploy**. A plan with more aircraft than this machine runs reliably (measured: 6, see `docs/FLEET_CAPACITY.md`) can still be deployed as a plan only.
4. In Mission Control press **Restart at mission start** if the aircraft is not at the mission start (the pre-start list says so). It works from any state, from any distance, and keeps the last home.
5. Press **Start in simulation** and confirm. Every flying aircraft (primary and decoys) starts; standby aircraft stay on the ground. The Aircraft runs block gives each aircraft its own Pause, Resume, Return and Land here.
6. Watch the GPS threat assessment. High and Critical show a visible alert (and an optional tone). Choose a response, or leave it.

## 2. If something is red

| Symptom | Likely cause | Fix |
|---|---|---|
| Doctor: backend does not answer | backend not started | `cd backend ; ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000` or run the doctor with `--start` |
| Doctor: WSL does not answer | WSL is busy or stuck | `wsl -d Ubuntu` in a terminal; if it hangs `wsl --shutdown` and retry. Do not edit `.wslconfig` without asking |
| PX4 running but telemetry does not arrive | the WSL bridge sends to a different UDP port than the backend listens on | press **Activate and verify** again: it replaces a mismatching bridge |
| Start button disabled | one of the pre-start checks is red | the sentence beside the button names it (for example "The aircraft is 0.1 km from the mission start. Use Restart at mission start.") |
| Only some aircraft came up after a restart | CPU is busy | the dialog says which aircraft are ready: **Retry the missing ones** or **Continue with the ready aircraft** |
| Mission Control is slow with several aircraft | the page polls large telemetry payloads | known limit, see `docs/FLEET_CAPACITY.md` section 3 |

## 3. What is real and what is not

Every number on screen comes from the backend (REST/WebSocket) or from PX4/Gazebo in the simulator. Simulated GPS faults from the Attack Lab are applied to the threat monitor's own copy of the GPS data only. Nothing here is real flight evidence.
