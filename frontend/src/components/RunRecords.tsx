import { useCallback, useEffect, useState } from 'react'
import { Download } from 'lucide-react'
import { api } from '../services/api'
import type { LiveRunRecord, RunTimelinePoint, StoredResult } from './attackLabTypes'
import { ContinuityList } from './ContinuityPanel'

const OUTCOME_TEXT: Record<string, string> = {
  detected: 'Detected', missed: 'Missed', unclear: 'Unclear (the monitor was already alarming)', not_reached: 'Not reached (the flight ended first)', no_attack: 'No attack (baseline)',
}
const TIER_COLOUR: Record<string, string> = { Low: '#4fb286', Medium: '#e0b341', High: '#e8863a', Critical: '#e5484d' }

const isLiveRun = (value: StoredResult['result']): value is LiveRunRecord => (value as { kind?: string }).kind === 'live_run'
const when = (iso: string) => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
const seconds = (value: number | null | undefined) => (value === null || value === undefined ? '—' : `${value.toFixed(1)} s`)

/** Threat score over flight time for one aircraft; the attack window is shaded. Plain SVG, no data is invented. */
export function RunTimeline({ aircraft, points, attacks }: { aircraft: string; points: RunTimelinePoint[]; attacks: LiveRunRecord['attacks'] }) {
  const width = 720, height = 190, left = 40, bottom = 26, top = 10
  if (points.length < 2) return <p className="planner-empty">No assessments were recorded for {aircraft} in this run.</p>
  const end = Math.max(points[points.length - 1].t, ...attacks.filter(item => item.target_aircraft === aircraft).map(item => item.start_s + item.duration_s))
  const x = (t: number) => left + (t / Math.max(end, 1)) * (width - left - 8)
  const y = (score: number) => top + (1 - Math.min(Math.max(score, 0), 100) / 100) * (height - top - bottom)
  const path = points.map((point, index) => `${index ? 'L' : 'M'}${x(point.t).toFixed(1)},${y(point.score).toFixed(1)}`).join(' ')
  return <svg role="img" className="run-timeline" viewBox={`0 0 ${width} ${height}`} aria-label={`Threat score of ${aircraft} over flight time`}>
    {attacks.filter(item => item.target_aircraft === aircraft).map((item, index) => <g key={index}>
      <rect x={x(item.start_s)} y={top} width={Math.max(2, x(item.start_s + item.duration_s) - x(item.start_s))} height={height - top - bottom} fill="currentColor" opacity="0.12"/>
      <text x={x(item.start_s) + 3} y={top + 11} fontSize="10" fill="currentColor">attack: {item.type}</text></g>)}
    {[0, 25, 50, 75, 100].map(score => <g key={score}><line x1={left} x2={width - 8} y1={y(score)} y2={y(score)} stroke="currentColor" opacity="0.15"/>
      <text x={4} y={y(score) + 3} fontSize="10" fill="currentColor">{score}</text></g>)}
    <path d={path} fill="none" stroke="currentColor" strokeWidth="1.6"/>
    {points.filter((_, index) => index % Math.ceil(points.length / 140) === 0).map((point, index) => <circle key={index} cx={x(point.t)} cy={y(point.score)} r="2" fill={TIER_COLOUR[point.tier] ?? 'currentColor'}/>)}
    <text x={left} y={height - 6} fontSize="10" fill="currentColor">0 s</text>
    <text x={width - 8} y={height - 6} fontSize="10" textAnchor="end" fill="currentColor">{end.toFixed(0)} s flight time</text>
  </svg>
}

/** Run records of the experiments that ran during flights: summary table, a detail view with the timeline, and the Excel export. */
export function RunRecords() {
  const [results, setResults] = useState<StoredResult[] | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => Promise.resolve().then(() => api.attackLabResults()).then(setResults).catch(cause => setError(cause instanceof Error ? cause.message : 'Run records could not be loaded.')), [])
  useEffect(() => { void load() }, [load])

  const exportAll = async () => {
    setBusy(true); setError('')
    try {
      const blob = await api.exportRunRecords()
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = 'trinetra_run_records.xlsx'
      link.click()
      setTimeout(() => URL.revokeObjectURL(link.href), 4000)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'The export failed.') } finally { setBusy(false) }
  }

  const live = (results ?? []).filter(item => isLiveRun(item.result))
  const open = live.find(item => item.id === openId)
  return <section className="panel attack-card attack-run-records" aria-label="Run records">
    <div className="panel-heading"><div><span className="eyebrow">RUN RECORDS · MEASURED IN SIMULATED FLIGHTS</span>
      <h3>Run records <span className="count-tag">{live.length}</span></h3></div>
      <button type="button" className="button-secondary" disabled={busy || live.length === 0} onClick={() => void exportAll()}><Download size={13}/> EXPORT RUN RECORDS</button></div>
    {error && <p className="attack-flag warn" role="alert">{error}</p>}
    {results === null ? <div className="skeleton-row" role="status" aria-label="Loading run records"><span className="skeleton"/><span className="skeleton"/><span className="skeleton"/></div>
      : live.length === 0 ? <p className="planner-empty">No run records yet. Switch Implement on for an experiment, deploy its mission version and start the flight: a record is written when the flight ends.</p>
        : <table className="run-table"><thead><tr><th>Experiment</th><th>Ended</th><th>Outcome</th><th>First reaction</th><th>Latency</th><th>False alarms</th><th>Operator responses</th><th/></tr></thead>
          <tbody>{live.map(item => {
            const record = item.result as LiveRunRecord
            const first = record.attacks.find(attack => attack.latency_s !== null)
            return <tr key={item.id} className={openId === item.id ? 'selected' : ''}>
              <td>{item.experiment_name}{item.from_deleted_experiment && <small> (deleted experiment)</small>}</td>
              <td>{when(record.ended_at)}<small> · {record.end_reason}</small></td>
              <td>{OUTCOME_TEXT[record.outcome] ?? record.outcome}</td>
              <td>{first ? `${first.first_reaction_tier}, at ${seconds(first.first_reaction_s)}` : '—'}</td>
              <td>{seconds(first?.latency_s)}</td><td>{record.false_alarms}</td><td>{record.operator_responses.length}</td>
              <td><button type="button" className="button-quiet" aria-label={`Open run record of ${item.experiment_name}`} onClick={() => setOpenId(openId === item.id ? null : item.id)}>{openId === item.id ? 'CLOSE' : 'OPEN'}</button></td></tr>
          })}</tbody></table>}
    {open && (() => {
      const record = open.result as LiveRunRecord
      return <div className="run-detail" role="region" aria-label={`Run record of ${open.experiment_name}`}>
        <h4>{open.experiment_name}: {OUTCOME_TEXT[record.outcome] ?? record.outcome}</h4>
        <p><small>Started {when(record.started_at)} · {record.run_seconds.toFixed(0)} s · ended: {record.end_reason} · {record.assessments_recorded} assessments recorded</small></p>
        <table className="run-table"><thead><tr><th>Aircraft</th><th>Attack</th><th>Scheduled</th><th>Outcome</th><th>First reaction</th><th>Latency</th><th>First High/Critical</th><th>Peak</th></tr></thead>
          <tbody>{record.attacks.map((attack, index) => <tr key={index}>
            <td>{attack.target_aircraft}</td><td>{attack.type} ({attack.magnitude})</td><td>{attack.start_s.toFixed(0)}–{(attack.start_s + attack.duration_s).toFixed(0)} s</td>
            <td>{OUTCOME_TEXT[attack.outcome] ?? attack.outcome}</td><td>{attack.first_reaction_tier ? `${attack.first_reaction_tier} at ${seconds(attack.first_reaction_s)}` : '—'}</td>
            <td>{seconds(attack.latency_s)}</td><td>{seconds(attack.first_alert_latency_s)}</td><td>{attack.peak_score === null ? '—' : `${attack.peak_score.toFixed(0)} (${attack.peak_tier})`}</td></tr>)}</tbody></table>
        {Object.entries(record.timeline).map(([aircraft, points]) => <div key={aircraft}><b>{aircraft}</b><RunTimeline aircraft={aircraft} points={points} attacks={record.attacks}/></div>)}
        {(record.reconfigurations ?? []).length > 0 && <><h5>Mission continuity after responses</h5><ContinuityList items={record.reconfigurations ?? []}/></>}
        <h5>Operator responses</h5>
        {record.operator_responses.length === 0 ? <p className="planner-empty">No operator response was recorded during this run.</p>
          : <ul>{record.operator_responses.map((response, index) => <li key={index}>{response.after_run_start_s.toFixed(0)} s after the run started: {response.by} chose “{response.action}” for {response.aircraft_id} (tier {response.tier ?? 'unknown'}, score {response.threat_score ?? '—'})</li>)}</ul>}
        <ul className="attack-caveats">{record.caveats.map((text, index) => <li key={index}><small>{text}</small></li>)}</ul>
      </div>
    })()}
  </section>
}
