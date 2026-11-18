/**
 * Quick mission and the assistant: request/response types and the single function that turns the backend's answer into a draft. The derivation
 * rules live ONLY in the backend (`app/mission_planning/quick.py`, served by `GET /api/mission-plans/limits` as `quick`); nothing here copies a
 * default, a spacing rule, a speed rule or a limit formula.
 */
import { MISSION_OBJECTIVES, SCHEMA_VERSION, normalizePlan, type GeoPoint, type MissionPlan, type PlanIssue } from './missionPlan'
import type { ScenarioIntent } from '../services/auth'

export type QuickWhere = 'centre' | 'destination' | 'points'
export interface QuickType {
  id: string; label: string; summary: string; size_label: string; task_type: string; objectives: string[]
  /** `centre`: the clicked point is the middle of an area. `destination`: one point to fly to. `points`: several clicked points, in order. */
  where: QuickWhere
  /** `where`: the aircraft starts at the clicked point. `aircraft`: it starts where it is now. */
  start: 'where' | 'aircraft'
}
export interface QuickRules {
  types: QuickType[]
  defaults: { speed_mps: number; altitude_m: number; size_m: number; drone_count: number }
  size_range_m: [number, number]
  max_drones: number
  /** Planning has no product limit; this is only the id format (px4-1 ... px4-999). */
  plan_max_aircraft?: number
  /** How many aircraft this machine can RUN (limits activating and starting, never planning). */
  simulation_capacity?: number
  default_extra_role?: Role
  typo_guard_over?: number
  rules: string[]
  speed_reason: string
  research_speed_mps: number
  extra_roles: ExtraRole[]
  far_aircraft_m: number
  point_limit: number
  decoy_offset_m: number
}
export type ExtraRole = 'decoy' | 'standby'
export type Role = 'primary' | 'decoy' | 'standby'
/** One row of the aircraft box. `null` means "use the default" (auto stays auto). */
export interface QuickAircraftRow { name: string | null; role: Role | null; altitudeM: number | null; speedMps: number | null; startOffsetM: number | null }
export const EMPTY_ROW: QuickAircraftRow = { name: null, role: null, altitudeM: null, speedMps: null, startOffsetM: null }
export interface QuickAircraftRequest { name?: string | null; role?: Role | null; altitude_m?: number | null; speed_mps?: number | null; start_offset_m?: number | null }
export const rowToRequest = (row: QuickAircraftRow | undefined): QuickAircraftRequest => ({ name: row?.name?.trim() || null, role: row?.role ?? null, altitude_m: row?.altitudeM ?? null, speed_mps: row?.speedMps ?? null, start_offset_m: row?.startOffsetM ?? null })
export const rowFromRequest = (row: QuickAircraftRequest | undefined): QuickAircraftRow => ({ name: row?.name ?? null, role: row?.role ?? null, altitudeM: row?.altitude_m ?? null, speedMps: row?.speed_mps ?? null, startOffsetM: row?.start_offset_m ?? null })
/** The role a row shows: the typed one, else UAV-1 primary and every other aircraft the served default (decoy). */
export const effectiveRole = (rows: QuickAircraftRow[], index: number, rules: QuickRules | null): Role => rows[index]?.role ?? (index === 0 ? 'primary' : rules?.default_extra_role ?? 'decoy')
/** Rows from the older stored form (roles of the 2nd, 3rd ... aircraft). */
export const rowsFromRoles = (roles: string[] | undefined): QuickAircraftRow[] => [EMPTY_ROW, ...(roles ?? []).map(role => ({ ...EMPTY_ROW, role: (role === 'standby' || role === 'decoy' || role === 'primary' ? role : null) as Role | null }))]
export type WhereSource = 'map' | 'place' | 'aircraft'
export interface QuickRequest {
  mission_type: string
  where?: GeoPoint | null
  points?: GeoPoint[]
  drop_index?: number | null
  where_source: WhereSource
  size_m?: number | null
  drone_count: number
  aircraft_ids: string[]
  roles?: ExtraRole[]
  aircraft?: QuickAircraftRequest[]
  aircraft_position?: GeoPoint | null
  aircraft_positions?: GeoPoint[]
  speed_mps?: number | null
  altitude_m?: number | null
  place_name?: string | null
}
export interface SpeedInfo {
  speed_mps: number; mode: 'derived' | 'typed'; derived_speed_mps: number; research_speed_mps: number; above_validated: boolean
  limited_by: 'corner' | 'ceiling'; ceiling_mps: number; corner_limit_mps: number | null; tightest_corner_radius_m: number | null
  lateral_accel_mps2: number; acceptance_radius_m: number
}
export interface AircraftExplanation { uav_id: string; role: string | null; task_type: string | null; steps: string[] }
export interface QuickSummary {
  mission_type: string; label: string; area: { kind: 'circle'; radius_m: number; centre: GeoPoint }; start: GeoPoint | null; drones: number; aircraft_ids: string[]; roles: (string | null)[]
  speed_mps: number; altitude_m: number; estimated_s: number; length_m: number; waypoints: number; track_spacing_m: number | null
  limits: { max_distance_from_start_m: number | null; max_altitude_m: number | null; max_duration_minutes: number | null }
  confirmed: boolean; fingerprint?: string; speed_info: SpeedInfo; explanation: AircraftExplanation[]; points: GeoPoint[]; drop_index: number | null
  aircraft: { uav_id: string; name?: string; role: string | null; task_type: string | null; length_m: number; duration_s: number; waypoints: number; line?: string; altitude_m?: number | null; speed_mps?: number | null }[]
  simulation_capacity?: number
}
export interface QuickResult { plan: MissionPlan; summary: QuickSummary; warnings: PlanIssue[]; derived: Record<string, number>; suggested_name: string }

export interface ParseState {
  mission_type: string | null; size_m: number | null; drone_count: number | null; speed_mps: number | null; altitude_m: number | null
  where_source: WhereSource | null; place_query: string | null; asked: string | null
  /** roles of the 2nd, 3rd ... aircraft named in the sentence (decoy | standby) */
  roles?: string[]
  decoys?: number | null; standbys?: number | null; role_changes?: Record<string, string>; drone_confirmed?: number | null
}
export const EMPTY_PARSE_STATE: ParseState = { mission_type: null, size_m: null, drone_count: null, speed_mps: null, altitude_m: null, where_source: null, place_query: null, asked: null }
export interface ParseQuestion { id: string; text: string; choices: { id: string; label: string }[] }
export interface ParseRequest { text?: string | null; choice?: string | null; state: ParseState; context: { available_aircraft: number; has_aircraft_position: boolean } }
export interface ParseResult {
  intent: 'mission' | 'unsupported' | 'refused' | 'other'
  state: ParseState
  understood: string[]
  question: ParseQuestion | null
  complete: boolean
  unsupported: { reason: string; closest: string | null } | null
  notes: string[]
  refused: string | null
  place_pending?: boolean
}

export interface FleetAircraft { id: string; position: GeoPoint | null }

/** Everything the user chose on the Quick Mission page (or the assistant chose for them). `null` means "use the derived default". */
export interface QuickInputs {
  type: string | null
  where: GeoPoint | null
  points: GeoPoint[]
  /** waypoints then drop: index of the drop waypoint; null = the last one */
  dropIndex: number | null
  source: WhereSource | null
  placeName: string | null
  sizeM: number | null
  droneCount: number
  /** roles of the 2nd, 3rd ... aircraft; missing entries take the served defaults */
  roles: ExtraRole[]
  /** one row per aircraft (may hold more rows than the count, so typed rows survive a smaller count) */
  aircraft: QuickAircraftRow[]
  speedMps: number | null
  altitudeM: number | null
}
export const EMPTY_QUICK_INPUTS: QuickInputs = { type: null, where: null, points: [], dropIndex: null, source: null, placeName: null, sizeM: null, droneCount: 1, roles: [], aircraft: [], speedMps: null, altitudeM: null }

/** Whether the inputs hold enough to ask the backend for a plan (a place and a type). */
export function inputsReady(inputs: QuickInputs, rules: QuickRules | null): boolean {
  const kind = rules?.types.find(item => item.id === inputs.type)
  if (!kind) return false
  return kind.where === 'points' ? inputs.points.length > 0 : Boolean(inputs.where)
}

/** The one request builder: the Quick Mission page and the assistant both call this, so the same inputs always give the same draft. */
export function buildQuickRequest(inputs: QuickInputs, fleet: FleetAircraft[], rules: QuickRules | null): QuickRequest {
  const kind = rules?.types.find(item => item.id === inputs.type)
  const count = Math.max(1, Math.min(Math.floor(inputs.droneCount) || 1, rules?.plan_max_aircraft ?? 999))
  // Running aircraft are named when there are any (their positions are used); the backend fills the other ids, so planning never needs a running fleet.
  const chosen = fleet.slice(0, count)
  const rows = Array.from({ length: count }, (_, index) => rowToRequest(inputs.aircraft[index]))
  const legacyRoles = inputs.aircraft.length ? [] : inputs.roles.slice(0, Math.max(0, count - 1))
  const usePoints = kind?.where === 'points'
  const known = chosen.map(item => item.position).filter((item): item is GeoPoint => item !== null)
  return {
    mission_type: inputs.type ?? '', where: usePoints ? inputs.points[0] ?? null : inputs.where, points: usePoints ? inputs.points : undefined,
    drop_index: usePoints && inputs.type === 'waypoints_then_drop' ? inputs.dropIndex : undefined, where_source: inputs.source ?? 'map',
    size_m: inputs.sizeM, drone_count: count, aircraft_ids: chosen.map(item => item.id), roles: legacyRoles, aircraft: rows.some(row => Object.values(row).some(value => value !== null)) ? rows : undefined,
    aircraft_position: chosen[0]?.position ?? null, aircraft_positions: known.length === chosen.length && known.length ? known : undefined,
    speed_mps: inputs.speedMps, altitude_m: inputs.altitudeM, place_name: inputs.placeName,
  }
}

/** The stored scenario intent for a plan (what Scenario Lab saves too). */
export function intentFromPlan(plan: MissionPlan): ScenarioIntent {
  return {
    mission_objectives: plan.objectives.map(value => MISSION_OBJECTIVES.find(item => item.value === value)?.title ?? value),
    mission_description: plan.description, proposed_tasks: [], uav_role_suggestions: [], operating_area_notes: '', environment_notes: '', constraints: [],
    geographic_mission: null, schema_version: SCHEMA_VERSION, mission_plan: plan, uav_fault_assignments: [],
  } as ScenarioIntent
}

/** What a draft takes from a quick result. */
export interface QuickDraft { plan: MissionPlan; title: string; requiredCount: number }
export function draftFromQuick(result: QuickResult, currentTitle: string, titleEdited: boolean): QuickDraft {
  const plan = normalizePlan(result.plan)
  return { plan, title: titleEdited && currentTitle.trim() ? currentTitle : result.suggested_name, requiredCount: plan.required_uav_count ?? plan.aircraft.length }
}

export const fmtMinutes = (seconds: number) => `${+(seconds / 60).toFixed(1)} min`
export const fmtKm = (metres: number) => `${(metres / 1000).toFixed(2)} km`

/** One-screen summary lines (the same text for the Quick Mission page and the assistant). */
export function summaryLines(summary: QuickSummary): string[] {
  const limits = summary.limits
  const roles = summary.roles.length > 1 ? ` (${summary.roles.join(', ')})` : ''
  return [
    `${summary.label}${summary.area ? ` · area radius ${summary.area.radius_m} m` : ''}`,
    summary.start ? `Start: ${summary.start.latitude.toFixed(5)}, ${summary.start.longitude.toFixed(5)}` : 'Start: the aircraft',
    `${summary.drones} aircraft (${summary.aircraft_ids.join(', ')})${roles} · ${summary.speed_mps} m/s · ${summary.altitude_m} m altitude`,
    `Estimated ${fmtMinutes(summary.estimated_s)} · ${fmtKm(summary.length_m)} · ${summary.waypoints} waypoints`,
    `Limits (auto): max distance ${limits.max_distance_from_start_m} m · max altitude ${limits.max_altitude_m} m · max duration ${limits.max_duration_minutes} min`,
  ]
}

/** Whether a limit is currently derived (shown as "auto (value)") or typed. */
export function limitMode(plan: MissionPlan, key: 'max_distance_from_start_m' | 'max_altitude_m' | 'max_duration_minutes'): 'auto' | 'typed' {
  return plan.auto_limits?.[key] ? 'auto' : 'typed'
}

/** Applies a patch to the inputs, converting between place kinds when the type changes (centre <-> destination <-> points). */
export function applyInputPatch(current: QuickInputs, patch: Partial<QuickInputs>, rules: QuickRules | null): QuickInputs {
  const next = { ...current, ...patch }
  if (patch.type !== undefined && patch.type !== current.type) {
    const kind = rules?.types.find(item => item.id === patch.type)
    if (kind?.where === 'points') {
      next.points = current.points.length ? current.points : current.where && current.source !== 'aircraft' ? [current.where] : []
      next.where = null; next.source = current.source === 'aircraft' ? null : current.source; next.dropIndex = null
    } else if (kind) {
      next.where = current.where ?? current.points[0] ?? null; next.points = []; next.dropIndex = null
      if (kind.where === 'destination' && next.source === 'aircraft') { next.where = null; next.source = null }
    }
  }
  return next
}

/** The aircraft rows a parsed sentence asks for ("12 UAVs, 2 decoys", "make UAV-7 standby"). Roles are only what the user said; everything else stays default. */
export function rowsFromParse(state: ParseState): QuickAircraftRow[] {
  const count = Math.max(1, state.drone_count ?? 1)
  const rows: QuickAircraftRow[] = Array.from({ length: count }, () => ({ ...EMPTY_ROW }))
  const decoys = state.decoys ?? null, standbys = state.standbys ?? null
  if (decoys !== null || standbys !== null) {
    for (let index = 1; index < count; index++) {
      const position = index - 1
      if (decoys !== null && position < decoys) rows[index].role = 'decoy'
      else if (standbys !== null && position < (decoys ?? 0) + standbys) rows[index].role = 'standby'
      else rows[index].role = decoys !== null && standbys === null ? 'standby' : decoys === null ? 'decoy' : null
    }
  }
  for (const [number, role] of Object.entries(state.role_changes ?? {})) {
    const index = Number(number) - 1
    if (index >= 0 && index < count && (role === 'primary' || role === 'decoy' || role === 'standby')) rows[index].role = role
  }
  return rows.some(row => row.role !== null) ? rows : []
}
