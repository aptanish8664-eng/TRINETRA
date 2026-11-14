import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, MapPin } from 'lucide-react'
import { api, type FleetCheck, type RelocationResult, type SimulationLocation } from '../services/api'
import './simulation-location.css'

type Props = {
  scenarioId: string
  versionId: string
  /** Accounts that may activate a fleet may also move the simulation; others only see the notice. */
  canRelocate: boolean
  /** Polling stops while the page that hosts the notice is not shown. */
  active?: boolean
  pollMs?: number
  /** Called after a successful, verified restart so the host can refresh fleet state. */
  onRelocated?: () => void
}

type Failure = { message: string; code?: string; status?: number; recovery?: string; checks: [string, FleetCheck][] }

const km = (metres: number) => (metres >= 10_000 ? `${(metres / 1000).toFixed(0)} km` : metres >= 1000 ? `${(metres / 1000).toFixed(1)} km` : `${metres.toFixed(0)} m`)
const checkRows = (checks: Record<string, FleetCheck> | undefined): [string, FleetCheck][] => Object.entries(checks ?? {})
const CHECK_LABEL: Record<string, string> = {
  wsl: 'WSL fleet manager', gazebo: 'Gazebo server', px4: 'PX4 process', telemetry_bridge: 'Telemetry bridge', pose_bridge: 'Pose bridge',
  receivers: 'Backend receivers', source_ip: 'Source address', verification: 'Telemetry and Gazebo pose', simulation_home: 'Position at mission start', armed: 'Armed aircraft',
}

function CheckList({ rows, label }: { rows: [string, FleetCheck][]; label: string }) {
  return <ul className="sim-location-checks" aria-label={label}>{rows.map(([name, check]) =>
    <li key={name} className={check.status === 'ok' || check.status === 'running' || check.status === 'started' ? 'ok' : 'bad'}>
      <b>{CHECK_LABEL[name] ?? name.replace(/_/g, ' ')}</b><span>{check.status}</span><small>{check.detail}</small></li>)}</ul>
}

/**
 * Where the simulated aircraft is relative to the mission start of one saved version, with the explicit
 * "Move simulation to mission start" action. The distance, thresholds and wording come from the backend.
 * Nothing here restarts the simulation on its own: it needs a press and an in-page confirmation.
 */
export function SimulationLocationNotice({ scenarioId, versionId, canRelocate, active = true, pollMs = 20000, onRelocated }: Props) {
  const [location, setLocation] = useState<SimulationLocation | null>(null)
  const [loadError, setLoadError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<RelocationResult | null>(null)
  const [failure, setFailure] = useState<Failure | null>(null)
  const key = `${scenarioId}:${versionId}`
  const keyRef = useRef(key)
  keyRef.current = key

  const load = useCallback(async () => {
    const requested = `${scenarioId}:${versionId}`
    try {
      const next = await api.simulationLocation(scenarioId, versionId)
      if (keyRef.current === requested) { setLocation(next); setLoadError('') }
    } catch (cause) {
      if (keyRef.current === requested) setLoadError(cause instanceof Error ? cause.message : 'The simulation location could not be read.')
    }
  }, [scenarioId, versionId])

  useEffect(() => {
    setLocation(null); setResult(null); setFailure(null); setConfirming(false); setLoadError('')
    if (!active) return
    void load()
    const timer = window.setInterval(() => { if (typeof document === 'undefined' || !document.hidden) void load() }, pollMs)
    return () => window.clearInterval(timer)
  }, [key, active, load, pollMs])

  const relocate = async () => {
    setBusy(true); setFailure(null); setResult(null)
    try {
      const done = await api.relocateSimulation(scenarioId, versionId)
      setResult(done)
      setConfirming(false)
      await load()
      onRelocated?.()
    } catch (cause) {
      const error = cause as { message?: string; status?: number; detail?: { code?: string; recovery?: string; checks?: Record<string, FleetCheck> } } | null
      setFailure({
        message: error?.message ?? 'The simulation could not be moved.', code: error?.detail?.code, status: error?.status,
        recovery: error?.detail?.recovery, checks: checkRows(error?.detail?.checks),
      })
      setConfirming(false)
      await load()
    } finally { setBusy(false) }
  }

  const start = location?.mission_start ?? null
  const needsMove = Boolean(location?.relocation_recommended || location?.far_from_mission_area)
  if (!location && !loadError) return null
  return <div className={`sim-location ${needsMove ? 'warn' : ''}`} aria-label="Simulation location">
    {loadError && <p className="sim-location-line" role="status"><AlertTriangle size={13}/> Simulation location unknown: {loadError}</p>}
    {location && !needsMove && !result && <p className="sim-location-line ok" role="status">
      {location.position_known ? <CheckCircle2 size={13}/> : <MapPin size={13}/>} {location.message}</p>}
    {location && needsMove && <div className="sim-location-notice" role="alert">
      <AlertTriangle size={15}/>
      <div>
        <b>{location.far_from_mission_area ? 'THE SIMULATION IS AT A DIFFERENT LOCATION THAN THIS MISSION' : 'THE AIRCRAFT IS NOT AT THE MISSION START'}</b>
        <span>{location.message}</span>
        {start && <small>Mission start ({start.uav_id}, take-off point of the primary aircraft): {start.latitude.toFixed(5)}, {start.longitude.toFixed(5)}
          {location.max_distance_to_start_m !== null ? ` · aircraft is ${km(location.max_distance_to_start_m)} away` : ''}. This does not block deployment.</small>}
      </div>
      {canRelocate
        ? <button type="button" className="button-secondary" disabled={busy || confirming} onClick={() => { setConfirming(true); setFailure(null); setResult(null) }}><MapPin size={13}/> MOVE SIMULATION TO MISSION START</button>
        : <small className="permission-note">Your account cannot restart the simulation.</small>}
    </div>}
    {confirming && start && <div className="sim-location-confirm" role="dialog" aria-modal="false" aria-label="Confirm moving the simulation">
      <b>Restart the simulation at the mission start?</b>
      <p>This stops and restarts the PX4 and Gazebo simulation at the mission start point ({start.latitude.toFixed(5)}, {start.longitude.toFixed(5)}).
        It commands no flight: nothing is armed, and no take-off or mission is sent. Telemetry stops for up to a minute while the simulation restarts, and anything in progress in the simulator is lost.</p>
      <div className="sim-location-actions">
        <button type="button" className="button-primary" disabled={busy} onClick={() => void relocate()}>{busy ? 'RESTARTING AND VERIFYING…' : 'RESTART SIMULATION AT MISSION START'}</button>
        <button type="button" className="button-quiet" disabled={busy} onClick={() => setConfirming(false)}>CANCEL</button>
      </div>
      {busy && <small role="status">Stopping PX4 and Gazebo, starting them at the mission start, then waiting for telemetry and the Gazebo pose…</small>}
    </div>}
    {result && <div className="sim-location-result" role="status">
      <b><CheckCircle2 size={14}/> Simulation moved to the mission start{result.max_distance_to_start_m !== null ? ` (aircraft ${result.max_distance_to_start_m.toFixed(1)} m from it)` : ''}.</b>
      <span>{result.note}</span>
      <CheckList rows={checkRows(result.checks)} label="Relocation checks"/>
    </div>}
    {failure && <div className="sim-location-failure" role="alert">
      <b><AlertTriangle size={14}/> The simulation was not moved.</b>
      <span>{failure.message}</span>
      <small>{failure.status ? `HTTP ${failure.status}` : ''}{failure.code ? ` · ${failure.code}` : ''}</small>
      {failure.checks.length > 0 && <CheckList rows={failure.checks} label="Relocation checks"/>}
      {failure.recovery && <span className="sim-location-recovery">What to do: {failure.recovery}</span>}
    </div>}
  </div>
}
