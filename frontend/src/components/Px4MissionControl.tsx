import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, Battery, BatteryWarning, CheckCircle2, Compass, Link2, MapPin, Radio, Satellite, ShieldAlert, WifiOff } from 'lucide-react'
import { api, ApiError } from '../services/api'
import type { Px4TelemetrySnapshot, Px4TelemetryStreamSnapshot, Px4TelemetryVehicle } from '../types'
import { CesiumLiveMap } from './CesiumLiveMap'
import { GazeboPoseMirror } from './GazeboPoseMirror'
import { mergePx4TelemetryStream } from '../services/telemetryStreams'
import './px4-mission-control.css'

type LinkState = 'checking' | 'connecting' | 'connected' | 'disabled' | 'stale' | 'offline' | 'unauthorized'

// PX4 SITL instance 0 uses MAVLink system ID 1; Gazebo names that same
// instance x500_0. Keep the mapping explicit and reject names outside MAVLink's
// 1..255 system-ID range instead of selecting an unrelated aircraft.
function gazeboModelForSystemId(systemId: number): string | null {
  return Number.isInteger(systemId) && systemId >= 1 && systemId <= 255
    ? `x500_${systemId - 1}`
    : null
}

function systemIdForGazeboModel(modelName: string): number | null {
  const match = /^x500_(\d{1,3})$/.exec(modelName)
  if (!match) return null
  const modelIndex = Number(match[1])
  return modelIndex <= 254 ? modelIndex + 1 : null
}

function displayNumber(value: number | null | undefined, digits = 1, suffix = '') {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : '—'
}

function linkState(snapshot: Px4TelemetrySnapshot | null): LinkState {
  if (!snapshot) return 'checking'
  if (snapshot.transport.status === 'receiving') return 'connected'
  if (snapshot.transport.status === 'stale' || snapshot.transport.status === 'disconnected') return 'stale'
  return 'connecting'
}

export function Px4MissionControl() {
  const [snapshot, setSnapshot] = useState<Px4TelemetrySnapshot | null>(null)
  const [status, setStatus] = useState<LinkState>('checking')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedGazeboModel, setSelectedGazeboModel] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let stopped = false
    let socket: WebSocket | null = null
    let retryTimer: number | undefined
    let currentSnapshot: Px4TelemetrySnapshot | null = null

    const connect = (since: string) => {
      if (stopped) return
      setStatus('connecting')
      socket = api.px4TelemetrySocket(since)
      socket.onopen = () => { if (!stopped) setError('') }
      socket.onmessage = event => {
        try {
          const update = JSON.parse(String(event.data)) as Px4TelemetryStreamSnapshot
          if (update.source !== 'px4_mavlink' || !Array.isArray(update.vehicles)) return
          const next = mergePx4TelemetryStream(currentSnapshot, update)
          if (!stopped) {
            currentSnapshot = next
            setSnapshot(next)
            setStatus(linkState(next))
            setSelectedId(current => current ?? next.vehicles[0]?.id ?? null)
          }
        } catch {
          if (!stopped) setError('The telemetry stream returned an invalid message.')
        }
      }
      socket.onerror = () => {
        if (!stopped) setStatus('offline')
      }
      socket.onclose = event => {
        if (stopped) return
        if (event.code === 4401) {
          setStatus('unauthorized')
          window.dispatchEvent(new Event('trinetra:unauthorized'))
          return
        }
        if (event.code === 4403) {
          setStatus('unauthorized')
          setError('Your account does not have permission to view mission telemetry.')
          return
        }
        if (event.code === 1013) {
          setStatus('disabled')
          return
        }
        setStatus('offline')
        retryTimer = window.setTimeout(() => connect(currentSnapshot?.generated_at ?? since), 2000)
      }
    }

    void api.px4Telemetry().then(initial => {
      if (stopped) return
      currentSnapshot = initial
      setSnapshot(initial)
      setStatus(linkState(initial))
      setSelectedId(initial.vehicles[0]?.id ?? null)
      connect(initial.generated_at)
    }).catch(cause => {
      if (stopped) return
      if (cause instanceof ApiError && cause.status === 503) {
        setStatus('disabled')
        setError('The PX4 UDP receiver is disabled. Enable the configured, source-filtered WSL relay and restart the backend.')
      } else if (cause instanceof ApiError && cause.status === 401) {
        setStatus('unauthorized')
      } else {
        setStatus('offline')
        setError(cause instanceof Error ? cause.message : 'Could not load PX4 telemetry.')
      }
    })

    return () => {
      stopped = true
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
      socket?.close()
    }
  }, [])

  const vehicles = snapshot?.vehicles ?? []
  const selected = useMemo(
    () => vehicles.find(vehicle => vehicle.id === selectedId) ?? vehicles[0] ?? null,
    [vehicles, selectedId],
  )
  const vehiclesRef = useRef(vehicles)
  vehiclesRef.current = vehicles
  const selectedModel = selectedGazeboModel ?? (selected ? gazeboModelForSystemId(selected.system_id) : null)
  const selectPx4Vehicle = useCallback((vehicleId: string) => {
    const vehicle = vehiclesRef.current.find(candidate => candidate.id === vehicleId)
    setSelectedId(vehicleId)
    setSelectedGazeboModel(vehicle ? gazeboModelForSystemId(vehicle.system_id) : null)
  }, [])
  const selectGazeboModel = useCallback((modelName: string) => {
    setSelectedGazeboModel(modelName)
    const systemId = systemIdForGazeboModel(modelName)
    const vehicle = systemId === null ? null : vehiclesRef.current.find(candidate => candidate.system_id === systemId)
    if (vehicle) setSelectedId(vehicle.id)
  }, [])
  const connectedCount = vehicles.filter(vehicle => vehicle.status === 'connected').length
  const staleCount = vehicles.filter(vehicle => vehicle.status === 'stale' || vehicle.status === 'disconnected').length
  const batteryCount = vehicles.filter(vehicle => vehicle.battery?.remaining_percent !== null && vehicle.battery?.remaining_percent !== undefined).length
  const averageBattery = batteryCount
    ? vehicles.reduce((sum, vehicle) => sum + (vehicle.battery?.remaining_percent ?? 0), 0) / batteryCount
    : null
  const statusLabel: Record<LinkState, string> = {
    checking: 'CHECKING BACKEND',
    connecting: 'AWAITING PX4 MAVLINK',
    connected: 'LIVE MAVLINK',
    disabled: 'RECEIVER DISABLED',
    stale: 'TELEMETRY STALE',
    offline: 'STREAM OFFLINE',
    unauthorized: 'ACCESS REQUIRED',
  }

  return <section className="px4-command-view" aria-label="Live PX4 mission control">
    <div className="px4-source-banner panel">
      <div className="px4-source-title"><span className={`px4-link-dot ${status}`}/><div><span className="eyebrow">PX4 SITL Â· ACTUAL MAVLINK DATA</span><strong>{statusLabel[status]}</strong></div></div>
      <div className="px4-source-details"><span>QGroundControl is optional</span><span>PX4 configuration unchanged</span><span>{snapshot?.transport.frames_decoded ?? 0} decoded frames</span></div>
    </div>
    {error && <div className="px4-inline-error" role="status"><AlertTriangle size={15}/>{error}</div>}

    <div className="metrics-row px4-metrics">
      <article className="metric-card"><span>CONNECTED AIRCRAFT</span><strong>{connectedCount} / {vehicles.length}</strong><small>{staleCount ? `${staleCount} stale or disconnected` : status === 'connected' ? 'heartbeats current' : 'awaiting valid heartbeat'}</small></article>
      <article className="metric-card"><span>FLEET BATTERY</span><strong>{displayNumber(averageBattery, 0, '%')}</strong><small>{batteryCount} / {vehicles.length} report battery values</small></article>
      <article className="metric-card"><span>GPS FIXES</span><strong>{vehicles.filter(vehicle => vehicle.gps_raw?.valid).length} / {vehicles.length}</strong><small>from incoming GPS_RAW_INT messages</small></article>
      <article className="metric-card"><span>THREAT ANALYSIS</span><strong>NOT VALIDATED</strong><small>no PX4 spoofing score is inferred</small></article>
    </div>

    <GazeboPoseMirror selectedModel={selectedModel} onSelect={selectGazeboModel}/>

    <div className="px4-live-layout">
      <section className="px4-map-panel panel">
        <div className="panel-heading"><div><span className="eyebrow">LIVE GEOSPATIAL TRACKING <span className={status === 'connected' ? 'live-dot' : 'px4-link-dot'}/></span><h3>PX4 fleet Â· CesiumJS 3D</h3></div><span className="px4-map-caption">WGS84 Â· initial region: Bengaluru</span></div>
        <CesiumLiveMap vehicles={vehicles} selected={selected?.id ?? null} onSelect={selectPx4Vehicle}/>
      </section>

      <aside className="px4-fleet-panel panel">
        <div className="panel-heading"><div><span className="eyebrow">IDENTIFIED FROM MAVLINK SYSID</span><h3>Live fleet <span className="count-tag">{vehicles.length}</span></h3></div><Radio size={16}/></div>
        {vehicles.length === 0 ? <div className="px4-empty-fleet"><Satellite size={20}/><span>No PX4 system heartbeat received.</span><small>Only systems decoded from the configured MAVLink source appear here.</small></div> : <div className="px4-vehicle-list">{vehicles.map(vehicle => <Px4VehicleRow key={vehicle.id} vehicle={vehicle} selected={selected?.id === vehicle.id} onSelect={() => selectPx4Vehicle(vehicle.id)}/>)}</div>}
        {selected && <Px4VehicleDetails vehicle={selected}/>}
      </aside>
    </div>

    <div className="px4-lower-grid">
    <section className="panel px4-capability-panel"><div className="panel-heading"><div><span className="eyebrow">SECURITY MONITORING</span><h3><ShieldAlert size={16}/> Threat analysis</h3></div></div><p>PX4 raw GPS and IMU packets are collected when the autopilot publishes them. The existing detector was validated only against the abstract research simulator, so this page does not label live PX4 data as spoofed or assign a threat score.</p><div className="px4-capability-status"><span>GPS spoof detection</span><b>NOT VALIDATED FOR PX4</b></div><div className="px4-capability-status"><span>Independent navigation estimate</span><b>NOT CONNECTED TO LIVE PX4</b></div><div className="px4-capability-status"><span>Native Gazebo world/video</span><b>NOT STREAMED</b></div><p>The read-only Gazebo pose mirror above displays live X500 poses using the PX4 mesh. Gazebo world geometry, lighting, camera/video and physics overlays are not streamed. The Cesium layer below uses MAVLink geospatial positions; Gazebo local XYZ poses are shown separately.</p></section>
      <section className="panel px4-capability-panel"><div className="panel-heading"><div><span className="eyebrow">MISSION COORDINATION</span><h3><Link2 size={16}/> PX4 mission state</h3></div></div>{selected?.mission ? <><p>{selected.mission.mission_state === 1 ? 'PX4 reports no mission loaded.' : 'Mission-current data received from PX4. This is the autopilot sequence state and is not yet linked to TRINETRA task assignments.'}</p><div className="px4-capability-status"><span>{selected.mission.mission_state === 1 ? 'Mission status' : 'Current waypoint'}</span><b>{selected.mission.mission_state === 1 ? 'NO MISSION LOADED' : selected.mission.total_sequence !== null && selected.mission.total_sequence > 0 ? `${selected.mission.current_sequence + 1} / ${selected.mission.total_sequence}` : 'NOT REPORTED'}</b></div><div className="px4-capability-status"><span>Task reassignment</span><b>NOT LINKED TO LIVE PX4</b></div></> : <p>No MAVLink mission-current message has been received. Mission progress and task reassignment remain unavailable for this live vehicle.</p>}</section>
    </div>
  </section>
}

function Px4VehicleRow({ vehicle, selected, onSelect }: { vehicle: Px4TelemetryVehicle; selected: boolean; onSelect: () => void }) {
  const statusText = vehicle.status.toUpperCase()
  return <button className={`px4-vehicle-row ${selected ? 'selected' : ''}`} onClick={onSelect} aria-pressed={selected}>
    <span className={`px4-vehicle-state ${vehicle.status}`}/><span><b>PX4 SYS {vehicle.system_id}</b><small>COMP {vehicle.component_id} Â· {vehicle.armed === null ? 'ARM STATE UNKNOWN' : vehicle.armed ? 'ARMED' : 'DISARMED'}</small></span><span className={`px4-vehicle-status ${vehicle.status}`}>{statusText}</span>
  </button>
}

function Px4VehicleDetails({ vehicle }: { vehicle: Px4TelemetryVehicle }) {
  const position = vehicle.position
  return <div className="px4-selected-details">
    <div className="selected-head"><div><span className="eyebrow">SELECTED AUTOPILOT</span><h4>PX4 SYS {vehicle.system_id}</h4></div><span className={`mission-state ${vehicle.status}`}><i/>{vehicle.status.toUpperCase()}</span></div>
    <div className="px4-telemetry-grid">
      <TelemetryDatum icon={<Radio size={13}/>} label="PX4 FLIGHT MODE" value={vehicle.heartbeat?.flight_mode ?? 'NOT REPORTED'}/>
      <TelemetryDatum icon={<MapPin size={13}/>} label="POSITION · WGS84" value={position ? `${position.latitude.toFixed(6)}, ${position.longitude.toFixed(6)}` : 'NO VALID POSITION'}/>
      <TelemetryDatum icon={<Compass size={13}/>} label="ALTITUDE · MSL / RELATIVE" value={position ? `${displayNumber(position.altitude_m, 1, ' m')} / ${displayNumber(position.relative_altitude_m, 1, ' m')}` : '—'}/>
      <TelemetryDatum icon={<Compass size={13}/>} label="PX4 ATTITUDE · ROLL / PITCH / YAW" value={vehicle.attitude ? `${displayNumber(vehicle.attitude.roll_rad, 2, ' rad')} / ${displayNumber(vehicle.attitude.pitch_rad, 2, ' rad')} / ${displayNumber(vehicle.attitude.yaw_rad, 2, ' rad')}` : 'ATTITUDE NOT REPORTED'}/>
      <TelemetryDatum icon={<Compass size={13}/>} label="TRACK HEADING" value={displayNumber(position?.heading_deg, 0, 'Â°')}/>
      <TelemetryDatum icon={<Radio size={13}/>} label="GROUND / VERTICAL SPEED" value={vehicle.velocity ? `${displayNumber(vehicle.velocity.ground_speed_m_s, 2, ' m/s')} / ${displayNumber(vehicle.velocity.down_m_s, 2, ' m/s down')}` : '—'}/>
      <TelemetryDatum icon={vehicle.battery?.remaining_percent !== null && vehicle.battery?.remaining_percent !== undefined ? <Battery size={13}/> : <BatteryWarning size={13}/>} label="BATTERY · VOLTAGE" value={vehicle.battery ? `${displayNumber(vehicle.battery.remaining_percent, 0, '%')} / ${displayNumber(vehicle.battery.voltage_v, 2, ' V')}` : 'NOT REPORTED'}/>
      <TelemetryDatum icon={<Satellite size={13}/>} label="GPS FIX · SATELLITES" value={vehicle.gps_raw ? `${vehicle.gps_raw.fix_type} / ${vehicle.gps_raw.satellites_visible ?? '—'}` : 'GPS_RAW_INT NOT REPORTED'}/>
      <TelemetryDatum icon={<Satellite size={13}/>} label="GPS HORIZONTAL / VERTICAL ACCURACY" value={vehicle.gps_raw ? `${displayNumber(vehicle.gps_raw.eph_m, 1, ' m')} / ${displayNumber(vehicle.gps_raw.epv_m, 1, ' m')}` : 'NOT REPORTED'}/>
      <TelemetryDatum icon={<Radio size={13}/>} label="RAW INPUT STATUS · NOT A HEALTH SCORE" value={`GPS ${vehicle.gps_raw?.valid ? 'VALID FIX' : vehicle.gps_raw ? 'NO VALID FIX' : 'NOT REPORTED'} / IMU ${vehicle.imu_raw ? 'RECEIVED' : 'NOT REPORTED'}`}/>
      <TelemetryDatum icon={<WifiOff size={13}/>} label="LAST HEARTBEAT" value={vehicle.heartbeat_age_seconds === null ? 'NEVER RECEIVED' : `${vehicle.heartbeat_age_seconds.toFixed(1)} s ago`}/>
      <TelemetryDatum icon={<CheckCircle2 size={13}/>} label="MISSION SEQUENCE" value={vehicle.mission ? `${vehicle.mission.current_sequence + 1} / ${vehicle.mission.total_sequence || '—'}` : 'NOT REPORTED'}/>
    </div>
    <div className="px4-imu-summary"><strong>RAW HIGHRES_IMU · NO INDEPENDENT FUSION APPLIED</strong>{vehicle.imu_raw ? <>
      <div className="px4-imu-vector"><span>ACCELERATION · M/S²</span><b>{vehicle.imu_raw.acceleration_m_s2.map(value => displayNumber(value, 2)).join(' / ')}</b></div>
      <div className="px4-imu-vector"><span>ANGULAR RATE · RAD/S</span><b>{vehicle.imu_raw.angular_rate_rad_s.map(value => displayNumber(value, 3)).join(' / ')}</b></div>
      <div className="px4-imu-vector"><span>MAGNETIC FIELD · GAUSS</span><b>{vehicle.imu_raw.magnetic_field_gauss.map(value => displayNumber(value, 3)).join(' / ')}</b></div>
      <small>Fields updated 0x{vehicle.imu_raw.fields_updated.toString(16)} · source {vehicle.imu_raw.source_time_usec} µs · received {vehicle.imu_raw.received_at}</small>
    </> : <b>HIGHRES_IMU NOT RECEIVED</b>}</div>
    <div className="px4-source-foot">Latest message {vehicle.last_message_at} Â· {vehicle.flight_trail.length} trail samples retained</div>
  </div>
}

function TelemetryDatum({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return <div className="px4-telemetry-datum"><span>{icon}{label}</span><b>{value}</b></div>
}
