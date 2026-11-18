import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import type { ScenarioSummary } from '../services/auth'
import type { QuickRules } from './quickMission'
import { normalizePlan } from './missionPlan'

/** What one saved mission is, in the words the list shows. Shared by the list and the tests. */
export function describeSavedMission(item: ScenarioSummary, rules: QuickRules | null) {
  const intent = item.latest_version.intent
  const quick = intent.quick_source ?? null
  const plan = intent.mission_plan ? normalizePlan(intent.mission_plan) : null
  const typeLabel = quick ? rules?.types.find(type => type.id === quick.mission_type)?.label ?? quick.mission_type : intent.schema_version === 2 ? 'Scenario Lab mission' : 'Legacy (read-only)'
  const place = quick?.place_name ?? (quick ? (quick.where_source === 'aircraft' ? 'the aircraft’s position' : 'a map point') : plan?.operating_area ? 'an operating area' : 'no place yet')
  const status = item.latest_version.approval?.status ?? 'DRAFT'
  return { quick, typeLabel, place, aircraft: plan?.aircraft.length ?? 0, status, quickMade: Boolean(quick) }
}

type Props = {
  scenarios: ScenarioSummary[]
  rules: QuickRules | null
  selectedId: string
  /** Called with the scenario id the user picked (the page decides whether to warn about unsaved changes first). */
  onPick: (scenarioId: string) => void
  disabled?: boolean
}

const STATUSES = ['DRAFT', 'PENDING', 'APPROVED', 'REJECTED', 'CHANGES_REQUESTED']
const when = (value: string) => new Date(value).toLocaleString()

/** "Open a saved mission": the same scenarios Scenario Lab lists (same visibility for GENERAL and OPERATOR), with a search box and the status filter. */
export function SavedMissionPicker({ scenarios, rules, selectedId, onPick, disabled = false }: Props) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const rows = useMemo(() => scenarios.map(item => ({ item, ...describeSavedMission(item, rules) })), [scenarios, rules])
  const shown = rows.filter(row => (!status || row.status === status) && (!search.trim() || row.item.title.toLowerCase().includes(search.trim().toLowerCase())))
  return <section className="saved-picker" aria-label="Open a saved mission">
    <div className="saved-picker-head"><b>OPEN A SAVED MISSION</b><small>{scenarios.length} saved</small></div>
    <div className="saved-picker-filters">
      <label className="saved-picker-search"><Search size={12}/><input aria-label="Search saved missions" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search by name"/></label>
      <select aria-label="Filter saved missions by status" value={status} onChange={event => setStatus(event.target.value)}>
        <option value="">All statuses</option>{STATUSES.map(item => <option key={item} value={item}>{item.toLowerCase().replace(/_/g, ' ')}</option>)}</select>
    </div>
    {!shown.length ? <p className="planner-empty">{scenarios.length ? 'No saved mission matches these filters.' : 'No saved missions yet. Pick a type and a place below, then save.'}</p>
      : <ul className="saved-picker-list" aria-label="Saved missions">{shown.map(row => <li key={row.item.id}>
        <button type="button" className={row.item.id === selectedId ? 'selected' : ''} disabled={disabled} aria-pressed={row.item.id === selectedId} onClick={() => onPick(row.item.id)}>
          <b>{row.item.title}</b>
          <span>{row.typeLabel} · {row.place} · {row.aircraft} aircraft</span>
          <small>{row.status.toLowerCase().replace(/_/g, ' ')} · v{row.item.latest_version.version_number} · changed {when(row.item.updated_at)}</small>
        </button></li>)}</ul>}
  </section>
}
