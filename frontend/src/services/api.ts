import type { GazeboPoseSnapshot, MissionState, Px4TelemetrySnapshot, RunRecord } from '../types'
import type {
  AuditLogEntry,
  CommandDecision,
  ManagedUser,
  LoginCredentials,
  ScenarioApprovalRequest,
  ScenarioSummary,
  UserSession,
  RestrictedCommand,
  RegistrationApplication,
  RegistrationReceipt,
  RegistrationStatus,
  ScenarioDetail,
  ScenarioConfig,
  ScenarioIntent,
  MissionDeployment,
  DeploymentOverview,
} from './auth'
import type { AIStatus, GeographicMissionPlanningRequest, GeographicMissionProposal, MissionAssessment, MissionConversation, MissionProposal, OperatorSimulationConfig, PendingMissionProposal } from './ai'

export function resolveApiBaseUrl(
  configuredApiUrl: string | undefined,
  protocol: string,
  hostname: string,
  development: boolean,
): string {
  if (!configuredApiUrl) return `${protocol}//${hostname}:8000`
  if (!development) return configuredApiUrl

  const configuredUrl = new URL(configuredApiUrl)
  const loopbackHosts = new Set(['localhost', '127.0.0.1'])
  if (
    loopbackHosts.has(hostname.toLowerCase())
    && loopbackHosts.has(configuredUrl.hostname.toLowerCase())
  ) {
    configuredUrl.hostname = hostname
    return configuredUrl.origin
  }
  return configuredApiUrl
}

const API = resolveApiBaseUrl(
  import.meta.env.VITE_API_URL,
  window.location.protocol,
  window.location.hostname,
  import.meta.env.DEV,
)
let csrfToken: string | null = null
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export type FleetCheck = { status: string; detail: string; [key: string]: unknown }
export type StructuredApiDetail = { code: string; message: string; checks?: Record<string, FleetCheck>; recovery?: string; [key: string]: unknown }
export interface SimulationLocation {
  scenario_id: string
  scenario_version_id: string
  mission_start: { latitude: number; longitude: number; altitude_m: number; uav_id: string; role: string | null; definition: string } | null
  aircraft: { aircraft_id: string; status: string; latitude: number | null; longitude: number | null; distance_to_start_m: number | null; distance_to_area_m: number | null }[]
  max_distance_to_start_m: number | null
  max_distance_to_area_m: number | null
  position_known: boolean
  relocation_recommended: boolean
  far_from_mission_area: boolean
  message: string
  thresholds: { relocation_notice_m: number; far_from_area_m: number; home_tolerance_m: number }
}
export interface ExecutionControl { allowed: boolean; reason: string | null }
/** One run of a deployed mission on the managed PX4 SITL instance, as recorded by the backend. */
export interface ExecutionView {
  id: string; deployment_id: string; scenario_id: string; scenario_version_id: string; aircraft_id: string
  state: string
  stage: 'starting' | 'uploaded' | 'executing' | 'completed' | 'aborted' | 'failed' | 'link_lost'
  reason: string; active: boolean; started_by_name: string; started_by_role: string
  created_at: string; updated_at: string; finished_at: string | null
  uploaded: boolean; armed: boolean; px4_mode: string | null
  route_total: number | null; route_reached: number | null; route_current: number | null; percent: number | null
  elapsed_s: number | null; estimated_s: number | null; px4_heartbeat_age_s: number | null; px4_messages: string[]
  controls: { pause: ExecutionControl; resume: ExecutionControl; abort: ExecutionControl; land?: ExecutionControl }
  landed_state?: string | null; on_ground?: boolean | null; on_ground_source?: string | null; relative_altitude_m?: number | null
  simulation_only: true
}
export interface PreflightCheck {
  key: string; label: string; ok: boolean; detail: string; offer_relocation?: boolean; code?: string
  /** aircraft_count: the machine runs fewer aircraft than the mission has (the plain sentence is `detail`) and what can be done about it */
  capacity?: number; required?: number; running?: number; offer_activation?: number | null; options?: { id: string; label: string; aircraft: number | null }[]
}
export interface ExecutionPreflight {
  deployment_id: string; aircraft_id?: string; can_start: boolean
  role_rule: { allowed: boolean; code: string; message: string }
  checks: PreflightCheck[]; blocking: string[]; confirmation: string
  estimated_duration_s?: number | null; route_waypoints?: number
}
export interface ExecutionEvent { id: number; execution_id: string; source: 'user' | 'backend' | 'executor'; type: string; message: string; data: Record<string, unknown>; actor_name: string | null; occurred_at: string }
export interface RelocationResult {
  relocated: boolean
  verified: boolean
  mission_start: NonNullable<SimulationLocation['mission_start']>
  checks: Record<string, FleetCheck>
  max_distance_to_start_m: number | null
  commands_sent_to_aircraft: false
  note: string
}

/** Land here: land at the current position and disarm (in the air or armed on the ground). */
export interface LandResult {
  aircraft_id: string; already_landing: boolean; mode: 'air' | 'ground' | null; state: 'landing' | 'disarmed'; forced?: false
  execution_id?: string | null; execution?: ExecutionView | null; started_at?: string; px4_messages?: string[]; simulation_only: true
}
/** `skipped` = not run: an earlier step failed (it is never left on running or waiting). */

/** Part E: the GPS threat summary (headline, probability, suggestion, story) and the operator actions. Software-only. */
export type ThreatLevel = 'clear' | 'watch' | 'suspected' | 'confirmed'
export interface StoryEvent { at: string; t: number; kind: string; aircraft_id: string | null; by: string | null; text: string; data: Record<string, unknown> }
export interface ThreatSuggestion { action: 'continue_monitoring' | 'reassign' | 'hold' | 'return_to_launch'; label: string; reason: string; level: ThreatLevel; taker?: string }
export interface ThreatSummary {
  deployment_id: string; level: ThreatLevel; headline: string; probability: number | null; aircraft_id: string | null; tier: string | null; threat_active: boolean
  suggestion: ThreatSuggestion | null; acknowledged: { at: string; by: string; reminder_probability: number } | null; raised_again: boolean
  acknowledgements: Array<{ aircraft_id: string; at: string; by: string; reminder_probability: number }>
  thresholds: { watch: number; act: number; confirmed: number }; explanation: Record<ThreatLevel, string>
  probability_series: Array<{ t: number; p: number }>; story: StoryEvent[]; monitor_ready: boolean; note: string
}
export interface RejoinPoint { index: number; waypoint_number: number; of: number; latitude: number; longitude: number; altitude_m: number; distance_m: number | null; why: string; is_landing: boolean; remaining_waypoints: number }
export interface ResumePreview { aircraft_id: string; deployment_id: string; run_state: string | null; airborne: boolean | null; rejoin: RejoinPoint; how: string; allowed: boolean; reason: string | null }
export interface ReassignCandidate {
  aircraft_id: string; role: string | null; state: string; has_active_run: boolean; run_state: string | null; battery_percent: number | null; link: string
  distance_from_task_m: number | null; monitor_state: string | null; ready: boolean; not_ready_reasons: string[]; verified: boolean
}
export type OriginalChoice = 'hold' | 'return_to_launch' | 'land' | 'decoy'
export interface ReassignOptions {
  aircraft_id: string; role: string | null; run_state: string | null; rejoin: RejoinPoint; candidates: ReassignCandidate[]
  original_choices: Array<{ value: OriginalChoice; label: string; meaning: string }>; what_happens: string
}
export interface AircraftSummaryRow {
  aircraft_id: string; role: string | null; plan_role?: string | null; link: string; gazebo: string | null; armed: boolean | null; mode: string | null; landed_state: string | null
  battery_percent: number | null; altitude_m: number | null; latitude: number | null; longitude: number | null; gps_valid: boolean | null
  pose_m: { x: number | null; y: number | null; z: number | null } | null; execution_state: string | null; route_reached: number | null; route_total: number | null
  verified: boolean; not_verified_reason: string | null; telemetry_age_s: number | null
}
export interface AircraftSummary { deployment_id: string; aircraft: AircraftSummaryRow[]; counts: { with_mission: number; verified: number; not_verified: Array<{ aircraft_id: string; reason: string }> }; safe_maximum: number }
export interface CapacityAssessment { level: 'ok' | 'warn' | 'overload'; requested: number; running: number; safe_maximum: number; host_free_mb: number | null; host_total_mb: number | null; estimated_needed_mb: number; reasons: string[]; advice: string }

export interface RestartStep { key: string; label: string; status: 'pending' | 'running' | 'ok' | 'failed' | 'skipped'; detail: string }
/** One aircraft of a (re)start: starting -> telemetry_ok / pose_ok -> ready, or failed with the reason. */
export interface RestartAircraft { aircraft_id: string; state: 'starting' | 'telemetry_ok' | 'pose_ok' | 'ready' | 'failed'; detail: string; manager_state?: string; telemetry?: boolean; pose?: boolean }
/** One restart of the simulation at a mission start (also the one-click mission switch: purpose "switch"). */
export interface RestartJob {
  id: string; status: 'running' | 'succeeded' | 'failed' | 'partial'; purpose: 'restart' | 'switch'; deployment_id: string; reason: string
  /** every aircraft with its own state and a live count ("3 of 5 ready, waiting for px4-4 and px4-5"); `partial`: only some came up */
  aircraft: RestartAircraft[]; requested_count: number; ready_count: number; summary: string; partial: boolean; accepted_partial: boolean; missing: string[]
  can_retry_missing: boolean; can_continue: boolean; retry_of: string | null
  steps: RestartStep[]; failure: { step: string | null; code: string; message: string } | null; can_retry: boolean; elapsed_s: number
  started_at: string; finished_at: string | null; started_by_name: string
  result: { mission_start: { latitude: number; longitude: number }; max_distance_to_start_m: number | null; armed: boolean | null; ended_executions: string[]; killed_processes: { kind: string; pid: number }[] } | null
  simulation_only: true
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly detail?: StructuredApiDetail) {
    super(message)
  }
}

async function refreshCsrfToken(): Promise<void> {
  const response = await fetch(`${API}/api/auth/session`, { credentials: 'include' })
  if (response.status === 401) {
    csrfToken = null
    window.dispatchEvent(new Event('trinetra:unauthorized'))
    throw new ApiError('Your session expired. Sign in again to continue.', 401)
  }
  if (!response.ok) {
    throw new ApiError('Could not refresh the session security token. Please retry.', response.status)
  }
  const session = await response.json() as UserSession
  if (!session.csrf_token) {
    csrfToken = null
    throw new ApiError('The server did not provide a valid session security token.', response.status)
  }
  csrfToken = session.csrf_token
}

async function responseError(response: Response): Promise<ApiError> {
  let detail = `${response.status} ${response.statusText}`
  try {
    const body: unknown = await response.json()
    if (typeof body !== 'object' || body === null || !('detail' in body)) {
      return new ApiError(detail, response.status)
    }
    const errorDetail = body.detail
    if (typeof errorDetail === 'string') {
      detail = errorDetail
    } else if (Array.isArray(errorDetail)) {
      const validationMessages = errorDetail.flatMap(item => {
        if (typeof item !== 'object' || item === null) return []
        const validationError = item as { loc?: unknown; msg?: unknown }
        if (typeof validationError.msg !== 'string') return []
        const location = Array.isArray(validationError.loc)
          ? validationError.loc.filter(part => typeof part === 'string' && !['body', 'query', 'path'].includes(part)).join('.')
          : ''
        return [location ? `${location}: ${validationError.msg}` : validationError.msg]
      })
      if (validationMessages.length) detail = validationMessages.join(' ')
    } else if (typeof errorDetail === 'object' && errorDetail !== null && 'code' in errorDetail && typeof errorDetail.code === 'string'
      && 'message' in errorDetail && typeof errorDetail.message === 'string') {
      return new ApiError(errorDetail.message, response.status, errorDetail as StructuredApiDetail)
    } else if (typeof errorDetail === 'object' && errorDetail !== null && 'msg' in errorDetail && typeof errorDetail.msg === 'string') {
      detail = errorDetail.msg
    }
  } catch { /* response may not be JSON */ }
  return new ApiError(detail, response.status)
}

async function request<T>(path: string, options?: RequestInit, csrfRetry = true): Promise<T> {
  const method = (options?.method ?? 'GET').toUpperCase()
  const needsCsrf = !SAFE_METHODS.has(method)
    && !path.endsWith('/api/auth/login')
    && !path.endsWith('/api/auth/register')
  if (needsCsrf && !csrfToken) await refreshCsrfToken()

  const headers = new Headers(options?.headers)
  if (options?.body) headers.set('Content-Type', 'application/json')
  if (needsCsrf && csrfToken) headers.set('X-CSRF-Token', csrfToken)
  const response = await fetch(`${API}${path}`, { ...options, credentials: 'include', headers })
  if (!response.ok) {
    const error = await responseError(response)
    if (needsCsrf && csrfRetry && response.status === 403 && /csrf.*(?:validation failed|required)/i.test(error.message)) {
      await refreshCsrfToken()
      return request<T>(path, options, false)
    }
    if (response.status === 401 && !path.endsWith('/api/auth/login')) {
      csrfToken = null
      if (!path.endsWith('/api/auth/session')) window.dispatchEvent(new Event('trinetra:unauthorized'))
    }
    throw error
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

let fleetStatusShared: { at: number; promise: Promise<unknown> } | null = null
const FLEET_STATUS_SHARE_MS = 4000
function sharedFleetStatus<T>(): Promise<T> {
  const now = Date.now()
  if (fleetStatusShared && now - fleetStatusShared.at < FLEET_STATUS_SHARE_MS) return fleetStatusShared.promise as Promise<T>
  const promise = request<T>('/api/fleet/status')
  const entry = { at: now, promise }
  fleetStatusShared = entry
  promise.catch(() => { if (fleetStatusShared === entry) fleetStatusShared = null })
  return promise
}

export const api = {
  health: (signal?: AbortSignal) => request<{
    status: string
    active_simulations: number
    database: string
    database_status?: string
    detector_mode?: 'heuristic' | 'trained_ml'
    model_id?: string | null
    components?: {
      api: { status: string }
      database: { status: string; dialect: string }
      simulation: { status: string; kind: 'abstract_software'; managed_runs: number; running_runs: number }
      websocket: { status: string; active_clients: number }
      detector: { status: string; mode: 'heuristic' | 'trained_ml'; model_id: string | null; confidence_calibrated: boolean | null }
      ai_provider: { status: 'configured' | 'not_configured' }
      px4: { status: string; vehicle_count?: number; error_code?: string }
      telemetry: { status: string; freshness: string; connected_vehicles?: number; stale_vehicles?: number }
      gazebo?: { status: string; world?: string | null; model_count?: number; error_code?: string }
      default_sitl_startup?: { status: string; detail?: string; aircraft_id?: string | null }
    }
  }>('/api/health', { signal }),
  login: async (credentials: LoginCredentials) => {
    const session = await request<UserSession>('/api/auth/login', { method: 'POST', body: JSON.stringify(credentials) })
    csrfToken = session.csrf_token
    return session
  },
  session: async () => {
    const session = await request<UserSession>('/api/auth/session')
    csrfToken = session.csrf_token
    return session
  },
  register: (application: RegistrationApplication) => request<RegistrationReceipt>('/api/auth/register', { method: 'POST', body: JSON.stringify(application) }),
  registrationStatus: (applicationId: string) => request<RegistrationStatus>(`/api/auth/registration/${encodeURIComponent(applicationId)}`),
  logout: async () => {
    await request<{ status: string }>('/api/auth/logout', { method: 'POST' })
    csrfToken = null
  },
  create: (config: Record<string, string | number | null>) => request<{ id: string; state: MissionState }>('/api/simulations', { method: 'POST', body: JSON.stringify(config) }),
  state: (id: string) => request<{ id: string; state: MissionState }>(`/api/simulations/${id}`),
  control: (id: string, action: string) => request<MissionState>(`/api/simulations/${id}/${action}`, { method: 'POST' }),
  commandDecisions: async () => (await request<{ requests: CommandDecision[] }>('/api/command-decisions')).requests,
  requestCommandDecision: (command: RestrictedCommand, run_id: string, payload: { uav_id?: string; task_id?: string; target_uav_id?: string } = {}) =>
    request<{ id: string; status: 'pending'; command: RestrictedCommand; run_id: string }>('/api/command-decisions', {
      method: 'POST', body: JSON.stringify({ command, run_id, ...payload }),
    }),
  reviewCommandDecision: (id: string, decision: 'approve' | 'reject', reason?: string) =>
    request<{ id: string; status: string; state?: MissionState }>(`/api/command-decisions/${encodeURIComponent(id)}/decision`, {
      method: 'POST', body: JSON.stringify({ decision, reason }),
    }),
  acknowledgeAlert: (runId: string, uavId: string) => request<{ state: MissionState }>(`/api/simulations/${runId}/alerts/${uavId}/acknowledge`, { method: 'POST' }),
  isolateUav: (runId: string, uavId: string) => request<{ state: MissionState }>(`/api/simulations/${runId}/uavs/${uavId}/isolate`, { method: 'POST' }),
  reassignTask: (runId: string, taskId: string, targetUavId: string) => request<{ state: MissionState }>(`/api/simulations/${runId}/tasks/${taskId}/reassign`, { method: 'POST', body: JSON.stringify({ target_uav_id: targetUavId }) }),
  history: () => request<{ runs: RunRecord[] }>('/api/history'),
  replay: (id: string) => request<{ id: string; snapshots: MissionState[]; events: MissionState['events'] }>(`/api/replay/${id}`),
  compare: (config: Record<string, string | number | null>) => request<Record<string, unknown>>('/api/experiments/compare', { method: 'POST', body: JSON.stringify(config) }),
  exportExperiment: (id: string) => request<{ filename: string; content: string }>(`/api/experiments/${id}.csv`),
  export: (id: string) => request<{ filename: string; content: string }>(`/api/analytics/${id}.csv`),
  socket: (id: string) => new WebSocket(`${API.replace(/^http/, 'ws')}/api/simulations/${id}/stream`),
  /** `trails: false` leaves out the flight trails: polls that only need positions and states use it (a full frame is only needed once). */
  px4Telemetry: (options: { trails?: boolean } = {}) => request<Px4TelemetrySnapshot>(`/api/telemetry/px4${options.trails === false ? '?trails=false' : ''}`),
  px4TelemetrySocket: (since: string) => new WebSocket(`${API.replace(/^http/, 'ws')}/api/telemetry/px4/stream?trail_mode=delta-v1&since=${encodeURIComponent(since)}`),
  gazeboPoses: (options: { trails?: boolean } = {}) => request<GazeboPoseSnapshot>(`/api/telemetry/gazebo${options.trails === false ? '?trails=false' : ''}`),
  gazeboPoseSocket: (since: string) => new WebSocket(`${API.replace(/^http/, 'ws')}/api/telemetry/gazebo/stream?trail_mode=delta-v1&since=${encodeURIComponent(since)}`),
  aiStatus: () => request<AIStatus>('/api/ai/status'),
  createAIProposal: (description: string, operator_config: OperatorSimulationConfig) => request<MissionProposal>('/api/ai/proposals', { method: 'POST', body: JSON.stringify({ description, operator_config }) }),
  planGeographicMission: (payload: GeographicMissionPlanningRequest) =>
    request<GeographicMissionProposal>('/api/ai/geographic-mission-plan', { method: 'POST', body: JSON.stringify(payload) }),
  createAIConversation: (message: string, operator_config: OperatorSimulationConfig) => request<MissionConversation>('/api/ai/conversations', { method: 'POST', body: JSON.stringify({ message, operator_config }) }),
  continueAIConversation: (conversationId: string, message: string) => request<MissionConversation>(`/api/ai/conversations/${encodeURIComponent(conversationId)}/turn`, { method: 'POST', body: JSON.stringify({ message }) }),
  aiConversation: (conversationId: string) => request<MissionConversation>(`/api/ai/conversations/${encodeURIComponent(conversationId)}`),
  saveAIConversationScenario: (conversationId: string, scenarioId?: string) => request<ScenarioDetail>(
    `/api/ai/conversations/${encodeURIComponent(conversationId)}/scenario`,
    { method: 'POST', ...(scenarioId ? { body: JSON.stringify({ scenario_id: scenarioId }) } : {}) },
  ),
  pendingAIProposals: () => request<{ proposals: PendingMissionProposal[] }>('/api/ai/proposals/pending'),
  aiProposal: (proposalId: string) => request<MissionProposal>(`/api/ai/proposals/${proposalId}`),
  approveAIProposal: (proposalId: string) => request<{ proposal_id: string; status: string; run_id: string; state: MissionState; started: false }>(`/api/ai/proposals/${proposalId}/approve`, { method: 'POST', body: JSON.stringify({ confirmation: 'approve' }) }),
  rejectAIProposal: (proposalId: string, reason: string) => request<{ proposal_id: string; status: string; reason: string }>(`/api/ai/proposals/${proposalId}/reject`, { method: 'POST', body: JSON.stringify({ reason }) }),
  assessMission: (runId: string) => request<MissionAssessment>(`/api/ai/assessments/${runId}`, { method: 'POST' }),
  generalUsers: async (status?: string) => (await request<{ users: ManagedUser[] }>(`/api/general/users${status ? `?status=${encodeURIComponent(status)}` : ''}`)).users,
  decideRegistration: (userId: string, decision: 'approve' | 'reject' | 'hold' | 'resume', role?: 'GENERAL' | 'OPERATOR', rejection_message?: string) =>
    request<{ status: string }>(`/api/general/users/${encodeURIComponent(userId)}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision, role, rejection_message }),
    }),
  setUserRole: (userId: string, role: 'GENERAL' | 'OPERATOR') =>
    request<{ status: string }>(`/api/general/users/${encodeURIComponent(userId)}/role`, { method: 'PUT', body: JSON.stringify({ role }) }),
  setUserActive: (userId: string, active: boolean) =>
    request<{ status: string }>(`/api/general/users/${encodeURIComponent(userId)}/${active ? 'activate' : 'deactivate'}`, { method: 'POST' }),
  generalAudit: async (params: URLSearchParams) =>
    request<{ events: AuditLogEntry[]; total: number; limit: number; offset: number }>(`/api/general/audit?${params.toString()}`),
  exportAuditCsv: async (params: URLSearchParams) => {
    const response = await fetch(`${API}/api/general/audit.csv?${params.toString()}`, { credentials: 'include' })
    if (!response.ok) {
      const error = await responseError(response)
      if (response.status === 401) {
        csrfToken = null
        window.dispatchEvent(new Event('trinetra:unauthorized'))
      }
      throw error
    }
    return response.blob()
  },
  scenarioList: async () => (await request<{ scenarios: ScenarioSummary[] }>('/api/scenarios')).scenarios,
  /** `version` (optional): a saved version whose mission start is used as home for instances this call has to START. */
  activateFleet: (requiredCount: number, version?: { scenario_id: string; scenario_version_id: string }) =>
    request<{ required_uav_count: number; activated_uav_count: number; assigned_uav_count: number;
      vehicles: { aircraft_id: string; instance_number: number; system_id: number; gazebo_model: string;
        px4_status: string; gazebo_status: string; verified: boolean }[]; verified: boolean; request_id: string; checks?: Record<string, FleetCheck> }>(
      '/api/fleet/activate', { method: 'POST', body: JSON.stringify({ required_uav_count: requiredCount, ...(version ?? {}) }) }),
  simulationLocation: (scenarioId: string, versionId: string) =>
    request<SimulationLocation>(`/api/fleet/simulation-location?scenario_id=${encodeURIComponent(scenarioId)}&scenario_version_id=${encodeURIComponent(versionId)}`),
  /** Restarts the managed PX4/Gazebo simulation at the version's mission start. Sends no flight command. */
  relocateSimulation: (scenarioId: string, versionId: string) =>
    request<RelocationResult>('/api/fleet/relocate', { method: 'POST', body: JSON.stringify({ scenario_id: scenarioId, scenario_version_id: versionId, confirm: true }) }),
  // --- Start in simulation (PX4 SITL only): fixed endpoints, no command or MAVLink content from the browser ---
  executionPreflight: (deploymentId: string) => request<ExecutionPreflight>(`/api/mission-control/executions/preflight?deployment_id=${encodeURIComponent(deploymentId)}`),
  latestExecution: async () => (await request<{ execution: ExecutionView | null }>('/api/mission-control/executions/latest')).execution,
  startExecution: (deploymentId: string) => request<ExecutionView>('/api/mission-control/executions', { method: 'POST', body: JSON.stringify({ deployment_id: deploymentId, confirm: true }) }),
  fleetExecutions: (deploymentId: string) => request<{ deployment_id: string; executions: Array<ExecutionView & { role: string | null }>; on_the_ground: Array<{ aircraft_id: string; role: string }>; simulation_only: true }>(`/api/mission-control/executions/fleet?deployment_id=${encodeURIComponent(deploymentId)}`),
  pauseExecution: (id: string) => request<ExecutionView>(`/api/mission-control/executions/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
  resumeExecution: (id: string) => request<ExecutionView>(`/api/mission-control/executions/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
  disarmAircraft: (aircraftId: string) => request<{ aircraft_id: string; disarmed: boolean; armed: boolean; forced: false; px4_messages: string[] }>(`/api/mission-control/aircraft/${encodeURIComponent(aircraftId)}/disarm`, { method: 'POST' }),
  landHere: (aircraftId: string) => request<LandResult>(`/api/mission-control/aircraft/${encodeURIComponent(aircraftId)}/land`, { method: 'POST', body: JSON.stringify({ confirm: true }) }),
  // Part E: the operator is always in control. Each of these is an explicit, confirmed action (never pressed for the user).
  threatSummary: (deploymentId: string) => request<ThreatSummary>(`/api/mission-control/threat-summary?deployment_id=${encodeURIComponent(deploymentId)}`),
  continueMonitoring: (aircraftId: string, deploymentId: string, reminderProbability?: number) =>
    request<{ message: string; reminder_probability: number }>(`/api/mission-control/aircraft/${encodeURIComponent(aircraftId)}/continue-monitoring`, { method: 'POST', body: JSON.stringify({ deployment_id: deploymentId, ...(reminderProbability === undefined ? {} : { reminder_probability: reminderProbability }) }) }),
  resumePreview: (aircraftId: string, deploymentId: string) => request<ResumePreview>(`/api/mission-control/aircraft/${encodeURIComponent(aircraftId)}/resume-preview?deployment_id=${encodeURIComponent(deploymentId)}`),
  resumeOnTrack: (aircraftId: string, deploymentId: string) =>
    request<{ message: string; rejoin: RejoinPoint }>(`/api/mission-control/aircraft/${encodeURIComponent(aircraftId)}/resume-on-track`, { method: 'POST', body: JSON.stringify({ deployment_id: deploymentId, confirm: true }) }),
  reassignOptions: (aircraftId: string, deploymentId: string) => request<ReassignOptions>(`/api/mission-control/aircraft/${encodeURIComponent(aircraftId)}/reassign-options?deployment_id=${encodeURIComponent(deploymentId)}`),
  reassign: (aircraftId: string, deploymentId: string, taker: string, original: OriginalChoice) =>
    request<{ message: string; steps: string[]; original_result: string; new_roles: Record<string, string> }>(`/api/mission-control/aircraft/${encodeURIComponent(aircraftId)}/reassign`, { method: 'POST', body: JSON.stringify({ deployment_id: deploymentId, taker, original, confirm: true }) }),
  respond: (aircraftId: string, action: 'hold' | 'return_to_launch' | 'land_here' | 'resume') =>
    request<{ message: string }>('/api/research/responses', { method: 'POST', body: JSON.stringify({ aircraft_id: aircraftId, action, confirm: true }) }),
  switchPrimary: (aircraftId: string, candidate: string) =>
    request<{ message: string }>('/api/research/responses', { method: 'POST', body: JSON.stringify({ aircraft_id: aircraftId, action: 'switch_primary', candidate, confirm: true }) }),
  aircraftSummary: (deploymentId: string) => request<AircraftSummary>(`/api/mission-control/aircraft-summary?deployment_id=${encodeURIComponent(deploymentId)}`),
  fleetCapacity: (count: number) => request<CapacityAssessment>(`/api/fleet/capacity?count=${count}`),
  startRestart: (deploymentId: string, purpose: 'restart' | 'switch' = 'restart') =>
    request<RestartJob>('/api/mission-control/restart', { method: 'POST', body: JSON.stringify({ deployment_id: deploymentId, confirm: true, purpose }) }),
  restartJob: (id: string) => request<RestartJob>(`/api/mission-control/restart/${encodeURIComponent(id)}`),
  /** Restart only the aircraft that did not come up (the ready ones keep running). */
  retryMissingAircraft: (id: string) => request<RestartJob>(`/api/mission-control/restart/${encodeURIComponent(id)}/retry-missing`, { method: 'POST' }),
  /** Accept a partial result: continue with the ready aircraft; the others stay not available. */
  continueWithReady: (id: string) => request<RestartJob>(`/api/mission-control/restart/${encodeURIComponent(id)}/continue`, { method: 'POST' }),
  latestRestart: async () => (await request<{ job: RestartJob | null }>('/api/mission-control/restart')).job,
  deploymentRoleRule: async (deploymentId: string) =>
    (await request<{ role_rule: { allowed: boolean; code: string; message: string } }>(`/api/mission-control/deployments/${encodeURIComponent(deploymentId)}/role-rule`)).role_rule,
  abortExecution: (id: string) => request<ExecutionView>(`/api/mission-control/executions/${encodeURIComponent(id)}/abort`, { method: 'POST' }),
  executionEvents: (id: string, after = 0) => request<{ execution_id: string; state: string; events: ExecutionEvent[] }>(`/api/mission-control/executions/${encodeURIComponent(id)}/events?after=${after}`),
  attackLabMissions: async () => (await request<{ missions: import('../components/attackLabTypes').PickerMission[]; legacy_only_count: number }>('/api/attack-lab/missions')),
  scenarioDetail: (scenarioId: string) => request<ScenarioDetail>(`/api/scenarios/${encodeURIComponent(scenarioId)}`),
  attackLabLimits: () => request<import('../components/attackLabTypes').AttackLimits>('/api/attack-lab/limits'),
  attackExperiments: async () => (await request<{ experiments: import('../components/attackLabTypes').Experiment[] }>('/api/attack-lab/experiments')).experiments,
  validateExperiment: (input: unknown) => request<{ valid: boolean; issues: import('../components/attackLabTypes').ExperimentIssue[] }>('/api/attack-lab/validate', { method: 'POST', body: JSON.stringify(input) }),
  applyAttackPreset: (presetId: string, scenarioId: string, versionId: string, seed: number) =>
    request<{ preset_id: string; seed: number; config: import('../components/attackLabTypes').ExperimentConfig }>(
      `/api/attack-lab/presets/${encodeURIComponent(presetId)}/apply`, { method: 'POST', body: JSON.stringify({ scenario_id: scenarioId, scenario_version_id: versionId, seed }) }),
  createExperiment: (input: unknown) => request<import('../components/attackLabTypes').Experiment>('/api/attack-lab/experiments', { method: 'POST', body: JSON.stringify(input) }),
  updateExperiment: (id: string, input: unknown) => request<import('../components/attackLabTypes').Experiment>(`/api/attack-lab/experiments/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(input) }),
  copyExperiment: (id: string, input?: unknown) => request<import('../components/attackLabTypes').Experiment>(`/api/attack-lab/experiments/${encodeURIComponent(id)}/copy`, { method: 'POST', ...(input ? { body: JSON.stringify(input) } : {}) }),
  reconfigurations: async () => (await request<{ reconfigurations: import('../components/attackLabTypes').Reconfiguration[] }>('/api/research/reconfigurations')).reconfigurations,
  threatResponseOptions: (aircraftId: string) => request<import('../components/attackLabTypes').ResponseOptions>(`/api/research/responses?aircraft_id=${encodeURIComponent(aircraftId)}`),
  respondToThreat: (payload: { aircraft_id: string; action: string; confirm: boolean; candidate?: string }) => request<import('../components/attackLabTypes').ThreatResponseResult>('/api/research/responses', { method: 'POST', body: JSON.stringify(payload) }),
  implementExperiment: (id: string, enabled: boolean) => request<import('../components/attackLabTypes').Experiment>(`/api/attack-lab/experiments/${encodeURIComponent(id)}/implement`, { method: 'POST', body: JSON.stringify({ enabled }) }),
  attackLabLive: () => request<import('../components/attackLabTypes').LiveExperimentStatus>('/api/attack-lab/live'),
  attackLabResults: async () => (await request<{ results: import('../components/attackLabTypes').StoredResult[] }>('/api/attack-lab/results')).results,
  exportRunRecords: async () => {
    const response = await fetch(`${API}/api/attack-lab/results/export`, { credentials: 'include' })
    if (!response.ok) throw await responseError(response)
    return response.blob()
  },
  repinExperiment: (id: string) => request<import('../components/attackLabTypes').Experiment>(`/api/attack-lab/experiments/${encodeURIComponent(id)}/repin`, { method: 'POST' }),
  deleteExperiment: (id: string) => request<void>(`/api/attack-lab/experiments/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  researchModelInfo: () => request<import('../components/attackLabTypes').ModelInfo>('/api/research/model-info'),
  researchLive: () => request<import('../components/attackLabTypes').LiveThreat>('/api/research/live'),
  assessThreat: (payload: unknown) => request<import('../components/attackLabTypes').Assessment>('/api/research/assess', { method: 'POST', body: JSON.stringify(payload) }),
  recordDecision: (payload: unknown) => request<import('../components/attackLabTypes').DecisionRecord>('/api/research/decisions', { method: 'POST', body: JSON.stringify(payload) }),
  researchDecisions: async () => (await request<{ decisions: import('../components/attackLabTypes').DecisionRecord[] }>('/api/research/decisions')).decisions,
  createReplay: (experimentId: string) => request<import('../components/attackLabTypes').Replay>('/api/research/replays', { method: 'POST', body: JSON.stringify({ experiment_id: experimentId }) }),
  finishReplay: (replayId: string) => request<{ finished: boolean }>(`/api/research/replays/${encodeURIComponent(replayId)}/finish`, { method: 'POST' }),
  replayEvaluation: (replayId: string) => request<import('../components/attackLabTypes').ReplayEvaluation>(`/api/research/replays/${encodeURIComponent(replayId)}/evaluation`),
  previewRoute: (plan: unknown, uavId: string) =>
    request<{ route: import('../components/missionPlan').GeneratedRoute; summary: { length_m: number; duration_s: number; waypoints: number; limit_duration_s?: number; limit_length_m?: number }
      warnings?: import('../components/missionPlan').PlanIssue[]; limits?: { max_distance_from_start_m: number | null; max_altitude_m: number | null; max_duration_minutes: number | null; auto: import('../components/missionPlan').AutoLimits } | null }>(
      '/api/mission-plans/preview-route', { method: 'POST', body: JSON.stringify({ plan, uav_id: uavId }) }),
  planningLimits: () => request<{ schema_version: number; limits: Record<string, number>; quick?: import('../components/quickMission').QuickRules }>('/api/mission-plans/limits'),
  quickMission: (payload: import('../components/quickMission').QuickRequest) =>
    request<import('../components/quickMission').QuickResult>('/api/mission-plans/quick', { method: 'POST', body: JSON.stringify(payload) }),
  parseMission: (payload: import('../components/quickMission').ParseRequest) =>
    request<import('../components/quickMission').ParseResult>('/api/mission-plans/quick/parse', { method: 'POST', body: JSON.stringify(payload) }),
  scenarioAssistant: (payload: { draft_id: string; message: string; history: { role: 'user' | 'assistant'; content: string }[]; snapshot: unknown }) =>
    request<{ reply: string; actions: unknown[]; source: 'ai'; dropped_actions: number }>(
      '/api/ai/scenario-assistant', { method: 'POST', body: JSON.stringify(payload) }),
  /** One shared read: the backend asks the WSL fleet manager for every status call, so callers within a few seconds share one answer. */
  fleetStatus: () => sharedFleetStatus<{ capacity: number; verified_count: number; instances: { aircraft_id: string; instance_number: number; system_id: number; gazebo_model: string; verified: boolean }[]; manager_error: string | null; bridges?: { telemetry?: { status: string }; pose?: { status: string } } | null; gazebo_server?: string | null; wsl_address?: string | null }>(),
  createScenario: (title: string, config: ScenarioConfig, intent?: ScenarioIntent) =>
    request<ScenarioDetail>('/api/scenarios', { method: 'POST', body: JSON.stringify({ title, config, intent }) }),
  updateScenario: (scenarioId: string, title: string, config: ScenarioConfig, intent?: ScenarioIntent) =>
    request<ScenarioDetail>(`/api/scenarios/${encodeURIComponent(scenarioId)}`, { method: 'PUT', body: JSON.stringify({ title, config, intent }) }),
  deleteScenario: (scenarioId: string) =>
    request<void>(`/api/scenarios/${encodeURIComponent(scenarioId)}`, { method: 'DELETE' }),
  submitScenario: (scenarioId: string, versionId: string) =>
    request<{ id: string; status: string }>(`/api/scenarios/${encodeURIComponent(scenarioId)}/versions/${encodeURIComponent(versionId)}/submit`, { method: 'POST' }),
  /** `plan_only`: hand the mission to Mission Control without checking or starting any aircraft (Start stays blocked until enough aircraft run). */
  deployScenario: (scenarioId: string, versionId: string, mode?: 'plan_only') =>
    request<MissionDeployment>(`/api/scenarios/${encodeURIComponent(scenarioId)}/versions/${encodeURIComponent(versionId)}/deploy`,
      mode ? { method: 'POST', body: JSON.stringify({ mode }) } : { method: 'POST' }),
  generalScenarios: async (filters: { owner?: string; status?: string; q?: string } = {}) => {
    const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value) as [string, string][])
    return (await request<{ scenarios: import('./auth').GeneralScenarioRow[] }>(`/api/general/scenarios${params.size ? `?${params}` : ''}`)).scenarios
  },
  generalScenarioExport: (scenarioId: string) => request<Record<string, unknown>>(`/api/general/scenarios/${encodeURIComponent(scenarioId)}/export`),
  generalDeletePreview: (scenarioId: string) => request<import('./auth').ScenarioDeletePreview>(`/api/general/scenarios/${encodeURIComponent(scenarioId)}/delete-preview`),
  generalDeleteScenario: (scenarioId: string, confirmName: string, deleteResults: boolean) =>
    request<{ scenario_id: string; title: string; counts: Record<string, number>; content_hash: string }>(`/api/general/scenarios/${encodeURIComponent(scenarioId)}/delete`,
      { method: 'POST', body: JSON.stringify({ confirm_name: confirmName, delete_results: deleteResults }) }),
  latestMissionDeployment: async () =>
    (await request<{ deployment: MissionDeployment | null }>('/api/mission-control/deployments/latest')).deployment,
  /** The latest deployment plus the deployment the newest execution belongs to (mission switching). */
  missionDeployments: () => request<DeploymentOverview>('/api/mission-control/deployments/latest'),
  myScenarioRequests: async () =>
    (await request<{ requests: ScenarioApprovalRequest[] }>('/api/scenarios/my-approval-requests')).requests,
  pendingScenarioApprovals: async () =>
    (await request<{ requests: ScenarioApprovalRequest[] }>('/api/general/scenario-approvals')).requests,
  reviewScenario: (requestId: string, decision: 'approve' | 'reject' | 'request_changes', reason: string) =>
    request<{ id: string; status: string }>(`/api/general/scenario-approvals/${encodeURIComponent(requestId)}/decision`, { method: 'POST', body: JSON.stringify({ decision, reason }) }),
  sandboxScenario: (scenarioId: string, versionId: string) =>
    request<{ run_id: string; state: MissionState }>(`/api/scenarios/${encodeURIComponent(scenarioId)}/versions/${encodeURIComponent(versionId)}/sandbox`, { method: 'POST' }),
  executeScenario: (scenarioId: string, versionId: string) =>
    request<{ run_id: string; state: MissionState }>(`/api/scenarios/${encodeURIComponent(scenarioId)}/execute`, { method: 'POST', body: JSON.stringify({ version_id: versionId }) }),
}
