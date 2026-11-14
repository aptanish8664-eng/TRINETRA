import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { api, ApiError } from '../services/api'
import type { GazeboPoseSnapshot, Px4TelemetrySnapshot, Px4TelemetryStreamSnapshot } from '../types'
import type { MissionDeployment } from '../services/auth'
import { readLiveBasemap, storeLiveBasemap } from './basemapPreference'
import { CesiumLiveMap, DEFAULT_LAYERS, type Basemap, type MapLayers } from './CesiumLiveMap'
import { GazeboPoseMirror } from './GazeboPoseMirror'
import { MapErrorBoundary } from './MapErrorBoundary'
import { ThreatMonitor } from './ThreatMonitor'
import { SimulationLocationNotice } from './SimulationLocationNotice'
import type { Tier } from './attackLabTypes'
import { mergePx4TelemetryStream } from '../services/telemetryStreams'
import {
  AircraftCards, AlertsPanel, BatteryPanel, DetailDrawer, FlightLog, GazeboPanel, HandoffsPanel, HeaderStrip, MapToolbar,
  MissionStatus, SystemStatus, type ChipState, type StatusChip,
} from './MissionControlPanels'
import {
  type Alert, THRESHOLDS, buildAircraftRows, buildMissionOverlay, deploymentPlan, deriveAlerts, diffObservations, isLegacyDeployment, systemIdForGazeboModel, type FleetPayload, type HealthPayload, type LogEntry, type Observed,
} from './missionControlModel'
import './px4-mission-control.css'
import './mission-control-dashboard.css'

type LinkState = 'checking' | 'connecting' | 'connected' | 'disabled' | 'stale' | 'offline' | 'unauthorized'

const HEALTH_POLL_MS = 5000
const FLEET_POLL_MS = 20000
const GAZEBO_POLL_MS = 3000

function linkState(snapshot: Px4TelemetrySnapshot | null): LinkState {
  if (!snapshot) return 'checking'
  if (snapshot.transport.status === 'receiving') return 'connected'
  if (snapshot.transport.status === 'stale' || snapshot.transport.status === 'disconnected') return 'stale'
  return 'connecting'
}

/** Polls a read-only endpoint while the page is visible; failures are reported to the caller, never papered over. */
function usePolling<T>(load: () => Promise<T>, intervalMs: number): { value: T | null; failed: boolean } {
  const [value, setValue] = useState<T | null>(null)
  const [failed, setFailed] = useState(false)
  const loadRef = useRef(load)
  loadRef.current = load
  useEffect(() => {
    let stopped = false
    const tick = () => {
      if (typeof document !== 'undefined' && document.hidden) return
      void loadRef.current().then(next => { if (!stopped) { setValue(next); setFailed(false) } }).catch(() => { if (!stopped) setFailed(true) })
    }
    tick()
    const timer = window.setInterval(tick, intervalMs)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [intervalMs])
  return { value, failed }
}

const chipFor = (state: ChipState, key: string, label: string, detail: string): StatusChip => ({ key, label, state, detail })
const receiverState = (status: string | undefined): ChipState =>
  status === 'receiving' ? 'ok' : status === 'awaiting_data' ? 'warn' : status ? 'bad' : 'unknown'

type Px4MissionControlProps = {
  /** Short confirmation shown once after Scenario Lab deploys and navigates here. */
  notice?: string
  onDismissNotice?: () => void
  onOpenScenarioLab?: () => void
  /** Accounts that may activate a fleet may also move the simulation to the mission start. */
  canManageFleet?: boolean
}

export function Px4MissionControl({ notice = '', onDismissNotice, onOpenScenarioLab, canManageFleet = false }: Px4MissionControlProps = {}) {
  const [snapshot, setSnapshot] = useState<Px4TelemetrySnapshot | null>(null)
  const [status, setStatus] = useState<LinkState>('checking')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [deployment, setDeployment] = useState<MissionDeployment | null>(null)
  const [layers, setLayers] = useState<MapLayers>(DEFAULT_LAYERS)
  const [basemap, setBasemap] = useState<Basemap>(readLiveBasemap)  // stored choice, otherwise the street default
  const chooseBasemap = (next: Basemap) => { setBasemap(next); storeLiveBasemap(next) }
  const [follow, setFollow] = useState(false)
  const [fitSignal, setFitSignal] = useState(0)
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())
  const [log, setLog] = useState<LogEntry[]>([])
  const previousObserved = useRef<Observed | null>(null)
  const [threatAlerts, setThreatAlerts] = useState<Alert[]>([])
  const [threatTiers, setThreatTiers] = useState<Record<string, Tier>>({})
  const addThreatAlerts = useCallback((entries: Alert[]) => setThreatAlerts(current => [...current, ...entries].slice(-30)), [])

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
      socket.onerror = () => { if (!stopped) setStatus('offline') }
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
        if (event.code === 1013) { setStatus('disabled'); return }
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

  useEffect(() => {
    let active = true
    void api.latestMissionDeployment().then(value => { if (active) setDeployment(value) }).catch(() => { if (active) setDeployment(null) })
    return () => { active = false }
  }, [])

  const health = usePolling<HealthPayload>(useCallback(() => api.health() as Promise<HealthPayload>, []), HEALTH_POLL_MS)
  const fleet = usePolling<FleetPayload>(useCallback(() => api.fleetStatus() as Promise<FleetPayload>, []), FLEET_POLL_MS)
  const gazebo = usePolling<GazeboPoseSnapshot>(useCallback(() => api.gazeboPoses(), []), GAZEBO_POLL_MS)

  const vehicles = snapshot?.vehicles ?? []
  const plan = deploymentPlan(deployment)
  const poses = gazebo.value?.models ?? []
  const rows = useMemo(() => buildAircraftRows(vehicles, plan, poses), [vehicles, plan, poses])
  const overlay = useMemo(() => buildMissionOverlay(plan), [plan])
  const selectedRow = rows.find(row => row.id === selectedId) ?? rows[0] ?? null
  const selectedVehicleId = selectedRow?.vehicle ? selectedRow.id : null

  const select = useCallback((id: string) => setSelectedId(id), [])
  const alerts = useMemo(() => deriveAlerts({
    rows, health: health.value, healthError: health.failed, fleet: fleet.value, fleetError: fleet.failed,
    streamStatus: status, hasDeployment: Boolean(deployment), extra: threatAlerts,
  }), [rows, health.value, health.failed, fleet.value, fleet.failed, status, deployment, threatAlerts])
  const openAlerts = alerts.filter(alert => !acknowledged.has(alert.id)).length

  // Session log: real transitions observed by this browser only.
  useEffect(() => {
    const observed: Observed = {
      deploymentId: deployment?.id ?? null,
      streamStatus: status,
      px4Receiver: health.value?.components?.px4?.status ?? null,
      gazeboReceiver: health.value?.components?.gazebo?.status ?? null,
      verifiedCount: fleet.value?.verified_count ?? null,
      vehicles: Object.fromEntries(vehicles.map(vehicle => [vehicle.id, vehicle.status])),
    }
    const entries = diffObservations(previousObserved.current, observed, new Date().toISOString())
    previousObserved.current = observed
    if (entries.length) setLog(current => [...current, ...entries].slice(-THRESHOLDS.logLimit))
  }, [deployment, status, health.value, fleet.value, vehicles])

  const statusLabel: Record<LinkState, string> = {
    checking: 'checking', connecting: 'awaiting PX4 MAVLink', connected: 'live MAVLink', disabled: 'receiver disabled',
    stale: 'stale', offline: 'stream offline', unauthorized: 'access required',
  }
  const components = health.value?.components
  const bridges = fleet.value?.bridges
  const bridgeState: ChipState = !bridges ? 'unknown'
    : bridges.telemetry?.status === 'running' && bridges.pose?.status === 'running' ? 'ok' : 'warn'
  const chips: StatusChip[] = [
    chipFor(health.failed ? 'bad' : health.value ? (health.value.status === 'ready' ? 'ok' : 'warn') : 'unknown', 'backend', 'Backend', health.failed ? 'unreachable' : health.value?.status ?? 'checking'),
    chipFor(fleet.failed ? 'bad' : !fleet.value ? 'unknown' : fleet.value.manager_error ? 'bad' : 'ok', 'fleet', 'WSL fleet manager', fleet.failed ? 'unreachable' : !fleet.value ? 'checking' : fleet.value.manager_error ? 'error' : 'responding'),
    chipFor(receiverState(components?.px4?.status ?? (status === 'connected' ? 'receiving' : undefined)), 'px4', 'PX4 receiver', components?.px4?.status ?? statusLabel[status]),
    chipFor(receiverState(components?.gazebo?.status), 'gazebo', 'Gazebo pose', components?.gazebo?.status ?? 'no data'),
    chipFor(bridgeState, 'bridges', 'Bridges', !bridges ? 'no data' : `telemetry ${bridges.telemetry?.status ?? '?'} · pose ${bridges.pose?.status ?? '?'}`),
  ]

  return <section className="mc-dashboard" aria-label="Live PX4 mission control">
    {notice && <div className="mc-deploy-notice" role="status"><CheckCircle2 size={15}/><span>{notice}</span>
      {onDismissNotice && <button type="button" onClick={onDismissNotice}>DISMISS</button>}</div>}
    <HeaderStrip deployment={deployment} chips={chips} onOpenScenarioLab={onOpenScenarioLab}/>
    {error && <div className="px4-inline-error" role="status"><AlertTriangle size={15}/>{error}</div>}
    {/* Where the simulated aircraft is relative to the deployed mission; the restart is always an explicit, confirmed action. */}
    {deployment && !isLegacyDeployment(deployment) && <section className="panel mc-location" aria-label="Mission location">
      <SimulationLocationNotice scenarioId={deployment.scenario_id} versionId={deployment.scenario_version_id} canRelocate={canManageFleet}/>
    </section>}

    <div className="mc-main">
      <div className="mc-rail mc-rail-left">
        <AircraftCards rows={rows} selectedId={selectedRow?.id ?? null} onSelect={select} threatTiers={threatTiers}/>
        <SystemStatus chips={chips} verified={fleet.value?.verified_count ?? null} withMission={rows.filter(row => row.plan).length} openAlerts={openAlerts}/>
        <BatteryPanel rows={rows}/>
      </div>
      <section className="panel mc-map-panel" aria-label="Mission map">
        <div className="panel-heading"><div><span className="eyebrow">LIVE GEOSPATIAL PICTURE <span className={status === 'connected' ? 'live-dot' : 'px4-link-dot'}/></span><h3>Mission map · CesiumJS 3D</h3></div><span className="px4-map-caption">WGS84 · {statusLabel[status]}</span></div>
        <MapToolbar layers={layers} onLayers={setLayers} basemap={basemap} onBasemap={chooseBasemap} follow={follow} onFollow={setFollow}
          onFit={() => setFitSignal(value => value + 1)} selectedId={selectedVehicleId}/>
        <MapErrorBoundary label="The mission map"><CesiumLiveMap vehicles={vehicles} selected={selectedVehicleId} onSelect={select} overlay={overlay} layers={layers} basemap={basemap} follow={follow} fitSignal={fitSignal}/></MapErrorBoundary>
        <DetailDrawer row={selectedRow}/>
      </section>
      <div className="mc-rail mc-rail-right">
        <AlertsPanel alerts={alerts} acknowledged={acknowledged} onAcknowledge={id => setAcknowledged(current => new Set(current).add(id))}/>
        <MissionStatus deployment={deployment} rows={rows}/>
        <HandoffsPanel/>
      </div>
    </div>

    <ThreatMonitor canRecord onTierTransitions={addThreatAlerts} onTiers={setThreatTiers}/>

    <div className="mc-lower">
      <FlightLog entries={log}/>
      <GazeboPanel rows={rows} snapshot={gazebo.value} error={gazebo.failed ? 'Gazebo pose data could not be read from the backend.' : ''}>
      <GazeboPoseMirror
        selectedModel={selectedRow?.gazeboModel ?? null}
        onSelect={modelName => {
          const systemId = systemIdForGazeboModel(modelName)
          const row = systemId === null ? null : rows.find(item => item.systemId === systemId)
          if (row) select(row.id)
        }}
      />
      </GazeboPanel>
    </div>
  </section>
}

