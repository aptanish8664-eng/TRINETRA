import { useMemo, useState } from 'react'
import { AlertTriangle, Navigation, Rocket, Save, SlidersHorizontal, Trash2, Undo2 } from 'lucide-react'
import type { GeographicPoint, GeographicUavAssignment, GeographicZone } from '../services/auth'
import { MissionPlannerMap } from './MissionPlannerMap'
import { MapErrorBoundary } from './MapErrorBoundary'
import { areaPreviewVertices } from './missionPlan'
import { EMPTY_ROW, effectiveRole, type QuickAircraftRow, type Role } from './quickMission'
import type { useQuickPlanner } from './useQuickPlanner'
import './quick-mission.css'

type Props = {
  planner: ReturnType<typeof useQuickPlanner>
  /** The name field (shared with the draft the assistant and Save use). */
  name: string
  nameEdited: boolean
  onName: (value: string) => void
  disabled: boolean
  /** One Cesium viewer per page: the map exists only while this page is shown. */
  showMap: boolean
  assistantWaiting: boolean
  /** The assistant is waiting for a place: the click answers it instead of editing the form. */
  onAssistantPick: (point: GeographicPoint, source: 'map' | 'place', place?: string | null) => void
  onActivate: () => void
  activating: boolean
  saveLabel: string
  onSave: () => void
  deploy: { allowed: boolean; reason: string; onDeploy: () => void; busy: boolean; label: string }
  /** A saved mission is open: Save as a new mission and Clear appear. */
  saved: { open: boolean; onSaveAsNew: () => void; onClear: () => void }
  fineTune: { enabled: boolean; note: string; onClick: () => void }
}

const asPoint = (point: GeographicPoint) => ({ latitude: point.latitude, longitude: point.longitude })
const marker = (isDefault: boolean) => <span className={`quick-marker ${isDefault ? 'auto' : 'typed'}`}>{isDefault ? 'default' : 'typed'}</span>

/** The Quick Mission page body: type dropdown, where (clicks, search, aircraft), derived settings with markers, the plan in words, one map. */
export function QuickMissionForm({ planner, name, nameEdited, onName, disabled, showMap, assistantWaiting, onAssistantPick, onActivate, activating, saveLabel, onSave, saved, deploy, fineTune }: Props) {
  const { rules, inputs, setInputs, result, busy, error, fleet } = planner
  const type = rules?.types.find(item => item.id === inputs.type) ?? null
  const available = fleet.length
  const capacity = rules?.simulation_capacity ?? null
  const planMax = rules?.plan_max_aircraft ?? 999
  const guardOver = rules?.typo_guard_over ?? 50
  const aircraftPosition = fleet[0]?.position ?? null
  const summary = result?.summary ?? null
  const speedInfo = summary?.speed_info ?? null
  const research = rules?.research_speed_mps ?? 8
  const kind = type?.where
  const count = Math.max(1, Math.floor(inputs.droneCount) || 1)
  // The number field: what is typed, the typo guard (asked once per number, never a limit) and which rows show their Advanced overrides.
  const [countText, setCountText] = useState<string | null>(null)
  const [guard, setGuard] = useState<number | null>(null)
  const [confirmed, setConfirmed] = useState<number | null>(null)
  const [openRows, setOpenRows] = useState<Record<number, boolean>>({})
  const shownName = nameEdited ? name : result?.suggested_name ?? ''

  const zones: GeographicZone[] = useMemo(() => {
    const area = result?.plan.operating_area
    return area ? [{ id: 'quick-area', name: 'Operating area', vertices: areaPreviewVertices(area) }] : []
  }, [result])
  const assignments: GeographicUavAssignment[] = useMemo(() => {
    const first = result?.plan.aircraft[0]
    const route = first?.route?.waypoints ?? []
    if (first && route.length) {
      return [{ uav_id: 'quick', role: first.role ?? '', task: '', zone_id: null, zone_ids: [],
        start_location: { latitude: route[0].latitude, longitude: route[0].longitude },
        destination: route.length > 1 ? { latitude: route[route.length - 1].latitude, longitude: route[route.length - 1].longitude } : null,
        waypoints: route.length > 2 ? route.slice(1, -1).map(item => ({ latitude: item.latitude, longitude: item.longitude })) : [], constraints: [], confirmed: false }]
    }
    return inputs.points.length ? [{ uav_id: 'quick', role: '', task: '', zone_id: null, zone_ids: [], start_location: null, destination: null, waypoints: inputs.points.map(asPoint), constraints: [], confirmed: false }] : []
  }, [result, inputs.points])

  const pick = (point: GeographicPoint, source: 'map' | 'place', place?: string | null) => {
    if (assistantWaiting) { onAssistantPick(point, source, place); return }
    if (!type) return
    if (kind === 'points') setInputs({ points: [...inputs.points, asPoint(point)].slice(0, rules?.point_limit ?? 20), source, placeName: place ?? inputs.placeName })
    else setInputs({ where: asPoint(point), source, placeName: place ?? null })
  }
  const whereText = !type ? 'Choose a mission type first.'
    : kind === 'points' ? (inputs.points.length ? `${inputs.points.length} point${inputs.points.length === 1 ? '' : 's'} clicked. Click again to add more.` : 'Click the place on the map (the first waypoint).')
      : inputs.where ? `${inputs.source === 'aircraft' ? 'The aircraft’s position' : inputs.placeName ?? 'The map point'} · ${inputs.where.latitude.toFixed(5)}, ${inputs.where.longitude.toFixed(5)}`
        : 'Click the place on the map'
  const farNote = result?.warnings.find(item => item.code === 'aircraft_far')
  const otherWarnings = (result?.warnings ?? []).filter(item => item.code !== 'aircraft_far' && item.code !== 'speed_above_validated')
  const num = (value: string) => (value === '' ? null : Number(value))
  const rows = inputs.aircraft
  /** Changes one row and keeps every other row exactly as typed (rows beyond the count are kept too, so a smaller count loses nothing). */
  const setRow = (index: number, patch: Partial<QuickAircraftRow>) => {
    const next = Array.from({ length: Math.max(rows.length, index + 1) }, (_, i) => rows[i] ?? EMPTY_ROW)
    next[index] = { ...next[index], ...patch }
    setInputs({ aircraft: next })
  }
  const applyCount = (value: number) => { setGuard(null); setCountText(null); setInputs({ droneCount: value }) }
  const typeCount = (text: string) => {
    setCountText(text)
    const value = Number(text)
    if (!text || !Number.isInteger(value) || value < 1 || value > planMax) return
    if (value > guardOver && confirmed !== value) { setGuard(value); return }
    applyCount(value)
  }
  const countInvalid = countText !== null && countText !== '' && (!Number.isInteger(Number(countText)) || Number(countText) < 1 || Number(countText) > planMax)
  const lineOf = (index: number) => summary?.aircraft[index]?.line ?? ''

  if (!rules) return <section className="scenario-lab-section panel quick-mission"><p className="planner-empty">Loading the quick mission rules…</p></section>
  return <section className="quick-page quick-mission" aria-labelledby="quick-mission-heading">
    <div className="quick-layout">
      <div className="quick-controls panel">
        <header><span className="eyebrow">QUICK MISSION</span><h3 id="quick-mission-heading">Pick a type, click the place, check the plan</h3></header>
        <label className="quick-field">MISSION TYPE
          <select aria-label="Mission type" value={inputs.type ?? ''} disabled={disabled} onChange={event => setInputs({ type: event.target.value || null })}>
            <option value="">Choose a mission type</option>
            {rules.types.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
          <small>{type?.summary ?? 'Area survey, search spiral, relief delivery, perimeter patrol, waypoint route, or waypoints then drop.'}</small></label>

        <section className="quick-explain" aria-label="What the aircraft will do">
          <b>WHAT THE AIRCRAFT WILL DO</b>
          {!summary ? <p className="planner-empty">Pick a mission type and a place to see the plan.</p> : <>
            {summary.explanation.slice(0, 3).map((item, index) => <div key={item.uav_id} className="quick-explain-aircraft">
              {summary.explanation.length > 1 && <b>{item.uav_id} · {item.role}</b>}
              <ol aria-label={index === 0 ? 'Primary aircraft steps' : `${item.uav_id} steps`}>{item.steps.map(step => <li key={step}>{step}</li>)}</ol></div>)}
            {summary.explanation.length > 3 && <p className="quick-limits">The other {summary.explanation.length - 3} aircraft are described in the aircraft list below.</p>}
            <p className="quick-limits">Limits (auto): max distance {summary.limits.max_distance_from_start_m} m · max altitude {summary.limits.max_altitude_m} m · max duration {summary.limits.max_duration_minutes} min.</p>
            {summary.drones > 1 && <p className="quick-warning" role="note"><AlertTriangle size={12}/> Needs {summary.drones} verified aircraft to fly; multi-aircraft execution is not available yet, so Start in simulation will say so.</p>}
            {otherWarnings.map(item => <p className="quick-warning" role="note" key={`${item.code}-${item.uav_id ?? ''}`}><AlertTriangle size={12}/> {item.message}</p>)}</>}
        </section>

        <div className="quick-where"><b>WHERE</b><span aria-live="polite">{assistantWaiting ? 'Click the place on the map for the assistant.' : whereText}</span>
          <div className="quick-where-actions">
            {kind === 'centre' && <button type="button" className="button-secondary" disabled={disabled || !aircraftPosition} onClick={() => aircraftPosition && setInputs({ where: aircraftPosition, source: 'aircraft', placeName: null })}
              title={aircraftPosition ? undefined : 'No aircraft position is known yet.'}><Navigation size={13}/> USE THE AIRCRAFT’S POSITION</button>}
            {kind === 'points' && <>
              <button type="button" className="button-quiet" disabled={!inputs.points.length} onClick={() => setInputs({ points: inputs.points.slice(0, -1), dropIndex: null })}><Undo2 size={13}/> UNDO LAST POINT</button>
              <button type="button" className="button-quiet" disabled={!inputs.points.length} onClick={() => setInputs({ points: [], dropIndex: null })}><Trash2 size={13}/> CLEAR POINTS</button></>}
          </div>
          {inputs.type === 'waypoints_then_drop' && inputs.points.length > 1 && <label className="quick-field">DROP AT
            <select aria-label="Drop at" value={inputs.dropIndex ?? ''} onChange={event => setInputs({ dropIndex: event.target.value === '' ? null : Number(event.target.value) })}>
              <option value="">the last waypoint (default)</option>{inputs.points.map((_, index) => <option key={index} value={index}>waypoint {index + 1}</option>)}</select></label>}
          {kind !== 'centre' && type?.start === 'aircraft' && <small>The aircraft starts from where it is and comes back to land.</small>}
        </div>
        {farNote && <div className="quick-note" role="note"><AlertTriangle size={13}/> {farNote.message}</div>}

        <div className="quick-settings">
          {kind === 'centre' && <label className="quick-field">{(type?.size_label ?? 'Size (m)').toUpperCase()} {marker(inputs.sizeM === null)}
            <input type="number" aria-label="Size" min={rules.size_range_m[0]} max={rules.size_range_m[1]} value={inputs.sizeM ?? rules.defaults.size_m} onChange={event => setInputs({ sizeM: num(event.target.value) })}/></label>}
          <label className="quick-field">HOW MANY UAVS? {marker(count === 1)}
            <input type="number" aria-label="Number of aircraft" min={1} step={1} value={countText ?? count} aria-invalid={countInvalid} disabled={disabled}
              onChange={event => typeCount(event.target.value)} onBlur={() => { if (guard === null) setCountText(null) }}/>
            <small>{available ? `${available} verified aircraft available` : 'none verified yet'}{capacity ? ` · this machine runs up to ${capacity}` : ''}. Any number can be planned.</small>
            {countInvalid && <small className="quick-invalid" role="alert">Enter a whole number from 1 to {planMax}.</small>}</label>
          {guard !== null && <div className="quick-note" role="alertdialog" aria-label="Check the number of aircraft"><AlertTriangle size={13}/> Did you mean {guard} aircraft?
            <button type="button" className="button-secondary" onClick={() => { setConfirmed(guard); applyCount(guard) }}>YES, {guard} AIRCRAFT</button>
            <button type="button" className="button-quiet" onClick={() => { setGuard(null); setCountText(null) }}>NO, KEEP {count}</button></div>}
          <label className="quick-field">SPEED (m/s) {inputs.speedMps === null ? <span className="quick-marker auto">auto (derived)</span> : marker(false)}
            <input type="number" aria-label="Speed" min={1} step={0.5} value={inputs.speedMps ?? speedInfo?.derived_speed_mps ?? ''} onChange={event => setInputs({ speedMps: num(event.target.value) })}/>
            <span className="quick-speed-actions">
              <button type="button" className="button-quiet" onClick={() => setInputs({ speedMps: research })}>RESEARCH SPEED ({research} m/s)</button>
              {inputs.speedMps !== null && <button type="button" className="button-quiet" onClick={() => setInputs({ speedMps: null })}>USE AUTO</button>}</span>
            {speedInfo && <small>{speedInfo.mode === 'derived'
              ? `Highest safe speed for this route: ${speedInfo.limited_by === 'corner' ? `the tightest corner (radius ${speedInfo.tightest_corner_radius_m} m, lateral acceleration ${speedInfo.lateral_accel_mps2} m/s²)` : `the speed ceiling (${speedInfo.ceiling_mps} m/s)`}.`
              : `You chose ${speedInfo.speed_mps} m/s; the highest safe speed here is ${speedInfo.derived_speed_mps} m/s.`}</small>}
            {speedInfo?.above_validated && <span className="quick-badge" role="note"><AlertTriangle size={12}/> Threat monitor is validated only at {research} m/s</span>}</label>
          <label className="quick-field">ALTITUDE (m) {marker(inputs.altitudeM === null)}
            <input type="number" aria-label="Altitude" min={1} value={inputs.altitudeM ?? rules.defaults.altitude_m} onChange={event => setInputs({ altitudeM: num(event.target.value) })}/></label>
          <label className="quick-field quick-name">SCENARIO NAME <span className="quick-marker auto">{nameEdited ? 'typed' : 'suggested'}</span>
            <input aria-label="Quick mission name" value={shownName} maxLength={120} placeholder="Suggested from the type and place" onChange={event => onName(event.target.value)}/></label>
        </div>
        <section className="quick-aircraft" aria-label="Aircraft">
          <div className="quick-aircraft-head"><b>AIRCRAFT · {count}</b><small>Name, role and (under Advanced) altitude, speed and start offset per aircraft. Empty fields use the mission values.</small></div>
          <ul className="quick-aircraft-list" aria-label="Aircraft list">{Array.from({ length: count }, (_, index) => {
            const row = rows[index] ?? EMPTY_ROW
            const open = Boolean(openRows[index]) || row.altitudeM !== null || row.speedMps !== null || row.startOffsetM !== null
            return <li key={index} className="quick-aircraft-row">
              <div className="quick-aircraft-main">
                <span className="quick-aircraft-number">{index + 1}</span>
                <input aria-label={`Name of aircraft ${index + 1}`} value={row.name ?? ''} maxLength={40} placeholder={`UAV-${index + 1}`} disabled={disabled}
                  onChange={event => setRow(index, { name: event.target.value || null })}/>
                <select aria-label={`Role of aircraft ${index + 1}`} value={effectiveRole(rows, index, rules)} disabled={disabled} onChange={event => setRow(index, { role: event.target.value as Role })}>
                  <option value="primary">primary (flies the mission route)</option><option value="decoy">decoy (parallel path)</option><option value="standby">standby (stays on the ground)</option></select>
                <button type="button" className="button-quiet" aria-expanded={open} aria-label={`Advanced settings of aircraft ${index + 1}`} onClick={() => setOpenRows(current => ({ ...current, [index]: !open }))}>{open ? 'HIDE' : 'ADVANCED'}</button>
              </div>
              {open && <div className="quick-aircraft-advanced">
                <label>ALTITUDE (m)<input type="number" aria-label={`Altitude of aircraft ${index + 1}`} min={1} value={row.altitudeM ?? ''} placeholder="auto" disabled={disabled} onChange={event => setRow(index, { altitudeM: num(event.target.value) })}/></label>
                <label>SPEED (m/s)<input type="number" aria-label={`Speed of aircraft ${index + 1}`} min={1} step={0.5} value={row.speedMps ?? ''} placeholder="auto" disabled={disabled} onChange={event => setRow(index, { speedMps: num(event.target.value) })}/></label>
                <label>START OFFSET (m north)<input type="number" aria-label={`Start offset of aircraft ${index + 1}`} value={row.startOffsetM ?? ''} placeholder="auto" disabled={disabled} onChange={event => setRow(index, { startOffsetM: num(event.target.value) })}/></label>
              </div>}
              {lineOf(index) && <small className="quick-aircraft-line">{lineOf(index)}</small>}
            </li>
          })}</ul>
        </section>
        {!available && <div className="quick-note" role="status"><AlertTriangle size={13}/> No verified aircraft is running. You can still plan and save; flying needs a running aircraft.
          <button type="button" className="button-secondary" disabled={disabled || activating} onClick={onActivate}>{activating ? 'STARTING AND VERIFYING…' : 'START AND VERIFY 1 AIRCRAFT'}</button></div>}
        {busy && <p className="planner-empty" role="status">Planning the route…</p>}
        {error && <div className="scenario-library-error" role="alert"><AlertTriangle size={14}/>{error}</div>}
        <div className="quick-actions">
          <button type="button" className="button-secondary" disabled={!result || busy} onClick={() => void planner.refresh()}>CREATE PREVIEW</button>
          <button type="button" className="button-secondary" disabled={disabled || !result || busy} onClick={onSave}><Save size={13}/> {saveLabel}</button>
          <button type="button" className="button-primary" disabled={disabled || !result || busy || deploy.busy || !deploy.allowed} title={deploy.allowed ? undefined : deploy.reason} onClick={deploy.onDeploy}><Rocket size={13}/> {deploy.busy ? 'WORKING…' : deploy.label}</button>
          {saved.open && <button type="button" className="button-secondary" disabled={disabled || !result || busy} onClick={saved.onSaveAsNew}><Save size={13}/> SAVE AS A NEW MISSION</button>}
          <button type="button" className="button-quiet" disabled={busy} onClick={saved.onClear}><Trash2 size={13}/> CLEAR</button>
          <button type="button" className="button-secondary" disabled={!fineTune.enabled || busy} onClick={fineTune.onClick}><SlidersHorizontal size={13}/> FINE-TUNE IN SCENARIO LAB</button>
        </div>
        {!deploy.allowed && deploy.reason && result && <small className="quick-reason">{deploy.reason}</small>}
        <small className="quick-reason">{fineTune.note} Deploying hands the saved version to Mission Control; it never starts a flight.</small>
      </div>
      <div className="quick-right"><div className={`quick-map ${type || assistantWaiting ? 'picking' : ''}`}>
        {showMap && <MapErrorBoundary label="The quick mission map"><MissionPlannerMap
          start={kind === 'points' ? null : inputs.where ? asPoint(inputs.where) : null} destination={null} zones={zones} draftVertices={[]} assignments={assignments} activeAssignmentId="quick"
          mode={!type && !assistantWaiting ? 'navigate' : kind === 'points' && !assistantWaiting ? 'add_aircraft_waypoint' : 'set_start'} focus={aircraftPosition}
          onStartChange={point => pick(point, 'map')} onPlacePicked={(point, label) => pick(point, 'place', label)} onAddAircraftWaypoint={(_id, point) => pick(point, 'map')}
          onDestinationChange={() => undefined} onAddVertex={() => undefined} onSelectVertex={() => undefined} onMoveVertex={() => undefined}
          onMoveLocation={(_location, point) => pick(point, 'map')} onAircraftLocationChange={() => undefined}/></MapErrorBoundary>}
      </div></div>
      {/* The explanation of the derived numbers stays collapsed at the bottom of the page. */}
      <details className="quick-rules"><summary>How these numbers are chosen</summary><p>{rules.speed_reason}</p><ul>{rules.rules.map(rule => <li key={rule}>{rule}</li>)}</ul></details>
    </div>
  </section>
}
