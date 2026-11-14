import { useEffect, useMemo, useRef, useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import { api } from '../services/api'
import { TIER_COLORS, type ActionId, type DecisionRecord, type ModelInfo, type Tier } from './attackLabTypes'
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

/** Mission Control threat monitor. Reuses the offline replay session and the same assessment engine; it never invents values. */
export function ThreatMonitor({ canRecord, onTierTransitions, onTiers }: Props) {
  const session = useThreatSession()
  const [model, setModel] = useState<ModelInfo | null>(null)
  const [decisions, setDecisions] = useState<DecisionRecord[]>([])
  const [now, setNow] = useState(() => Date.now())
  const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({})
  const previousTiers = useRef<Record<string, Tier>>({})

  useEffect(() => {
    let active = true
    void api.researchModelInfo().then(info => { if (active) setModel(info) }).catch(() => undefined)
    void api.researchDecisions().then(list => { if (active) setDecisions(list) }).catch(() => undefined)
    const timer = window.setInterval(() => setNow(Date.now()), CLOCK_MS)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  const leaked = useMemo(() => (session ? findGroundTruthKeys(session) : []), [session])
  const freshness = sessionFreshness(leaked.length ? null : session, now)
  const cards = useMemo(() => (leaked.length || !session ? [] : Object.values(session.aircraft)
    .sort((a, b) => (b.assessment?.threat_score ?? -1) - (a.assessment?.threat_score ?? -1))), [session, leaked])

  useEffect(() => {
    if (!session || leaked.length) return
    const alerts: Alert[] = []
    const tiers: Record<string, Tier> = {}
    for (const item of Object.values(session.aircraft)) {
      const tier = item.assessment?.tier
      if (!tier) continue
      tiers[item.aircraftId] = tier
      const before = previousTiers.current[item.aircraftId]
      if (before && before !== tier) {
        const message = `${item.aircraftId} threat tier ${before} → ${tier} (score ${item.assessment!.threat_score.toFixed(0)}).`
        alerts.push({ id: `threat:${item.aircraftId}:${before}>${tier}:${session.updatedAt}`, severity: tier === 'Critical' ? 'critical' : tier === 'High' || tier === 'Medium' ? 'warning' : 'info', source: 'threat monitor', message })
        if (tier !== 'Critical') setAcknowledged(current => ({ ...current, [item.aircraftId]: false }))
      }
      previousTiers.current[item.aircraftId] = tier
    }
    onTiers(tiers)
    if (alerts.length) onTierTransitions(alerts)
  }, [session, leaked, onTierTransitions, onTiers])

  const top = cards.find(item => item.assessment)
  const critical = cards.filter(item => item.assessment?.tier === 'Critical' && !acknowledged[item.aircraftId])
  const record = async (aircraftId: string, action: ActionId, candidate: string | null) => {
    const assessment = session?.aircraft[aircraftId]?.assessment
    const saved = await api.recordDecision({
      aircraft_id: aircraftId, action, handover_candidate: candidate, source: 'offline_replay', source_name: session?.sourceName ?? null,
      tier: assessment?.tier ?? null, threat_score: assessment?.threat_score ?? null,
    })
    setDecisions(current => [saved, ...current].slice(0, 50))
  }
  const sourceLine = session
    ? `Source: offline replay (${session.sourceName}) · last update ${new Date(session.updatedAt).toLocaleTimeString()}${session.state === 'finished' ? ' · replay ended' : ''}`
    : 'Source: none'

  return <section className="panel mc-panel threat-monitor" aria-label="Threat monitor">
    <div className="panel-heading"><div><span className="eyebrow">THREAT MONITOR</span><h3><ShieldAlert size={15}/> GPS threat assessment</h3></div>
      {top?.assessment && freshness !== 'no_data' && <span className="count-tag" style={{ color: TIER_COLORS[top.assessment.tier] }}>FLEET: {top.assessment.tier.toUpperCase()} · {top.aircraftId}</span>}</div>
    {critical.length > 0 && <div className="threat-banner" role="alert"><b>CRITICAL</b><span>{critical.map(item => item.aircraftId).join(', ')}: threat tier Critical. Review the recommended action below.</span>
      <button type="button" className="button-secondary" onClick={() => setAcknowledged(current => ({ ...current, ...Object.fromEntries(critical.map(item => [item.aircraftId, true])) }))}>ACKNOWLEDGE</button></div>}
    {leaked.length > 0 && <p role="alert" className="scenario-library-error">Threat data was rejected because it contained ground-truth fields.</p>}
    {freshness === 'no_data' || cards.length === 0 ? <div className="mc-empty">
      <span>No threat data source.</span>
      <small>{model && model.live_residual_inputs_available === false ? 'Live residual inputs not available; showing offline replay only.' : 'Live residual inputs not available; showing offline replay only.'} Start a replay under Research → Threat assessment (offline replay) and it appears here.</small>
    </div> : <>
      <p className="mc-note" role="status">{sourceLine}{freshness === 'stale' ? ' · STALE: no recent update' : ''}</p>
      <div className="threat-cards">{cards.map(item => <ThreatCard key={item.aircraftId} aircraftId={item.aircraftId} role={item.role} assessment={item.assessment}
        state={!item.assessment ? 'no_data' : freshness === 'stale' ? 'stale' : 'ok'} history={item.history} sourceLine={sourceLine} canRecord={canRecord} decisions={decisions}
        highlighted={item.assessment?.tier !== undefined && item.assessment.tier !== 'Low'} onConfirm={(action, candidate) => record(item.aircraftId, action, candidate)}/>)}</div>
    </>}
  </section>
}
