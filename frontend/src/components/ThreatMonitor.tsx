import { useEffect, useMemo, useRef, useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import { api } from '../services/api'
import { TIER_COLORS, type ActionId, type DecisionRecord, type LiveAircraft, type LiveThreat, type ModelInfo, type Tier } from './attackLabTypes'
import { ThreatCard } from './ThreatCard'
import { findGroundTruthKeys, sessionFreshness, useThreatSession } from './threatSession'
import type { Alert } from './missionControlModel'
import './threat-panel.css'

type Props = {
  canRecord: boolean
  /** One entry per tier transition (never per poll); the dashboard merges them into its alerts list. */
  onTierTransitions: (alerts: Alert[]) => void
  onTiers: (tiers: Record<string, Tier>) => void
}

const CLOCK_MS = 5000
/** The server computes at most once a second; the panel asks at the same calm rate. */
export const LIVE_POLL_MS = 1000
const HISTORY_POINTS = 60
/** No answer from the backend for this long: the live values are marked stale instead of left looking current. */
const LIVE_STALE_MS = 5000

const rate = (value: number | null | undefined) => (value == null ? '—' : `${value.toFixed(value >= 10 ? 0 : 1)} Hz`)
const age = (seconds: number | null | undefined) => (seconds == null ? 'an unknown time' : seconds < 120 ? `${seconds.toFixed(0)} s` : `${Math.round(seconds / 60)} min`)
/** States with something to show: flying now, lost in flight, or landed a short while ago (the backend drops both after a while). */
const SHOWN_STATES = ['live', 'warming_up', 'inputs_missing', 'model_unavailable', 'link_lost']

/** One live aircraft that has no score to show: collecting data, an input missing, or the link lost. */
function LiveNotice({ item }: { item: LiveAircraft }) {
  const required = item.missing_inputs.filter(entry => entry.required)
  if (item.state === 'warming_up') {
    const collected = item.warming_up?.seconds_collected ?? 0
    const needed = item.warming_up?.seconds_needed ?? 10
    return <p className="threat-live-note" role="status"><b>{item.aircraft_id}</b> Collecting data: {collected.toFixed(0)} of {needed.toFixed(0)} s. The first assessment follows.</p>
  }
  if (item.state === 'inputs_missing') {
    return <div className="threat-live-note amber" role="alert"><b>{item.aircraft_id} · INPUT MISSING</b>
      <ul>{required.map(entry => <li key={entry.input}><b>{entry.title}</b>: {entry.detail}. {entry.meaning}</li>)}</ul></div>
  }
  if (item.state === 'model_unavailable') {
    return <p className="threat-live-note amber" role="alert"><b>{item.aircraft_id}</b> No trained threat model is loaded on the backend, so no score can be computed.</p>
  }
  return null
}

/** Mission Control threat monitor: live assessments computed by the backend from PX4 SITL telemetry, or an offline replay. It never invents values. */
export function ThreatMonitor({ canRecord, onTierTransitions, onTiers }: Props) {
  const session = useThreatSession()
  const [model, setModel] = useState<ModelInfo | null>(null)
  const [live, setLive] = useState<LiveThreat | null>(null)
  const [liveAt, setLiveAt] = useState(0)
  const [decisions, setDecisions] = useState<DecisionRecord[]>([])
  const [now, setNow] = useState(() => Date.now())
  const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({})
  const [showReplay, setShowReplay] = useState(false)
  const previousTiers = useRef<Record<string, Tier>>({})
  const history = useRef<Record<string, number[]>>({})
  const lastComputed = useRef<Record<string, string>>({})

  useEffect(() => {
    let active = true
    void api.researchModelInfo().then(info => { if (active) setModel(info) }).catch(() => undefined)
    void api.researchDecisions().then(list => { if (active) setDecisions(list) }).catch(() => undefined)
    const timer = window.setInterval(() => setNow(Date.now()), CLOCK_MS)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  // Live assessments: one request a second, the previous answer stays on screen until the next one arrives (no flicker).
  useEffect(() => {
    let active = true
    let busy = false
    const poll = async () => {
      if (busy) return
      busy = true
      try {
        const payload = await api.researchLive()
        if (!active) return
        if (findGroundTruthKeys(payload).length) return   // never show data that carries ground truth
        for (const item of payload.aircraft) {
          const stamp = item.assessment?.computed_at
          if (item.state === 'live' && item.assessment && stamp && lastComputed.current[item.aircraft_id] !== stamp) {
            lastComputed.current[item.aircraft_id] = stamp
            history.current[item.aircraft_id] = [...(history.current[item.aircraft_id] ?? []), item.assessment.threat_score].slice(-HISTORY_POINTS)
          }
          if (!item.armed) { delete history.current[item.aircraft_id]; delete lastComputed.current[item.aircraft_id] }
        }
        setLive(payload); setLiveAt(Date.now())
      } catch { /* keep the last answer; it is marked stale by age below */ } finally { busy = false }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), LIVE_POLL_MS)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  const leaked = useMemo(() => (session ? findGroundTruthKeys(session) : []), [session])
  const freshness = sessionFreshness(leaked.length ? null : session, now)
  const replayCards = useMemo(() => (leaked.length || !session ? [] : Object.values(session.aircraft)
    .sort((a, b) => (b.assessment?.threat_score ?? -1) - (a.assessment?.threat_score ?? -1))), [session, leaked])

  const flying = useMemo(() => (live?.aircraft ?? []).filter(item => SHOWN_STATES.includes(item.state) || (item.state === 'landed' && item.assessment)), [live])
  const flightRunning = flying.length > 0
  const liveStale = flightRunning && liveAt > 0 && now - liveAt > LIVE_STALE_MS
  const replayActive = !flightRunning && replayCards.length > 0 && (freshness === 'ok' || showReplay)

  // tier transitions (one alert per change) for whichever source is on screen
  useEffect(() => {
    const alerts: Alert[] = []
    const tiers: Record<string, Tier> = {}
    const seen = (id: string, tier: Tier, score: number, stamp: string | number) => {
      tiers[id] = tier
      const before = previousTiers.current[id]
      if (before && before !== tier) {
        alerts.push({ id: `threat:${id}:${before}>${tier}:${stamp}`, severity: tier === 'Critical' ? 'critical' : tier === 'High' || tier === 'Medium' ? 'warning' : 'info',
          source: 'threat monitor', message: `${id} threat tier ${before} → ${tier} (score ${score.toFixed(0)}).` })
        if (tier !== 'Critical') setAcknowledged(current => ({ ...current, [id]: false }))
      }
      previousTiers.current[id] = tier
    }
    if (flightRunning) {
      for (const item of flying) if (item.state === 'live' && item.assessment) seen(item.aircraft_id, item.assessment.tier, item.assessment.threat_score, item.assessment.computed_at ?? '')
    } else if (session && !leaked.length) {
      for (const item of Object.values(session.aircraft)) if (item.assessment) seen(item.aircraftId, item.assessment.tier, item.assessment.threat_score, session.updatedAt)
    }
    onTiers(tiers)
    if (alerts.length) onTierTransitions(alerts)
  }, [flying, flightRunning, session, leaked, onTierTransitions, onTiers])

  const rule = live?.handover_rule?.message ?? model?.handover_rule?.message
  const recordLive = async (aircraftId: string, action: ActionId, candidate: string | null) => {
    const assessment = flying.find(item => item.aircraft_id === aircraftId)?.assessment
    // The server logs its own score and tier for a live decision; the values here are only what the operator was looking at.
    const saved = await api.recordDecision({ aircraft_id: aircraftId, action, handover_candidate: candidate, source: 'live_telemetry', source_name: 'PX4 SITL',
      tier: assessment?.tier ?? null, threat_score: assessment?.threat_score ?? null })
    setDecisions(current => [saved, ...current].slice(0, 50))
  }
  const recordReplay = async (aircraftId: string, action: ActionId, candidate: string | null) => {
    const assessment = session?.aircraft[aircraftId]?.assessment
    const saved = await api.recordDecision({
      aircraft_id: aircraftId, action, handover_candidate: candidate, source: 'offline_replay', source_name: session?.sourceName ?? null,
      tier: assessment?.tier ?? null, threat_score: assessment?.threat_score ?? null,
      ...(session?.replayId ? { replay_id: session.replayId, replay_t_s: session.clockS ?? 0 } : {}),
    })
    setDecisions(current => [saved, ...current].slice(0, 50))
  }

  const liveTop = [...flying].filter(item => item.state === 'live' && item.assessment).sort((a, b) => b.assessment!.threat_score - a.assessment!.threat_score)[0]
  const replayTop = replayCards.find(item => item.assessment)
  const top = flightRunning ? (liveTop ? { id: liveTop.aircraft_id, tier: liveTop.assessment!.tier } : null)
    : replayActive && replayTop?.assessment ? { id: replayTop.aircraftId, tier: replayTop.assessment.tier } : null
  const critical = flightRunning ? flying.filter(item => item.state === 'live' && item.assessment?.tier === 'Critical' && !acknowledged[item.aircraft_id]).map(item => item.aircraft_id)
    : replayActive ? replayCards.filter(item => item.assessment?.tier === 'Critical' && !acknowledged[item.aircraftId]).map(item => item.aircraftId) : []
  const replayLine = session
    ? `Source: offline replay (${session.sourceName}) · last update ${new Date(session.updatedAt).toLocaleTimeString()}${session.state === 'finished' ? ' · replay ended' : ''}`
    : 'Source: none'
  const idle = !flightRunning && !replayActive

  return <section className={`panel mc-panel threat-monitor ${idle ? 'idle' : ''}`} aria-label="Threat monitor">
    <div className="panel-heading"><div><span className="eyebrow">THREAT MONITOR</span><h3><ShieldAlert size={15}/> GPS threat assessment</h3></div>
      {top && !liveStale && <span className="count-tag" style={{ color: TIER_COLORS[top.tier] }}>FLEET: {top.tier.toUpperCase()} · {top.id}</span>}
      {idle && <p className="threat-waiting" role="status">
        <span>Waiting for a flight.</span>
        {live?.unavailable_reason ? <small>{live.unavailable_reason}</small> : live && !live.model_available ? <small>No trained threat model is loaded on the backend.</small>
          : <small>Live assessment starts when an aircraft is armed.</small>}
        {replayCards.length > 0 && session && <button type="button" className="button-quiet" onClick={() => setShowReplay(true)}>Last offline replay: {session.sourceName}{freshness === 'ended' ? ' (ended)' : freshness === 'stale' ? ' (stopped)' : ''} · VIEW</button>}
      </p>}
    </div>
    {critical.length > 0 && <div className="threat-banner" role="alert"><b>CRITICAL</b><span>{critical.join(', ')}: threat tier Critical. Review the recommended action below.</span>
      <button type="button" className="button-secondary" onClick={() => setAcknowledged(current => ({ ...current, ...Object.fromEntries(critical.map(id => [id, true])) }))}>ACKNOWLEDGE</button></div>}
    {leaked.length > 0 && <p role="alert" className="scenario-library-error">Threat data was rejected because it contained ground-truth fields.</p>}

    {flightRunning && <>
      <p className="mc-note" role="status">Source: live telemetry (PX4 SITL), computed on the backend about once a second{liveStale ? ` · STALE: no answer from the backend for ${Math.round((now - liveAt) / 1000)} s` : ''}</p>
      <div className="threat-cards">{flying.map(item => {
        const optional = item.missing_inputs.filter(entry => !entry.required)
        const quality = item.data_quality
        const sourceLine = `Source: live telemetry · ${item.assessment?.computed_at ? `computed ${new Date(item.assessment.computed_at).toLocaleTimeString()}` : 'no assessment yet'}`
          + (quality ? ` · window ${quality.window_seconds.toFixed(0)} s` : '')
          + ` · GPS ${rate(item.rates_hz.GPS_RAW_INT)} · velocity ${rate(item.rates_hz.LOCAL_POSITION_NED)} · IMU ${rate(item.rates_hz.HIGHRES_IMU)}`
        if (item.state === 'live' || item.state === 'link_lost' || item.state === 'landed') {
          const lost = item.state === 'link_lost'
          const landed = item.state === 'landed'
          const lastText = item.assessment
            ? `The last assessment (${item.assessment.tier}, score ${item.assessment.threat_score.toFixed(0)}) is ${age(item.assessment_age_s)} old and is not updating.`
            : 'No assessment had been computed yet.'
          const staleNote = landed ? `Landed ${age(item.landed_age_s)} ago${item.link.status === 'connected' ? '' : ' (the link has since dropped)'}. ${lastText}`
            : lost ? `Link lost in flight: no telemetry for ${age(item.link.telemetry_age_s)}. ${lastText}`
            : 'The backend stopped answering; the last number is hidden because it is stale.'
          return <div key={item.aircraft_id} className="threat-live-item">
            <ThreatCard aircraftId={item.aircraft_id} role={item.role} assessment={item.assessment} state={landed ? 'landed' : lost || liveStale ? 'stale' : 'ok'} staleNote={staleNote}
              history={history.current[item.aircraft_id] ?? []} sourceLine={sourceLine} canRecord={canRecord && !lost && !landed} decisions={decisions} handoverRule={rule}
              highlighted={!lost && !landed && item.assessment?.tier !== undefined && item.assessment.tier !== 'Low'} onConfirm={(action, candidate) => recordLive(item.aircraft_id, action, candidate)}/>
            {item.state === 'live' && optional.length > 0 && <p className="threat-live-note amber" role="status"><b>Input missing (score still computed):</b> {optional.map(entry => `${entry.title} — ${entry.meaning}`).join(' ')}</p>}
            {item.state === 'live' && quality && quality.flags.length > 0 && <p className="threat-live-note" role="status">{quality.flags.join(' ')}</p>}
          </div>
        }
        return <LiveNotice key={item.aircraft_id} item={item}/>
      })}</div>
    </>}

    {replayActive && <>
      <p className="mc-note" role="status">{replayLine}{freshness === 'stale' ? ' · STALE: no recent update' : ''}
        {showReplay && freshness !== 'ok' && <button type="button" className="button-quiet threat-hide" onClick={() => setShowReplay(false)}>HIDE</button>}</p>
      <div className="threat-cards">{replayCards.map(item => <ThreatCard key={item.aircraftId} aircraftId={item.aircraftId} role={item.role} assessment={item.assessment}
        state={!item.assessment ? 'no_data' : freshness === 'stale' ? 'stale' : 'ok'} history={item.history} sourceLine={replayLine} canRecord={canRecord} decisions={decisions} handoverRule={rule}
        highlighted={item.assessment?.tier !== undefined && item.assessment.tier !== 'Low'} onConfirm={(action, candidate) => recordReplay(item.aircraftId, action, candidate)}/>)}</div>
    </>}
  </section>
}
