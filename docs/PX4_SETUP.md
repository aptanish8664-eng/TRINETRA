# PX4 SITL and Gazebo Setup (Phase 1)

## Verified local environment

Checked 2026-11-08: Windows 11 Pro, WSL2 Ubuntu 24.04.5 LTS x86_64. Direct read-only probe found CMake 3.28.3, Ninja 1.11.1, and Gazebo Sim 8.15.0. Owner-provided output reports clean PX4 commit `14a7aec`, successful PX4 SITL launch, and Gazebo displaying `x500_0`; `commander status` was Disarmed/Hold/no failsafe, but preflight failed due to no GCS. QGroundControl is now running with UDP AutoConnect and listening on UDP 14550; a vehicle connection is still unverified. No arm/takeoff/hover/waypoint/landing/shutdown flight was completed. MAVSDK Server is not installed/in PATH. Verified PX4 checkout: `/home/apt8055/uav-sim/PX4-Autopilot`, commit `14a7aec`, clean tree; built SITL binary exists. Do not clone or reinstall PX4/Gazebo. Prior resource inventory (8 logical CPUs, 7.5 GiB WSL memory, 953 GiB free, Git 2.43.0, Python 3.12.3) was recorded 2026-11-07 and not remeasured. GPU capability remains unverified.

## Selected compatible versions

Use PX4 v1.17.0 stable with the modern Gazebo (Gazebo Harmonic) on Ubuntu 24.04. PX4 v1.17's official docs support Ubuntu 24.04 and list Gazebo Harmonic (plus newer Gazebo releases) for it. Use modern `gz_*` targets; do not combine Gazebo Classic models or packages. The official X500 target is `make px4_sitl gz_x500`.

Sources: [PX4 v1.17 Ubuntu setup](https://docs.px4.io/v1.17/en/dev_setup/dev_env_linux_ubuntu), [PX4 v1.17 Gazebo setup](https://docs.px4.io/v1.17/en/sim_gazebo_gz/index), [PX4 X500 vehicles](https://docs.px4.io/v1.17/en/sim_gazebo_gz/vehicles), and [PX4 v1.17.0 stable release](https://github.com/PX4/PX4-Autopilot/releases/tag/v1.17.0).

## User-run installation command (requires package-manager privileges)

No privileged command was run. From PowerShell, first open the verified Ubuntu distribution:

```powershell
wsl.exe -d Ubuntu
```

Then in the Ubuntu shell, clone the pinned official source with submodules and run the PX4-supported installer:

```bash
cd ~
git clone --branch v1.17.0 --recursive https://github.com/PX4/PX4-Autopilot.git
cd ~/PX4-Autopilot
bash ./Tools/setup/ubuntu.sh
```

The official script installs dependencies, including Gazebo Harmonic and the PX4 build toolchain; it may invoke `sudo`/APT and prompt for confirmation. Review prompts before accepting them. The official guide says to restart after setup. The script is designed for a clean supported Ubuntu LTS; do not use a downloaded standalone installer from an unverified mirror. The clone is the external PX4 dependency, not the TRINETRA project workspace.

## Verify versions and first single-X500 build

After installation/restart, in Ubuntu:

```bash
cd ~/PX4-Autopilot
git describe --tags --always --dirty
cmake --version
ninja --version
gz sim --versions
python3 --version
make px4_sitl gz_x500
```

First validation must observe Gazebo, PX4 SITL, X500 spawn, connection, arm, takeoff, hover, a safe simulated waypoint mission, land, and clean shutdown. Record actual version output, build logs, sequence/telemetry, CPU/RAM, and failures. The command above starts a simulator but does not by itself prove those flight acceptance steps.

For headless resource-limited checks, the official PX4 guide documents:

```bash
HEADLESS=1 make px4_sitl gz_x500
```

Stop with Ctrl+C in the simulation terminal and verify PX4/Gazebo processes exit. No API endpoint may expose arbitrary shell execution.

## Status and blockers

Phase 1 setup/build is verified at the existing checkout; current SITL processes are active. Preserve the setup; do not rerun the PX4 installer or reinstall Gazebo. Phase 2 remains incomplete: no arm/takeoff/hover/waypoint/landing/shutdown flight is verified. QGroundControl is optional for read-only TRINETRA telemetry. The owner explicitly directed that PX4 configuration, startup, MAVLink links, network, firewall, and running simulator remain unchanged. Do not apply the proposed GCS unicast route or restart PX4. Direct read-only MAVLink telemetry is available through the TRINETRA WSL relay, and the installed Gazebo pose topic is bridged separately. Install/use MAVSDK only if later authorized and the flight gate is completed.

All attack work remains software-only. Do not attach a physical flight controller, real vehicle, SDR, or GPS transmitter.

## Read-only MAVLink integration and three-instance verification (2026-11-08)

TRINETRA now includes a `pymavlink` decoder/store, an authenticated FastAPI REST endpoint and WebSocket stream, and a source-filtered one-way WSL UDP relay. The React PX4 view is inside the existing Mission Control route, and its Cesium map uses actual valid PX4 GPS/MAVLink state. No PX4 startup file, parameter, GCS route, firewall, or network configuration was changed.

The observed runtime configuration was:

- PX4 checkout: `/home/apt8055/uav-sim/PX4-Autopilot`, commit `14a7aec`.
- WSL address: `172.23.85.230`; Windows WSL gateway/host candidate: `172.23.80.1` (recheck both after a WSL restart).
- Backend UDP receiver: `172.23.80.1:14600`, accepting only `172.23.85.230:14601`.
- WSL relay: listens on loopback `127.0.0.1:14550`, accepts PX4 GCS source ports `18570` through `18574`, and forwards only to the configured Windows receiver.
- PX4/Gazebo: the existing `x500_0` plus PX4 instances 1 and 2 spawned `x500_1` and `x500_2` in the same running world, using PX4's `PX4_GZ_STANDALONE=1`, unique `-i` values, `PX4_SYS_AUTOSTART=4001`, `PX4_SIM_MODEL=gz_x500`, and distinct `PX4_GZ_MODEL_POSE` values. No rebuild or restart of instance 0 was performed.

An isolated backend smoke database, `backend/px4-live-smoke-20261108.sqlite3`, was used; `backend/trinetra.db` was not opened for writes. The live authenticated REST request returned systems `px4-1`, `px4-2`, and `px4-3`. All three reported `connected`, disarmed, valid position, GPS fix type 3, and LOITER mode. An authenticated WebSocket frame also carried all three IDs. This proves three independent telemetry identities and valid positions, but not vehicle movement in flight, QGroundControl connectivity, arming, waypoint execution, or landing.

### Starting the read-only integration

In PowerShell, verify the current WSL host/IP and set these environment values before starting FastAPI:

```powershell
$env:TRINETRA_PX4_UDP_ENABLED = 'true'
$env:TRINETRA_PX4_UDP_BIND_HOST = '172.23.80.1'
$env:TRINETRA_PX4_UDP_BIND_PORT = '14600'
$env:TRINETRA_PX4_UDP_ALLOWED_SOURCE_IP = '172.23.85.230'
$env:TRINETRA_PX4_UDP_ALLOWED_SOURCE_PORT = '14601'
Set-Location D:\TRINETRA\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a separate PowerShell terminal:

```powershell
wsl.exe -d Ubuntu -- bash -lc 'python3 /mnt/d/TRINETRA/backend/tools/px4_wsl_udp_bridge.py --windows-host 172.23.80.1'
```

For a temporary parallel TRINETRA backend, the read-only relay can mirror each accepted MAVLink datagram to a second Windows UDP listener while keeping the primary `14600` destination:

```bash
python3 /mnt/d/TRINETRA/backend/tools/px4_wsl_udp_bridge.py --windows-host 172.23.80.1 --forward-port 14600 --additional-forward-port 14620
```

This option changes only the relay's outbound destinations. The PX4 source, existing primary destination, MAVLink links, and simulator configuration remain unchanged. Use it only when the second listener is intentionally configured for the same WSL source IP and UDP source port; do not open or broaden firewall rules for it.

In another terminal, launch PX4/Gazebo using the existing session or the documented single-X500 command. To attach two additional disarmed X500 instances to that running Gazebo world, use separate WSL terminals:

```bash
cd /home/apt8055/uav-sim/PX4-Autopilot
PX4_GZ_STANDALONE=1 PX4_SYS_AUTOSTART=4001 PX4_GZ_MODEL_POSE="5,0,0,0,0,0" PX4_SIM_MODEL=gz_x500 ./build/px4_sitl_default/bin/px4 -i 1
```

```bash
cd /home/apt8055/uav-sim/PX4-Autopilot
PX4_GZ_STANDALONE=1 PX4_SYS_AUTOSTART=4001 PX4_GZ_MODEL_POSE="-5,0,0,0,0,0" PX4_SIM_MODEL=gz_x500 ./build/px4_sitl_default/bin/px4 -i 2
```

These commands start independent SITL processes; they do not arm or command flight. The base instance uses MAVLink localhost and must continue sending to the WSL relay on UDP 14550. The relay accepts the documented per-instance local ports while forwarding only outbound telemetry.

### Remaining live-integration gates

- **Gazebo render inside Mission Control: blocked.** Gazebo Web Visualization requires a websocket server system and a browser renderer. The installed Gazebo Sim 8.15 package has no WebSocketServer plugin, and no websocket scene renderer is configured. The Cesium map is a separate MAVLink geospatial view; it is not native Gazebo rendering or a Gazebo scene mirror. Do not call this acceptance criterion complete.
- **PX4 threat detection and adaptive reassignment: not implemented/validated.** The current threat and reassignment code uses the abstract research simulator. No spoof score is inferred from PX4 packets, and PX4 missions are not reassigned by TRINETRA.
- **Flight gate: incomplete.** The three current instances are disarmed. No arm, takeoff, hover, waypoint, landing, or clean-shutdown sequence is verified. MAVSDK Server is absent and no MAVSDK integration is included.
- **Browser login-to-live-map E2E: incomplete.** Unit tests cover the same-route component and backend-fed updates; the live smoke verified authenticated backend REST/WebSocket, not an automated browser login.

## Current WSL2-to-Windows GCS route inspection (read-only)

**Superseding owner instruction (2026-11-08): keep PX4 unchanged.** The unicast example below is historical analysis only; do not apply it, rebuild PX4, restart SITL, alter other links, or adjust firewall/network settings. QGroundControl is optional and is not required by the TRINETRA telemetry path.

- Verified WSL IP `172.23.85.230/20`; `172.23.80.1/20` is confirmed on Windows `vEthernet (WSL (Hyper-V firewall))` and is the current gateway/host target candidate. QGroundControl 5.1.5 is listening on UDP `0.0.0.0:14550`. Two enabled inbound QGroundControl allow rules were found on the Public profile; their address/port scopes were not confirmed. No firewall setting has been changed.
- Runtime PX4 startup file `build/px4_sitl_default/etc/init.d-posix/px4-rc.mavlink` binds its GCS instance to UDP `18570 + px4_instance` and starts it with `-x -u $udp_gcs_port_local -r 4000000 -f $mavlink_network_interface_arg`. The corresponding source template is `ROMFS/px4fmu_common/init.d-posix/px4-rc.mavlink`. PX4 output reports localhost-only. The optional `PX4_NET_INTERFACE` branch adds `-n <interface> -p` (interface binding plus broadcast), which is broader than needed and is not recommended here. The separate offboard link uses local `14580 + instance` and remote `14540 + instance`; leave it unchanged.
- PX4's `mavlink start` supports `-t` for a partner IP and `-o` for the remote UDP port; `-p` enables broadcast. A narrow candidate for the GCS line only is:

  ```sh
  mavlink start -x -u $udp_gcs_port_local -o 14550 -r 4000000 -f -t "${PX4_GCS_HOST:-172.23.80.1}"
  ```

  This directs the GCS MAVLink instance to the Windows host candidate on UDP 14550, retaining PX4's local receive port `18570 + instance`. Do not add `-p`, `-n`, set `PX4_NET_INTERFACE`, or set `MAV_0_BROADCAST=1`; keep other MAVLink links unchanged. The WSL gateway can change on restart, so verify the address first.
- **Not applied:** this is an edit to the PX4 startup template outside the TRINETRA workspace and requires rebuilding/relaunching the current SITL and will end its running session. The running session remains intact pending the user's review and approval. No PX4 parameter, route, firewall, `.wslconfig`, or process was changed. If one-way telemetry works but QGC commands cannot return to the WSL port, inspect WSL/Windows routing and firewall scopes and propose only a narrow allow rule; never disable firewall controls.
- If the owner later reverses the no-change instruction and explicitly requests flight validation, independently review the change and impact first. It is not part of the current continuation work.

## Gazebo pose bridge and Mission Control mirror (2026-11-08)

The existing Mission Control route now contains a read-only pose mirror alongside the Cesium PX4 map. It consumes actual Gazebo `/world/default/pose/info` messages over a separate UDP source-filtered relay; the panel applies live XYZ/quaternion poses to locally served PX4 X500 mesh assets and records bounded trails. This is not Gazebo's native world/video renderer.

Verified current network values were WSL `172.23.85.230`, Windows WSL vNIC/gateway `172.23.80.1`, WSL2 Ubuntu 24.04.5, and active Gazebo topic `/world/default/pose/info`. The isolated scratch backend on port 8012 reported `gazebo.status=receiving`, world `default`, and `model_count=3`; an unauthenticated REST request returned HTTP 401. The one-way WSL helper logged at least 2,787 snapshots forwarded before it was stopped. The smoke used `backend/gazebo-live-smoke-20261108.db`; it did not open `backend/trinetra.db`.

Enable the documented `TRINETRA_GAZEBO_POSE_UDP_*` variables in `.env.example` before backend startup, then run in WSL:

```bash
python3 /mnt/d/TRINETRA/backend/tools/gazebo_wsl_pose_bridge.py --windows-host 172.23.80.1 --forward-port 14610 --source-port 14611 --rate 10
```

The PX4 and Gazebo smoke checks used separate backend processes; do not call the combined authenticated browser acceptance complete until both sources are enabled in one backend and the existing Mission Control view is verified after login.
