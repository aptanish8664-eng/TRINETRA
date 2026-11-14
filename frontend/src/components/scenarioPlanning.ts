import type { GeographicMissionIntent, GeographicMissionObjective, GeographicUavAssignment } from '../services/auth'

export const MISSION_OBJECTIVES: {
  value: GeographicMissionObjective
  title: string
  description: string
}[] = [
  { value: 'area_survey', title: 'Area Surveillance', description: 'Observe and survey a defined geographic area.' },
  { value: 'search_rescue', title: 'Search and Rescue', description: 'Organize an advisory search and observation mission.' },
  { value: 'perimeter_monitoring', title: 'Border / Perimeter Monitoring', description: 'Plan monitoring coverage along a defined boundary.' },
  { value: 'inspection', title: 'Infrastructure Inspection', description: 'Plan inspection tasks for operator-defined sites.' },
  { value: 'disaster_assessment', title: 'Disaster Assessment', description: 'Plan an advisory survey of a response area.' },
  { value: 'agricultural_survey', title: 'Agricultural Surveying', description: 'Plan coverage of an operator-defined agricultural area.' },
  { value: 'mapping_survey', title: 'Mapping and Terrain Survey', description: 'Plan a mapped area for later operator review.' },
  { value: 'target_tracking', title: 'Target Tracking', description: 'Plan observation tasks; no tracking capability is implied.' },
  { value: 'environmental_monitoring', title: 'Environmental Monitoring', description: 'Plan observation tasks for an operator-defined area.' },
  { value: 'custom_mission', title: 'Custom Mission', description: 'Describe a different objective for operator review.' },
]

export const DEFAULT_GEOGRAPHIC_MISSION: GeographicMissionIntent = {
  objective: 'area_survey',
  start_location: null,
  destination: null,
  survey_zones: [],
  uav_assignments: [],
  constraints: [],
  ai_recommendations: [],
  objectives: [],
  fleet_size: null,
  required_uav_count: null,
  activated_uav_ids: [],
  mission_description: '',
  mission_area_description: '',
  mission_duration_minutes: null,
  workflow_stage: 'general',
  current_uav_step: 'role',
  current_uav_index: 0,
  selected_uav_ids: [],
}

export function normalizeGeographicMission(
  mission: GeographicMissionIntent | null | undefined,
): GeographicMissionIntent {
  if (!mission) return DEFAULT_GEOGRAPHIC_MISSION
  return {
    ...DEFAULT_GEOGRAPHIC_MISSION,
    ...mission,
    objectives: mission.objectives?.length ? mission.objectives : [mission.objective],
    fleet_size: mission.fleet_size ?? (mission.uav_assignments.length || null),
    required_uav_count: mission.required_uav_count ?? mission.fleet_size ?? (mission.uav_assignments.length || null),
    activated_uav_ids: mission.activated_uav_ids ?? [],
    mission_description: mission.mission_description ?? '',
    mission_area_description: mission.mission_area_description ?? '',
    mission_duration_minutes: mission.mission_duration_minutes ?? null,
    workflow_stage: mission.workflow_stage ?? 'general',
    current_uav_step: mission.current_uav_step ?? 'role',
    current_uav_index: mission.current_uav_index ?? 0,
    selected_uav_ids: mission.selected_uav_ids ?? mission.uav_assignments.map(item => item.uav_id),
    uav_assignments: mission.uav_assignments.map(assignment => ({
      ...assignment,
      zone_ids: assignment.zone_ids ?? (assignment.zone_id ? [assignment.zone_id] : []),
      waypoints: assignment.waypoints ?? [],
      constraints: assignment.constraints ?? [],
      individual_objective: assignment.individual_objective ?? '',
      flight_altitude_m: assignment.flight_altitude_m ?? null,
      speed_limit_m_s: assignment.speed_limit_m_s ?? null,
      destination_action: assignment.destination_action ?? '',
      action_duration_seconds: assignment.action_duration_seconds ?? null,
      data_requirements: assignment.data_requirements ?? '',
      completion_condition: assignment.completion_condition ?? '',
      expected_result: assignment.expected_result ?? '',
      return_destination: assignment.return_destination ?? null,
      return_waypoints: assignment.return_waypoints ?? [],
      return_completion_condition: assignment.return_completion_condition ?? '',
      confirmed: assignment.confirmed ?? false,
    })),
  }
}

export function isAircraftMissionComplete(assignment: GeographicUavAssignment): boolean {
  return assignment.confirmed === true
    && Boolean(assignment.role.trim())
    && Boolean(assignment.task.trim())
    && Boolean(assignment.start_location)
    && Boolean(assignment.destination)
    && Boolean(assignment.individual_objective?.trim())
    && Boolean(assignment.destination_action?.trim())
    && assignment.action_duration_seconds !== null && assignment.action_duration_seconds !== undefined
    && Boolean(assignment.data_requirements?.trim())
    && Boolean(assignment.completion_condition?.trim())
    && Boolean(assignment.expected_result?.trim())
    && Boolean(assignment.return_destination)
    && Boolean(assignment.return_completion_condition?.trim())
    && assignment.flight_altitude_m !== null && assignment.flight_altitude_m !== undefined
    && assignment.speed_limit_m_s !== null && assignment.speed_limit_m_s !== undefined
}

export function isGeographicMissionComplete(mission: GeographicMissionIntent): boolean {
  const expected = mission.fleet_size ?? 0
  return expected > 0
    && mission.required_uav_count === expected
    && (mission.activated_uav_ids?.length ?? 0) === expected
    && mission.uav_assignments.length === expected
    && (mission.selected_uav_ids?.length ?? expected) === expected
    && mission.uav_assignments.every(isAircraftMissionComplete)
}

export type MissionValidationContext = {
  title: string
  mission: GeographicMissionIntent
  fleetVerified: boolean
  availableVehicleIds: string[]
}

/** Legacy (schema 1) validation messages. Schema-2 plans use `computePlanMissing` in missionPlan.ts. */
export function computeMissionMissing({ title, mission, fleetVerified, availableVehicleIds }: MissionValidationContext): string[] {
  const selectedAircraftIds = mission.selected_uav_ids ?? []
  const missionComplete = isGeographicMissionComplete(mission)
  return [
    !title.trim() ? 'Scenario name is required.' : '',
    !(mission.objectives?.length) ? 'Select at least one mission objective.' : '',
    !mission.mission_description?.trim() ? 'Add a mission description.' : '',
    !selectedAircraftIds.length ? 'Select at least one connected PX4 aircraft.' : '',
    !mission.required_uav_count || mission.required_uav_count !== selectedAircraftIds.length ? 'Requested, activated and assigned UAV counts must match.' : '',
    (mission.activated_uav_ids?.length ?? 0) !== (mission.required_uav_count ?? 0) || mission.activated_uav_ids?.some(id => !selectedAircraftIds.includes(id)) ? 'The requested PX4/Gazebo fleet activation is not recorded for this exact selection.' : '',
    !fleetVerified ? 'The selected PX4 processes, telemetry and Gazebo models have not been freshly verified.' : '',
    !mission.mission_area_description?.trim() ? 'Mission area description is required.' : '',
    mission.workflow_stage !== 'review' ? 'Complete the scenario workflow and final review.' : '',
    selectedAircraftIds.some(id => !availableVehicleIds.includes(id)) ? 'One or more selected aircraft are not currently available.' : '',
    mission.uav_assignments.some(item => !isAircraftMissionComplete(item)) ? 'Every selected aircraft must have a confirmed individual mission.' : '',
    missionComplete && ['area_survey', 'perimeter_monitoring', 'agricultural_survey', 'mapping_survey'].includes(mission.objective) && !mission.survey_zones.length
      ? 'This objective requires at least one individually mapped area.' : '',
  ].filter(Boolean)
}

export type DeployContext = MissionValidationContext & {
  hasSavedVersion: boolean
  hasUnsavedChanges: boolean
  isOperator: boolean
  approvalStatus: string | null
  canManage: boolean
}

/** Legacy (schema 1) deploy blockers: validation plus save/approval state. */
export function computeDeployBlockers(context: DeployContext): string[] {
  const blockers = [...computeMissionMissing(context)]
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

export function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([a], [b]) => a.localeCompare(b))
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`).join(',')}}`
  }
  return JSON.stringify(value)
}

/** Allowed fleet size for one mission; shared by the form, the assistant validation and the assistant option picker. */
export const UAV_COUNT_RANGE = { min: 1, max: 5 } as const
export const MISSION_DURATION_RANGE_MINUTES = { min: 1, max: 1440 } as const
