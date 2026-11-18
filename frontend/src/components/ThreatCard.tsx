import { useState } from 'react'
import type { ActionId, ActionRow, Assessment, DecisionRecord, HandoverCandidate, ResponseOption, Tier } from './attackLabTypes'
import { TIER_COLORS } from './attackLabTypes'

export type ThreatState = 'ok' | 'stale' | 'landed' | 'no_data'

type Props = {
  aircraftId: string
  role?: string | null
  assessment: Assessment | null
  state: ThreatState
  history: number[]
  /** "Source: live telemetry" / "Source: offline replay (name)" plus last update. */
  sourceLine: string
  canRecord: boolean
  decisions: DecisionRecord[]
  onConfirm: (action: ActionId, candidate: string | null) => Promise<void>
  /** Live flights: real single-aircraft responses (simulator only) with their enabled state and reasons. When given, they replace the recorded-only options. */
  responses?: ResponseOption[]
  onRespond?: (action: string, candidate?: string) => Promise<string>
  onRestart?: () => void
  highlighted?: boolean
  /** The backend's handover battery rule, shown as served (the backend enforces it). */
  handoverRule?: string
  /** Replaces the default stale text (for example a lost link, with the age of the last assessment). */
  staleNote?: string
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="threat-spark empty" aria-hidden="true"/>
  const points = values.map((value, index) => `${(index / (values.length - 1)) * 100},${28 - (Math.max(0, Math.min(100, value)) / 100) * 26}`).join(' ')
  return <svg className="threat-spark" viewBox="0 0 100 30" preserveAspectRatio="none" role="img" aria-label={`Threat score trend, last ${values.length} assessments`}><polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.6" vectorEffect="non-scaling-stroke"/></svg>
}

/** One aircraft's threat assessment with a recorded-only decision flow. It never commands an aircraft. */
export function ThreatCard({ aircraftId, role, assessment, state, history, sourceLine, canRecord, decisions, onConfirm, highlighted, handoverRule, staleNote, responses, onRespond, onRestart }: Props) {
  const [respPending, setRespPending] = useState<ResponseOption | null>(null)
  const [respMessage, setRespMessage] = useState('')
  const [respError, setRespError] = useState('')
  const [respBusy, setRespBusy] = useState(false)
  const [respCandidate, setRespCandidate] = useState<string | null>(null)
  const runResponse = async (option: ResponseOption) => {
    if (!onRespond) return
    setRespBusy(true); setRespError(''); setRespMessage('')
    try { setRespMessage(await onRespond(option.action, option.candidates ? respCandidate ?? undefined : undefined)); setRespPending(null) } catch (cause) { setRespError(cause instanceof Error ? cause.message : 'The response could not be sent.') } finally { setRespBusy(false) }
  }
  const pickResponse = (option: ResponseOption) => {
    setRespError(''); setRespMessage('')
    if (option.action === 'restart') { onRestart?.(); return }
    if (option.action === 'acknowledge') { void runResponse(option); return }
    setRespCandidate(option.candidates?.find(item => item.eligible)?.aircraft_id ?? null)
    setRespPending(option)
  }
  const [pending, setPending] = useState<ActionRow | null>(null)
  const [candidate, setCandidate] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [recorded, setRecorded] = useState('')
  const live = state === 'ok' && assessment
  const tier: Tier = live ? assessment.tier : 'Low'
  const top = assessment?.actions.find(item => item.eligible)
  const candidates: HandoverCandidate[] = assessment?.handover_candidates ?? []
  const mine = decisions.filter(item => item.aircraft_id === aircraftId).slice(0, 4)

  const choose = (action: ActionRow) => {
    setPending(action); setCandidate(null); setError(''); setRecorded('')
    if (action.action === 'handover_to_standby') setCandidate(candidates.find(item => item.eligible)?.aircraft_id ?? null)
  }
  const confirm = async () => {
    if (!pending) return
    setBusy(true); setError('')
    try {
      await onConfirm(pending.action, pending.action === 'handover_to_standby' ? candidate : null)
      setRecorded(`Recorded: ${pending.label}${candidate && pending.action === 'handover_to_standby' ? ` → ${candidate}` : ''}. The aircraft was not commanded.`)
      setPending(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The decision could not be recorded.')
    } finally { setBusy(false) }
  }

  return <article className={`threat-card tier-${tier.toLowerCase()} ${highlighted ? 'highlight' : ''}`} aria-label={`Threat assessment ${aircraftId}`}>
    <header>
      <b>{aircraftId}</b>{role && <small>{role}</small>}
      {state === 'ok' && assessment ? <span className="threat-tier" style={{ color: TIER_COLORS[tier], borderColor: TIER_COLORS[tier] }}>{tier.toUpperCase()}</span>
        : <span className="threat-tier muted">{state === 'stale' ? 'STALE' : state === 'landed' ? 'LANDED' : 'NO DATA'}</span>}
    </header>
    {!live ? <p className="threat-empty">{state === 'stale' || state === 'landed' ? (staleNote ?? 'The assessment source stopped updating; the last number is hidden because it is stale.') : 'No assessment data for this aircraft.'}</p> : <div className="threat-body">
      <div className="threat-part threat-part-left">
      <div className="threat-gauge-row">
        <div className="threat-gauge" role="meter" aria-label={`Threat score ${assessment.threat_score.toFixed(0)} of 100, ${tier}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(assessment.threat_score)}>
          <i style={{ width: `${Math.max(2, assessment.threat_score)}%`, background: TIER_COLORS[tier] }}/></div>
        <strong>{assessment.threat_score.toFixed(0)}</strong>
        <span style={{ color: TIER_COLORS[tier] }}><Sparkline values={history}/></span>
      </div>
      <p className="threat-labels"><span>Threat score <small>(0–100, not a probability)</small></span></p>
      <dl className="threat-values">
        <div><dt>Spoofing confidence</dt><dd>{(assessment.anomaly_confidence * 100).toFixed(0)}%</dd></div>
        <div><dt>Severity</dt><dd>{(assessment.severity * 100).toFixed(0)}%</dd></div>
        <div><dt>Mission impact</dt><dd>{(assessment.mission_impact * 100).toFixed(0)}%</dd></div>
      </dl>
      <section aria-label="Why">
        {tier === 'Low' && assessment.evidence.length === 0 ? <p className="threat-calm">No threat detected</p>
          : <ul className="threat-evidence">{assessment.evidence.slice(0, 3).map(item => <li key={item.feature}>{item.text}</li>)}</ul>}
      </section>
      </div>
      <div className="threat-part threat-part-right">
      {top && <p className="threat-recommend"><b>Recommended: {top.label}.</b> {top.reason}. <small>Trade-off: {top.tradeoff}</small></p>}
      {responses && onRespond && <div className="threat-responses" role="group" aria-label={`Respond to ${aircraftId}`}>
        <p className="threat-rule"><b>Respond (simulation only):</b> each button acts on the simulated aircraft through the same command as the matching Mission Control button, and is audited with this score and evidence.</p>
        <div className="threat-options">{responses.map(option => <button key={option.action} type="button" disabled={!canRecord || !option.enabled || respBusy}
          className={respPending?.action === option.action ? 'selected' : ''} title={option.enabled ? option.meaning : `Not available: ${option.reason}`} onClick={() => pickResponse(option)}>{option.label}</button>)}</div>
        <ul className="threat-why-disabled">{responses.filter(option => !option.enabled).map(option => <li key={option.action}><small><b>{option.label}:</b> {option.reason}</small></li>)}</ul>
        {respPending && <div className="threat-confirm" role="group" aria-label="Confirm response"><p><b>{respPending.label}</b></p><p>{respPending.meaning}</p>
          {respPending.candidates && <fieldset><legend>Choose the other aircraft</legend>{respPending.candidates.map(item => <label key={item.aircraft_id} className={item.eligible ? '' : 'ineligible'}>
            <input type="radio" name={`resp-candidate-${aircraftId}`} disabled={!item.eligible} checked={respCandidate === item.aircraft_id} onChange={() => setRespCandidate(item.aircraft_id)}/>
            {item.aircraft_id} · {item.role ?? 'role unknown'} · battery {item.battery_pct == null ? 'unavailable' : `${item.battery_pct.toFixed(0)}%`}{item.eligible ? '' : ` · not available: ${item.reason}`}</label>)}</fieldset>}
          {respError && <p role="alert" className="threat-error">{respError}</p>}
          <div className="threat-confirm-actions"><button type="button" className="button-primary" disabled={respBusy || Boolean(respPending.candidates && !respCandidate)} onClick={() => void runResponse(respPending)}>CONFIRM: {respPending.label.toUpperCase()}</button>
          <button type="button" className="button-quiet" disabled={respBusy} onClick={() => setRespPending(null)}>CANCEL</button></div></div>}
        {!respPending && respError && <p role="alert" className="threat-error">{respError}</p>}
        {respMessage && <p role="status" className="threat-recorded">{respMessage}</p>}
      </div>}
      {!(responses && onRespond) && <div className="threat-options" role="group" aria-label={`Response options for ${aircraftId}`}>
        {assessment.actions.map(action => <button key={action.action} type="button" disabled={!canRecord || !action.eligible || busy}
          className={pending?.action === action.action ? 'selected' : ''} title={action.eligible ? `${action.reason}. Trade-off: ${action.tradeoff}` : `Not available: ${action.reason}`}
          onClick={() => choose(action)}>{action.rank}. {action.label}{!action.eligible ? ' (unavailable)' : ''}</button>)}
      </div>}
      {!canRecord && <p className="threat-empty">Your account cannot record decisions.</p>}
      {!(responses && onRespond) && pending && <div className="threat-confirm" role="group" aria-label="Confirm decision">
        <p><b>{pending.label}</b></p>
        <p>This records your decision. It does not command the aircraft yet.</p>
        {pending.action === 'handover_to_standby' && <fieldset><legend>Handover candidate</legend>
          <p className="threat-rule">{handoverRule ?? 'The backend refuses a handover to an aircraft below the minimum battery.'}</p>
          {candidates.length === 0 ? <p className="threat-empty">No other aircraft reported.</p> : candidates.map(item => <label key={item.aircraft_id} className={item.eligible ? '' : 'ineligible'}>
            <input type="radio" name={`candidate-${aircraftId}`} disabled={!item.eligible} checked={candidate === item.aircraft_id} onChange={() => setCandidate(item.aircraft_id)}/>
            {item.aircraft_id} · {item.role ?? 'role unavailable'} · battery {item.battery_pct == null ? 'unavailable' : `${item.battery_pct.toFixed(0)}%`} · {item.distance_to_task_m == null ? 'distance to the task unavailable' : `${item.distance_to_task_m.toFixed(0)} m from the task`}
            {item.eligible ? ` · score ${item.score.toFixed(2)}` : ` · not eligible: ${item.ineligible_reason}`}</label>)}
        </fieldset>}
        {error && <p role="alert" className="threat-error">{error}</p>}
        <div className="threat-confirm-actions">
          <button type="button" className="button-primary" disabled={busy || (pending.action === 'handover_to_standby' && !candidate)} onClick={() => void confirm()}>CONFIRM AND RECORD</button>
          <button type="button" className="button-quiet" disabled={busy} onClick={() => setPending(null)}>IGNORE RECOMMENDATION</button>
        </div>
      </div>}
      {recorded && <p role="status" className="threat-recorded">{recorded}</p>}
      </div>
    </div>}
    {mine.length > 0 && <ul className="threat-history" aria-label={`Recorded decisions for ${aircraftId}`}>{mine.map(item => <li key={item.id}>
      <time>{new Date(item.created_at).toLocaleTimeString()}</time> {item.username}: {item.action.replace(/_/g, ' ')}{item.handover_candidate ? ` → ${item.handover_candidate}` : ''} <small>({item.executed ? 'executed in the simulator' : item.source_name?.startsWith('acknowledged') ? 'acknowledged' : item.source_name?.startsWith('refused') ? 'refused' : 'recorded, not executed'})</small></li>)}</ul>}
    <footer>{sourceLine}</footer>
  </article>
}
