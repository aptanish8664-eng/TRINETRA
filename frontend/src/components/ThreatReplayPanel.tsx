import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Play, Pause, RotateCcw } from 'lucide-react'
import { api } from '../services/api'
import { ATTACK_LAB_NAME, GROUND_TRUTH_NOTE, TIER_COLORS, type ActionId, type Assessment, type DecisionRecord, type Experiment, type ModelInfo, type Replay, type ReplayEvaluation } from './attackLabTypes'
import { ThreatCard } from './ThreatCard'
import { findGroundTruthKeys, threatSessionStore, type SessionAircraft, type ThreatSession } from './threatSession'
import './threat-panel.css'

const WINDOW_SAMPLES = 600 // whole history up to the API limit: the detector normalises by the flight's own early-flight scale
const ASSESS_EVERY_S = 2
const TICK_MS = 500

export function buildAssessPayload(replay: Replay, index: number, end: number) {
  const aircraft = replay.aircraft[index]
  const start = Math.max(0, end - WINDOW_SAMPLES)
  const window = (item: Replay['aircraft'][number]) => item.t.slice(start, end).map((t, offset) => ({
    t, gps_position: item.gps_position[start + offset], gps_velocity: item.gps_velocity[start + offset], imu_velocity: item.imu_velocity[start + offset],
  }))
  const last = end - 1
  const others = replay.aircraft.filter((_, position) => position !== index)
  return {
    aircraft_id: aircraft.aircraft_id, role: aircraft.role ?? 'standby', battery_pct: aircraft.battery_pct[last],
    remaining_fraction: Math.max(0, Math.min(1, 1 - aircraft.t[last] / Math.max(replay.duration_s, 1))),
    samples: window(aircraft),
    peers: others.filter(item => item.t.length >= end).map(item => ({ aircraft_id: item.aircraft_id, samples: window(item) })),
    handover_candidates: others.filter(item => item.t.length >= end).map(item => ({
      aircraft_id: item.aircraft_id, role: item.role ?? 'standby', battery_pct: item.battery_pct[last],
      distance_to_task_m: Math.hypot(item.gps_position[last][0] - aircraft.gps_position[last][0], item.gps_position[last][1] - aircraft.gps_position[last][1]), workload: 0,
    })),
    min_battery_pct: replay.min_battery_pct, candidate_roles: replay.candidate_roles,
  }
}

type Props = { canRecord: boolean }

/** Research: offline replay of a pinned experiment through the same assessment engine. The schedule stays hidden until the end. */
export function ThreatReplayPanel({ canRecord }: Props) {
  const [model, setModel] = useState<ModelInfo | null>(null)
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [experimentId, setExperimentId] = useState('')
  const [replay, setReplay] = useState<Replay | null>(null)
  const [clock, setClock] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(10)
  const [aircraft, setAircraft] = useState<Record<string, SessionAircraft>>({})
  const [evaluation, setEvaluation] = useState<ReplayEvaluation | null>(null)
  const [decisions, setDecisions] = useState<DecisionRecord[]>([])
  const [error, setError] = useState('')
  const [lastUpdate, setLastUpdate] = useState(0)
  const clockRef = useRef(0)
  const lastAssess = useRef<Record<string, number>>({})
  const inflight = useRef(false)
  const aircraftRef = useRef<Record<string, SessionAircraft>>({})
  const finishedRef = useRef(false)

  useEffect(() => {
    let active = true
    void Promise.all([api.researchModelInfo(), api.attackExperiments(), api.researchDecisions()]).then(([info, list, recorded]) => {
      if (!active) return
      setModel(info); setExperiments(list); setDecisions(recorded)
    }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Threat assessment data could not load.') })
    return () => { active = false }
  }, [])

  const publish = useCallback((state: ThreatSession['state'], current: Record<string, SessionAircraft>) => {
    if (!replay) return
    threatSessionStore.publish({ sourceName: replay.experiment_name, experimentId: replay.experiment_id, state, updatedAt: Date.now(), minBatteryPct: replay.min_battery_pct, aircraft: current })
  }, [replay])

  const reset = useCallback(() => {
    setPlaying(false); setReplay(null); setClock(0); clockRef.current = 0; setAircraft({}); aircraftRef.current = {}; lastAssess.current = {}
    setEvaluation(null); finishedRef.current = false; threatSessionStore.publish(null)
  }, [])

  useEffect(() => () => { threatSessionStore.publish(null) }, [])

  const start = async () => {
    if (!experimentId) return
    reset(); setError('')
    try {
      const created = await api.createReplay(experimentId)
      const leaked = findGroundTruthKeys(created)
      if (leaked.length) { setError(`The replay was rejected because it contained ground-truth fields (${leaked.join(', ')}).`); return }
      setReplay(created)
      const initial = Object.fromEntries(created.aircraft.map(item => [item.aircraft_id, { aircraftId: item.aircraft_id, role: item.role, assessment: null, history: [], tiers: [], battery: item.battery_pct[0] ?? null, reassignable: item.reassignable }]))
      aircraftRef.current = initial; setAircraft(initial); setPlaying(true)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'The replay could not start.') }
  }

  const finish = useCallback(async () => {
    if (!replay || finishedRef.current) return
    finishedRef.current = true
    setPlaying(false)
    publish('finished', aircraftRef.current)
    try {
      await api.finishReplay(replay.replay_id)
      setEvaluation(await api.replayEvaluation(replay.replay_id))
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'The evaluation could not be computed.') }
  }, [replay, publish])

  useEffect(() => {
    if (!replay || !playing) return
    const timer = window.setInterval(() => {
      const next = Math.min(replay.duration_s, clockRef.current + speed * (TICK_MS / 1000))
      clockRef.current = next
      setClock(next)
      if (!inflight.current) {
        inflight.current = true
        void (async () => {
          try {
            for (let index = 0; index < replay.aircraft.length; index += 1) {
              const item = replay.aircraft[index]
              const end = Math.min(item.t.length, Math.floor(next * replay.hz))
              if (end < 6 || next - (lastAssess.current[item.aircraft_id] ?? -Infinity) < ASSESS_EVERY_S) continue
              lastAssess.current[item.aircraft_id] = next
              const result = await api.assessThreat(buildAssessPayload(replay, index, end))
              if (findGroundTruthKeys(result).length) { setError('An assessment response was rejected: it contained ground-truth fields.'); return }
              const previous = aircraftRef.current[item.aircraft_id]
              aircraftRef.current = { ...aircraftRef.current, [item.aircraft_id]: {
                ...previous, assessment: result, battery: item.battery_pct[end - 1],
                history: [...previous.history, result.threat_score].slice(-40), tiers: [...previous.tiers, result.tier].slice(-120),
              } }
            }
            setAircraft(aircraftRef.current); setLastUpdate(Date.now())
            publish('playing', aircraftRef.current)
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : 'An assessment failed.')
          } finally { inflight.current = false }
        })()
      }
      if (next >= replay.duration_s) void finish()
    }, TICK_MS)
    return () => window.clearInterval(timer)
  }, [replay, playing, speed, publish, finish])

  const record = async (aircraftId: string, action: ActionId, candidate: string | null) => {
    const assessment: Assessment | null = aircraft[aircraftId]?.assessment ?? null
    const saved = await api.recordDecision({
      aircraft_id: aircraftId, action, handover_candidate: candidate, source: 'offline_replay', source_name: replay?.experiment_name ?? null,
      tier: assessment?.tier ?? null, threat_score: assessment?.threat_score ?? null,
    })
    setDecisions(current => [saved, ...current].slice(0, 50))
  }

  const ordered = useMemo(() => Object.values(aircraft).sort((a, b) => (b.assessment?.threat_score ?? -1) - (a.assessment?.threat_score ?? -1)), [aircraft])
  const done = Boolean(replay && clock >= replay.duration_s)
  const eligibleExperiments = experiments

  return <section className="panel threat-panel" aria-label="Threat assessment (offline replay)">
    <div className="panel-heading"><div><span className="eyebrow">OFFLINE REPLAY · SYNTHETIC · NOT LIVE PX4</span><h3>Threat assessment (offline replay)</h3></div>
      <span className="count-tag">{model?.available ? `model ${model.model_id}` : 'NO MODEL'}</span></div>
    <p className="mc-note">Replays a pinned {ATTACK_LAB_NAME} experiment over synthetic telemetry. The detector sees sensor streams only; the attack schedule stays hidden until the replay ends. Decisions are recorded, not executed.</p>
    {model && !model.available && <p role="alert" className="scenario-library-error">{model.reason}</p>}
    {error && <p role="alert" className="scenario-library-error">{error}</p>}
    <div className="threat-controls">
      <label>EXPERIMENT<select aria-label="Experiment" value={experimentId} disabled={playing} onChange={event => { setExperimentId(event.target.value); reset() }}>
        <option value="">Choose a pinned experiment</option>{eligibleExperiments.map(item => <option key={item.id} value={item.id}>{item.name} ({item.status})</option>)}</select></label>
      <label>SPEED<select aria-label="Replay speed" value={speed} onChange={event => setSpeed(Number(event.target.value))}>{[1, 5, 10, 20].map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
      {!replay || done ? <button type="button" className="button-primary" disabled={!experimentId || !model?.available} onClick={() => void start()}><Play size={13}/> {done ? 'REPLAY AGAIN' : 'START REPLAY'}</button>
        : <button type="button" className="button-secondary" onClick={() => setPlaying(value => !value)}>{playing ? <><Pause size={13}/> PAUSE</> : <><Play size={13}/> RESUME</>}</button>}
      {replay && <button type="button" className="button-quiet" onClick={reset}><RotateCcw size={13}/> RESET</button>}
      {replay && <span className="threat-clock" role="status">t = +{clock.toFixed(0)} s of {replay.duration_s.toFixed(0)} s{done ? ' · replay ended' : playing ? '' : ' · paused'}</span>}
    </div>
    {replay && <div className="threat-cards">{ordered.map(item => <ThreatCard key={item.aircraftId} aircraftId={item.aircraftId} role={item.role} assessment={item.assessment}
      state={item.assessment ? 'ok' : 'no_data'} history={item.history} canRecord={canRecord} decisions={decisions}
      sourceLine={`Source: offline replay (${replay.experiment_name}) · last update ${lastUpdate ? new Date(lastUpdate).toLocaleTimeString() : 'none yet'}`}
      onConfirm={(action, candidate) => record(item.aircraftId, action, candidate)}/>)}</div>}
    {replay && ordered.some(item => item.tiers.length > 0) && <div className="threat-strips" aria-label="Tier over time">{ordered.map(item => <div key={item.aircraftId} className="threat-strip">
      <b>{item.aircraftId}</b><span role="img" aria-label={`${item.aircraftId} tiers over time: ${item.tiers.length ? item.tiers[item.tiers.length - 1] : 'none'} now`}>{item.tiers.map((tier, index) => <i key={index} title={tier} style={{ background: TIER_COLORS[tier] }}/>)}</span></div>)}</div>}
    {done && !evaluation && !error && <p className="mc-note" role="status">Computing the evaluation summary…</p>}
    {evaluation && <section className="threat-evaluation" aria-label="Evaluation summary">
      <h4>Evaluation summary (revealed after the replay)</h4>
      <p className="mc-note">{GROUND_TRUTH_NOTE}</p>
      <ul>{evaluation.attacks.map((attack, index) => <li key={index}>Scheduled: {attack.type.replace(/_/g, ' ')} on {attack.target_aircraft} from +{attack.start_s} s for {attack.duration_s} s (magnitude {attack.magnitude}).</li>)}
        {evaluation.attacks.length === 0 && <li>Baseline: no attack was scheduled.</li>}</ul>
      <table className="mc-table" aria-label="Evaluation per aircraft"><thead><tr><th>Aircraft</th><th>Attacked</th><th>Detection latency</th><th>False alarms</th><th>Max position error</th><th>Highest tier</th></tr></thead>
        <tbody>{evaluation.aircraft.map(row => <tr key={row.aircraft_id}><td>{row.aircraft_id}</td><td>{row.attacked ? (row.detected ? 'yes, detected' : 'yes, missed') : 'no'}</td>
          <td>{row.detection_latency_s === null ? 'n/a' : `${row.detection_latency_s} s`}</td><td>{row.false_alarms} ({row.false_alarms_per_hour}/h)</td><td>{row.max_gps_position_error_m} m</td><td>{row.max_tier}</td></tr>)}</tbody></table>
      <small className="mc-note">Not measured: {Object.entries(evaluation.not_measured).map(([name, why]) => `${name} (${why})`).join('; ')}.</small>
    </section>}
  </section>
}
