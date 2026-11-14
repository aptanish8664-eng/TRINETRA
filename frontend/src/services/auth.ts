import type { MissionState, RunRecord } from '../types'

export type ApplicationRole = 'GENERAL' | 'OPERATOR'
export type AccountStatus = 'PENDING' | 'ON_HOLD' | 'APPROVED' | 'REJECTED' | 'DEACTIVATED'

export interface LoginCredentials {
  username: string
  password: string
}

export interface UserSession {
  expires_at: string
  csrf_token: string
  permissions: string[]
  user: {
    id: string
    username: string
    full_name: string
    email: string | null
    role: ApplicationRole
    account_status: 'APPROVED'
  }
}

export interface RegistrationApplication {
  full_name: string
  username: string
  email: string | null
  password: string
  requested_role: ApplicationRole
  justification: string
}

export interface RegistrationReceipt {
  application_id: string
  status: 'PENDING'
}

export interface RegistrationStatus {
  application_id: string
  status: AccountStatus
  rejection_message: string | null
  assigned_role: ApplicationRole | null
}

export interface ManagedUser {
  id: string
  full_name: string
  username: string
  email: string | null
  requested_role: ApplicationRole | null
  role: ApplicationRole | null
  account_status: AccountStatus
  registration_note: string | null
  rejection_reason: string | null
  created_at: string
  updated_at: string
  approved_at: string | null
  last_login_at: string | null
}

export interface AuditLogEntry {
  id: string
  created_at: string
  actor_user_id: string | null
  actor_name: string | null
  actor_role: ApplicationRole | null
  event_type: string
  target_type: string | null
  target_id: string | null
  outcome: string
  reason: string
  request_id: string
  metadata: Record<string, unknown>
}

export type RestrictedCommand = 'stop' | 'reset' | 'isolate_uav' | 'reassign_task'

export interface CommandDecision {
  id: string
  requester_id: string
  requester_username: string
  reviewer_id: string | null
  run_id: string
  command: RestrictedCommand
  payload: { uav_id?: string | null; task_id?: string | null; target_uav_id?: string | null }
  status: 'pending' | 'executing' | 'approved' | 'rejected' | 'failed'
  created_at: string
  decided_at: string | null
  decision_reason: string | null
}

export interface ScenarioVersion {
  id: string
  version_number: number
  config: ScenarioConfig
  intent: ScenarioIntent
  config_hash: string
  created_at: string
  sandbox_run_id?: string | null
  approval: {
    id: string
    status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED'
    reason: string | null
    submitted_at: string
    reviewed_at: string | null
    reviewed_by: string | null
  } | null
}

export interface GeographicPoint {
  latitude: number
  longitude: number
}

export interface GeographicZone {
  id: string
  name: string
  vertices: GeographicPoint[]
}

export type GeographicMissionObjective =
  | 'area_survey'
  | 'search_rescue'
  | 'perimeter_monitoring'
  | 'inspection'
  | 'disaster_assessment'
  | 'agricultural_survey'
  | 'mapping_survey'
  | 'target_tracking'
  | 'environmental_monitoring'
  | 'custom_mission'
  | 'search_observation'
  | 'communication_relay'

export type GeographicUavRole = string

export interface GeographicUavAssignment {
  uav_id: string
  role: GeographicUavRole
  individual_objective?: string
  task: string
  zone_id: string | null
  zone_ids?: string[]
  start_location?: GeographicPoint | null
  destination?: GeographicPoint | null
  waypoints?: GeographicPoint[]
  constraints?: string[]
  flight_altitude_m?: number | null
  speed_limit_m_s?: number | null
  destination_action?: string
  action_duration_seconds?: number | null
  data_requirements?: string
  completion_condition?: string
  expected_result?: string
  return_destination?: GeographicPoint | null
  return_waypoints?: GeographicPoint[]
  return_completion_condition?: string
  confirmed?: boolean
}

export interface GeographicMissionIntent {
  objective: GeographicMissionObjective
  start_location: GeographicPoint | null
  destination: GeographicPoint | null
  survey_zones: GeographicZone[]
  uav_assignments: GeographicUavAssignment[]
  constraints: string[]
  ai_recommendations: string[]
  objectives?: GeographicMissionObjective[]
  fleet_size?: number | null
  required_uav_count?: number | null
  activated_uav_ids?: string[]
  mission_description?: string
  mission_area_description?: string
  mission_duration_minutes?: number | null
  workflow_stage?: 'general' | 'aircraft' | 'faults' | 'review'
  current_uav_step?: 'role' | 'task' | 'start' | 'destination' | 'geography' | 'constraints' | 'review'
  current_uav_index?: number
  selected_uav_ids?: string[]
}

export interface PlannerConversationMessage {
  role: 'user' | 'assistant'
  content: string
}

export type ScenarioFaultType =
  | 'none'
  | 'sudden'
  | 'gradual'
  | 'intermittent'
  | 'jump'
  | 'freeze'
  | 'noise'
  | 'degradation'
  | 'velocity_inconsistency'

export interface ScenarioIntent {
  mission_objectives: string[]
  mission_description?: string
  proposed_tasks: { description: string; task_reference: string | null; assigned_uav_id: string | null; priority: number }[]
  uav_role_suggestions: { role: 'general' | 'search' | 'monitor' | 'relay' | 'reserve'; count: number; rationale: string }[]
  operating_area_notes: string
  environment_notes: string
  constraints: string[]
  /** Legacy (schema 1) mission; read-only. New scenarios use `mission_plan` with schema_version 2. */
  geographic_mission: GeographicMissionIntent | null
  schema_version?: number
  mission_plan?: import('../components/missionPlan').MissionPlan | null
  planner_conversation?: PlannerConversationMessage[]
  uav_fault_assignments: {
    uav_id: string
    fault_type: ScenarioFaultType
    spoof_start: number
    spoof_duration: number
    spoof_magnitude: number
  }[]
}

export interface ScenarioConfig {
  uav_count: number
  task_count: number
  seed: number
  duration: number
  spoofing_type: string
  target_uav: string
  spoof_start: number
  spoof_duration: number
  spoof_magnitude: number
  response_policy: string
  detector_threshold: number | null
}

export interface ScenarioSummary {
  id: string
  owner_user_id: string
  title: string
  created_at: string
  updated_at: string
  latest_version: ScenarioVersion
}

export interface ScenarioDetail {
  id: string
  owner_user_id: string
  title: string
  created_at: string
  updated_at: string
  versions: ScenarioVersion[]
}

export interface MissionDeployment {
  id: string
  mission_id: string
  scenario_id: string
  scenario_version_id: string
  scenario_version_hash: string
  scenario_title: string
  execution_status: string
  requested_uav_count: number
  activated_uav_count: number
  assigned_uav_count: number
  approval: ScenarioApprovalRequest | null
  validation: { passed: boolean; validated_at: string }
  intent: ScenarioIntent
  /** Added by the backend on read: version number, schema, whether a newer version exists, and the mission start. */
  scenario_version_number?: number | null
  latest_version_number?: number | null
  schema_version?: number
  legacy?: boolean
  superseded?: boolean
  mission_start?: { latitude: number; longitude: number; altitude_m: number; uav_id: string; role: string | null; definition: string } | null
}

export interface ScenarioApprovalRequest {
  id: string
  scenario_id: string
  scenario_title: string
  scenario_version_id: string
  version_number: number
  config: Record<string, string | number | null>
  intent: ScenarioIntent
  config_hash: string
  owner_user_id: string
  owner_name: string
  status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED'
  reason: string | null
  reviewer_name: string | null
  submitted_at: string
  reviewed_at: string | null
  sandbox_run_id: string | null
  latest_version: boolean
}

export interface MissionBriefing {
  activeMission: MissionState | null
  recentEvents: MissionState['events']
  mostRecentRun: RunRecord | null
}

export interface AuthenticationApi {
  login(credentials: LoginCredentials): Promise<UserSession>
  register(application: RegistrationApplication): Promise<RegistrationReceipt>
}
