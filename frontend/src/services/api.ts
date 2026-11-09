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
} from './auth'
import type { AIStatus, MissionAssessment, MissionConversation, MissionProposal, OperatorSimulationConfig, PendingMissionProposal } from './ai'

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

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
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
      px4: { status: 'not_integrated' }
      telemetry: { status: 'not_integrated'; freshness: 'not_available' }
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
  px4Telemetry: () => request<Px4TelemetrySnapshot>('/api/telemetry/px4'),
  px4TelemetrySocket: (since: string) => new WebSocket(`${API.replace(/^http/, 'ws')}/api/telemetry/px4/stream?trail_mode=delta-v1&since=${encodeURIComponent(since)}`),
  gazeboPoses: () => request<GazeboPoseSnapshot>('/api/telemetry/gazebo'),
  gazeboPoseSocket: (since: string) => new WebSocket(`${API.replace(/^http/, 'ws')}/api/telemetry/gazebo/stream?trail_mode=delta-v1&since=${encodeURIComponent(since)}`),
  aiStatus: () => request<AIStatus>('/api/ai/status'),
  createAIProposal: (description: string, operator_config: OperatorSimulationConfig) => request<MissionProposal>('/api/ai/proposals', { method: 'POST', body: JSON.stringify({ description, operator_config }) }),
  createAIConversation: (message: string, operator_config: OperatorSimulationConfig) => request<MissionConversation>('/api/ai/conversations', { method: 'POST', body: JSON.stringify({ message, operator_config }) }),
  continueAIConversation: (conversationId: string, message: string) => request<MissionConversation>(`/api/ai/conversations/${encodeURIComponent(conversationId)}/turn`, { method: 'POST', body: JSON.stringify({ message }) }),
  aiConversation: (conversationId: string) => request<MissionConversation>(`/api/ai/conversations/${encodeURIComponent(conversationId)}`),
  saveAIConversationScenario: (conversationId: string) => request<ScenarioDetail>(`/api/ai/conversations/${encodeURIComponent(conversationId)}/scenario`, { method: 'POST' }),
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
  createScenario: (title: string, config: ScenarioConfig, intent?: ScenarioIntent) =>
    request<ScenarioDetail>('/api/scenarios', { method: 'POST', body: JSON.stringify({ title, config, intent }) }),
  updateScenario: (scenarioId: string, title: string, config: ScenarioConfig, intent?: ScenarioIntent) =>
    request<ScenarioDetail>(`/api/scenarios/${encodeURIComponent(scenarioId)}`, { method: 'PUT', body: JSON.stringify({ title, config, intent }) }),
  deleteScenario: (scenarioId: string) =>
    request<void>(`/api/scenarios/${encodeURIComponent(scenarioId)}`, { method: 'DELETE' }),
  submitScenario: (scenarioId: string, versionId: string) =>
    request<{ id: string; status: string }>(`/api/scenarios/${encodeURIComponent(scenarioId)}/versions/${encodeURIComponent(versionId)}/submit`, { method: 'POST' }),
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
