import { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { Reconfiguration } from './attackLabTypes'

const STATUS_TEXT: Record<string, string> = {
  verifying: 'Checking…', completed: 'Completed', degraded: 'Degraded', failed: 'Failed',
}
const CHECK_LABEL: Record<string, string> = { new_primary_flying: 'New primary flying its route', previous_aircraft_safe: 'Previous aircraft safe', mission_progressing: 'Mission progressing' }

/** What the aircraft really did after a hand-over or a primary switch: measured checks and one outcome (completed, degraded or failed). */
export function ContinuityList({ items }: { items: Reconfiguration[] }) {
  return <ul className="continuity-list" aria-label="Mission continuity">{items.map(item => <li key={item.id} className={`continuity ${item.status}`}>
    <b>{item.kind === 'handover' ? 'Hand over' : 'Primary switch'}: {item.previous_primary} → {item.new_primary}</b> <span className="continuity-status">{STATUS_TEXT[item.status] ?? item.status}</span>
    <ul>{Object.entries(item.checks).map(([key, check]) => <li key={key}><small>{check === null ? '○' : check.ok ? '✓' : '✗'} <b>{CHECK_LABEL[key]}:</b> {check?.detail ?? 'not decided yet'}</small></li>)}</ul>
    {item.notes.map((text, index) => <small key={index} className="continuity-note">{text}</small>)}
  </li>)}</ul>
}

/** Polls the backend for the latest reconfigurations; renders nothing when there are none. */
export function ContinuityPanel() {
  const [items, setItems] = useState<Reconfiguration[]>([])
  useEffect(() => {
    let stopped = false
    const read = () => { void Promise.resolve().then(() => api.reconfigurations()).then(value => { if (!stopped) setItems(value.slice(0, 2)) }).catch(() => undefined) }
    read()
    const timer = window.setInterval(() => { if (typeof document === 'undefined' || !document.hidden) read() }, 3000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [])
  if (items.length === 0) return null
  return <section className="threat-continuity" aria-label="Mission continuity after a response"><span className="eyebrow">MISSION CONTINUITY · MEASURED AFTER THE RESPONSE</span><ContinuityList items={items}/></section>
}
