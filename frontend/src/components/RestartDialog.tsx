import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react'
import { api, type RestartJob } from '../services/api'
import { ConfirmDialog } from './ConfirmDialog'
import './mc-actions.css'

export const RESTART_TEXT = 'This restarts PX4 and Gazebo at the mission start (about 20 s). A flight in progress ends.'
export const SWITCH_TEXT = "The current flight ends, and PX4 and Gazebo restart at the NEW mission's start (about 20 s). Then the new deployment is shown, with the aircraft at its start, disarmed and ready to Start."

type Props = {
  /** What is restarted at: the deployment shown (restart) or the NEW deployment (switch). */
  deploymentId: string
  /** The mission's name and version, for the text. */
  missionLabel: string
  purpose: 'restart' | 'switch'
  /** A restart already running (found after a page reload): the dialog opens on its progress. */
  resumeJob?: RestartJob | null
  onClose: () => void
  /** Called once when the restart finished and the aircraft is verified at the mission start. */
  onSucceeded: (job: RestartJob) => void
  pollMs?: number
  /** How many aircraft the mission has and how many are running now: when they differ the dialog says which aircraft will be stopped or started. */
  missionAircraft?: number
  runningAircraft?: number
}

const errorMessage = (cause: unknown, fallback: string) => {
  const failure = cause as { message?: string; status?: number; detail?: { code?: string } } | null
  return `${failure?.message ?? fallback}${failure?.status ? ` (HTTP ${failure.status}${failure.detail?.code ? ` · ${failure.detail.code}` : ''})` : ''}`
}

const stepIcon = (status: string) => status === 'ok' ? <CheckCircle2 size={13}/> : status === 'failed' ? <XCircle size={13}/> : status === 'running' ? <Loader2 size={13} className="spin"/> : <Circle size={13}/>
const STEP_WORD: Record<string, string> = { ok: 'DONE', failed: 'FAILED', running: 'RUNNING', skipped: 'NOT RUN', pending: 'WAITING' }
const AIRCRAFT_WORD: Record<string, string> = { starting: 'STARTING', telemetry_ok: 'TELEMETRY OK', pose_ok: 'POSE OK', ready: 'READY', failed: 'FAILED' }

/**
 * Restart at mission start / Switch and restart at the new start: the confirmation, then the step table of the backend job
 * (GET /api/mission-control/restart/{id}), then the result, or the failed step with its reason and a Retry. It never leaves the user on "Restarting".
 */
export function RestartDialog({ deploymentId, missionLabel, purpose, resumeJob = null, onClose, onSucceeded, pollMs = 1000, missionAircraft, runningAircraft }: Props) {
  const aircraftNote = missionAircraft !== undefined && runningAircraft !== undefined && missionAircraft !== runningAircraft
    ? (runningAircraft > missionAircraft
      ? `This mission has ${missionAircraft} aircraft and ${runningAircraft} are running now. The restart starts exactly ${missionAircraft}: the other ${runningAircraft - missionAircraft} running aircraft are stopped and disappear from Mission Control.`
      : `This mission has ${missionAircraft} aircraft and ${runningAircraft} are running now. The restart starts all ${missionAircraft}.`) : ''
  const [job, setJob] = useState<RestartJob | null>(resumeJob)
  const [phase, setPhase] = useState<'confirm' | 'working'>(resumeJob ? 'working' : 'confirm')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const reported = useRef<string | null>(null)
  const succeededRef = useRef(onSucceeded)
  succeededRef.current = onSucceeded
  const jobId = job?.id ?? null
  const running = job?.status === 'running'

  const begin = useCallback(async () => {
    setBusy(true); setError('')
    try {
      const started = await api.startRestart(deploymentId, purpose)
      setJob(started); setPhase('working')
    } catch (cause) {
      const detail = (cause as { detail?: { code?: string; job_id?: string } } | null)?.detail
      if (detail?.code === 'restart_in_progress' && detail.job_id) {      // someone (or this page before a reload) already started it: show that one
        try { setJob(await api.restartJob(detail.job_id)); setPhase('working'); return } catch { /* fall through to the message */ }
      }
      setError(errorMessage(cause, 'The restart could not be started.'))
    } finally { setBusy(false) }
  }, [deploymentId, purpose])

  useEffect(() => {
    if (!jobId || !running) return
    let stopped = false
    let timer: number | undefined
    const tick = async () => {
      try {
        const next = await api.restartJob(jobId)
        if (stopped) return
        setJob(next)
        if (next.status === 'running') timer = window.setTimeout(tick, pollMs)
      } catch (cause) {
        if (stopped) return
        setError(errorMessage(cause, 'The restart progress could not be read.'))
        timer = window.setTimeout(tick, pollMs * 3)
      }
    }
    timer = window.setTimeout(tick, pollMs)
    return () => { stopped = true; if (timer !== undefined) window.clearTimeout(timer) }
  }, [jobId, running, pollMs])

  useEffect(() => {
    if (job?.status === 'succeeded' && reported.current !== job.id) { reported.current = job.id; succeededRef.current(job) }
  }, [job])

  /** Retry only the aircraft that did not come up, or accept the partial result and continue with the ready aircraft. */
  const retryMissing = async () => {
    if (!job) return
    setBusy(true); setError('')
    try { setJob(await api.retryMissingAircraft(job.id)) } catch (cause) { setError(errorMessage(cause, 'The missing aircraft could not be restarted.')) } finally { setBusy(false) }
  }
  const continueReady = async () => {
    if (!job) return
    setBusy(true); setError('')
    try { setJob(await api.continueWithReady(job.id)) } catch (cause) { setError(errorMessage(cause, 'The partial result could not be accepted.')) } finally { setBusy(false) }
  }
  const label = purpose === 'switch' ? 'switch and restart' : 'restart at mission start'
  if (phase === 'confirm') {
    return <ConfirmDialog label={purpose === 'switch' ? 'Confirm switch and restart' : 'Confirm restart at mission start'} title={purpose === 'switch' ? `Switch to ${missionLabel} and restart at its start?` : `Restart at the start of ${missionLabel}?`} onCancel={onClose}>
      <p>{purpose === 'switch' ? SWITCH_TEXT : RESTART_TEXT}</p>
      <p>Simulation only: nothing is armed or flown, and no take-off or mission command is sent.</p>
      {aircraftNote && <p className="restart-count-note" role="note">{aircraftNote}</p>}
      {error && <div className="mc-exec-error" role="alert"><AlertTriangle size={13}/>{error}</div>}
      <div className="mc-exec-confirm-actions">
        <button type="button" className="button-primary" disabled={busy} onClick={() => void begin()}>{busy ? 'STARTING…' : purpose === 'switch' ? 'SWITCH AND RESTART' : 'RESTART NOW'}</button>
        <button type="button" className="button-quiet" disabled={busy} onClick={onClose}>CANCEL</button>
      </div>
    </ConfirmDialog>
  }
  const title = job?.status === 'succeeded' ? `Simulation restarted at ${missionLabel}` : job?.status === 'failed' ? `The ${label} did not finish` : job?.status === 'partial' ? `Only some aircraft are ready at ${missionLabel}` : `Restarting at ${missionLabel}…`
  return <ConfirmDialog label="Restart progress" title={title} onCancel={() => { if (!running) onClose() }}>
    {job && <ul className="restart-steps" aria-label="Restart steps">{job.steps.map(step => <li key={step.key} className={step.status}>
      {stepIcon(step.status)}<b>{step.label}</b><i>{STEP_WORD[step.status] ?? 'WAITING'}</i><small>{step.detail}</small></li>)}</ul>}
    {job && (job.aircraft?.length ?? 0) > 0 && <section className="restart-aircraft" aria-label="Aircraft">
      <b role="status" aria-live="polite">{job.summary || `0 of ${job.requested_count ?? 0} ready`}</b>
      <ul aria-label="Aircraft state">{(job.aircraft ?? []).map(row => <li key={row.aircraft_id} className={row.state}><span>{row.aircraft_id}</span><i>{AIRCRAFT_WORD[row.state] ?? row.state}</i><small>{row.detail}</small></li>)}</ul></section>}
    {running && <small role="status">Working… {Math.round(job?.elapsed_s ?? 0)} s. Starting several aircraft takes longer: they start in small groups and each is waited for. Telemetry stops for a short time while PX4 and Gazebo restart.</small>}
    {job?.status === 'succeeded' && <div className="restart-result" role="status">
      <b><CheckCircle2 size={14}/> {job.result?.max_distance_to_start_m !== null && job.result?.max_distance_to_start_m !== undefined ? `${job.result.max_distance_to_start_m.toFixed(1)} m from the mission start` : 'At the mission start'} · {job.result?.armed === false ? 'disarmed' : job.result?.armed ? 'armed' : 'armed state not reported'} · ready</b>
      <span>{job.result?.ended_executions.length ? `The run on ${job.result.ended_executions.join(', ')} ended (${job.reason}). ` : ''}Start in simulation is available again.</span></div>}
    {job?.status === 'partial' && job.failure && <div className="restart-failure" role="alert">
      <b><AlertTriangle size={14}/> {job.accepted_partial ? 'Continuing with the ready aircraft' : 'Only some aircraft are ready'}</b>
      <span>{job.accepted_partial ? `${(job.aircraft ?? []).filter(row => row.state === 'ready').map(row => row.aircraft_id).join(', ')} are ready. ${(job.missing ?? []).join(', ')} ${(job.missing ?? []).length === 1 ? 'is' : 'are'} not available in Mission Control, and a mission that needs more aircraft will say so before Start.` : job.failure.message}</span></div>}
    {job?.status === 'failed' && job.failure && <div className="restart-failure" role="alert">
      <b><XCircle size={14}/> Failed step: {job.steps.find(step => step.key === job.failure?.step)?.label ?? job.failure.step ?? 'unknown'}</b>
      <span>{job.failure.message}</span><small>{job.failure.code}</small></div>}
    {error && !running && <div className="mc-exec-error" role="alert"><AlertTriangle size={13}/>{error}</div>}
    <div className="mc-exec-confirm-actions">
      {job?.status === 'failed' && <button type="button" className="button-primary" disabled={busy} onClick={() => void begin()}>{busy ? 'STARTING…' : 'RETRY'}</button>}
      {job?.status === 'partial' && job.can_retry_missing && <button type="button" className="button-primary" disabled={busy} onClick={() => void retryMissing()}>{busy ? 'STARTING…' : 'RETRY THE MISSING ONES'}</button>}
      {job?.status === 'partial' && job.can_continue && <button type="button" className="button-secondary" disabled={busy} onClick={() => void continueReady()}>CONTINUE WITH THE READY AIRCRAFT</button>}
      <button type="button" className="button-quiet" disabled={running} onClick={onClose}>{job?.status === 'succeeded' ? 'CLOSE' : 'CLOSE'}</button>
    </div>
  </ConfirmDialog>
}
