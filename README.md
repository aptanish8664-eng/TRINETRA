# TRINETRA

TRINETRA is a software-only research demonstrator for cyber-resilient multi-UAV mission simulation. Its deterministic abstract simulator remains the default. The existing Mission Control route includes an optional read-only PX4 MAVLink receiver, authenticated telemetry REST/WebSocket, a CesiumJS map using actual PX4 GPS positions, and a read-only Gazebo pose mirror using actual `/world/default/pose/info` data and the PX4 X500 mesh. Three disarmed X500 SITL instances with distinct PX4 system IDs, valid GPS, battery and mission messages were observed together with Gazebo models `x500_0/1/2` in the same authenticated backend. An authenticated Mission Control browser view showed both live data panels and all three fleet/model rows, but Cesium previously failed to render its imagery because of a local asset-path error. That path has been corrected and asset responses verified; post-fix globe, marker and mesh rendering still needs visual confirmation. The native Gazebo world, lighting and video are not streamed, the vehicles have not completed a verified flight, and live PX4 threat detection/reassignment are not validated. No takeoff, waypoint flight, landing, deployment or real aircraft operation is claimed. See `TRINETRA_IMPLEMENTATION_PROGRESS.md` for evidence and blockers.

## Windows development

Use two PowerShell terminals and use one hostname consistently in the browser and API configuration. The frontend defaults its API host to the hostname used to open the page.

Backend, from `D:\TRINETRA\backend`:

```powershell
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend, from `D:\TRINETRA\frontend`:

```powershell
npm.cmd run dev
```

Open `http://127.0.0.1:5173` or `http://localhost:5173`; the frontend selects the corresponding API hostname. In development, if `VITE_API_URL` is explicitly set to the other loopback alias, the frontend normalizes it to the browser's hostname so the strict session cookie remains same-site. `GET http://127.0.0.1:8000/api/health` is a basic API readiness response, not a full service-health proof. The frontend uses a session cookie; use an existing authorized account. The configured local SQLite file already contains two account rows, so first-General bootstrap will refuse and no account credentials are documented here.

The active database is `backend\trinetra.db`. Keep it and the sensitive backup files intact. The default database location can be overridden with `TRINETRA_DB_PATH`. Use interactive secure provisioning only for an empty database; never put passwords in source, a command argument, or frontend config.

PostgreSQL is optional and configured with a server-side `DATABASE_URL`; schema changes must be applied with Alembic before backend startup. Follow `docs/DATABASE_MIGRATIONS.md`. Do not point test or migration commands at the populated local SQLite database or an unknown cloud database.

## Verification

From `D:\TRINETRA\backend`, run `..\.venv\Scripts\python.exe -m pytest -q tests`. From `D:\TRINETRA\frontend`, run `npm.cmd test` and `npm.cmd run build`. Baseline results and limitations are recorded in `TRINETRA_TEST_STRATEGY.md`.

## PX4/Gazebo

For a read-only live telemetry run, configure the backend environment before starting it (recheck the current WSL and Windows gateway addresses after WSL restarts):

```powershell
$env:TRINETRA_PX4_UDP_ENABLED = 'true'
$env:TRINETRA_PX4_UDP_BIND_HOST = '172.23.80.1'
$env:TRINETRA_PX4_UDP_BIND_PORT = '14600'
$env:TRINETRA_PX4_UDP_ALLOWED_SOURCE_IP = '172.23.85.230'
$env:TRINETRA_PX4_UDP_ALLOWED_SOURCE_PORT = '14601'
```

In WSL Ubuntu, start the one-way relay while the backend is running:

```bash
python3 /mnt/d/TRINETRA/backend/tools/px4_wsl_udp_bridge.py --windows-host 172.23.80.1
```

For the in-page Gazebo pose mirror, set a separate receiver and start its read-only WSL subscriber in another terminal:

```powershell
$env:TRINETRA_GAZEBO_POSE_UDP_ENABLED = 'true'
$env:TRINETRA_GAZEBO_POSE_UDP_BIND_HOST = '172.23.80.1'
$env:TRINETRA_GAZEBO_POSE_UDP_BIND_PORT = '14610'
$env:TRINETRA_GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP = '172.23.85.230'
$env:TRINETRA_GAZEBO_POSE_UDP_ALLOWED_SOURCE_PORT = '14611'
```

```bash
python3 /mnt/d/TRINETRA/backend/tools/gazebo_wsl_pose_bridge.py --windows-host 172.23.80.1 --forward-port 14610 --source-port 14611 --rate 10
```

PX4's existing loopback GCS route remains unchanged. For three SITL instances, start the first with the existing documented `make px4_sitl gz_x500`; in separate WSL terminals from `/home/apt8055/uav-sim/PX4-Autopilot`, attach instances 1 and 2 to the running Gazebo world:

```bash
PX4_GZ_STANDALONE=1 PX4_SYS_AUTOSTART=4001 PX4_GZ_MODEL_POSE="5,0,0,0,0,0" PX4_SIM_MODEL=gz_x500 ./build/px4_sitl_default/bin/px4 -i 1
PX4_GZ_STANDALONE=1 PX4_SYS_AUTOSTART=4001 PX4_GZ_MODEL_POSE="-5,0,0,0,0,0" PX4_SIM_MODEL=gz_x500 ./build/px4_sitl_default/bin/px4 -i 2
```

The 2026-11-08 PX4 check reached three distinct systems through authenticated REST and WebSocket; all were disarmed with valid GPS positions. Their movement in flight is unverified. A separate read-only Gazebo bridge smoke forwarded over 2,700 snapshots and `/api/health` reported three X500 models. The in-page Gazebo pose mirror and Cesium map now share the existing Mission Control route in source, but a single authenticated browser run combining both paths is pending. The mirror is a pose visualization, not a native Gazebo world/video stream. MAVSDK Server and QGroundControl are not runtime dependencies. Threat detection and adaptive reassignment remain abstract-simulator capabilities, not PX4-validated functions. See [PX4 setup](docs/PX4_SETUP.md) and the progress checkpoint for exact evidence.

## Research dataset generation

The backend can export a versioned, seed-split dataset and train/evaluate a scikit-learn classifier from its deterministic abstract simulator. Current dataset v2 covers nine software fault profiles and has a six-feature reported-GPS/velocity contract. The validated JSON model can be enabled with `TRINETRA_ML_MODEL_PATH`; the default detector remains heuristic. Mission Control displays model probability, severity and explicitly uncalibrated confidence only when a model is configured. See `docs/RESEARCH_METHODS.md` for artifact IDs, commands, held-out metrics and limits. These outputs and resource constraints are abstract-only, not PX4/field evidence. Simulated reassignment checks workload, payload, capability, communication availability, and estimated battery/endurance reserve; no physical suitability is implied.

## Documentation

- `TRINETRA_COMPLETE_ARCHITECTURE.md`: inspected implementation and target boundaries.
- `TRINETRA_IMPLEMENTATION_ROADMAP.md`: phased scope/status.
- `TRINETRA_API_CONTRACT.md`: current route inventory.
- `TRINETRA_SIMULATION_SPECIFICATION.md`: current abstract simulator and target SITL contract.
- `TRINETRA_DATA_DICTIONARY.md`: current tables/state and proposed sensor envelope.
- `TRINETRA_TEST_STRATEGY.md`: commands, results, and gaps.
- `TRINETRA_RISK_REGISTER.md`: current risks and mitigations.
- `TRINETRA_IMPLEMENTATION_PROGRESS.md`: continuation checkpoint updated at each milestone.
- `docs/DATABASE_MIGRATIONS.md`: SQLite default, PostgreSQL environment configuration, Alembic commands, and validation limits.
- `docs/DEPLOYMENT.md`: Netlify and FastAPI container preparation, same-site cookie requirements, host environment, and unverified deployment gates.
