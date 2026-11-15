import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Circle, CircleDot, Home, Pause, Play, PlayCircle, Power, XCircle } from 'lucide-react'
import { api, type ExecutionEvent, type ExecutionPreflight, type ExecutionView } from '../services/api'
import type { MissionDeployment } from '../services/auth'
import { disarmState, isLegacyDeployment, routeSummary, type AircraftRow } from './missionControlModel'

type Props = {
  deployment: MissionDeployment | null
  rows: AircraftRow[]
  /** New backend execution events (each delivered once), for the flight log. */
  onEvents?: (events: ExecutionEvent[]) => void
  activePollMs?: number
  idlePollMs?: number
}

export const EXECUTION_STATE_LABEL: Record<string, string> = {
  connecting: 'Connecting to PX4', uploading: 'Uploading the mission', starting: 'Starting the mission', arming: 'Arming',
  executing: 'Executing', pausing: 'Pausing', paused: 'Paused (Hold)', resuming: 'Resuming', returning: 'Returning to launch',
  failsafe: 'PX4 failsafe', completed: 'Completed', aborted: 'Aborted', failed: 'Failed', link_lost: 'Link lost',
}
const FINAL_LABEL: Record<string, string> = { completed: 'Completed', aborted: 'Aborted', failed: 'Failed', link_lost: 'Link lost' }

export const clock = (seconds: number | null | undefined) => {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '—'
  const whole = Math.max(0, Math.round(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}
const errorText = (cause: unknown, fallback: string) => {
  const failure = cause as { message?: string; status?: number; detail?: { code?: string } } | null
  return `${failure?.message ?? fallback}${failure?.status ? ` (HTTP ${failure.status}${failure.detail?.code ? ` · ${failure.detail.code}` : ''})` : ''}`
}

/**
 * The "Deployed mission" panel: real stages (deployed, uploaded to PX4, executing, final state) and the simulation
 * controls. Everything shown comes from the backend execution record; the buttons call fixed endpoints only.
 * A control that is unavailable stays visible, disabled, with the backend's reason beside it.
 */
export function MissionExecutionPanel({ deployment, rows, onEvents, activePollMs = 1500, idlePollMs = 6000 }: Props) {
  const [execution, setExecution] = useState<ExecutionView | null>(null)
  const [preflight, setPreflight] = useState<ExecutionPreflight | null>(null)
  const [preflightError, setPreflightError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [confirmingDisarm, setConfirmingDisarm] = useState(false)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const lastEventId = useRef<{ execution: string | null; id: number }>({ execution: null, id: 0 })
  const onEventsRef = useRef(onEvents)
  onEventsRef.current = onEvents
  const deploymentId = deployment?.id ?? null
  const legacy = isLegacyDeployment(deployment)
  const mine = execution && deploymentId && execution.deployment_id === deploymentId ? execution : null
  const active = Boolean(execution?.active)

  const loadEvents = useCallback(async (current: ExecutionView) => {
    if (lastEventId.current.execution !== current.id) lastEventId.current = { execution: current.id, id: 0 }
    const result = await api.executionEvents(current.id, lastEventId.current.id)
    if (result.events.length) {
      lastEventId.current.id = result.events[result.events.length - 1].id
      onEventsRef.current?.(result.events)
    }
  }, [])

  const loadExecution = useCallback(async () => {
    const latest = await api.latestExecution()
    setExecution(latest)
    setLoaded(true)
    if (latest) await loadEvents(latest).catch(() => undefined)
    return latest
  }, [loadEvents])

  const loadPreflight = useCallback(async () => {
    if (!deploymentId) { setPreflight(null); return null }
    try {
      const result = await api.executionPreflight(deploymentId)
      setPreflight(result); setPreflightError('')
      return result
    } catch (cause) {
      setPreflightError(errorText(cause, 'The pre-start checks could not be read.'))
      return null
    }
  }, [deploymentId])

  // The run is read from the backend: every 1.5 s while one is active, slowly otherwise.
  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    const tick = async () => {
      const latest = await loadExecution().catch(() => null)
      if (!stopped) timer = window.setTimeout(tick, latest?.active ? activePollMs : idlePollMs)
    }
    void tick()
    return () => { stopped = true; if (timer !== undefined) window.clearTimeout(timer) }
  }, [loadExecution, activePollMs, idlePollMs])

  // Pre-start checks are refreshed while nothing is running, so the Start button's reason is current.
  useEffect(() => {
    if (!deploymentId || active || !loaded) return
    let stopped = false
    void loadPreflight()
    const timer = window.setInterval(() => { if (!stopped && (typeof document === 'undefined' || !document.hidden)) void loadPreflight() }, 10000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [deploymentId, active, loaded, loadPreflight])

  const openConfirmation = async () => { setError(''); setConfirming(true); await loadPreflight() }

  const start = async () => {
    if (!deploymentId) return
    setBusy('start'); setError('')
    try {
      const started = await api.startExecution(deploymentId)
      setExecution(started); setConfirming(false)
      await loadEvents(started).catch(() => undefined)
    } catch (cause) {
      setError(errorText(cause, 'The simulation did not start.'))
      await Promise.all([loadPreflight(), loadExecution().catch(() => null)])
    } finally { setBusy('') }
  }

  const control = async (command: 'pause' | 'resume' | 'abort') => {
    if (!execution) return
    setBusy(command); setError('')
    try {
      const next = command === 'pause' ? await api.pauseExecution(execution.id) : command === 'resume' ? await api.resumeExecution(execution.id) : await api.abortExecution(execution.id)
      setExecution(next)
      await loadEvents(next).catch(() => undefined)
    } catch (cause) {
      setError(errorText(cause, `The ${command} request failed.`))
      await loadExecution().catch(() => null)
    } finally { setBusy('') }
  }

  // Disarm: the aircraft this run flew (or the primary one), judged from live telemetry; the backend checks it again.
  const target = rows.find(row => row.id === execution?.aircraft_id) ?? rows.find(row => row.plan?.role === 'primary') ?? rows[0] ?? null
  const disarm = disarmState(target, execution)
  const armedOnGroundAfterRun = Boolean(execution && !execution.active && disarm.armedOnGround)
  const disarmNow = async () => {
    if (!target) return
    setBusy('disarm'); setError(''); setNotice('')
    try {
      await api.disarmAircraft(target.id)
      setNotice(`${target.id} is disarmed (normal disarm, not forced).`)
      setConfirmingDisarm(false)
      await loadExecution().catch(() => null)
    } catch (cause) {
      setError(errorText(cause, 'The disarm request failed.'))
    } finally { setBusy('') }
  }

  const startReason = !deployment ? 'No mission is deployed.'
    : active ? (mine ? 'A run of this mission is in progress.' : 'A run of another deployment is in progress on this aircraft.')
      : preflightError ? preflightError
        : !preflight ? 'Reading the pre-start checks…'
          : preflight.can_start ? ''
            : `${preflight.blocking[0] ?? 'A pre-start check failed.'}${preflight.blocking.length > 1 ? ` (+${preflight.blocking.length - 1} more in the pre-start checks)` : ''}`
  // A finished run of another deployment says nothing about this mission: its "already aborted" reasons are not shown here.
  const controls = execution && (mine || execution.active) ? execution.controls : undefined
  const idle = 'Nothing is running.'
  const reasons: [string, string][] = ([
    ['Start', startReason],
    ['Pause', controls ? controls.pause.reason ?? '' : idle],
    ['Resume', controls ? controls.resume.reason ?? '' : idle],
    ['Abort', controls ? controls.abort.reason ?? '' : idle],
    ['Disarm', disarm.allowed ? '' : disarm.reason],
  ] as [string, string][]).filter(([, reason]) => reason)
  const percent = typeof mine?.percent === 'number' ? Math.max(0, Math.min(100, mine.percent)) : 0
  const executingNow = mine?.stage === 'executing'
  const final = mine && !mine.active ? mine.stage : null

  return <section className="panel mc-panel mc-exec" aria-label="Mission status">
    <div className="panel-heading"><div><span className="eyebrow">MISSION STATUS</span><h3>Deployed mission</h3></div>
      {mine && <span className={`mc-exec-state ${mine.state}`} role="status" aria-label="Execution state">{EXECUTION_STATE_LABEL[mine.state] ?? mine.state}</span>}</div>
    {!deployment ? <div className="mc-empty"><Circle size={18}/><span>No deployed mission.</span></div> : <>
      <ol className="mc-lifecycle" aria-label="Mission lifecycle">
        <li className="done"><CheckCircle2 size={13}/>Deployed to Mission Control</li>
        <li className={mine?.uploaded ? 'done' : mine?.active ? 'now' : 'na'}>{mine?.uploaded ? <CheckCircle2 size={13}/> : mine?.active ? <CircleDot size={13}/> : <Circle size={13}/>}Uploaded to PX4
          <small>{mine?.uploaded ? 'mission accepted by PX4 (simulation)' : mine?.active ? EXECUTION_STATE_LABEL[mine.state] ?? mine.state : 'not started'}</small></li>
        <li className={final === 'completed' ? 'done' : executingNow ? 'now' : mine?.uploaded && final ? 'done' : 'na'}>
          {final === 'completed' ? <CheckCircle2 size={13}/> : executingNow ? <CircleDot size={13}/> : <Circle size={13}/>}Executing
          <small>{mine && (executingNow || final) && mine.route_total
            ? `waypoint ${mine.route_current ?? 0} of ${mine.route_total} · ${percent.toFixed(0)}% · ${clock(mine.elapsed_s)} of about ${clock(mine.estimated_s)}`
            : 'not started'}</small></li>
        {final && <li className={final === 'completed' ? 'done' : 'bad'}>{final === 'completed' ? <CheckCircle2 size={13}/> : <XCircle size={13}/>}{FINAL_LABEL[final] ?? final}
          {mine?.reason && <small>{mine.reason}</small>}</li>}
        {mine?.active && mine.stage === 'link_lost' && <li className="bad"><AlertTriangle size={13}/>Link lost<small>{mine.reason}</small></li>}
      </ol>
      {mine && <div className="mc-exec-progress">
        <div className="mc-exec-bar" role="progressbar" aria-label="Mission progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}><i style={{ width: `${percent}%` }}/></div>
        <small>{mine.px4_mode ? `PX4 mode ${mine.px4_mode} · ` : ''}{mine.armed ? 'armed' : 'disarmed'} · started by {mine.started_by_name} ({mine.started_by_role})
          {mine.active && mine.stage !== 'link_lost' && mine.reason ? ` · ${mine.reason}` : ''}</small>
      </div>}
      {legacy && <small className="mc-note">This is a legacy (schema 1) deployment: no generated routes are available to draw or to fly.</small>}

      <div className="mc-exec-controls" role="group" aria-label="Simulation controls">
        <button type="button" className="button-primary" disabled={Boolean(startReason) || Boolean(busy)} onClick={() => void openConfirmation()}><PlayCircle size={14}/> START IN SIMULATION</button>
        <button type="button" className="button-secondary" disabled={!controls?.pause.allowed || Boolean(busy)} onClick={() => void control('pause')}><Pause size={13}/> PAUSE</button>
        <button type="button" className="button-secondary" disabled={!controls?.resume.allowed || Boolean(busy)} onClick={() => void control('resume')}><Play size={13}/> RESUME</button>
        <button type="button" className="button-secondary mc-exec-abort" disabled={!controls?.abort.allowed || busy === 'abort'} onClick={() => void control('abort')}><Home size={13}/> ABORT (RETURN TO LAUNCH)</button>
        <button type="button" className="button-secondary" disabled={!disarm.allowed || Boolean(busy)} title={disarm.allowed ? 'Normal disarm of the aircraft standing on the ground' : disarm.reason}
          onClick={() => { setError(''); setNotice(''); setConfirmingDisarm(true) }}><Power size={13}/> DISARM</button>
      </div>
      {armedOnGroundAfterRun && !confirmingDisarm && <div className="mc-exec-ground" role="status" aria-label="Armed on the ground">
        <b>Armed on the ground</b>
        <span>{target?.id} finished its run ({execution?.state}) but PX4 still reports it armed. PX4 normally disarms by itself after landing; it has not yet.</span>
        <button type="button" className="button-secondary" disabled={Boolean(busy)} onClick={() => { setError(''); setNotice(''); setConfirmingDisarm(true) }}>DISARM…</button></div>}
      {notice && <div className="mc-exec-ground ok" role="status"><CheckCircle2 size={13}/>{notice}</div>}
      {confirmingDisarm && <div className="mc-exec-confirm" role="dialog" aria-modal="false" aria-label="Confirm disarm">
        <b>Disarm {target?.id}?</b>
        <p>This sends a normal disarm (not forced): the motors stop. PX4 refuses it if the aircraft is not safely on the ground. Simulation only.</p>
        <div className="mc-exec-confirm-actions">
          <button type="button" className="button-primary" disabled={!disarm.allowed || busy === 'disarm'} onClick={() => void disarmNow()}>{busy === 'disarm' ? 'DISARMING…' : 'CONFIRM DISARM'}</button>
          <button type="button" className="button-quiet" disabled={busy === 'disarm'} onClick={() => setConfirmingDisarm(false)}>CANCEL</button>
        </div>
        {!disarm.allowed && <small role="status">{disarm.reason}</small>}
      </div>}
      {reasons.length > 0 && <ul className="mc-exec-reasons" aria-label="Why controls are unavailable">{reasons.map(([name, reason]) => <li key={name}><b>{name}</b>{reason}</li>)}</ul>}
      {preflight && <small className="mc-note" aria-label="Role rule">Role rule: {preflight.role_rule.message}</small>}
      {!active && deployment && <button type="button" className="button-quiet mc-exec-checks-link" onClick={() => void openConfirmation()} disabled={Boolean(busy)}>VIEW PRE-START CHECKS</button>}
      {error && <div className="mc-exec-error" role="alert"><AlertTriangle size={13}/>{error}</div>}

      {confirming && <div className="mc-exec-confirm" role="dialog" aria-modal="false" aria-label="Confirm start in simulation">
        <b>Start in simulation?</b>
        <p>{preflight?.confirmation ?? 'This runs your route in the PX4/Gazebo simulation. The aircraft will arm, take off and fly the plan.'}</p>
        {!preflight ? <small role="status">{preflightError || 'Reading the pre-start checks…'}</small> : <ul className="mc-exec-checklist" aria-label="Pre-start checklist">
          {preflight.checks.map(item => <li key={item.key} className={item.ok ? 'pass' : 'fail'}>
            {item.ok ? <CheckCircle2 size={13} aria-hidden="true"/> : <XCircle size={13} aria-hidden="true"/>}
            <span><b>{item.label}</b> <i>{item.ok ? 'PASS' : 'FAIL'}</i><small>{item.detail}{item.offer_relocation ? ' The action is in the Mission location panel above.' : ''}</small></span></li>)}
        </ul>}
        <div className="mc-exec-confirm-actions">
          <button type="button" className="button-primary" disabled={!preflight?.can_start || Boolean(busy) || active} onClick={() => void start()}>{busy === 'start' ? 'STARTING…' : 'START NOW'}</button>
          <button type="button" className="button-quiet" disabled={busy === 'start'} onClick={() => setConfirming(false)}>CANCEL</button>
        </div>
        {preflight && !preflight.can_start && <small role="status">Start is disabled: {preflight.blocking.length} check(s) above must pass first.</small>}
      </div>}

      <small className="mc-note">Simulation only: this flies the route in PX4 SITL and Gazebo. Abort returns the simulated aircraft to its launch point.</small>
      <ul className="mc-mission-rows">{rows.filter(row => row.plan).map(row => <li key={row.id}>
        <b>{row.id}</b><span>{row.plan?.role ?? 'no role'} · {row.plan?.task_type?.replace(/_/g, ' ') ?? 'no task'}</span>
        <small>{routeSummary(row.plan)}</small>
      </li>)}</ul>
    </>}
  </section>
}
