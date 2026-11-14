/**
 * Schema-version-2 mission plan: types, shared constants and the client mirror of the backend validation.
 *
 * Every field here is read by the backend validator, the route generator, Mission Control hand-off or human
 * review. The backend (`app/mission_planning`) is authoritative; this mirror only drives the form, the
 * assistant and the Section C (review) checklist. `backend/tests/test_mission_planning.py` checks these lists stay in step.
 * Software-only: a plan is a route for PX4 SITL / the simulator and never commands a vehicle.
 */
import { MISSION_DURATION_RANGE_MINUTES, UAV_COUNT_RANGE } from './scenarioPlanning'

export interface GeoPoint { latitude: number; longitude: number }
export type TaskType =
  | 'expanding_square_search' | 'sector_search' | 'coverage_survey' | 'perimeter_patrol'
  | 'point_inspection' | 'waypoint_route' | 'loiter_hold' | 'payload_dropoff'
export type AircraftRole = 'primary' | 'decoy' | 'standby'
export type ReturnMode = 'return_to_start' | 'land_at_destination'
export type PayloadCategory = 'relief_supplies' | 'medical_kit' | 'flotation_device'
export type ObjectiveValue =
  | 'area_survey' | 'search_rescue' | 'perimeter_monitoring' | 'inspection' | 'disaster_assessment'
  | 'agricultural_survey' | 'mapping_survey' | 'target_tracking' | 'environmental_monitoring'
  | 'custom_mission' | 'relief_supply_delivery'

export interface OperatingArea { kind: 'circle' | 'polygon'; centre: GeoPoint | null; radius_m: number | null; vertices: GeoPoint[] }
export interface TaskParameters {
  datum?: GeoPoint | null; track_spacing_m?: number | null; search_radius_m?: number | null; heading_deg?: number
  sector_radius_m?: number | null; sector_angle_deg?: number; sector_count?: number
  line_spacing_m?: number | null; path?: GeoPoint[]; laps?: number
  points?: GeoPoint[]; orbit_radius_m?: number; orbit_points?: number; hover_s?: number; hold_point?: GeoPoint | null
  drop_point?: GeoPoint | null; drop_altitude_m?: number | null; hover_duration_s?: number | null
  payload_category?: PayloadCategory | null
}
export interface RouteWaypoint {
  seq: number; latitude: number; longitude: number; altitude_m: number; speed_mps: number; hover_s: number
  action: 'takeoff' | 'waypoint' | 'hover' | 'payload_release_point' | 'land'
}
export interface RouteEvent {
  type: 'payload_released'; aircraft_id: string; waypoint_seq: number; planned_time_s: number
  payload_category: PayloadCategory; simulated: true
}
export interface GeneratedRoute {
  generator: string; generator_version: number; waypoints: RouteWaypoint[]; events: RouteEvent[]
  length_m: number; duration_s: number; assumptions: string
}
export interface AircraftPlan {
  uav_id: string; role: AircraftRole | null; priority: number; reassignable: boolean
  task_type: TaskType | null; parameters: TaskParameters; start_location: GeoPoint | null
  altitude_m: number | null; speed_mps: number | null; loiter_duration_s: number; return_mode: ReturnMode
  route: GeneratedRoute | null; confirmed: boolean
}
export interface MissionPlan {
  objectives: ObjectiveValue[]; description: string; operating_area: OperatingArea | null
  required_uav_count: number | null; max_duration_minutes: number | null
  constraints: { max_distance_from_start_m: number | null; max_altitude_m: number | null }
  notes: string; allow_multiple_primary: boolean; selected_uav_ids: string[]; activated_uav_ids: string[]
  aircraft: AircraftPlan[]; workflow_stage: 'general' | 'aircraft' | 'faults' | 'review'; current_uav_index: number
}
export interface PlanIssue { code: string; message: string; uav_id?: string | null; field?: string | null }

export const SCHEMA_VERSION = 2
export const EMPTY_PLAN: MissionPlan = {
  objectives: [], description: '', operating_area: null, required_uav_count: null, max_duration_minutes: null,
  constraints: { max_distance_from_start_m: null, max_altitude_m: null }, notes: '', allow_multiple_primary: false,
  selected_uav_ids: [], activated_uav_ids: [], aircraft: [], workflow_stage: 'general', current_uav_index: 0,
}

export const MISSION_OBJECTIVES: { value: ObjectiveValue; title: string; description: string }[] = [
  { value: 'area_survey', title: 'Area Surveillance', description: 'Observe and survey a defined area.' },
  { value: 'search_rescue', title: 'Search and Rescue', description: 'Organize an advisory search of an area.' },
  { value: 'perimeter_monitoring', title: 'Border / Perimeter Monitoring', description: 'Patrol a boundary.' },
  { value: 'inspection', title: 'Infrastructure Inspection', description: 'Inspect operator-selected points.' },
  { value: 'disaster_assessment', title: 'Disaster Assessment', description: 'Survey a response area.' },
  { value: 'agricultural_survey', title: 'Agricultural Surveying', description: 'Cover an agricultural area.' },
  { value: 'mapping_survey', title: 'Mapping and Terrain Survey', description: 'Cover an area for mapping.' },
  { value: 'target_tracking', title: 'Target Tracking', description: 'Waypoint route only; no tracking capability exists.' },
  { value: 'environmental_monitoring', title: 'Environmental Monitoring', description: 'Observe operator-selected points.' },
  { value: 'custom_mission', title: 'Custom Mission', description: 'Waypoint route only.' },
  { value: 'relief_supply_delivery', title: 'Relief Supply Delivery', description: 'Simulated delivery of relief supplies to a drop point.' },
]
/** Objective -> suggested default task type only; the operator always picks the task type explicitly. */
export const OBJECTIVE_DEFAULT_TASK: Record<ObjectiveValue, TaskType> = {
  search_rescue: 'expanding_square_search',
  area_survey: 'coverage_survey', disaster_assessment: 'coverage_survey', agricultural_survey: 'coverage_survey', mapping_survey: 'coverage_survey',
  perimeter_monitoring: 'perimeter_patrol',
  inspection: 'point_inspection', environmental_monitoring: 'point_inspection',
  target_tracking: 'waypoint_route', custom_mission: 'waypoint_route',
  relief_supply_delivery: 'payload_dropoff',
}
export const TRACKING_ONLY_OBJECTIVES: ObjectiveValue[] = ['target_tracking', 'custom_mission']
export const DELIVERY_OBJECTIVES: ObjectiveValue[] = ['relief_supply_delivery', 'search_rescue', 'disaster_assessment']
/** Task types the objectives allow; mirrors the backend rule (tracking-only objectives allow a waypoint route only; drop-off needs a delivery objective). */
export function allowedTaskTypes(objectives: ObjectiveValue[]): TaskType[] {
  const trackingOnly = objectives.length > 0 && objectives.every(value => TRACKING_ONLY_OBJECTIVES.includes(value))
  return TASK_TYPE_VALUES.filter(value => (!trackingOnly || value === 'waypoint_route')
    && (value !== 'payload_dropoff' || objectives.some(objective => DELIVERY_OBJECTIVES.includes(objective))))
}
export const TRACKING_NOTE = 'No tracking capability exists: this objective only allows a waypoint route you define.'

export const AIRCRAFT_ROLES: { value: AircraftRole; label: string; help: string }[] = [
  { value: 'primary', label: 'Primary', help: 'The aircraft that carries the mission task. Exactly one primary per mission unless multiple are enabled.' },
  { value: 'decoy', label: 'Decoy', help: 'A simulated decoy aircraft used by later threat and reassignment research.' },
  { value: 'standby', label: 'Standby', help: 'A reserve aircraft that may be reassigned when another is degraded.' },
]
export const RETURN_MODES: { value: ReturnMode; label: string }[] = [
  { value: 'return_to_start', label: 'Return to start and land' },
  { value: 'land_at_destination', label: 'Land at the last point' },
]
export const PAYLOAD_CATEGORIES: { value: PayloadCategory; label: string }[] = [
  { value: 'relief_supplies', label: 'Relief supplies' },
  { value: 'medical_kit', label: 'Medical kit' },
  { value: 'flotation_device', label: 'Flotation device' },
]

export interface ParamSpec {
  key: keyof TaskParameters
  label: string
  kind: 'number' | 'point' | 'points' | 'select'
  required: boolean
  min?: number; max?: number; step?: number
  help: string
  example: string
}
export interface TaskSpec { value: TaskType; label: string; summary: string; params: ParamSpec[] }

const P = (spec: ParamSpec): ParamSpec => spec
export const TASK_SPECS: TaskSpec[] = [
  { value: 'expanding_square_search', label: 'Expanding square search', summary: 'A square spiral outward from a datum point.', params: [
    P({ key: 'datum', label: 'Datum point', kind: 'point', required: true, help: 'Centre of the search, picked on the map.', example: 'Click the last known position.' }),
    P({ key: 'track_spacing_m', label: 'Track spacing (m)', kind: 'number', required: true, min: 2, max: 1000, step: 1, help: 'Distance between spiral tracks.', example: '30' }),
    P({ key: 'search_radius_m', label: 'Search radius (m)', kind: 'number', required: true, min: 1, max: 20000, step: 1, help: 'How far the spiral extends from the datum.', example: '200' }),
    P({ key: 'heading_deg', label: 'First leg heading (deg)', kind: 'number', required: false, min: 0, max: 359, step: 1, help: 'Bearing of the first leg, clockwise from north.', example: '0' }),
  ] },
  { value: 'sector_search', label: 'Sector search', summary: 'Triangular sweeps through a datum point.', params: [
    P({ key: 'datum', label: 'Datum point', kind: 'point', required: true, help: 'Point every sweep passes through.', example: 'Click the datum on the map.' }),
    P({ key: 'sector_radius_m', label: 'Sector radius (m)', kind: 'number', required: true, min: 1, max: 20000, step: 1, help: 'Length of each sweep leg.', example: '150' }),
    P({ key: 'sector_angle_deg', label: 'Sector angle (deg)', kind: 'number', required: false, min: 30, max: 180, step: 1, help: 'Angular width of each sweep (120 with 3 sweeps covers a full circle).', example: '120' }),
    P({ key: 'sector_count', label: 'Number of sweeps', kind: 'number', required: false, min: 1, max: 6, step: 1, help: 'How many sweeps to fly.', example: '3' }),
    P({ key: 'heading_deg', label: 'First sweep heading (deg)', kind: 'number', required: false, min: 0, max: 359, step: 1, help: 'Bearing where the first sweep starts.', example: '0' }),
  ] },
  { value: 'coverage_survey', label: 'Coverage survey', summary: 'Parallel tracks (lawnmower) over the operating area.', params: [
    P({ key: 'line_spacing_m', label: 'Line spacing (m)', kind: 'number', required: true, min: 2, max: 1000, step: 1, help: 'Distance between parallel tracks.', example: '25' }),
    P({ key: 'heading_deg', label: 'Track heading (deg)', kind: 'number', required: false, min: 0, max: 359, step: 1, help: 'Direction the tracks run, clockwise from north.', example: '90' }),
  ] },
  { value: 'perimeter_patrol', label: 'Perimeter patrol', summary: 'Fly the boundary of the operating area, or your own path, for several laps.', params: [
    P({ key: 'path', label: 'Custom path (optional)', kind: 'points', required: false, help: 'Map points to patrol instead of the area boundary.', example: 'Click two or more points.' }),
    P({ key: 'laps', label: 'Laps', kind: 'number', required: false, min: 1, max: 10, step: 1, help: 'Times to repeat the patrol.', example: '2' }),
  ] },
  { value: 'point_inspection', label: 'Point inspection', summary: 'Hover over, or orbit, each selected point.', params: [
    P({ key: 'points', label: 'Inspection points', kind: 'points', required: true, help: 'Points to inspect, picked on the map.', example: 'Click the sites on the map.' }),
    P({ key: 'orbit_radius_m', label: 'Orbit radius (m, 0 = hover)', kind: 'number', required: false, min: 0, max: 500, step: 1, help: 'Radius of the orbit around each point; 0 hovers over it.', example: '25' }),
    P({ key: 'orbit_points', label: 'Waypoints per orbit', kind: 'number', required: false, min: 4, max: 16, step: 1, help: 'Waypoints used to approximate each orbit.', example: '8' }),
    P({ key: 'hover_s', label: 'Hover per point (s)', kind: 'number', required: false, min: 0, max: 3600, step: 1, help: 'Time spent at each point.', example: '10' }),
  ] },
  { value: 'waypoint_route', label: 'Waypoint route', summary: 'Fly the ordered map points you click.', params: [
    P({ key: 'points', label: 'Waypoints', kind: 'points', required: true, help: 'Ordered route points picked on the map.', example: 'Click the route points in order.' }),
  ] },
  { value: 'loiter_hold', label: 'Loiter / hold', summary: 'Climb, hold position, then return. Uses the loiter duration.', params: [
    P({ key: 'hold_point', label: 'Hold point (optional)', kind: 'point', required: false, help: 'Where to hold; defaults to above the start.', example: 'Click on the map.' }),
  ] },
  { value: 'payload_dropoff', label: 'Relief supply drop-off (simulated)', summary: 'Fly to a drop point, descend to a low hover and log a simulated payload_released event. No release command is sent.', params: [
    P({ key: 'drop_point', label: 'Drop point', kind: 'point', required: true, help: 'Map point inside the operating area.', example: 'Click the delivery spot.' }),
    P({ key: 'drop_altitude_m', label: 'Drop altitude (m)', kind: 'number', required: true, min: 1, max: 500, step: 0.5, help: 'Low hover height at the drop point; must not exceed the altitude limit.', example: '8' }),
    P({ key: 'hover_duration_s', label: 'Hover duration (s)', kind: 'number', required: true, min: 0, max: 3600, step: 1, help: 'Seconds hovering before the simulated release.', example: '20' }),
    P({ key: 'payload_category', label: 'Payload category', kind: 'select', required: true, help: 'Fixed list: relief supplies, medical kit or flotation device.', example: 'Medical kit' }),
  ] },
]
export const TASK_TYPE_VALUES: TaskType[] = TASK_SPECS.map(item => item.value)
export const taskSpec = (value: TaskType | null | undefined): TaskSpec | undefined => TASK_SPECS.find(item => item.value === value)

export interface AircraftFieldSpec { key: string; label: string; help: string; example: string; command?: string }
/** Aircraft-level fields (everything except per-task parameters). */
export const AIRCRAFT_FIELD_SPECS: AircraftFieldSpec[] = [
  { key: 'role', label: 'Role', help: 'Primary, decoy or standby. Read by the validator (one primary) and by later threat/reassignment research.', example: 'Primary', command: 'Say "set role of UAV 1 to primary".' },
  { key: 'priority', label: 'Priority (1-3)', help: 'Reassignment priority; 1 is highest. Carried to Mission Control.', example: '1', command: 'Say "set priority of UAV 1 to 2".' },
  { key: 'reassignable', label: 'Reassignable', help: 'Whether the aircraft may be taken over by another task when needed.', example: 'Yes' },
  { key: 'task_type', label: 'Task type', help: 'What the aircraft does; it decides which parameters appear and which route is generated.', example: 'Coverage survey', command: 'Say "set task type of UAV 1 to coverage survey".' },
  { key: 'start_location', label: 'Start location (map)', help: 'Home position the route is planned around, picked on the map. Typed coordinates are not accepted.', example: 'Click the launch point.' },
  { key: 'altitude_m', label: 'Cruise altitude (m)', help: 'Cruise height; checked against the vehicle and mission altitude limits.', example: '30', command: 'Say "set altitude to 30".' },
  { key: 'speed_mps', label: 'Speed (m/s)', help: 'Cruise speed; used to estimate duration and checked against the vehicle limit.', example: '5', command: 'Say "set speed to 5".' },
  { key: 'loiter_duration_s', label: 'Loiter at destination (s)', help: 'Hover time after the task ends (the hold time for a loiter task).', example: '30', command: 'Say "set loiter to 30".' },
  { key: 'return_mode', label: 'Return mode', help: 'Return to the start and land, or land at the last point.', example: 'Return to start and land' },
  { key: 'route', label: 'Generated route', help: 'The waypoints the backend generated and validated; shown on the map and stored in the version.', example: 'Press GENERATE / PREVIEW ROUTE.' },
]

export const MISSION_FIELD_SPECS: AircraftFieldSpec[] = [
  { key: 'objectives', label: 'Mission objectives', help: 'Descriptive labels; each only suggests a default task type.', example: 'Search and Rescue', command: 'Say "change the mission objective to Search and Rescue".' },
  { key: 'operating_area', label: 'Operating area (map)', help: 'A circle or polygon on the map. Search points must lie inside it and coverage surveys fill it.', example: 'Draw a circle or polygon.' },
  { key: 'required_uav_count', label: 'Required UAVs (1-5)', help: 'How many disarmed PX4/Gazebo aircraft the mission needs; counted only after telemetry and a Gazebo pose are verified.', example: '2', command: 'Say "I need 2 UAVs".' },
  { key: 'max_distance_from_start_m', label: 'Max distance from start (m)', help: 'Enforced: every generated waypoint must be within this distance of the aircraft start.', example: '1500' },
  { key: 'max_altitude_m', label: 'Max altitude (m)', help: 'Enforced: no waypoint may be higher than this.', example: '100' },
  { key: 'max_duration_minutes', label: 'Max duration (min, optional)', help: 'If set, the estimated route time must not exceed it.', example: '20', command: 'Say "set max duration to 20 minutes".' },
  { key: 'description', label: 'Description (optional)', help: 'A short purpose statement shown in review.', example: 'Search the shoreline.' },
  { key: 'notes', label: 'Notes (optional)', help: 'One free-text note for reviewers. Nothing reads it automatically.', example: 'Coordinate with range control.' },
]

/** A schema-2 starting point built from a stored legacy (schema 1) intent. The legacy version itself is never modified. */
export function prefillPlanFromLegacy(legacy: { mission_description?: string; constraints?: string[]; geographic_mission?: { objective?: string; objectives?: string[]; mission_description?: string } | null }): MissionPlan {
  const known = new Set<string>(MISSION_OBJECTIVES.map(item => item.value))
  const legacyMission = legacy.geographic_mission
  const raw = legacyMission?.objectives?.length ? legacyMission.objectives : legacyMission?.objective ? [legacyMission.objective] : []
  const constraints = (legacy.constraints ?? []).filter(Boolean)
  return {
    ...EMPTY_PLAN,
    objectives: [...new Set(raw.filter(value => known.has(value)))] as ObjectiveValue[],
    description: (legacy.mission_description || legacyMission?.mission_description || '').slice(0, 500),
    notes: constraints.length ? `Legacy constraints (text only): ${constraints.join('; ')}`.slice(0, 500) : '',
  }
}

export function newAircraft(uavId: string, index: number): AircraftPlan {
  return {
    uav_id: uavId, role: index === 0 ? 'primary' : 'standby', priority: 2, reassignable: true, task_type: null, parameters: {},
    start_location: null, altitude_m: null, speed_mps: null, loiter_duration_s: 0, return_mode: 'return_to_start',
    route: null, confirmed: false,
  }
}

export function normalizePlan(plan: Partial<MissionPlan> | null | undefined): MissionPlan {
  if (!plan) return EMPTY_PLAN
  return {
    ...EMPTY_PLAN, ...plan,
    constraints: { ...EMPTY_PLAN.constraints, ...(plan.constraints ?? {}) },
    aircraft: (plan.aircraft ?? []).map((item, index) => ({ ...newAircraft(item.uav_id, index), ...item, parameters: item.parameters ?? {} })),
  }
}

const present = (value: unknown): boolean => value !== null && value !== undefined && value !== '' && !(Array.isArray(value) && value.length === 0)

/** Missing required inputs for one aircraft, mirroring the backend (`validation.REQUIRED_PARAMETERS`). */
export function missingAircraftInputs(aircraft: AircraftPlan): { key: string; label: string; help: string; example: string; command?: string }[] {
  const missing: { key: string; label: string; help: string; example: string; command?: string }[] = []
  const field = (key: string) => AIRCRAFT_FIELD_SPECS.find(item => item.key === key)!
  if (!aircraft.role) missing.push(field('role'))
  if (!aircraft.task_type) missing.push(field('task_type'))
  if (!aircraft.start_location) missing.push(field('start_location'))
  if (aircraft.altitude_m === null || aircraft.altitude_m === undefined) missing.push(field('altitude_m'))
  if (aircraft.speed_mps === null || aircraft.speed_mps === undefined) missing.push(field('speed_mps'))
  const spec = taskSpec(aircraft.task_type)
  for (const param of spec?.params ?? []) {
    if (param.required && !present(aircraft.parameters[param.key])) missing.push({ key: param.key, label: param.label, help: param.help, example: param.example })
  }
  if (aircraft.task_type === 'loiter_hold' && !(aircraft.loiter_duration_s > 0)) missing.push(field('loiter_duration_s'))
  if (missing.length === 0 && !aircraft.route) missing.push(field('route'))
  return missing
}

export const isAircraftComplete = (aircraft: AircraftPlan): boolean => aircraft.confirmed && missingAircraftInputs(aircraft).length === 0

export function isPlanComplete(plan: MissionPlan): boolean {
  const expected = plan.required_uav_count ?? 0
  return expected > 0 && plan.selected_uav_ids.length === expected && plan.activated_uav_ids.length === expected
    && plan.aircraft.length === expected && plan.aircraft.every(isAircraftComplete)
}

/** Simulated X500 endurance served by the backend (`GET /api/mission-plans/limits`, config TRINETRA_MP_MAX_FLIGHT_*). */
export interface EnduranceLimit { duration_s: number; length_m: number }
export const ENDURANCE_ADVICE = 'Reduce the area, widen track spacing, or raise speed.'

/** Blocking messages for one generated route against the aircraft limit; the same wording the backend returns. */
export function enduranceIssues(route: { duration_s: number; length_m: number } | null | undefined, limit: EnduranceLimit | null | undefined): string[] {
  if (!route || !limit) return []
  const issues: string[] = []
  if (route.duration_s > limit.duration_s) issues.push(`Route takes about ${(route.duration_s / 60).toFixed(0)} min; the aircraft limit is ${+(limit.duration_s / 60).toFixed(2)} min. ${ENDURANCE_ADVICE}`)
  if (route.length_m > limit.length_m) issues.push(`Route is about ${(route.length_m / 1000).toFixed(1)} km long; the aircraft limit is ${+(limit.length_m / 1000).toFixed(2)} km. ${ENDURANCE_ADVICE}`)
  return issues
}

export interface PlanValidationContext { title: string; plan: MissionPlan; fleetVerified: boolean; availableVehicleIds: string[]; endurance?: EnduranceLimit | null }

export const FLEET_NOT_VERIFIED_ITEM = 'The selected PX4 processes, telemetry and Gazebo models have not been freshly verified.'
export const WORKFLOW_ITEM = 'Complete the A–C scenario workflow and final review.'

/** Section C (review) checklist (client mirror; the backend re-validates on save, approval and deploy). */
export function computePlanMissing({ title, plan, fleetVerified, availableVehicleIds, endurance }: PlanValidationContext): string[] {
  const primaries = plan.aircraft.filter(item => item.role === 'primary').length
  return [
    !title.trim() ? 'Scenario name is required.' : '',
    !plan.objectives.length ? 'Select at least one mission objective.' : '',
    !plan.operating_area ? 'Select the operating area on the map.' : '',
    plan.constraints.max_distance_from_start_m === null ? 'Set the maximum distance from start.' : '',
    plan.constraints.max_altitude_m === null ? 'Set the maximum altitude.' : '',
    !plan.selected_uav_ids.length ? 'Select at least one connected PX4 aircraft.' : '',
    !plan.required_uav_count || plan.required_uav_count !== plan.selected_uav_ids.length || plan.aircraft.length !== plan.selected_uav_ids.length
      ? 'Requested, activated and assigned UAV counts must match.' : '',
    plan.activated_uav_ids.length !== (plan.required_uav_count ?? 0) || plan.activated_uav_ids.some(id => !plan.selected_uav_ids.includes(id))
      ? 'The requested PX4/Gazebo fleet activation is not recorded for this exact selection.' : '',
    !fleetVerified ? FLEET_NOT_VERIFIED_ITEM : '',
    plan.workflow_stage !== 'review' ? WORKFLOW_ITEM : '',
    plan.selected_uav_ids.some(id => !availableVehicleIds.includes(id)) ? 'One or more selected aircraft are not currently available.' : '',
    plan.aircraft.some(item => !isAircraftComplete(item)) ? 'Every selected aircraft needs a generated route and a confirmed mission.' : '',
    ...plan.aircraft.flatMap(item => enduranceIssues(item.route, endurance).map(text => `${item.uav_id}: ${text}`)),
    ...plan.aircraft.filter(item => item.task_type && !allowedTaskTypes(plan.objectives).includes(item.task_type))
      .map(item => `${item.uav_id}: the ${taskSpec(item.task_type)?.label ?? item.task_type} task is not allowed for the selected objectives.`),
    plan.aircraft.length && !plan.allow_multiple_primary && primaries !== 1 ? 'Exactly one aircraft must have the primary role.' : '',
    plan.aircraft.length && plan.allow_multiple_primary && primaries < 1 ? 'At least one aircraft must have the primary role.' : '',
  ].filter(Boolean)
}

export type PlanDeployContext = PlanValidationContext & {
  hasSavedVersion: boolean; hasUnsavedChanges: boolean; isOperator: boolean; approvalStatus: string | null; canManage: boolean
}

export function computePlanDeployBlockers(context: PlanDeployContext): string[] {
  const blockers = [...computePlanMissing(context)]
  if (!context.canManage) blockers.push('Your account cannot create or edit scenario drafts.')
  if (!context.hasSavedVersion) blockers.push('Save the draft first: deployment uses the saved immutable version.')
  else if (context.hasUnsavedChanges) blockers.push('There are unsaved changes: save a new immutable version before deploying.')
  if (context.isOperator && context.approvalStatus !== 'APPROVED') {
    blockers.push(context.approvalStatus
      ? `Operators can deploy only an approved version; this version is ${context.approvalStatus}.`
      : 'Operators need General approval of this exact version: submit it for review first.')
  }
  return blockers
}

// --- the next action after saving (deploy or submit) -------------------------------------------------------------

/** How fresh the fleet check is. `stale`: it was verified, but that result is older than the freshness window. */
export type FleetVerification = 'verified' | 'not_verified' | 'stale' | 'checking'
export const FLEET_VERIFICATION_LABEL: Record<FleetVerification, string> = {
  verified: 'VERIFIED', not_verified: 'NOT VERIFIED', stale: 'STALE', checking: 'CHECKING',
}
export const DEPLOY_REASON = {
  fleetNotVerified: 'Fleet not freshly verified: run Activate and verify',
  fleetStale: 'Fleet verification is stale: run Activate and verify',
  fleetChecking: 'Fleet verification is still running',
  unsaved: 'Unsaved changes',
  superseded: 'Version superseded',
  role: 'Your account cannot create or edit scenarios',
  legacy: 'Legacy version: read-only',
  pending: 'Already submitted: waiting for General review',
  rejected: 'This version was rejected: save a new version to submit again',
  changesRequested: 'Changes were requested: save a new version to submit again',
} as const

export interface NextActionInput {
  isOperator: boolean
  canManage: boolean
  hasUnsavedChanges: boolean
  superseded: boolean
  fleet: FleetVerification
  /** Section C checklist items (`computePlanMissing`). */
  checklist: string[]
  approvalStatus: string | null
  legacy?: boolean
}
export interface NextAction {
  kind: 'deploy' | 'submit'
  label: 'DEPLOY TO MISSION CONTROL' | 'SUBMIT FOR REVIEW'
  enabled: boolean
  /** Exact reasons the action is unavailable; empty when it is enabled. Shown beside the button, never hidden. */
  reasons: string[]
}

/**
 * The one action offered beneath Save once a version exists. GENERAL deploys; an OPERATOR submits for review and,
 * once that exact version is approved, deploys. The backend enforces every one of these rules again.
 */
export function computeNextAction(input: NextActionInput): NextAction {
  const kind: NextAction['kind'] = input.isOperator && input.approvalStatus !== 'APPROVED' ? 'submit' : 'deploy'
  const reasons: string[] = []
  if (input.legacy) reasons.push(DEPLOY_REASON.legacy)
  if (!input.canManage) reasons.push(DEPLOY_REASON.role)
  if (input.hasUnsavedChanges) reasons.push(DEPLOY_REASON.unsaved)
  if (input.superseded) reasons.push(DEPLOY_REASON.superseded)
  const planItems = input.checklist.filter(item => item !== FLEET_NOT_VERIFIED_ITEM)
  if (planItems.length) reasons.push(`Plan incomplete: ${planItems.length} item(s) in the checklist above`)
  if (input.fleet === 'not_verified') reasons.push(DEPLOY_REASON.fleetNotVerified)
  else if (input.fleet === 'stale') reasons.push(DEPLOY_REASON.fleetStale)
  else if (input.fleet === 'checking') reasons.push(DEPLOY_REASON.fleetChecking)
  if (kind === 'submit') {
    if (input.approvalStatus === 'PENDING') reasons.push(DEPLOY_REASON.pending)
    else if (input.approvalStatus === 'REJECTED') reasons.push(DEPLOY_REASON.rejected)
    else if (input.approvalStatus === 'CHANGES_REQUESTED') reasons.push(DEPLOY_REASON.changesRequested)
  }
  return { kind, label: kind === 'deploy' ? 'DEPLOY TO MISSION CONTROL' : 'SUBMIT FOR REVIEW', enabled: reasons.length === 0, reasons }
}

export { MISSION_DURATION_RANGE_MINUTES, UAV_COUNT_RANGE }

/** Areas drawn on the map are previewed as polygons; circles are approximated for display only. */
export function areaPreviewVertices(area: OperatingArea | null, segments = 48): GeoPoint[] {
  if (!area) return []
  if (area.kind === 'polygon') return area.vertices
  if (!area.centre || !area.radius_m) return []
  const metresPerDegree = 111_320
  const cosLat = Math.max(0.01, Math.cos((area.centre.latitude * Math.PI) / 180))
  return Array.from({ length: segments }, (_, index) => {
    const angle = (2 * Math.PI * index) / segments
    return {
      latitude: area.centre!.latitude + (area.radius_m! * Math.cos(angle)) / metresPerDegree,
      longitude: area.centre!.longitude + (area.radius_m! * Math.sin(angle)) / (metresPerDegree * cosLat),
    }
  })
}
