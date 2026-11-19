import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { api, ApiError, type RestartJob } from '../services/api'
import type { GazeboPoseSnapshot, Px4TelemetrySnapshot, Px4TelemetryStreamSnapshot } from '../types'
import type { MissionDeployment } from '../services/auth'
import { readLiveBasemap, storeLiveBasemap } from './basemapPreference'
import { CesiumLiveMap, DEFAULT_LAYERS, type Basemap, type MapLayers } from './CesiumLiveMap'
import { GazeboPoseMirror } from './GazeboPoseMirror'
import { MapErrorBoundary } from './MapErrorBoundary'
import { ThreatMonitor } from './ThreatMonitor'
import { ContinuityPanel } from './ContinuityPanel'
import { AircraftRail, DiagnosticsTabs, OperatorActions, ThreatPanel, useVisiblePolling } from './MissionControlRedesign'
import { SimulationLocationNotice } from './SimulationLocationNotice'
import { MissionExecutionPanel } from './MissionExecutionPanel'
import { MissionSwitchBanner } from './MissionSwitchBanner'
import { RestartDialog } from './RestartDialog'
import { useDeploymentOverview } from './useDeploymentOverview'
import type { ExecutionEvent } from '../services/api'
import type { Tier } from './attackLabTypes'
import { mergePx4TelemetryStream } from '../services/telemetryStreams'
import {
  AlertsPanel, BatteryPanel, FlightLog, GazeboPanel, HeaderStrip, MapToolbar,
  SystemStatus, type ChipState, type StatusChip,
} from './MissionControlPanels'
import {
  type Alert, THRESHOLDS, buildAircraftRows, buildMissionOverlay, deploymentPlan, deriveAlerts, diffObservations, executionLogEntries, isLegacyDeployment, mergeLog, systemIdForGazeboModel, type FleetPayload, type HealthPayload, type LogEntry, type Observed,
} from './missionControlModel'
import './px4-mission-control.css'
import './mission-control-dashboard.css'
import './mission-control-redesign.css'

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

/** Polls a read-only endpoint while the page is visible; failures are reported to the caller, never papered over. `waiting` counts the seconds until the first answer. */
function usePolling<T>(load: () => Promise<T>, intervalMs: number): { value: T | null; failed: boolean; waiting: number } {
  const [value, setValue] = useState<T | null>(null)
  const [failed, setFailed] = useState(false)
  const [waiting, setWaiting] = useState(0)
  useEffect(() => {
    if (value !== null || failed) { setWaiting(0); return }
    const started = Date.now()
    const timer = window.setInterval(() => setWaiting(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [value, failed])
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
    const onVisible = () => { if (!document.hidden) tick() }          // a tab that was hidden refreshes the moment it is shown again
    document.addEventListener('visibilitychange', onVisible)
    return () => { stopped = true; window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [intervalMs])
  return { value, failed, waiting }
}

/** What a status row says while its first answer is pending: never "checking" for ever. */
const pendingText = (seconds: number) => (seconds >= 15 ? 'no answer (15 s+)' : seconds >= 6 ? `slow (${seconds} s)` : 'checking')

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
  // Mission switching: the deployment the newest execution belongs to stays shown (pinned) while a newer one waits; the user switches explicitly.
  const { overview, loaded: deploymentLoaded, refresh: refreshOverview, error: overviewError, waiting: overviewWaiting } = useDeploymentOverview(true)
  const [switchedTo, setSwitchedTo] = useState<string | null>(null)
  const latestDeployment = overview?.deployment ?? null
  const executionDeployment = overview?.execution_deployment ?? null
  const executionSummary = overview?.execution ?? null
  const newerWaiting = Boolean(latestDeployment && executionDeployment && latestDeployment.id !== executionDeployment.id)
  const pinned = newerWaiting && (executionSummary?.active || switchedTo !== latestDeployment!.id)
  const deployment: MissionDeployment | null = pinned ? executionDeployment : latestDeployment
  // The one restart dialog of the page: "Restart at mission start" (the deployment shown) and "Switch and restart at the new start" (the newest deployment).
  const [restart, setRestart] = useState<{ purpose: 'restart' | 'switch'; deployment: MissionDeployment; resume: RestartJob | null } | null>(null)
  const [locationKey, setLocationKey] = useState(0)    // bumped after a restart so the location notice reads the new position at once
  const [layers, setLayers] = useState<MapLayers>(DEFAULT_LAYERS)
  const [basemap, setBasemap] = useState<Basemap>(readLiveBasemap)  // stored choice, otherwise the street default
  const chooseBasemap = (next: Basemap) => { setBasemap(next); storeLiveBasemap(next) }
  const [follow, setFollow] = useState(false)
  const [fitSignal, setFitSignal] = useState(0)
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())
  const [log, setLog] = useState<LogEntry[]>([])
  const [executionLog, setExecutionLog] = useState<LogEntry[]>([])
  const addExecutionEvents = useCallback((events: ExecutionEvent[]) => setExecutionLog(current => mergeLog(current, executionLogEntries(events))), [])
  const previousObserved = useRef<Observed | null>(null)
  const [threatAlerts, setThreatAlerts] = useState<Alert[]>([])
  const [threatTiers, setThreatTiers] = useState<Record<string, Tier>>({})
  const [dialog, setDialog] = useState<{ kind: 'resume' | 'reassign' | 'switch' | 'respond'; aircraftId: string; taker?: string; action?: 'hold' | 'return_to_launch' } | null>(null)
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
        if (document.hidden) return                       // closed on purpose while the tab is hidden; reopened when it is shown
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

    // Nobody is looking at a hidden tab: close the live stream and open it again (from where it stopped) when the tab is shown.
    const onVisibility = () => {
      if (stopped) return
      if (document.hidden) {
        if (retryTimer !== undefined) window.clearTimeout(retryTimer)
        socket?.close()
        socket = null
      } else if (!socket || socket.readyState === WebSocket.CLOSED) {
        connect(currentSnapshot?.generated_at ?? '')
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stopped = true
      document.removeEventListener('visibilitychange', onVisibility)
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
      socket?.close()
    }
  }, [])

  // A restart that is still running (the page was reloaded) is picked up again so the user never loses its progress.
  useEffect(() => {
    let stopped = false
    Promise.resolve().then(() => api.latestRestart()).then(job => {
      if (stopped || !job || job.status !== 'running') return
      const target = [latestDeployment, executionDeployment].find(item => item?.id === job.deployment_id)
      if (target) setRestart({ purpose: job.purpose, deployment: target, resume: job })
    }).catch(() => undefined)
    return () => { stopped = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deploymentLoaded])

  const health = usePolling<HealthPayload>(useCallback(() => api.health() as Promise<HealthPayload>, []), HEALTH_POLL_MS)
  const fleet = usePolling<FleetPayload>(useCallback(() => api.fleetStatus() as Promise<FleetPayload>, []), FLEET_POLL_MS)
  const gazebo = usePolling<GazeboPoseSnapshot>(useCallback(() => api.gazeboPoses({ trails: false }), []), GAZEBO_POLL_MS)
  // One small summary per aircraft (verified or why not, role, battery, route progress): the checks panel and the rail use it, so "verified" and "with a mission" always add up.
  const summaryLoad = useCallback(() => api.aircraftSummary(deployment!.id), [deployment])
  const summary = useVisiblePolling(deployment ? summaryLoad : null, 4000)

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
    chipFor(health.failed ? 'bad' : health.value ? (health.value.status === 'ready' ? 'ok' : 'warn') : health.waiting >= 15 ? 'bad' : health.waiting >= 6 ? 'warn' : 'unknown', 'backend', 'Backend', health.failed ? 'unreachable' : health.value?.status ?? pendingText(health.waiting)),
    chipFor(fleet.failed ? 'bad' : !fleet.value ? (fleet.waiting >= 15 ? 'bad' : fleet.waiting >= 6 ? 'warn' : 'unknown') : fleet.value.manager_error ? 'bad' : 'ok', 'fleet', 'WSL fleet manager', fleet.failed ? 'unreachable' : !fleet.value ? pendingText(fleet.waiting) : fleet.value.manager_error ? 'error' : 'responding'),
    chipFor(receiverState(components?.px4?.status ?? (status === 'connected' ? 'receiving' : undefined)), 'px4', 'PX4 receiver', components?.px4?.status ?? statusLabel[status]),
    chipFor(receiverState(components?.gazebo?.status), 'gazebo', 'Gazebo pose', components?.gazebo?.status ?? 'no data'),
    chipFor(bridgeState, 'bridges', 'Bridges', !bridges ? 'no data' : `telemetry ${bridges.telemetry?.status ?? '?'} · pose ${bridges.pose?.status ?? '?'}`),
  ]

  const summaryData = summary.value
  const changed = () => { summary.refresh(); void refreshOverview() }
  const hasPlan = Boolean(deployment && !isLegacyDeployment(deployment))
  return <section className="mc-dashboard" aria-label="Live PX4 mission control">
    {notice && <div className="mc-deploy-notice" role="status"><CheckCircle2 size={15}/><span>{notice}</span>
      {onDismissNotice && <button type="button" onClick={onDismissNotice}>DISMISS</button>}</div>}
    {/* A newer deployment never changes a flight in progress; the user switches explicitly. */}
    {pinned && latestDeployment && executionDeployment && executionSummary && <MissionSwitchBanner latest={latestDeployment} pinned={executionDeployment}
      execution={{ active: executionSummary.active, state: executionSummary.state, aircraft_id: executionSummary.aircraft_id }}
      row={rows.find(item => item.id === executionSummary.aircraft_id) ?? null} onSwitch={() => setSwitchedTo(latestDeployment.id)}
      onSwitchAndRestart={() => setRestart({ purpose: 'switch', deployment: latestDeployment, resume: null })}/>}
    <HeaderStrip deployment={deployment} loading={!deploymentLoaded} chips={chips} onOpenScenarioLab={onOpenScenarioLab} loadingSeconds={overviewWaiting} loadError={overviewError} onRetryLoad={() => void refreshOverview()}/>
    {error && <div className="px4-inline-error" role="status"><AlertTriangle size={15}/>{error}</div>}

    {/* The GPS threat assessment is the first thing on the page, above the map. */}
    <ThreatPanel deploymentId={hasPlan ? deployment!.id : null} onReassign={(aircraftId, taker) => setDialog({ kind: 'reassign', aircraftId, taker })} onRespond={(aircraftId, action) => setDialog({ kind: 'respond', aircraftId, action })}/>

    {/* Where the simulated aircraft is relative to the deployed mission; the restart is always an explicit, confirmed action. */}
    {hasPlan && <section className="panel mc-location" aria-label="Mission location">
      <SimulationLocationNotice key={locationKey} scenarioId={deployment!.scenario_id} versionId={deployment!.scenario_version_id} canRelocate={canManageFleet} inFlight={Boolean(executionSummary?.active)}
        onRestart={() => setRestart({ purpose: 'restart', deployment: deployment!, resume: null })}/>
    </section>}

    <div className="mc-main">
      <div className="mc-rail mc-rail-left">
        <AircraftRail rows={rows} selectedId={selectedRow?.id ?? null} onSelect={select} threatTiers={threatTiers} summary={summaryData}/>
      </div>
      <section className="panel mc-map-panel" aria-label="Mission map">
        <div className="panel-heading"><div><span className="eyebrow">LIVE GEOSPATIAL PICTURE <span className={status === 'connected' ? 'live-dot' : 'px4-link-dot'}/></span><h3>Mission map · CesiumJS 3D</h3></div><span className="px4-map-caption">WGS84 · {statusLabel[status]}</span></div>
        <MapToolbar layers={layers} onLayers={setLayers} basemap={basemap} onBasemap={chooseBasemap} follow={follow} onFollow={setFollow}
          onFit={() => setFitSignal(value => value + 1)} selectedId={selectedVehicleId}/>
        <div className="mc-map-frame"><MapErrorBoundary label="The mission map"><CesiumLiveMap vehicles={vehicles} selected={selectedVehicleId} onSelect={select} overlay={overlay} layers={layers} basemap={basemap} follow={follow} fitSignal={fitSignal}/></MapErrorBoundary>
          {vehicles.length === 0 && overlay === null && <div className="mc-map-empty" role="note">No aircraft position and no mission route to draw yet. The map fills in as soon as telemetry or a deployed mission arrives.</div>}</div>
      </section>
      <div className="mc-rail mc-rail-right">
        <MissionExecutionPanel deployment={deployment} rows={rows} onEvents={addExecutionEvents}
          onRestart={hasPlan ? () => setRestart({ purpose: 'restart', deployment: deployment!, resume: null }) : undefined}/>
        <OperatorActions deploymentId={hasPlan ? deployment!.id : null} selected={selectedRow} summary={summaryData} dialog={dialog} onDialog={setDialog} onChanged={changed}/>
        <AlertsPanel alerts={alerts} acknowledged={acknowledged} onAcknowledge={id => setAcknowledged(current => new Set(current).add(id))}/>
      </div>
    </div>

    {/* The detector's own per-aircraft cards (evidence, ranked responses) stay one click away. */}
    <details className="panel mc-details">
      <summary>GPS threat details by aircraft</summary>
      <ThreatMonitor canRecord onTierTransitions={addThreatAlerts} onTiers={setThreatTiers}
        onRestart={hasPlan ? () => setRestart({ purpose: 'restart', deployment: deployment!, resume: null }) : undefined}/>
    </details>

    <DiagnosticsTabs panels={{
      log: <><FlightLog entries={mergeLog(log, executionLog)}/><ContinuityPanel/></>,
      pose: <GazeboPanel rows={rows} snapshot={gazebo.value} error={gazebo.failed ? 'Gazebo pose data could not be read from the backend.' : ''}>
        <GazeboPoseMirror selectedModel={selectedRow?.gazeboModel ?? null} onSelect={modelName => {
          const systemId = systemIdForGazeboModel(modelName)
          const row = systemId === null ? null : rows.find(item => item.systemId === systemId)
          if (row) select(row.id)
        }}/>
      </GazeboPanel>,
      status: <SystemStatus chips={chips} verified={summaryData?.counts.verified ?? fleet.value?.verified_count ?? null} withMission={summaryData?.counts.with_mission ?? rows.filter(row => row.plan).length} openAlerts={openAlerts} notVerified={summaryData?.counts.not_verified}/>,
      batteries: <BatteryPanel rows={rows}/>,
    }}/>
    {restart && <RestartDialog deploymentId={restart.deployment.id} purpose={restart.purpose} resumeJob={restart.resume}
      missionLabel={`${restart.deployment.scenario_title} v${restart.deployment.scenario_version_number ?? '?'}`} onClose={() => setRestart(null)}
      missionAircraft={deploymentPlan(restart.deployment)?.aircraft.length} runningAircraft={vehicles.filter(item => item.status === 'connected').length}
      onSucceeded={() => {
        if (restart.purpose === 'switch') setSwitchedTo(restart.deployment.id)      // the new deployment becomes the shown one
        setFitSignal(value => value + 1)                                              // the map is refitted to the mission
        setLocationKey(value => value + 1)
        void refreshOverview()
      }}/>}
  </section>
}
