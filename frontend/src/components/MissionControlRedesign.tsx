import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, ShieldAlert } from 'lucide-react'
import { api, type AircraftSummary, type OriginalChoice, type ReassignOptions, type ResumePreview, type ThreatSummary } from '../services/api'
import { formatTime } from '../services/format'
import { ConfirmDialog } from './ConfirmDialog'
import { DetailDrawer } from './MissionControlPanels'
import { ROLE_COLORS, batteryTone, type AircraftRow } from './missionControlModel'
import { toast } from './Toasts'

const NO_DATA = 'no data'
const pct = (value: number | null | undefined) => (typeof value === 'number' ? `${Math.round(value * 100)} %` : '—')
const message = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause))

/** Polls while the page is visible, refreshes the moment the tab becomes visible again, and reports a failure (never an empty success). */
export function useVisiblePolling<T>(load: (() => Promise<T>) | null, intervalMs: number): { value: T | null; error: string; refresh: () => void } {
  const [value, setValue] = useState<T | null>(null)
  const [error, setError] = useState('')
  const loadRef = useRef(load)
  loadRef.current = load
  const active = load !== null
  const run = useCallback(() => {
    const fn = loadRef.current
    if (!fn || (typeof document !== 'undefined' && document.hidden)) return
    void fn().then(next => { setValue(next); setError('') }).catch(cause => setError(message(cause)))
  }, [])
  useEffect(() => {
    if (!active) return
    run()
    const timer = window.setInterval(run, intervalMs)
    const onVisible = () => { if (!document.hidden) run() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [active, intervalMs, run])
  return { value, error, refresh: run }
}

// ---------------------------------------------------------------------------------------------------- the GPS threat assessment (first on the page)
const LEVEL_TEXT: Record<string, string> = { clear: 'Clear', watch: 'Watch', suspected: 'Suspected', confirmed: 'Confirmed' }

export function ThreatPanel({ deploymentId, onReassign, onRespond }: { deploymentId: string | null; onReassign: (aircraftId: string, taker?: string) => void; onRespond: (aircraftId: string, action: 'hold' | 'return_to_launch') => void }) {
  const { value: summary, error, refresh } = useVisiblePolling<ThreatSummary>(deploymentId ? () => api.threatSummary(deploymentId) : null, 3000)
  const [busy, setBusy] = useState(false)
  const [storyOpen, setStoryOpen] = useState(false)
  if (!deploymentId) return <section className="panel mc-threat level-clear" aria-label="GPS threat assessment"><span className="eyebrow">GPS THREAT ASSESSMENT</span><p className="mc-threat-explain">Deploy a mission to start the GPS threat monitor.</p></section>
  if (!summary) return <section className="panel mc-threat level-clear" aria-label="GPS threat assessment" aria-busy={!error}>
    <span className="eyebrow">GPS THREAT ASSESSMENT</span>
    {error ? <p className="mc-threat-explain" role="alert"><AlertTriangle size={14}/> The threat summary could not be read: {error} <button type="button" className="button-quiet" onClick={refresh}>RETRY</button></p>
      : <p className="mc-threat-explain">Reading the GPS threat monitor…</p>}
  </section>
  const suggestion = summary.suggestion
  const keepMonitoring = async () => {
    if (!summary.aircraft_id) return
    setBusy(true)
    try { const result = await api.continueMonitoring(summary.aircraft_id, deploymentId); toast('success', result.message); refresh() } catch (cause) { toast('error', message(cause)) } finally { setBusy(false) }
  }
  const act = () => {
    if (!suggestion || !summary.aircraft_id) return
    if (suggestion.action === 'continue_monitoring') void keepMonitoring()
    else if (suggestion.action === 'reassign') onReassign(summary.aircraft_id, suggestion.taker)
    else onRespond(summary.aircraft_id, suggestion.action)
  }
  const series = summary.probability_series
  return <section className={`panel mc-threat level-${summary.level} ${summary.threat_active ? 'band' : ''}`} aria-label="GPS threat assessment" data-level={summary.level}>
    <div className="mc-threat-top">
      <div className="mc-threat-id"><span className="eyebrow">GPS THREAT ASSESSMENT</span>
        <div className="mc-threat-status"><b className="mc-threat-headline" role="status">{LEVEL_TEXT[summary.level] ?? summary.headline}</b>
          <span className="mc-threat-prob" title="The detector's probability for the most suspicious aircraft"><small>PROBABILITY</small><b>{pct(summary.probability)}</b></span>
          <span><small>AIRCRAFT</small><b>{summary.aircraft_id ?? '—'}</b></span>
          <span><small>TIER</small><b>{summary.tier ?? '—'}</b></span></div>
        <p className="mc-threat-explain">{summary.monitor_ready ? summary.explanation[summary.level] : 'The monitor needs about 10 s of flight data before it can assess an aircraft.'}</p>
      </div>
      {series.length > 1 && <svg className="mc-threat-spark" viewBox="0 0 120 34" role="img" aria-label="Probability over the last few minutes"><polyline fill="none" strokeWidth="1.6" points={series.map((point, index) => `${(index / (series.length - 1)) * 118 + 1},${33 - point.p * 31}`).join(' ')}/>
        {[summary.thresholds.watch, summary.thresholds.act, summary.thresholds.confirmed].map(level => <line key={level} x1="0" x2="120" y1={33 - level * 31} y2={33 - level * 31} strokeDasharray="2 3"/>)}</svg>}
    </div>
    {summary.raised_again && <p className="mc-threat-raised" role="alert"><ShieldAlert size={14}/> Raised again: the probability reached the reminder you set.</p>}
    {suggestion && <div className="mc-threat-suggest" role="group" aria-label="Suggested action"><span><b>Suggested:</b> {suggestion.reason}</span>
      <span className="mc-threat-buttons"><button type="button" className="button-primary" disabled={busy} onClick={act}>{suggestion.label.toUpperCase()}</button>
        {suggestion.action !== 'continue_monitoring' && <button type="button" className="button-quiet" disabled={busy} onClick={() => void keepMonitoring()}>KEEP MONITORING</button>}</span>
      <small>Only a suggestion: nothing is pressed for you. Every action stays available below.</small></div>}
    {!suggestion && summary.acknowledged && <p className="mc-threat-ack">Acknowledged by {summary.acknowledged.by}: keep flying and keep monitoring. It is raised again at {pct(summary.acknowledged.reminder_probability)}.</p>}
    <button type="button" className="mc-story-toggle" aria-expanded={storyOpen} onClick={() => setStoryOpen(value => !value)}>{storyOpen ? <ChevronDown size={13}/> : <ChevronRight size={13}/>} ATTACK STORY · {summary.story.length} {summary.story.length === 1 ? 'EVENT' : 'EVENTS'}</button>
    {storyOpen && <ol className="mc-story" aria-label="Attack story">{summary.story.length === 0 ? <li className="empty">Nothing has happened yet. Events appear here as the monitor and the operator act.</li>
      : summary.story.map((event, index) => <li key={`${event.at}-${index}`} className={event.kind}><time dateTime={event.at}>{formatTime(event.at, true)}</time><span>{event.text}</span></li>)}</ol>}
  </section>
}

// ---------------------------------------------------------------------------------------------------- aircraft rail
export function AircraftRail({ rows, selectedId, onSelect, threatTiers, summary }: { rows: AircraftRow[]; selectedId: string | null; onSelect: (id: string) => void; threatTiers: Record<string, string>; summary: AircraftSummary | null }) {
  const [open, setOpen] = useState<string | null>(null)
  const bySummary = new Map((summary?.aircraft ?? []).map(item => [item.aircraft_id, item]))
  return <section className="panel mc-panel mc-rail-panel" aria-label="Active aircraft">
    <div className="panel-heading"><div><span className="eyebrow">AIRCRAFT</span><h3>Aircraft <span className="count-tag">{rows.length}</span></h3></div></div>
    {rows.length === 0 ? <div className="mc-empty"><span>No aircraft reported or deployed.</span><small>Only systems decoded from the configured MAVLink source, or listed in a deployed mission, appear here.</small></div>
      : <ul className="mc-rail-list">{rows.map(row => {
        const vehicle = row.vehicle
        const extra = bySummary.get(row.id)
        const role = extra?.role ?? row.plan?.role ?? 'none'
        const tone = batteryTone(vehicle?.battery?.remaining_percent)
        const link = !vehicle ? 'no telemetry' : row.stale ? (vehicle.status === 'connected' ? 'stale' : vehicle.status) : 'linked'
        const state = extra?.execution_state ?? (vehicle?.armed ? 'armed' : vehicle ? 'on ground' : '—')
        const threat = threatTiers[row.id]
        return <li key={row.id} className={`mc-rail-card ${selectedId === row.id ? 'selected' : ''} ${threat && threat !== 'Low' ? 'threat' : ''}`}>
          <button type="button" className="mc-rail-main" aria-pressed={selectedId === row.id} aria-label={`Select aircraft ${row.id}`} onClick={() => onSelect(row.id)}>
            <span className="mc-rail-name"><i className="mc-role-dot" style={{ background: ROLE_COLORS[(role as 'primary') ?? 'none'] }} aria-hidden="true"/><b>{row.id}</b><span className="mc-rail-role">{role}</span>{threat && threat !== 'Low' && <small className="mc-threat-tag">THREAT {threat.toUpperCase()}</small>}</span>
            <span className="mc-rail-facts"><span className={`mc-link ${vehicle ? (row.stale ? 'stale' : 'ok') : 'none'}`}>{link.toUpperCase()}</span><span>{state}</span>
              <span className={`tone-${tone}`}>{typeof vehicle?.battery?.remaining_percent === 'number' ? `${Math.round(vehicle.battery.remaining_percent)} %` : NO_DATA}</span>
              <span>{typeof vehicle?.position?.relative_altitude_m === 'number' ? `${vehicle.position.relative_altitude_m.toFixed(0)} m` : NO_DATA}</span></span>
          </button>
          <button type="button" className="mc-rail-more" aria-expanded={open === row.id} aria-label={`${open === row.id ? 'Hide' : 'Show'} details of ${row.id}`} onClick={() => setOpen(current => (current === row.id ? null : row.id))}>{open === row.id ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}</button>
          {extra && !extra.verified && <small className="mc-rail-reason">Not verified: {extra.not_verified_reason}</small>}
          {open === row.id && <DetailDrawer row={row}/>}
        </li>
      })}</ul>}
  </section>
}

// ---------------------------------------------------------------------------------------------------- operator actions (any time, confirmed, never silent)
type Result = { kind: 'ok' | 'error'; text: string } | null

function ResultLine({ result }: { result: Result }) {
  if (!result) return null
  return <p className={`mc-action-result ${result.kind}`} role={result.kind === 'error' ? 'alert' : 'status'}>{result.text}</p>
}

export function ResumeDialog({ aircraftId, deploymentId, onClose, onDone }: { aircraftId: string; deploymentId: string; onClose: () => void; onDone: () => void }) {
  const [preview, setPreview] = useState<ResumePreview | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result>(null)
  useEffect(() => { let stopped = false; void api.resumePreview(aircraftId, deploymentId).then(value => { if (!stopped) setPreview(value) }).catch(cause => { if (!stopped) setError(message(cause)) }); return () => { stopped = true } }, [aircraftId, deploymentId])
  const confirm = async () => {
    setBusy(true); setResult(null)
    try { const done = await api.resumeOnTrack(aircraftId, deploymentId); setResult({ kind: 'ok', text: done.message }); toast('success', done.message); onDone() } catch (cause) { setResult({ kind: 'error', text: message(cause) }) } finally { setBusy(false) }
  }
  return <ConfirmDialog label={`Resume ${aircraftId} on track`} title={`Resume ${aircraftId} on track`} onCancel={onClose}>
    {error && <p className="mc-action-result error" role="alert">{error}</p>}
    {!preview && !error && <p>Working out where it would rejoin its route…</p>}
    {preview && <>
      <p>It would rejoin <b>waypoint {preview.rejoin.waypoint_number} of {preview.rejoin.of}</b>{preview.rejoin.distance_m !== null && <> ({Math.round(preview.rejoin.distance_m)} m from its present position)</>}: {preview.rejoin.why}. {preview.rejoin.remaining_waypoints} waypoints remain.</p>
      {preview.allowed ? <p>{preview.how}</p> : <p className="mc-action-result error" role="alert">{preview.reason}</p>}
    </>}
    <ResultLine result={result}/>
    <div className="mc-exec-confirm-actions"><button type="button" className="button-primary" disabled={!preview?.allowed || busy || result?.kind === 'ok'} onClick={() => void confirm()}>{busy ? 'SENDING…' : 'CONFIRM RESUME ON TRACK'}</button>
      <button type="button" className="button-quiet" onClick={onClose}>{result?.kind === 'ok' ? 'CLOSE' : 'CANCEL'}</button></div>
  </ConfirmDialog>
}

export function ReassignDialog({ aircraftId, deploymentId, preselect, onClose, onDone }: { aircraftId: string; deploymentId: string; preselect?: string; onClose: () => void; onDone: () => void }) {
  const [options, setOptions] = useState<ReassignOptions | null>(null)
  const [error, setError] = useState('')
  const [taker, setTaker] = useState<string>(preselect ?? '')
  const [original, setOriginal] = useState<OriginalChoice>('hold')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result>(null)
  useEffect(() => { let stopped = false; void api.reassignOptions(aircraftId, deploymentId).then(value => { if (!stopped) setOptions(value) }).catch(cause => { if (!stopped) setError(message(cause)) }); return () => { stopped = true } }, [aircraftId, deploymentId])
  const chosen = options?.candidates.find(item => item.aircraft_id === taker)
  const choice = options?.original_choices.find(item => item.value === original)
  const confirm = async () => {
    setBusy(true); setResult(null)
    try { const done = await api.reassign(aircraftId, deploymentId, taker, original); setResult({ kind: 'ok', text: done.message }); toast('success', done.message); onDone() } catch (cause) { setResult({ kind: 'error', text: message(cause) }) } finally { setBusy(false) }
  }
  return <ConfirmDialog label={`Reassign ${aircraftId}'s task`} title={`Reassign ${aircraftId}'s task to another aircraft`} onCancel={onClose}>
    {error && <p className="mc-action-result error" role="alert">{error}</p>}
    {!options && !error && <p>Reading the other aircraft…</p>}
    {options && <>
      <p>The chosen aircraft continues this route from <b>waypoint {options.rejoin.waypoint_number} of {options.rejoin.of}</b> ({options.rejoin.why}).</p>
      <fieldset className="mc-reassign-list"><legend>Which aircraft takes over?</legend>
        {options.candidates.length === 0 && <p>No other aircraft is part of this simulation.</p>}
        {options.candidates.map(item => <label key={item.aircraft_id} className={`mc-candidate ${item.ready ? '' : 'not-ready'}`}>
          <input type="radio" name="taker" value={item.aircraft_id} disabled={!item.ready} checked={taker === item.aircraft_id} onChange={() => setTaker(item.aircraft_id)}/>
          <span><b>{item.aircraft_id}</b> · {item.role ?? 'no role'} · {item.state}{item.has_active_run ? ` (run ${item.run_state})` : ''} · battery {item.battery_percent === null ? NO_DATA : `${Math.round(item.battery_percent)} %`} · link {item.link} · {item.distance_from_task_m === null ? 'distance unknown' : `${Math.round(item.distance_from_task_m)} m from the task`}</span>
          {!item.ready && <small>Not ready: {item.not_ready_reasons.join('; ')}.</small>}
        </label>)}
      </fieldset>
      <label className="mc-field">What should {aircraftId} do?<select value={original} onChange={event => setOriginal(event.target.value as OriginalChoice)}>
        {options.original_choices.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      {taker && chosen && <div className="mc-reassign-summary" role="note"><b>What will happen</b>
        <ul><li>{taker}: {chosen.state === 'airborne' ? 'is already flying; it ends its current run and' : 'takes off and'} flies the remaining {options.rejoin.remaining_waypoints} waypoints, then lands. It becomes {options.role ?? 'the primary'}.</li>
          <li>{aircraftId}: {choice?.meaning}{original === 'decoy' ? '' : ' Its role becomes standby.'}</li></ul>
        <small>Every step is recorded in the audit log with your name and the time.</small></div>}
    </>}
    <ResultLine result={result}/>
    <div className="mc-exec-confirm-actions"><button type="button" className="button-primary" disabled={!taker || busy || result?.kind === 'ok'} onClick={() => void confirm()}>{busy ? 'HANDING OVER…' : 'CONFIRM REASSIGN'}</button>
      <button type="button" className="button-quiet" onClick={onClose}>{result?.kind === 'ok' ? 'CLOSE' : 'CANCEL'}</button></div>
  </ConfirmDialog>
}

export function SwitchDialog({ aircraftId, candidates, onClose, onDone }: { aircraftId: string; candidates: string[]; onClose: () => void; onDone: () => void }) {
  const [other, setOther] = useState(candidates[0] ?? '')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result>(null)
  const confirm = async () => {
    setBusy(true); setResult(null)
    try { const done = await api.switchPrimary(aircraftId, other); setResult({ kind: 'ok', text: done.message }); toast('success', done.message); onDone() } catch (cause) { setResult({ kind: 'error', text: message(cause) }) } finally { setBusy(false) }
  }
  return <ConfirmDialog label="Switch primary" title={`Make another aircraft the primary`} onCancel={onClose}>
    {candidates.length === 0 ? <p className="mc-action-result error" role="alert">No flying decoy is available to swap roles with.</p>
      : <><p>{aircraftId} is the primary. Choose the flying decoy that becomes the primary; {aircraftId} becomes a decoy. No command is sent to either aircraft: only the roles the threat monitor uses change.</p>
        <label className="mc-field">New primary<select value={other} onChange={event => setOther(event.target.value)}>{candidates.map(item => <option key={item} value={item}>{item}</option>)}</select></label></>}
    <ResultLine result={result}/>
    <div className="mc-exec-confirm-actions"><button type="button" className="button-primary" disabled={!other || busy || result?.kind === 'ok'} onClick={() => void confirm()}>{busy ? 'SWITCHING…' : 'CONFIRM SWITCH'}</button>
      <button type="button" className="button-quiet" onClick={onClose}>{result?.kind === 'ok' ? 'CLOSE' : 'CANCEL'}</button></div>
  </ConfirmDialog>
}

export function RespondDialog({ aircraftId, action, onClose, onDone }: { aircraftId: string; action: 'hold' | 'return_to_launch'; onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result>(null)
  const label = action === 'hold' ? 'Hold (pause the mission)' : 'Return to launch'
  const confirm = async () => {
    setBusy(true); setResult(null)
    try { const done = await api.respond(aircraftId, action); setResult({ kind: 'ok', text: done.message }); toast('success', done.message); onDone() } catch (cause) { setResult({ kind: 'error', text: message(cause) }) } finally { setBusy(false) }
  }
  return <ConfirmDialog label={label} title={`${label}: ${aircraftId}`} onCancel={onClose}>
    <p>{action === 'hold' ? 'The aircraft stops and holds its position (simulation only). You can resume it afterwards.' : 'The mission is aborted and the aircraft flies back to launch (simulation only). This ends the run.'}</p>
    <ResultLine result={result}/>
    <div className="mc-exec-confirm-actions"><button type="button" className={action === 'return_to_launch' ? 'button-primary mc-danger' : 'button-primary'} disabled={busy || result?.kind === 'ok'} onClick={() => void confirm()}>{busy ? 'SENDING…' : `CONFIRM ${label.toUpperCase()}`}</button>
      <button type="button" className="button-quiet" onClick={onClose}>{result?.kind === 'ok' ? 'CLOSE' : 'CANCEL'}</button></div>
  </ConfirmDialog>
}

/** More actions for the selected aircraft (Pause, Resume, Return, Land here and Restart are in the Mission execution panel beside it). Available at any time, with or without a threat. */
export function OperatorActions({ deploymentId, selected, summary, dialog, onDialog, onChanged }: {
  deploymentId: string | null; selected: AircraftRow | null; summary: AircraftSummary | null
  dialog: { kind: 'resume' | 'reassign' | 'switch' | 'respond'; aircraftId: string; taker?: string; action?: 'hold' | 'return_to_launch' } | null
  onDialog: (value: { kind: 'resume' | 'reassign' | 'switch' | 'respond'; aircraftId: string; taker?: string; action?: 'hold' | 'return_to_launch' } | null) => void; onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result>(null)
  if (!deploymentId || !selected) return <section className="panel mc-panel mc-operator" aria-label="More actions"><span className="eyebrow">MORE ACTIONS</span><p className="mc-note">Select an aircraft to see its actions.</p></section>
  const id = selected.id
  const row = summary?.aircraft.find(item => item.aircraft_id === id)
  const flyingDecoys = (summary?.aircraft ?? []).filter(item => item.role === 'decoy' && item.armed && item.aircraft_id !== id).map(item => item.aircraft_id)
  const keep = async () => { setBusy(true); setResult(null); try { const done = await api.continueMonitoring(id, deploymentId); setResult({ kind: 'ok', text: done.message }); toast('success', done.message) } catch (cause) { setResult({ kind: 'error', text: message(cause) }) } finally { setBusy(false) } }
  return <section className="panel mc-panel mc-operator" aria-label="More actions">
    <div className="panel-heading"><div><span className="eyebrow">MORE ACTIONS · {id}</span><h3>Keep control</h3></div></div>
    <p className="mc-note">Available at any time, with or without a threat. Each one shows what it will do and asks you to confirm.</p>
    <div className="mc-operator-buttons">
      <button type="button" className="button-secondary" onClick={() => onDialog({ kind: 'resume', aircraftId: id })}>RESUME ON TRACK…</button>
      <button type="button" className="button-secondary" onClick={() => onDialog({ kind: 'reassign', aircraftId: id })}>REASSIGN…</button>
      <button type="button" className="button-secondary" disabled={row?.role !== 'primary'} title={row?.role !== 'primary' ? `${id} is not the primary` : undefined} onClick={() => onDialog({ kind: 'switch', aircraftId: id })}>SWITCH PRIMARY…</button>
      <button type="button" className="button-secondary" disabled={busy} onClick={() => void keep()}>KEEP MONITORING</button>
    </div>
    <ResultLine result={result}/>
    {row && !row.verified && <p className="mc-note"><AlertTriangle size={12}/> {id} is not verified: {row.not_verified_reason}</p>}
    {dialog?.kind === 'resume' && <ResumeDialog aircraftId={dialog.aircraftId} deploymentId={deploymentId} onClose={() => onDialog(null)} onDone={onChanged}/>}
    {dialog?.kind === 'reassign' && <ReassignDialog aircraftId={dialog.aircraftId} deploymentId={deploymentId} preselect={dialog.taker} onClose={() => onDialog(null)} onDone={onChanged}/>}
    {dialog?.kind === 'switch' && <SwitchDialog aircraftId={dialog.aircraftId} candidates={flyingDecoys} onClose={() => onDialog(null)} onDone={onChanged}/>}
    {dialog?.kind === 'respond' && dialog.action && <RespondDialog aircraftId={dialog.aircraftId} action={dialog.action} onClose={() => onDialog(null)} onDone={onChanged}/>}
  </section>
}

// ---------------------------------------------------------------------------------------------------- diagnostics tabs
export type DiagnosticTab = 'log' | 'pose' | 'status' | 'batteries'
const TAB_TEXT: Record<DiagnosticTab, string> = { log: 'Flight log', pose: 'Pose verification', status: 'System status', batteries: 'Batteries' }

export function DiagnosticsTabs({ panels }: { panels: Record<DiagnosticTab, ReactNode> }) {
  const [tab, setTab] = useState<DiagnosticTab>('log')
  return <section className="mc-diagnostics" aria-label="Diagnostics">
    <div role="tablist" aria-label="Diagnostics" className="mc-tabs">{(Object.keys(TAB_TEXT) as DiagnosticTab[]).map(key =>
      <button key={key} type="button" role="tab" id={`mc-tab-${key}`} aria-selected={tab === key} aria-controls={`mc-tabpanel-${key}`} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{TAB_TEXT[key]}</button>)}</div>
    <div role="tabpanel" id={`mc-tabpanel-${tab}`} aria-labelledby={`mc-tab-${tab}`} className="mc-tabpanel">{panels[tab]}</div>
  </section>
}

