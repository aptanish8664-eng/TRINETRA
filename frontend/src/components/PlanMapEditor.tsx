import { useState } from 'react'
import type { GeographicPoint, GeographicUavAssignment, GeographicZone } from '../services/auth'
import { MissionPlannerMap, type PlannerMapMode } from './MissionPlannerMap'
import { MapErrorBoundary } from './MapErrorBoundary'
import { formatCoordinate } from './missionPlanning'
import { areaPreviewVertices, taskSpec, type AircraftPlan, type GeoPoint, type MissionPlan, type OperatingArea, type ParamSpec, type TaskParameters } from './missionPlan'

type Props = {
  scope: 'area' | 'aircraft'
  plan: MissionPlan
  aircraft?: AircraftPlan
  disabled?: boolean
  onAreaChange: (area: OperatingArea | null) => void
  onAircraftChange: (patch: Partial<AircraftPlan>) => void
}

type Target = { kind: 'start' } | { kind: 'param'; spec: ParamSpec }

const asPoint = (point: GeoPoint): GeographicPoint => ({ latitude: point.latitude, longitude: point.longitude })

/** Map editing for the structured operating area (Section A) and an aircraft's start/target points plus route preview (Section B). */
export function PlanMapEditor({ scope, plan, aircraft, disabled = false, onAreaChange, onAircraftChange }: Props) {
  const [mode, setMode] = useState<PlannerMapMode>('navigate')
  const [target, setTarget] = useState<Target | null>(null)
  const [draft, setDraft] = useState<GeographicPoint[]>([])
  const area = plan.operating_area
  const zones: GeographicZone[] = area ? [{ id: 'operating-area', name: 'Operating area', vertices: areaPreviewVertices(area) }] : []
  const spec = taskSpec(aircraft?.task_type)
  const pointParams = (spec?.params ?? []).filter(item => item.kind === 'point' || item.kind === 'points')

  const setParam = (key: keyof TaskParameters, value: unknown) =>
    aircraft && onAircraftChange({ parameters: { ...aircraft.parameters, [key]: value } as TaskParameters, route: null, confirmed: false })

  const choose = (next: Target | null) => {
    setTarget(next)
    setDraft([])
    setMode(!next ? 'navigate' : next.kind === 'start' ? 'set_aircraft_start' : next.spec.kind === 'points' ? 'add_aircraft_waypoint' : 'set_aircraft_destination')
  }

  const finishPolygon = () => {
    if (draft.length < 3) return
    onAreaChange({ kind: 'polygon', centre: null, radius_m: null, vertices: draft.map(asPoint) })
    setDraft([]); setMode('navigate')
  }

  let assignments: GeographicUavAssignment[] = []
  if (scope === 'aircraft' && aircraft) {
    const route = aircraft.route?.waypoints ?? []
    const single = pointParams.filter(item => item.kind === 'point').map(item => aircraft.parameters[item.key] as GeoPoint | null | undefined).find(Boolean) ?? null
    const lists = pointParams.filter(item => item.kind === 'points').flatMap(item => (aircraft.parameters[item.key] as GeoPoint[] | undefined) ?? [])
    assignments = [{
      uav_id: aircraft.uav_id, role: aircraft.role ?? '', task: '', zone_id: null, zone_ids: [],
      start_location: route.length ? { latitude: route[0].latitude, longitude: route[0].longitude } : aircraft.start_location,
      destination: route.length > 1 ? { latitude: route[route.length - 1].latitude, longitude: route[route.length - 1].longitude } : single,
      waypoints: route.length > 2 ? route.slice(1, -1).map(item => ({ latitude: item.latitude, longitude: item.longitude })) : lists,
      constraints: [], confirmed: false,
    }]
  }

  return <div className="uav-geography-editor plan-map-editor">
    <div className="mission-map-toolbar" aria-label={scope === 'area' ? 'Operating area controls' : `${aircraft?.uav_id ?? ''} map controls`}>
      {scope === 'area' && <>
        <button type="button" className={`button-secondary ${area?.kind === 'circle' || mode === 'set_start' ? 'selected' : ''}`} disabled={disabled}
          onClick={() => { setDraft([]); setMode(mode === 'set_start' ? 'navigate' : 'set_start') }}>CIRCLE: PICK CENTRE</button>
        <button type="button" className={`button-secondary ${mode === 'draw_zone' ? 'selected' : ''}`} disabled={disabled}
          onClick={() => { setDraft([]); setMode(mode === 'draw_zone' ? 'navigate' : 'draw_zone') }}>POLYGON: DRAW</button>
        {mode === 'draw_zone' && <>
          <button type="button" className="button-primary" disabled={disabled || draft.length < 3} onClick={finishPolygon}>FINISH AREA ({draft.length} POINTS)</button>
          <button type="button" className="button-quiet" onClick={() => { setDraft([]); setMode('navigate') }}>CANCEL</button>
        </>}
        {area && <button type="button" className="button-quiet" disabled={disabled} onClick={() => onAreaChange(null)}>CLEAR AREA</button>}
        {mode === 'set_start' && <span>Click the map to set the circle centre.</span>}
        {mode === 'draw_zone' && <span>Click at least three map points, then finish.</span>}
      </>}
      {scope === 'aircraft' && aircraft && <>
        <button type="button" className={`button-secondary ${target?.kind === 'start' ? 'selected' : ''}`} disabled={disabled}
          onClick={() => choose(target?.kind === 'start' ? null : { kind: 'start' })}>SET START</button>
        {pointParams.map(item => <button key={item.key} type="button" className={`button-secondary ${target?.kind === 'param' && target.spec.key === item.key ? 'selected' : ''}`}
          disabled={disabled} onClick={() => choose(target?.kind === 'param' && target.spec.key === item.key ? null : { kind: 'param', spec: item })}>
          {item.kind === 'points' ? 'ADD' : 'SET'} {item.label.toUpperCase()}</button>)}
        {target && <span>Click the map to {target.kind === 'start' ? 'set the start' : target.spec.kind === 'points' ? `add to “${target.spec.label}”` : `set “${target.spec.label}”`}.</span>}
      </>}
    </div>
    <MapErrorBoundary label="The planning map"><MissionPlannerMap
      start={scope === 'area' && area?.kind === 'circle' ? (area.centre ? asPoint(area.centre) : null) : null}
      destination={null}
      zones={zones}
      draftVertices={draft}
      assignments={assignments}
      activeAssignmentId={aircraft?.uav_id ?? ''}
      mode={mode}
      onStartChange={point => {
        if (scope !== 'area') return
        onAreaChange({ kind: 'circle', centre: asPoint(point), radius_m: area?.kind === 'circle' && area.radius_m ? area.radius_m : 300, vertices: [] })
        setMode('navigate')
      }}
      onDestinationChange={() => undefined}
      onAddVertex={point => setDraft(current => [...current, point])}
      onSelectVertex={() => setMode('navigate')}
      onMoveVertex={(_zoneId, index, point) => area?.kind === 'polygon' && onAreaChange({ ...area, vertices: area.vertices.map((vertex, i) => (i === index ? asPoint(point) : vertex)) })}
      onMoveLocation={(_location, point) => area?.kind === 'circle' && onAreaChange({ ...area, centre: asPoint(point) })}
      onAircraftLocationChange={(_uavId, location, point) => {
        if (location === 'start_location') onAircraftChange({ start_location: asPoint(point), route: null, confirmed: false })
        else if (target?.kind === 'param') setParam(target.spec.key, asPoint(point))
      }}
      onAddAircraftWaypoint={(_uavId, point) => {
        if (target?.kind !== 'param' || !aircraft) return
        const current = (aircraft.parameters[target.spec.key] as GeoPoint[] | undefined) ?? []
        onAircraftChange({ parameters: { ...aircraft.parameters, [target.spec.key]: [...current, asPoint(point)] }, route: null, confirmed: false })
      }}
    /></MapErrorBoundary>
    <div className="mission-location-summary">
      {scope === 'area' && <span>AREA <b>{!area ? 'not selected' : area.kind === 'circle' ? `circle · ${formatCoordinate(area.centre ? asPoint(area.centre) : null)} · ${area.radius_m ?? 0} m` : `polygon · ${area.vertices.length} vertices`}</b></span>}
      {scope === 'aircraft' && aircraft && <>
        <span>START <b>{formatCoordinate(aircraft.start_location ? asPoint(aircraft.start_location) : null)}</b></span>
        {pointParams.map(item => {
          const value = aircraft.parameters[item.key]
          const label = item.kind === 'points' ? `${(value as GeoPoint[] | undefined)?.length ?? 0} point(s)` : formatCoordinate(value ? asPoint(value as GeoPoint) : null)
          return <span key={item.key}>{item.label.toUpperCase()} <b>{label}</b>
            {Boolean(value) && !disabled && <button type="button" className="button-quiet" onClick={() => setParam(item.key, item.kind === 'points' ? [] : null)}>CLEAR</button>}</span>
        })}
      </>}
    </div>
    <p className="geographic-planner-note">{scope === 'aircraft' && aircraft?.route
      ? 'The drawn route is the backend-generated, validated waypoint list. It is not uploaded to PX4.'
      : 'Map selections are advisory planning inputs. A route exists only after the backend generates and validates it.'}</p>
  </div>
}
