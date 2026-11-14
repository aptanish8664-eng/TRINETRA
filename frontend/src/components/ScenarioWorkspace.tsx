import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, MessageCircle, RefreshCw, Route, Save, Send, X } from 'lucide-react'
import type { Px4TelemetryVehicle } from '../types'
import type {
  ScenarioApprovalRequest, ScenarioConfig, ScenarioIntent, ScenarioSummary, PlannerConversationMessage,
  MissionDeployment,
} from '../services/auth'
import { api } from '../services/api'
import { ATTACK_LAB_NAME } from './attackLabTypes'
import { SimulationLocationNotice } from './SimulationLocationNotice'
import { explainSitlStartup } from './missionControlModel'
import { PlanMapEditor } from './PlanMapEditor'
import {
  AIRCRAFT_ROLES, allowedTaskTypes, EMPTY_PLAN, MISSION_DURATION_RANGE_MINUTES, MISSION_OBJECTIVES, OBJECTIVE_DEFAULT_TASK, PAYLOAD_CATEGORIES,
  RETURN_MODES, SCHEMA_VERSION, TASK_SPECS, TRACKING_NOTE, TRACKING_ONLY_OBJECTIVES, UAV_COUNT_RANGE,
  FLEET_VERIFICATION_LABEL, computeNextAction, enduranceIssues, type EnduranceLimit, computePlanDeployBlockers, computePlanMissing, isPlanComplete, missingAircraftInputs, newAircraft, normalizePlan, taskSpec,
  prefillPlanFromLegacy, type AircraftPlan, type FleetVerification, type MissionPlan, type ObjectiveValue, type ParamSpec, type PlanIssue, type TaskParameters, type TaskType,
} from './missionPlan'
import { stableStringify } from './scenarioPlanning'
import { aiFailureMessage, answerReadOnlyQuestion, buildAssistantSnapshot, sectionOf, unknownHelp, type HelpContext } from './assistantHelp'
import { applyAssistantOps, parseSetCommand, validateAssistantOps, type AssistantOp, type ValidatedOps } from './assistantActions'
import { buildAssistantOptions } from './assistantOptions'
import { AssistantOptionBar } from './AssistantOptionBar'
import './scenario-workspace.css'

type ScenarioWorkspaceProps = {
  userId: string
  config: ScenarioConfig
  onLoadConfig: (config: ScenarioConfig) => void
  /** Called after a successful deploy; `note` carries anything the user should know (for example a retry). */
  onMissionDeployed?: (deployment: MissionDeployment, note?: string) => void
  canManage: boolean
  isOperator?: boolean
  sharedScenarioId?: string | null
  onScenarioSelected?: (scenarioId: string | null) => void
  /** False while another tab is shown: background polling pauses and resumes with a fresh check. */
  active?: boolean
  onOpenAttackLab?: () => void
}

/** A fleet check older than this is shown as STALE and no longer allows deployment. */
const FLEET_FRESH_MS = 60_000
const FLEET_POLL_MS = 20_000
type FleetCheckState = { state: 'unknown' | 'checking' | 'verified' | 'not_verified'; at: number; detail: string }

type FleetIdentity = { aircraft_id: string; instance_number: number; system_id: number; gazebo_model: string; verified: boolean }
const NO_MESSAGES: PlannerConversationMessage[] = []

type AssistantAction =
  | { kind: 'fleet'; count: number }
  | { kind: 'batch'; ops: AssistantOp[]; summary: string[] }


/** Aircraft inputs that change the generated route; editing any of them discards the route and the confirmation. */
const ROUTE_INPUT_KEYS: (keyof AircraftPlan)[] = ['task_type', 'parameters', 'start_location', 'altitude_m', 'speed_mps', 'loiter_duration_s', 'return_mode']

function isAvailable(vehicle: Px4TelemetryVehicle): boolean {
  return vehicle.status === 'connected'
    && vehicle.position?.valid === true
    && Number.isFinite(vehicle.position.latitude)
    && Number.isFinite(vehicle.position.longitude)
}

const fmt = (value: number, digits = 5) => value.toFixed(digits)
const issueText = (issue: PlanIssue) => `${issue.uav_id ? `${issue.uav_id}: ` : ''}${issue.message}`

export function ScenarioWorkspace({
  userId, config, onLoadConfig, canManage, isOperator = false, onMissionDeployed, sharedScenarioId, onScenarioSelected, active = true, onOpenAttackLab,
}: ScenarioWorkspaceProps) {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([])
  const [requests, setRequests] = useState<ScenarioApprovalRequest[]>([])
  const [vehicles, setVehicles] = useState<Px4TelemetryVehicle[]>([])
  const [fleetIdentity, setFleetIdentity] = useState<FleetIdentity[]>([])
  const [fleetError, setFleetError] = useState('')
  const [requiredCountInput, setRequiredCountInput] = useState('')
  const [activationBusy, setActivationBusy] = useState(false)
  const [activationError, setActivationError] = useState('')
  const [activationFailure, setActivationFailure] = useState<{ code: string; status?: number; checks: [string, { status: string; detail: string }][] } | null>(null)
  const [fleetCheck, setFleetCheck] = useState<FleetCheckState>({ state: 'unknown', at: 0, detail: 'No fleet check has run for this plan yet.' })
  const [clock, setClock] = useState(() => Date.now())
  const [supersededBy, setSupersededBy] = useState<number | null>(null)
  const [endurance, setEndurance] = useState<EnduranceLimit | null>(null)
  const [startup, setStartup] = useState<{ status: string; detail?: string } | null>(null)
  // Fresh means: a backend check confirmed every aircraft of this plan within the last minute.
  const fleetState: FleetVerification = fleetCheck.state === 'checking' ? 'checking'
    : fleetCheck.state !== 'verified' ? 'not_verified'
      : clock - fleetCheck.at > FLEET_FRESH_MS ? 'stale' : 'verified'
  const fleetVerified = fleetState === 'verified'
  const setFleetVerified = (verified: boolean, detail = '') => setFleetCheck({
    state: verified ? 'verified' : 'not_verified', at: Date.now(), detail: detail || (verified ? 'Verified by the backend.' : 'Not verified.'),
  })
  const [selectedId, setSelectedId] = useState('')
  const [title, setTitle] = useState('')
  const [plan, setPlan] = useState<MissionPlan>(EMPTY_PLAN)
  const [legacy, setLegacy] = useState<ScenarioIntent | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [planIssues, setPlanIssues] = useState<PlanIssue[]>([])
  const [routeIssues, setRouteIssues] = useState<PlanIssue[]>([])
  const [routeBusy, setRouteBusy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [assistantInput, setAssistantInput] = useState('')
  const [assistantBusy, setAssistantBusy] = useState(false)
  const [pendingAssistantAction, setPendingAssistantAction] = useState<AssistantAction | null>(null)
  const confirmingRef = useRef(false)
  // Assistant conversations live only in this component's memory, one per draft. They are never written to browser
  // storage, so a page refresh or any new login starts empty; hiding the tab or collapsing the panel keeps them.
  const [chats, setChats] = useState<Record<string, PlannerConversationMessage[]>>({})
  const [chatDraftId, setChatDraftId] = useState(() => `draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`)

  const selected = scenarios.find(item => item.id === selectedId)
  const chatKey = selectedId || chatDraftId
  const chat = chats[chatKey] ?? NO_MESSAGES
  const setChat = (update: PlannerConversationMessage[] | ((current: PlannerConversationMessage[]) => PlannerConversationMessage[])) =>
    setChats(current => ({ ...current, [chatKey]: typeof update === 'function' ? update(current[chatKey] ?? NO_MESSAGES) : update }))
  const activeScenarioStorageKey = `trinetra-active-scenario:${userId}`
  const latest = selected?.latest_version
  const approvalRequest = latest ? requests.find(item => item.scenario_version_id === latest.id) : undefined
  const approvalStatus = latest?.approval?.status ?? approvalRequest?.status ?? null
  const availableVehicles = useMemo(() => vehicles.filter(isAvailable), [vehicles])
  const selectedAircraftIds = plan.selected_uav_ids
  const activeAircraft: AircraftPlan | undefined = plan.aircraft[activeIndex]
  const planComplete = isPlanComplete(plan)
  const editable = canManage && !legacy

  const refresh = async () => {
    // Settled separately: if the approval list cannot be read, the saved scenarios (and the next action) still update.
    const [scenarioResult, requestResult] = await Promise.allSettled([api.scenarioList(), api.myScenarioRequests()])
    if (requestResult.status === 'fulfilled') setRequests(requestResult.value)
    if (scenarioResult.status === 'rejected') throw scenarioResult.reason
    const nextScenarios = scenarioResult.value
    setScenarios(nextScenarios)
    if (selectedId && !nextScenarios.some(item => item.id === selectedId)) {
      setSelectedId('')
      setTitle('')
    }
    if (requestResult.status === 'rejected') {
      setError(`Approval requests could not be loaded (${requestResult.reason instanceof Error ? requestResult.reason.message : 'unknown error'}). Saved scenarios are up to date.`)
    }
  }

  const planRef = useRef(plan)
  planRef.current = plan
  /** Asks the backend whether every aircraft of the plan is running, reporting telemetry and has a Gazebo pose. */
  const checkFleet = useCallback(async (target?: MissionPlan): Promise<boolean> => {
    const current = target ?? planRef.current
    const required = current.required_uav_count
    if (!required || current.activated_uav_ids.length !== required) {
      setFleetCheck({ state: 'not_verified', at: Date.now(), detail: 'No fleet activation is recorded for this plan.' })
      return false
    }
    try {
      const [status, health] = await Promise.all([api.fleetStatus(), api.health().catch(() => null)])
      setStartup(health?.components?.default_sitl_startup ?? null)
      setFleetIdentity(status.instances)
      const live = new Set(status.instances.filter(item => item.verified).map(item => item.aircraft_id))
      const verified = status.verified_count >= required && current.activated_uav_ids.every(id => live.has(id))
      setFleetCheck({
        state: verified ? 'verified' : 'not_verified', at: Date.now(),
        detail: verified ? `${required} of ${required} aircraft running with telemetry and a Gazebo pose.`
          : status.manager_error ? `WSL fleet manager: ${status.manager_error}`
            : `${current.activated_uav_ids.filter(id => live.has(id)).length} of ${required} aircraft verified.`,
      })
      return verified
    } catch {
      // Keep the last result: if it was verified it turns STALE on its own once it is older than the window.
      setFleetCheck(previous => ({ ...previous, state: previous.state === 'verified' ? 'verified' : 'not_verified', detail: 'Fleet status could not be read from the backend.' }))
      return false
    }
  }, [])

  useEffect(() => {
    let active = true
    const load = async () => {
      setLoading(true)
      const results = await Promise.allSettled([api.scenarioList(), api.myScenarioRequests(), api.px4Telemetry()])
      if (!active) return
      if (results[0].status === 'fulfilled') setScenarios(results[0].value)
      else setError(results[0].reason instanceof Error ? results[0].reason.message : 'Saved scenarios could not be loaded.')
      if (results[1].status === 'fulfilled') setRequests(results[1].value)
      else setError(results[1].reason instanceof Error ? results[1].reason.message : 'Approval requests could not be loaded.')
      if (results[2].status === 'fulfilled') setVehicles(results[2].value.vehicles)
      else {
        setFleetError(results[2].reason instanceof Error ? results[2].reason.message : 'Current PX4 telemetry is unavailable.')
      }
      setLoading(false)
    }
    void load()
    return () => { active = false }
  }, [])

  useEffect(() => {
    // The aircraft endurance limit comes from the backend so the preview and the checklist use the validated definition.
    let active = true
    Promise.resolve().then(() => api.planningLimits()).then(value => {
      const seconds = value.limits.max_flight_s, metres = value.limits.max_flight_distance_m
      if (active && Number.isFinite(seconds) && Number.isFinite(metres)) setEndurance({ duration_s: seconds, length_m: metres })
    }).catch(() => undefined)
    return () => { active = false }
  }, [])

  useEffect(() => {
    // Earlier versions persisted conversations in sessionStorage; discard any leftovers so they can never be restored.
    try {
      for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
        const key = sessionStorage.key(index)
        if (key?.startsWith('trinetra-scenario-chat:') || key?.startsWith('trinetra-active-draft:')) sessionStorage.removeItem(key)
      }
    } catch { /* session storage may be unavailable */ }
  }, [])

  useEffect(() => {
    const clearAssistant = () => {
      try {
        for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
          const key = sessionStorage.key(index)
          if (key === activeScenarioStorageKey) sessionStorage.removeItem(key)
        }
      } catch { /* session storage may be unavailable */ }
      confirmingRef.current = false
      setChats({})
      setPendingAssistantAction(null)
      setAssistantBusy(false)
      setAssistantInput('')
      setAssistantOpen(false)
    }
    window.addEventListener('trinetra:logout', clearAssistant)
    return () => window.removeEventListener('trinetra:logout', clearAssistant)
  }, [activeScenarioStorageKey])

  const loadScenario = (scenarioId: string, source = scenarios) => {
    const next = source.find(item => item.id === scenarioId)
    if (!scenarioId) setChatDraftId(`draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`)
    setSelectedId(scenarioId)
    try {
      if (scenarioId) sessionStorage.setItem(activeScenarioStorageKey, scenarioId)
      else sessionStorage.removeItem(activeScenarioStorageKey)
    } catch { /* session storage may be unavailable */ }
    setTitle(next?.title ?? '')
    const intent = next?.latest_version.intent
    // Versions saved before schema 2 stay readable and unmodified; they are shown read-only, never rewritten.
    const isLegacy = Boolean(intent && intent.schema_version !== SCHEMA_VERSION && intent.geographic_mission)
    const nextPlan = isLegacy ? EMPTY_PLAN : normalizePlan(intent?.mission_plan)
    setLegacy(isLegacy && intent ? intent : null)
    setPlan(nextPlan)
    setActiveIndex(Math.min(nextPlan.current_uav_index, Math.max(0, nextPlan.aircraft.length - 1)))
    setRequiredCountInput(nextPlan.required_uav_count ? String(nextPlan.required_uav_count) : '')
    setFleetIdentity([])
    setSupersededBy(null)
    setPlanIssues([])
    setRouteIssues([])
    if (nextPlan.required_uav_count && nextPlan.activated_uav_ids.length === nextPlan.required_uav_count) {
      setFleetCheck({ state: 'checking', at: Date.now(), detail: 'Checking the fleet…' })
      void checkFleet(nextPlan)
    } else {
      setFleetCheck({ state: 'not_verified', at: Date.now(), detail: 'No fleet activation is recorded for this plan.' })
    }
    if (next) onLoadConfig(next.latest_version.config)
    onScenarioSelected?.(next?.id ?? null)
    setError('')
    setMessage(next ? `Loaded ${next.title} · version ${next.latest_version.version_number}${isLegacy ? ' (legacy, read-only)' : ''}.` : 'New manual scenario draft.')
  }

  /** Opens a blank schema-2 draft pre-filled with what maps from the legacy version; the legacy version is untouched. */
  const startFromLegacy = () => {
    if (!legacy) return
    const prefill = prefillPlanFromLegacy(legacy)
    const name = `${title.trim() || 'Untitled'} (schema 2)`.slice(0, 120)
    loadScenario('')
    setTitle(name)
    setPlan(prefill)
    setMessage('New schema-2 draft pre-filled from the legacy version (objectives, description, constraints as notes). Areas, aircraft and routes must be planned again; the legacy version is unchanged.')
  }

  useEffect(() => {
    if (sharedScenarioId && sharedScenarioId !== selectedId && scenarios.some(item => item.id === sharedScenarioId)) {
      loadScenario(sharedScenarioId)
    }
  }, [sharedScenarioId, scenarios, selectedId])

  useEffect(() => {
    if (!selectedId && scenarios.length && !sharedScenarioId) {
      try {
        const savedScenarioId = sessionStorage.getItem(activeScenarioStorageKey)
        if (savedScenarioId && scenarios.some(item => item.id === savedScenarioId)) loadScenario(savedScenarioId)
      } catch { /* session storage may be unavailable */ }
    }
  }, [scenarios, selectedId, sharedScenarioId, activeScenarioStorageKey])

  // Keep the fleet check fresh while a saved, editable scenario is open and this tab is shown. The check only reads state.
  useEffect(() => {
    if (!active || !selectedId || legacy) return
    void checkFleet()
    const poll = window.setInterval(() => { if (typeof document === 'undefined' || !document.hidden) void checkFleet() }, FLEET_POLL_MS)
    const tick = window.setInterval(() => setClock(Date.now()), 5000)
    return () => { window.clearInterval(poll); window.clearInterval(tick) }
  }, [active, selectedId, legacy, checkFleet])

  const runAction = async (action: () => Promise<void>) => {
    setBusy(true)
    setError('')
    setMessage('')
    setPlanIssues([])
    try {
      await action()
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Scenario action failed.')
      // A 409 means the saved versions changed (for example a newer version exists): refetch so the list is current.
      if ((cause as { status?: number } | null)?.status === 409) await refresh().catch(() => undefined)
      const issues = (cause as { detail?: { issues?: unknown } } | null)?.detail?.issues
      if (Array.isArray(issues)) setPlanIssues(issues.filter((item): item is PlanIssue => typeof item?.message === 'string'))
    } finally {
      setBusy(false)
    }
  }

  // --- plan state: every edit goes through these handlers (manual form, map, assistant) -----------------------

  const updatePlan = (patch: Partial<MissionPlan>) => setPlan(current => ({ ...current, ...patch }))
  /** Mission-level inputs the validator reads for every aircraft: changing one requires re-confirmation. */
  const updateMissionInputs = (patch: Partial<MissionPlan>, clearRoutes = false) =>
    setPlan(current => ({ ...current, ...patch, aircraft: current.aircraft.map(item => ({ ...item, route: clearRoutes ? null : item.route, confirmed: false })) }))
  const changeArea = (area: MissionPlan['operating_area']) =>
    setPlan(current => ({ ...current, operating_area: area, aircraft: current.aircraft.map(item => ({ ...item, route: null, confirmed: false })) }))

  const changeAircraft = (index: number, patch: Partial<AircraftPlan>) => {
    setRouteIssues([])
    setPlan(current => ({
      ...current,
      workflow_stage: 'aircraft',
      aircraft: current.aircraft.map((item, position) => {
        if (position !== index) return item
        const invalidates = ROUTE_INPUT_KEYS.some(key => key in patch)
        return { ...item, ...patch, route: 'route' in patch ? (patch.route ?? null) : invalidates ? null : item.route, confirmed: patch.confirmed ?? false }
      }),
    }))
  }

  const setParameter = (spec: ParamSpec, raw: string) => {
    if (!activeAircraft) return
    const parameters: TaskParameters = { ...activeAircraft.parameters }
    const key = spec.key as keyof TaskParameters
    if (raw === '') delete parameters[key]
    else (parameters as Record<string, unknown>)[key] = spec.kind === 'number' ? Number(raw) : raw
    changeAircraft(activeIndex, { parameters })
  }

  const setObjectives = (value: ObjectiveValue, checked: boolean) => {
    const objectives = checked ? [...new Set([...plan.objectives, value])] : plan.objectives.filter(item => item !== value)
    updateMissionInputs({ objectives })
  }

  const suggestedTasks = useMemo(
    () => [...new Set(plan.objectives.map(value => OBJECTIVE_DEFAULT_TASK[value]))],
    [plan.objectives],
  )
  const trackingOnly = plan.objectives.length > 0 && plan.objectives.every(value => TRACKING_ONLY_OBJECTIVES.includes(value))
  const taskChoices = TASK_SPECS.filter(task => allowedTaskTypes(plan.objectives).includes(task.value))

  // --- fleet ---------------------------------------------------------------------------------------------------

  /**
   * Starts or adopts the requested aircraft and verifies them. When the verified aircraft are exactly the ones the
   * plan already uses, nothing else changes: confirmations, routes and the review stage are kept. (Resetting the
   * stage here used to add a "complete the workflow" blocker every time the fleet was re-verified.)
   */
  const activateRequestedFleet = async (requested?: number): Promise<boolean> => {
    const count = requested ?? Number(requiredCountInput)
    if (requested !== undefined) setRequiredCountInput(String(requested))
    if (!Number.isInteger(count) || count < UAV_COUNT_RANGE.min || count > UAV_COUNT_RANGE.max) {
      setActivationError(`Enter a whole number from ${UAV_COUNT_RANGE.min} to ${UAV_COUNT_RANGE.max}.`)
      return false
    }
    updatePlan({ required_uav_count: count })
    setFleetCheck({ state: 'checking', at: Date.now(), detail: 'Activating and verifying the fleet…' })
    setActivationBusy(true)
    setActivationError('')
    setActivationFailure(null)
    try {
      // With a saved version, any instance that has to be STARTED is started at that version's mission start.
      const activated = await api.activateFleet(count, selected && latest && !legacy ? { scenario_id: selected.id, scenario_version_id: latest.id } : undefined)
      if (!activated.verified || activated.activated_uav_count !== count || activated.vehicles.length !== count
        || activated.vehicles.some(item => !item.verified)) throw new Error(`Requested ${count}, but only ${activated.activated_uav_count} PX4/Gazebo instances verified.`)
      const currentTelemetry = await api.px4Telemetry()
      setVehicles(currentTelemetry.vehicles)
      const ids = activated.vehicles.map(item => item.aircraft_id)
      setFleetIdentity(activated.vehicles)
      const prior = new Map(plan.aircraft.map(item => [item.uav_id, item]))
      const sameFleet = ids.length === plan.aircraft.length && ids.every((id, index) => plan.aircraft[index]?.uav_id === id)
        && ids.length === plan.activated_uav_ids.length && ids.every(id => plan.activated_uav_ids.includes(id))
        && ids.length === plan.selected_uav_ids.length && ids.every(id => plan.selected_uav_ids.includes(id))
      if (sameFleet) {
        updatePlan({ required_uav_count: count })
        setMessage(`Verified ${count} PX4/Gazebo instance${count === 1 ? '' : 's'}. The plan was not changed.`)
      } else {
        updatePlan({
          required_uav_count: count, activated_uav_ids: ids, selected_uav_ids: ids,
          aircraft: ids.map((id, index) => prior.get(id) ?? newAircraft(id, index)), workflow_stage: 'general', current_uav_index: 0,
        })
        setActiveIndex(0)
        setMessage(`Verified ${count} PX4/Gazebo instances. Each assignment remains individual and unconfirmed.`)
      }
      setFleetVerified(true, `${count} of ${count} aircraft running with telemetry and a Gazebo pose.`)
      return true
    } catch (cause) {
      setFleetVerified(false, cause instanceof Error ? cause.message : 'Fleet activation failed.')
      setActivationError(cause instanceof Error ? cause.message : 'Fleet activation failed. The requested count was not reduced.')
      const structured = (cause as { detail?: { code?: unknown; checks?: Record<string, { status?: unknown; detail?: unknown }> }; status?: number } | null)
      if (structured?.detail && typeof structured.detail.code === 'string' && structured.detail.checks) {
        setActivationFailure({
          code: structured.detail.code, status: structured.status,
          checks: Object.entries(structured.detail.checks).map(([name, check]) => [name, { status: String(check.status), detail: String(check.detail ?? '') }]),
        })
      }
      return false
    } finally {
      setActivationBusy(false)
    }
  }

  const selectAircraft = (uavId: string, selectedNow: boolean) => {
    const nextIds = selectedNow ? [...selectedAircraftIds, uavId] : selectedAircraftIds.filter(id => id !== uavId)
    if (nextIds.length > UAV_COUNT_RANGE.max) return
    const byId = new Map(plan.aircraft.map(item => [item.uav_id, item]))
    const aircraft = nextIds.map((id, index) => byId.get(id) ?? newAircraft(id, index))
    const nextIndex = selectedNow ? Math.max(0, aircraft.length - 1) : Math.min(activeIndex, Math.max(0, aircraft.length - 1))
    updatePlan({ selected_uav_ids: nextIds, aircraft, current_uav_index: nextIndex, workflow_stage: nextIds.length ? 'aircraft' : 'general' })
    setActiveIndex(nextIndex)
  }

  // --- route generation (backend is authoritative) -------------------------------------------------------------

  const generateRoute = async () => {
    if (!activeAircraft) return
    const index = activeIndex
    setRouteBusy(true)
    setRouteIssues([])
    try {
      const result = await api.previewRoute({ ...plan, aircraft: plan.aircraft.map(item => ({ ...item, route: item.uav_id === activeAircraft.uav_id ? null : item.route })) }, activeAircraft.uav_id)
      changeAircraft(index, { route: result.route })
      if (typeof result.summary.limit_duration_s === 'number' && typeof result.summary.limit_length_m === 'number') {
        setEndurance({ duration_s: result.summary.limit_duration_s, length_m: result.summary.limit_length_m })
      }
      const limitText = typeof result.summary.limit_duration_s === 'number' ? ` (aircraft limit ${+(result.summary.limit_duration_s / 60).toFixed(2)} min, ${+((result.summary.limit_length_m ?? 0) / 1000).toFixed(2)} km)` : ''
      setMessage(`Route generated and validated: ${result.summary.waypoints} waypoints, ${(result.summary.length_m / 1000).toFixed(2)} km, about ${(result.summary.duration_s / 60).toFixed(1)} min${limitText}.`)
    } catch (cause) {
      const issues = (cause as { detail?: { issues?: unknown } } | null)?.detail?.issues
      setRouteIssues(Array.isArray(issues) && issues.length
        ? issues.filter((item): item is PlanIssue => typeof item?.message === 'string')
        : [{ code: 'route_invalid', message: cause instanceof Error ? cause.message : 'The route could not be generated.' }])
    } finally {
      setRouteBusy(false)
    }
  }

  const confirmAircraft = () => {
    if (!activeAircraft || missingAircraftInputs(activeAircraft).length) return
    const aircraft = plan.aircraft.map((item, index) => (index === activeIndex ? { ...item, confirmed: true } : item))
    if (aircraft.every(item => item.confirmed)) {
      updatePlan({ aircraft, workflow_stage: 'review', current_uav_index: activeIndex })
      setMessage('All selected aircraft are confirmed. Review the plan, then save a version.')
    } else {
      const nextIndex = Math.min(activeIndex + 1, aircraft.length - 1)
      updatePlan({ aircraft, workflow_stage: 'aircraft', current_uav_index: nextIndex })
      setActiveIndex(nextIndex)
      setMessage(`Confirmed ${activeAircraft.uav_id}. Continue with ${aircraft[nextIndex]?.uav_id ?? 'the next selected aircraft'}.`)
    }
  }

  // --- save / review -------------------------------------------------------------------------------------------

  const planForSave = (): MissionPlan => ({
    ...plan,
    workflow_stage: plan.workflow_stage === 'review' && planComplete ? 'review' : planComplete && plan.workflow_stage === 'general' ? 'review' : plan.workflow_stage,
  })

  const intentForSave = (): ScenarioIntent => ({
    mission_objectives: plan.objectives.map(value => MISSION_OBJECTIVES.find(item => item.value === value)?.title ?? value),
    mission_description: plan.description,
    proposed_tasks: [], uav_role_suggestions: [], operating_area_notes: '', environment_notes: '', constraints: [],
    geographic_mission: null,
    schema_version: SCHEMA_VERSION,
    mission_plan: planForSave(),
    uav_fault_assignments: [],
  })

  const hasUnsavedChanges = Boolean(selected && latest && !legacy) && (
    title.trim() !== (selected?.title ?? '')
    || stableStringify(normalizePlan(planForSave())) !== stableStringify(normalizePlan(latest?.intent.mission_plan))
  )

  const saveDraft = () => {
    if (legacy) return
    if (!title.trim()) {
      setError('Enter a scenario name before saving this draft.')
      return
    }
    void runAction(async () => {
      const draftIntent = intentForSave()
      const saved = selected
        ? await api.updateScenario(selected.id, title.trim(), config, draftIntent)
        : await api.createScenario(title.trim(), config, draftIntent)
      if (!selected) setChats(current => ({ ...current, [saved.id]: current[chatKey] ?? NO_MESSAGES }))
      setSelectedId(saved.id)
      try { sessionStorage.setItem(activeScenarioStorageKey, saved.id) } catch { /* session storage may be unavailable */ }
      onScenarioSelected?.(saved.id)
      setPlan(normalizePlan(saved.versions[0].intent.mission_plan))
      setSupersededBy(null)
      setMessage(`Saved ${saved.title} version ${saved.versions[0].version_number}. Saving does not execute anything. The next action is shown beneath the Save button.`)
    })
  }

  /**
   * Deploys what is on screen, never something else: the latest version is refetched first, and it is used only
   * if its content equals the form. A 409 "latest immutable version" is refetched and retried once.
   */
  const deployToMissionControl = async () => {
    if (!selected || !latest || !onMissionDeployed) return
    setBusy(true); setError(''); setMessage(''); setPlanIssues([])
    const shown = stableStringify(normalizePlan(planForSave()))
    const resolveLatest = async () => {
      const detail = await api.scenarioDetail(selected.id)
      const newest = detail.versions[0]
      if (!newest) throw new Error('This scenario has no saved version.')
      if (newest.id === latest.id) return newest
      setScenarios(current => current.map(item => (item.id === detail.id ? { ...item, title: detail.title, updated_at: detail.updated_at, latest_version: newest } : item)))
      if (detail.title === title.trim() && stableStringify(normalizePlan(newest.intent.mission_plan)) === shown) return newest
      setSupersededBy(newest.version_number)
      setError(`Version superseded: version ${newest.version_number} is now the latest and differs from what is shown here. Nothing was deployed. Reopen the scenario from OPEN SAVED DRAFT to review version ${newest.version_number}, or save your changes as a new version.`)
      return null
    }
    const isSuperseded = (cause: unknown) => {
      const failure = cause as { status?: number; message?: string; detail?: { code?: string } } | null
      return failure?.status === 409 && (failure.detail?.code === 'version_superseded' || /latest immutable/i.test(failure.message ?? ''))
    }
    try {
      let target = await resolveLatest()
      if (!target) return
      let note = ''
      let deployment: MissionDeployment
      try {
        deployment = await api.deployScenario(selected.id, target.id)
      } catch (cause) {
        if (!isSuperseded(cause)) throw cause
        target = await resolveLatest()
        if (!target) return
        deployment = await api.deployScenario(selected.id, target.id)
        note = `A newer version was saved while deploying, so the deploy was retried once with the latest version (v${target.version_number}).`
      }
      setSupersededBy(null)
      await refresh().catch(() => undefined)
      onMissionDeployed(deployment, note)
    } catch (cause) {
      const failure = cause as { status?: number; message?: string; detail?: { code?: string; issues?: unknown } } | null
      setError(isSuperseded(cause)
        ? `${failure?.message ?? 'The version changed.'} The deploy was retried once after refetching and the version changed again. Nothing was deployed; try again.`
        : cause instanceof Error ? cause.message : 'Deployment failed.')
      if (failure?.detail?.code === 'fleet_not_verified') setFleetVerified(false, 'The backend could not verify every selected aircraft.')
      if (failure?.status === 409) await refresh().catch(() => undefined)
      if (Array.isArray(failure?.detail?.issues)) setPlanIssues(failure.detail.issues.filter((item): item is PlanIssue => typeof (item as PlanIssue | null)?.message === 'string'))
    } finally {
      setBusy(false)
    }
  }

  const submitForReview = () => {
    if (!selected || !latest) return
    void runAction(async () => {
      await api.submitScenario(selected.id, latest.id)
      setMessage(`Version ${latest.version_number} submitted for General review. No mission was executed.`)
    })
  }

  const validationContext = { title, plan, fleetVerified, availableVehicleIds: availableVehicles.map(vehicle => vehicle.id), endurance }
  const missionMissing = computePlanMissing(validationContext)
  const deployBlockers = legacy ? ['This legacy version is read-only.'] : computePlanDeployBlockers({
    ...validationContext, hasSavedVersion: Boolean(selected && latest), hasUnsavedChanges, isOperator, approvalStatus, canManage,
  })
  const nextAction = computeNextAction({
    isOperator, canManage, hasUnsavedChanges, superseded: supersededBy !== null, fleet: fleetState, checklist: missionMissing, approvalStatus, legacy: Boolean(legacy),
  })
  const startupNote = startup && (startup.status === 'DEGRADED' || startup.status === 'FAILED') ? explainSitlStartup(startup.status, startup.detail) : ''
  const helpContext: HelpContext = {
    title, plan, activeIndex, section: sectionOf(plan.workflow_stage),
    scenarioTitle: selected?.title ?? null, versionNumber: latest?.version_number ?? null, approvalStatus, isOperator,
    fleetVerified, requiredCountInput, activeFaultCount: 0,
    validationItems: missionMissing, deployBlockers, legacy: Boolean(legacy),
  }

  // --- assistant -------------------------------------------------------------------------------------------------

  /** One handler for typed text and for selectable options; `fromOption` messages never reach the AI endpoint. */
  const submitAssistantMessage = async (override?: string, fromOption = false) => {
    const text = (override ?? assistantInput).trim()
    if (!text || assistantBusy || pendingAssistantAction) return
    if (override === undefined) setAssistantInput('')
    setAssistantBusy(true)
    setChat(current => [...current, { role: 'user' as const, content: text }].slice(-80))
    const reply = (content: string) => setChat(current => [...current, { role: 'assistant' as const, content }].slice(-80))
    const propose = (checked: ValidatedOps) => {
      if (!checked.ops.length) { reply(`I did not propose that change: it involves ${checked.rejected.join('; ')}. Nothing was changed.`); return }
      setPendingAssistantAction({ kind: 'batch', ops: checked.ops, summary: checked.summary })
      reply(`Proposed: ${checked.summary.join('; ')}. Nothing has changed yet. Confirm to apply, or cancel to discard it.`)
    }
    try {
      if (legacy) {
        const readOnly = answerReadOnlyQuestion(text, helpContext)
        reply(readOnly?.text ?? 'This legacy version is read-only, so I cannot propose changes. Start a new draft to plan with the current model.')
        return
      }
      const fleet = text.match(/\b(?:need|require|want|fleet size(?:\s+to)?|set (?:the )?(?:required )?(?:uav )?count to)\s*(?:to\s*)?([1-5])\s*(?:uavs?|aircraft)?\b/i)
      if (fleet) {
        if (!canManage) { reply('Your role cannot activate a fleet. A General account can configure and activate aircraft.'); return }
        const count = Number(fleet[1])
        setPendingAssistantAction({ kind: 'fleet', count })
        reply(`Proposed: set the required count to ${count} and request that many disarmed PX4/Gazebo instances through the verified fleet workflow. Nothing has changed yet. Confirm to apply and start the request; cancel to discard the proposal.`)
        return
      }
      const setCommand = parseSetCommand(text, activeIndex)
      if (setCommand) {
        if (!canManage) { reply('Your role cannot edit scenario fields.'); return }
        propose(validateAssistantOps(setCommand.candidates, plan))
        return
      }
      const objective = MISSION_OBJECTIVES.find(item => text.toLowerCase().includes(item.title.toLowerCase())
        || text.toLowerCase().includes(item.value.replace(/_/g, ' ')))
      if (objective && /\bobjectives?\b/i.test(text) && /\b(set|change|make|select)\b/i.test(text)) {
        if (!canManage) { reply('Your role cannot edit scenario fields.'); return }
        propose(validateAssistantOps([{ op: 'set_objective', objective: objective.value }], plan))
        return
      }
      const aircraft = text.match(/\b(?:uav|aircraft)[ -]?(\d{1,2})\b/i)
      if (aircraft && /\b(destination|waypoint|route|datum|drop point|start)\b/i.test(text) && /\b(select|pick|choose|move|change|place)\b/i.test(text)) {
        const index = Number(aircraft[1]) - 1
        const target = plan.aircraft[index]
        if (!target) { reply(`UAV ${aircraft[1]} is not in the activated fleet. Activate and assign it first.`); return }
        if (index > 0 && !plan.aircraft[index - 1]?.confirmed) {
          const previous = plan.aircraft[index - 1]
          setActiveIndex(index - 1)
          updatePlan({ current_uav_index: index - 1, workflow_stage: 'aircraft' })
          reply(`Complete and confirm ${previous.uav_id} before changing ${target.uav_id}. I have selected ${previous.uav_id}; no location or route was changed.`)
          return
        }
        setActiveIndex(index)
        updatePlan({ current_uav_index: index, workflow_stage: 'aircraft' })
        reply(`Selected ${target.uav_id} in Section B. Pick its start and target points on the map, then press GENERATE / PREVIEW ROUTE; this text does not provide coordinates, so I left every location unchanged.`)
        return
      }
      const readOnly = answerReadOnlyQuestion(text, helpContext)
      if (readOnly) { reply(readOnly.text); return }
      // Only free text that no deterministic rule understood reaches the model; selectable options never do.
      if (fromOption) { reply(unknownHelp()); return }
      try {
        const history = chat.slice(-6).map(item => ({ role: item.role, content: item.content.slice(0, 600) }))
        const result = await api.scenarioAssistant({
          draft_id: selectedId || chatDraftId, message: text.slice(0, 500), history, snapshot: buildAssistantSnapshot(helpContext),
        })
        const checked = validateAssistantOps(result.actions, plan)
        if (checked.ops.length && !canManage) {
          reply(`${result.reply}\n\nYour account cannot edit scenario fields, so no changes were proposed.`)
        } else if (checked.ops.length) {
          setPendingAssistantAction({ kind: 'batch', ops: checked.ops, summary: checked.summary })
          reply(`${result.reply}\n\nProposed changes (nothing has changed yet):\n${checked.summary.map(item => `• ${item}`).join('\n')}\nConfirm to apply them, or cancel to discard.`)
        } else {
          reply(`${result.reply}${checked.rejected.length ? `\n(I ignored a proposed change involving ${checked.rejected.join('; ')}.)` : ''}`)
        }
      } catch (cause) {
        const failure = cause as { message?: string; detail?: { code?: string } } | null
        const fallback = failure?.message ? `${failure.message}${failure.detail?.code ? ` [${failure.detail.code}]` : ''}` : 'the AI request failed'
        reply(unknownHelp(`AI is unavailable: ${aiFailureMessage(failure?.detail?.code, fallback)}\nThe rule-based assistant still works.\n\n`))
      }
    } finally { setAssistantBusy(false) }
  }

  const confirmAssistantAction = async () => {
    const action = pendingAssistantAction
    if (!action || assistantBusy || confirmingRef.current) return
    confirmingRef.current = true
    setPendingAssistantAction(null)
    setAssistantBusy(true)
    const say = (content: string) => setChat(current => [...current, { role: 'assistant' as const, content }].slice(-80))
    try {
      if (action.kind === 'fleet') {
        const succeeded = await activateRequestedFleet(action.count)
        say(succeeded
          ? 'Fleet activation verified. Section A now shows the returned fleet identities and count.'
          : 'The requested fleet was not fully verified. Section A shows the actual error; the count was not marked active.')
      } else {
        // Re-validate against the live form: it may have changed since the proposal was made.
        const checked = validateAssistantOps(action.ops, plan)
        if (!checked.ops.length || checked.rejected.length) {
          say(`The form changed since this was proposed (${checked.rejected.join('; ') || 'nothing valid left'}). No changes were applied.`)
        } else {
          const applied = applyAssistantOps(plan, checked.ops)
          const patch = { ...applied.patch }
          if (applied.touchedAircraft !== null) {
            patch.workflow_stage = 'aircraft'
            patch.current_uav_index = applied.touchedAircraft
            setActiveIndex(applied.touchedAircraft)
          }
          if (Object.keys(patch).length) updatePlan(patch)
          setRouteIssues([])
          let fleetNote = ''
          if (applied.fleetCount !== null) {
            const succeeded = await activateRequestedFleet(applied.fleetCount)
            fleetNote = succeeded ? ' Fleet activation verified.' : ' The requested fleet was not fully verified; Section A shows the actual error.'
          }
          say(`Applied: ${checked.summary.join('; ')}.${fleetNote}`)
        }
      }
    } finally {
      confirmingRef.current = false
      setAssistantBusy(false)
    }
  }

  const cancelAssistantAction = () => {
    if (assistantBusy || !pendingAssistantAction) return
    setPendingAssistantAction(null)
    setChat(current => [...current, { role: 'assistant' as const, content: 'Cancelled. The proposal was discarded and nothing was changed.' }].slice(-80))
  }

  // --- render ----------------------------------------------------------------------------------------------------

  const renderParam = (spec: ParamSpec) => {
    const value = activeAircraft?.parameters[spec.key]
    if (spec.kind === 'number') {
      return <label className="scenario-workspace-field" key={spec.key}>{spec.label.toUpperCase()}{spec.required ? ' *' : ''}
        <input type="number" min={spec.min} max={spec.max} step={spec.step} value={typeof value === 'number' ? value : ''} disabled={!editable || busy}
          placeholder={spec.required ? `e.g. ${spec.example}` : `default · e.g. ${spec.example}`} onChange={event => setParameter(spec, event.target.value)}/>
        <small>{spec.help}</small></label>
    }
    if (spec.kind === 'select') {
      return <label className="scenario-workspace-field" key={spec.key}>{spec.label.toUpperCase()}{spec.required ? ' *' : ''}
        <select value={typeof value === 'string' ? value : ''} disabled={!editable || busy} onChange={event => setParameter(spec, event.target.value)}>
          <option value="">Choose one</option>
          {PAYLOAD_CATEGORIES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select><small>{spec.help}</small></label>
    }
    return <div className="scenario-workspace-field" key={spec.key}><b>{spec.label.toUpperCase()}{spec.required ? ' *' : ''}</b><small>{spec.help} Pick it with the map buttons below.</small></div>
  }

  return <section className="scenario-lab-workspace">
    <header className="scenario-lab-header panel">
      <div>
        <span className="eyebrow">SHARED VERSIONED SCENARIO · SCHEMA {SCHEMA_VERSION}</span>
        <h2>Scenario Lab</h2>
        <p>Configure manually or use the contextual assistant. Routes are generated and validated by the backend. Drafts are versioned; saving never runs a simulator or sends commands to PX4.</p>
      </div>
      <button className="small-icon" type="button" onClick={() => void refresh().catch(cause => setError(cause instanceof Error ? cause.message : 'Could not refresh scenarios.'))} title="Refresh scenarios" disabled={busy}><RefreshCw size={14}/></button>
    </header>
    {error && <div className="scenario-library-error" role="alert"><AlertTriangle size={14}/>{error}
      {planIssues.length > 0 && <ul aria-label="Validation issues">{planIssues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issueText(issue)}</li>)}</ul>}</div>}
    {message && <div className="scenario-library-message" role="status"><CheckCircle2 size={14}/>{message}</div>}
    <div className="scenario-lab-draftbar panel">
      <label>SCENARIO NAME<input aria-label="SCENARIO TITLE" value={title} onChange={event => setTitle(event.target.value)} maxLength={120} placeholder="Name this scenario" disabled={Boolean(legacy)}/></label>
      <label>OPEN SAVED DRAFT<select aria-label="SAVED SCENARIO" value={selectedId} onChange={event => loadScenario(event.target.value)}><option value="">NEW DRAFT</option>{scenarios.map(item => <option key={item.id} value={item.id}>{item.title} · v{item.latest_version.version_number}</option>)}</select></label>
      {latest && <div className="scenario-lab-version"><b>{selected?.title} · v{latest.version_number}</b><span>{approvalStatus ?? 'DRAFT'} · SHA-256 {latest.config_hash}</span>{approvalRequest?.reason && <small>Review: {approvalRequest.reason}</small>}</div>}
    </div>

    {legacy && <section className="scenario-lab-section panel" aria-label="Legacy version">
      <header><span className="eyebrow">LEGACY · SCHEMA 1 · READ-ONLY</span><h3>This version predates the route-planning model</h3>
        <p>It is shown exactly as stored and is never rewritten or migrated. To plan with generated, validated routes start a new draft.</p></header>
      <div className="scenario-review-summary"><article><b>STORED MISSION</b>
        <span>Objectives: {legacy.mission_objectives.join(', ') || 'none'}</span>
        <span>Description: {legacy.mission_description || legacy.geographic_mission?.mission_description || 'none'}</span>
        <span>Aircraft assignments: {legacy.geographic_mission?.uav_assignments.length ?? 0}</span>
        {legacy.geographic_mission?.uav_assignments.map(item => <span key={item.uav_id}>{item.uav_id} · {item.role || 'no role'} · {item.task || 'no task'} · {item.waypoints?.length ?? 0} stored waypoint(s)</span>)}
      </article></div>
      <div className="scenario-library-actions"><button className="button-primary" type="button" onClick={() => startFromLegacy()}>START A NEW SCHEMA-2 DRAFT</button></div>
    </section>}

    {!legacy && <>
    <section className="scenario-lab-section panel" aria-labelledby="scenario-general-heading">
      <header><span className="eyebrow">SECTION A</span><h3 id="scenario-general-heading">Mission</h3></header>
      <fieldset disabled={!editable || busy} className="scenario-objectives">
        <legend>MISSION OBJECTIVES · SELECT ALL THAT APPLY</legend>
        {MISSION_OBJECTIVES.map(objective => <label className="scenario-objective-choice" key={objective.value}>
          <input type="checkbox" checked={plan.objectives.includes(objective.value)} onChange={event => setObjectives(objective.value, event.target.checked)}/>
          <span><b>{objective.title}</b><small>{objective.description}</small></span>
        </label>)}
      </fieldset>
      {trackingOnly && <p className="scenario-unsupported-note">{TRACKING_NOTE}</p>}
      <div className="scenario-workspace-field"><b>OPERATING AREA *</b><small>Choose a circle or draw a polygon on the map. Search points and the drop point must lie inside it.</small></div>
      {plan.operating_area?.kind === 'circle' && <label className="scenario-workspace-field">CIRCLE RADIUS (m)
        <input type="number" min={10} max={50000} value={plan.operating_area.radius_m ?? ''} disabled={!editable || busy}
          onChange={event => changeArea({ ...plan.operating_area!, radius_m: event.target.value ? Number(event.target.value) : null })}/></label>}
      <PlanMapEditor scope="area" plan={plan} disabled={!editable || busy} onAreaChange={changeArea} onAircraftChange={() => undefined}/>
      <div className="scenario-workspace-field scenario-fault-values">
        <label>MAX DISTANCE FROM START (m) *<input type="number" min={1} max={100000} value={plan.constraints.max_distance_from_start_m ?? ''} disabled={!editable || busy}
          onChange={event => updateMissionInputs({ constraints: { ...plan.constraints, max_distance_from_start_m: event.target.value ? Number(event.target.value) : null } }, true)}/></label>
        <label>MAX ALTITUDE (m) *<input type="number" min={1} max={500} value={plan.constraints.max_altitude_m ?? ''} disabled={!editable || busy}
          onChange={event => updateMissionInputs({ constraints: { ...plan.constraints, max_altitude_m: event.target.value ? Number(event.target.value) : null } }, true)}/></label>
      </div>
      <label className="scenario-workspace-field">MAX DURATION (minutes, optional)<input type="number" min={MISSION_DURATION_RANGE_MINUTES.min} max={MISSION_DURATION_RANGE_MINUTES.max} value={plan.max_duration_minutes ?? ''} disabled={!editable || busy}
        placeholder="Optional" onChange={event => updateMissionInputs({ max_duration_minutes: event.target.value ? Number(event.target.value) : null }, true)}/></label>
      <label className="scenario-workspace-field">HOW MANY UAVS ARE REQUIRED FOR THIS MISSION? ({UAV_COUNT_RANGE.min}–{UAV_COUNT_RANGE.max})<input type="number" min={UAV_COUNT_RANGE.min} max={UAV_COUNT_RANGE.max} value={requiredCountInput} disabled={!editable || busy || activationBusy} onChange={event => setRequiredCountInput(event.target.value)}/></label>
      <button type="button" className="button-secondary" disabled={!editable || busy || activationBusy || !requiredCountInput} onClick={() => void activateRequestedFleet()}>{activationBusy ? 'ACTIVATING AND VERIFYING…' : 'ACTIVATE AND VERIFY PX4 / GAZEBO FLEET'}</button>
      {activationError && <div className="scenario-library-error" role="alert">
        <p>{activationError}</p>
        {activationFailure && <>
          <small>{activationFailure.status ? `HTTP ${activationFailure.status} · ` : ''}{activationFailure.code}</small>
          <ul aria-label="Fleet activation checks">{activationFailure.checks.map(([name, check]) =>
            <li key={name}><b>{name.replace(/_/g, ' ').toUpperCase()}</b>: {check.status} — {check.detail}</li>)}</ul>
        </>}
      </div>}
      <label className="scenario-workspace-field">DESCRIPTION (optional)<textarea rows={2} value={plan.description} disabled={!editable || busy} maxLength={500} placeholder="A short purpose statement." onChange={event => updatePlan({ description: event.target.value })}/></label>
      <label className="scenario-workspace-field">NOTES (optional, for reviewers)<textarea rows={2} value={plan.notes} disabled={!editable || busy} maxLength={500} placeholder="Nothing reads this automatically." onChange={event => updatePlan({ notes: event.target.value })}/></label>
      <label className="scenario-objective-choice"><input type="checkbox" checked={plan.allow_multiple_primary} disabled={!editable || busy} onChange={event => updateMissionInputs({ allow_multiple_primary: event.target.checked })}/><span><b>Allow more than one primary aircraft</b><small>Off by default: exactly one primary.</small></span></label>
      <div className="scenario-aircraft-selection">
        <div><b>SELECT CONNECTED AIRCRAFT · {selectedAircraftIds.length} SELECTED</b><small>Only backend-reported PX4 IDs with valid positions are listed. Selection does not imply capability or route feasibility.</small></div>
        {fleetError && <p className="scenario-library-error" role="alert">{fleetError}</p>}
        {!availableVehicles.length && !fleetError && <p className="planner-empty">No connected aircraft with valid position telemetry are currently available.</p>}
        <div className="scenario-aircraft-options">{vehicles.map(vehicle => {
          const usable = isAvailable(vehicle)
          return <label key={vehicle.id} className={!usable ? 'unavailable' : ''}>
            <input type="checkbox" checked={selectedAircraftIds.includes(vehicle.id)} disabled={!editable || busy || !usable} onChange={event => selectAircraft(vehicle.id, event.target.checked)}/>
            <span><b>{vehicle.id}</b><small>{usable ? 'Connected · valid position' : `${vehicle.status} · unavailable`}</small></span>
          </label>
        })}</div>
      </div>
    </section>

    <section className="scenario-lab-section panel" aria-labelledby="scenario-aircraft-heading">
      <header><span className="eyebrow">SECTION B</span><h3 id="scenario-aircraft-heading">Individual UAV mission configuration</h3><p>Configure, generate the route for, and confirm one aircraft at a time.</p></header>
      {!selectedAircraftIds.length ? <div className="scenario-stage-blocked">Select connected aircraft in Section A before configuring individual missions.</div> : <>
        <nav className="scenario-aircraft-progress" aria-label="Individual aircraft configuration progress">
          {plan.aircraft.map((item, index) => <button key={item.uav_id} type="button" className={index === activeIndex ? 'active' : item.confirmed ? 'complete' : ''} disabled={!editable || busy || (index > 0 && !plan.aircraft[index - 1]?.confirmed)} onClick={() => { setActiveIndex(index); updatePlan({ current_uav_index: index, workflow_stage: 'aircraft' }); setRouteIssues([]) }}>
            <b>{item.uav_id}</b><small>{item.confirmed ? 'CONFIRMED' : `AIRCRAFT ${index + 1} OF ${selectedAircraftIds.length}`}</small>
          </button>)}
        </nav>
        {activeAircraft && <article className="scenario-active-aircraft">
          <div className="scenario-active-aircraft-heading"><span className="eyebrow">CURRENT AIRCRAFT · {activeIndex + 1} OF {selectedAircraftIds.length}</span><h4>Configure {activeAircraft.uav_id}</h4></div>
          <label className="scenario-workspace-field">ROLE *<select value={activeAircraft.role ?? ''} disabled={!editable || busy} onChange={event => changeAircraft(activeIndex, { role: (event.target.value || null) as AircraftPlan['role'] })}>
            <option value="">Choose one role</option>
            {AIRCRAFT_ROLES.map(role => <option key={role.value} value={role.value}>{role.label}</option>)}
          </select></label>
          <div className="scenario-workspace-field scenario-fault-values">
            <label>PRIORITY (1 = highest)<select value={activeAircraft.priority} disabled={!editable || busy} onChange={event => changeAircraft(activeIndex, { priority: Number(event.target.value) })}>{[1, 2, 3].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
            <label className="scenario-objective-choice"><input type="checkbox" checked={activeAircraft.reassignable} disabled={!editable || busy} onChange={event => changeAircraft(activeIndex, { reassignable: event.target.checked })}/><span><b>Reassignable</b></span></label>
          </div>
          <label className="scenario-workspace-field">TASK TYPE *<select value={activeAircraft.task_type ?? ''} disabled={!editable || busy} onChange={event => changeAircraft(activeIndex, { task_type: (event.target.value || null) as TaskType | null, parameters: {} })}>
            <option value="">Choose a task type</option>
            {taskChoices.map(task => <option key={task.value} value={task.value}>{task.label}</option>)}
          </select>
            <small>{taskSpec(activeAircraft.task_type)?.summary ?? 'Pick the task explicitly; only its parameters will appear.'}</small>
            {suggestedTasks.length > 0 && <small>Suggested by your objectives: {suggestedTasks.map(value => taskSpec(value)?.label).join(', ')}.</small>}
          </label>
          {taskSpec(activeAircraft.task_type)?.params.map(renderParam)}
          <div className="scenario-workspace-field scenario-fault-values">
            <label>ALTITUDE (m) *<input type="number" min="0.1" max="500" step="0.1" value={activeAircraft.altitude_m ?? ''} disabled={!editable || busy} onChange={event => changeAircraft(activeIndex, { altitude_m: event.target.value ? Number(event.target.value) : null })}/></label>
            <label>SPEED (m/s) *<input type="number" min="0.1" max="50" step="0.1" value={activeAircraft.speed_mps ?? ''} disabled={!editable || busy} onChange={event => changeAircraft(activeIndex, { speed_mps: event.target.value ? Number(event.target.value) : null })}/></label>
          </div>
          <label className="scenario-workspace-field">LOITER AT DESTINATION (s){activeAircraft.task_type === 'loiter_hold' ? ' *' : ''}<input type="number" min="0" max="3600" value={activeAircraft.loiter_duration_s} disabled={!editable || busy} onChange={event => changeAircraft(activeIndex, { loiter_duration_s: event.target.value ? Number(event.target.value) : 0 })}/></label>
          <label className="scenario-workspace-field">RETURN MODE<select value={activeAircraft.return_mode} disabled={!editable || busy} onChange={event => changeAircraft(activeIndex, { return_mode: event.target.value as AircraftPlan['return_mode'] })}>
            {RETURN_MODES.map(mode => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
          </select></label>
          <PlanMapEditor scope="aircraft" plan={plan} aircraft={activeAircraft} disabled={!editable || busy}
            onAreaChange={changeArea} onAircraftChange={patch => changeAircraft(activeIndex, patch)}/>
          <div className="scenario-step-actions">
            <button type="button" className="button-secondary" disabled={!editable || busy || routeBusy || missingAircraftInputs(activeAircraft).some(item => item.key !== 'route')} onClick={() => void generateRoute()}>
              <Route size={14}/> {routeBusy ? 'GENERATING…' : 'GENERATE / PREVIEW ROUTE'}</button>
          </div>
          {routeIssues.length > 0 && <div className="scenario-library-error" role="alert"><AlertTriangle size={14}/>The route could not be generated:
            <ul aria-label="Route issues">{routeIssues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul></div>}
          {activeAircraft.route && <div className="scenario-individual-summary" aria-label="Route preview">
            <b>{activeAircraft.confirmed ? 'MISSION CONFIRMED' : 'ROUTE GENERATED AND VALIDATED'}</b>
            <span>{activeAircraft.route.waypoints.length} waypoints · {(activeAircraft.route.length_m / 1000).toFixed(2)} km · about {(activeAircraft.route.duration_s / 60).toFixed(1)} min{endurance ? ` · aircraft limit ${+(endurance.duration_s / 60).toFixed(2)} min, ${+(endurance.length_m / 1000).toFixed(2)} km` : ''}</span>
            {activeAircraft.route.events.map(event => <span key={event.waypoint_seq}>Simulated {event.type} ({event.payload_category.replace(/_/g, ' ')}) at +{event.planned_time_s} s — logged event only, no release command is sent.</span>)}
            <small>{activeAircraft.route.assumptions}</small>
          </div>}
          <div className="scenario-step-actions">
            <button type="button" className="button-secondary" disabled={!editable || busy || activeIndex === 0} onClick={() => { const index = Math.max(0, activeIndex - 1); setActiveIndex(index); updatePlan({ current_uav_index: index }) }}><ChevronLeft size={14}/> PREVIOUS AIRCRAFT</button>
            <button type="button" className="button-primary" disabled={!editable || busy || missingAircraftInputs(activeAircraft).length > 0} onClick={confirmAircraft}>
              {activeIndex === selectedAircraftIds.length - 1 ? 'CONFIRM ALL AIRCRAFT MISSIONS' : `CONFIRM ${activeAircraft.uav_id} AND CONTINUE`} <ChevronRight size={14}/>
            </button>
          </div>
        </article>}
      </>}
    </section>

    <section className="scenario-lab-section panel" aria-labelledby="scenario-review-heading">
      <header><span className="eyebrow">SECTION C</span><h3 id="scenario-review-heading">Review and validation</h3><p>Review the exact data that will be stored in the immutable version, including every generated waypoint.</p>
        <p className="scenario-attack-pointer">GPS attack experiments are designed in the {onOpenAttackLab ? <button type="button" className="inline-link" onClick={onOpenAttackLab}>{ATTACK_LAB_NAME}</button> : ATTACK_LAB_NAME} after saving.</p></header>
      <div className="scenario-review-summary">
        <article><b>MISSION</b><span>{title || 'Scenario name missing'}</span>
          <span>{plan.objectives.map(value => MISSION_OBJECTIVES.find(item => item.value === value)?.title ?? value).join(', ') || 'No objectives selected'}</span>
          <span>Area: {!plan.operating_area ? 'not selected' : plan.operating_area.kind === 'circle' ? `circle, radius ${plan.operating_area.radius_m ?? 0} m` : `polygon, ${plan.operating_area.vertices.length} vertices`}</span>
          <span>Limits: max distance {plan.constraints.max_distance_from_start_m ?? 'not set'} m; max altitude {plan.constraints.max_altitude_m ?? 'not set'} m; max duration {plan.max_duration_minutes ?? 'not set'} min</span>
          <span>Required: {plan.required_uav_count ?? 'unknown'}; activated: {plan.activated_uav_ids.length}; assigned: {selectedAircraftIds.length}</span>
          {plan.description && <span>Description: {plan.description}</span>}{plan.notes && <span>Notes: {plan.notes}</span>}</article>
        {plan.aircraft.map((item, index) => {
          const identity = fleetIdentity.find(value => value.aircraft_id === item.uav_id)
          const vehicle = vehicles.find(value => value.id === item.uav_id)
          return <article key={item.uav_id}><b>{item.uav_id} - {item.role ?? 'no role'} · priority {item.priority}{item.reassignable ? ' · reassignable' : ''}</b>
            <span>PX4 system {identity?.system_id ?? vehicle?.system_id ?? 'unknown'}; instance {identity?.instance_number ?? 'unknown'}; Gazebo {identity?.gazebo_model ?? 'unverified'}; link {vehicle?.status ?? 'unavailable'}</span>
            <span>Task: {taskSpec(item.task_type)?.label ?? 'not chosen'}; altitude {item.altitude_m ?? 'not set'} m; speed {item.speed_mps ?? 'not set'} m/s; loiter {item.loiter_duration_s} s; {RETURN_MODES.find(mode => mode.value === item.return_mode)?.label}</span>
            <span>Start: {item.start_location ? `${fmt(item.start_location.latitude)}, ${fmt(item.start_location.longitude)}` : 'not selected'}; assignment: {item.confirmed ? 'confirmed' : 'incomplete'}</span>
            {item.route ? <>
              <span>{item.route.waypoints.length} stored waypoints · {(item.route.length_m / 1000).toFixed(2)} km · about {(item.route.duration_s / 60).toFixed(1)} min</span>
              <span className={`scenario-endurance ${enduranceIssues(item.route, endurance).length ? 'over' : ''}`} aria-label={`${item.uav_id} endurance`}>
                Estimated {(item.route.duration_s / 60).toFixed(1)} min, {(item.route.length_m / 1000).toFixed(2)} km
                {endurance ? ` · aircraft limit ${+(endurance.duration_s / 60).toFixed(2)} min, ${+(endurance.length_m / 1000).toFixed(2)} km${plan.max_duration_minutes ? ` · your max duration ${plan.max_duration_minutes} min` : ''}` : ''}
                {enduranceIssues(item.route, endurance).length > 0 && <b role="alert"> · {enduranceIssues(item.route, endurance).join(' ')}</b>}</span>
              <table aria-label={`${item.uav_id} stored waypoints`}><thead><tr><th>#</th><th>Action</th><th>Latitude</th><th>Longitude</th><th>Alt (m)</th><th>Speed</th><th>Hover (s)</th></tr></thead>
                <tbody>{item.route.waypoints.map(point => <tr key={point.seq}><td>{point.seq}</td><td>{point.action}</td><td>{fmt(point.latitude, 7)}</td><td>{fmt(point.longitude, 7)}</td><td>{point.altitude_m}</td><td>{point.speed_mps}</td><td>{point.hover_s}</td></tr>)}</tbody></table>
              {item.route.events.map(event => <span key={event.waypoint_seq}>Event: {event.type} · {event.aircraft_id} · {event.payload_category} · planned +{event.planned_time_s} s (simulated, logged only)</span>)}
            </> : <span>No route generated yet.</span>}
            <button className="button-quiet" type="button" onClick={() => { setActiveIndex(index); updatePlan({ workflow_stage: 'aircraft', current_uav_index: index }) }}>EDIT AIRCRAFT</button></article>
        })}
      </div>
      <div className="scenario-review-validation" aria-live="polite">
        <b>{missionMissing.length ? `${missionMissing.length} ITEM(S) NEED ATTENTION` : 'SCENARIO INPUTS READY FOR DRAFT REVIEW'}</b>
        {missionMissing.length > 0 && <ul>{missionMissing.map(item => <li key={item}>{item}</li>)}</ul>}
        <small>Routes are generated on a local flat-earth approximation for small areas and validated against your limits. They are planning data only and are never uploaded to PX4.</small>
      </div>
      <div className="scenario-library-actions">
        <button className="button-primary" type="button" disabled={!editable || busy || loading} onClick={saveDraft}><Save size={14}/> {selected ? 'SAVE NEW IMMUTABLE VERSION' : 'SAVE SCENARIO DRAFT'}</button>
        {selected && latest && !legacy && <span className={`scenario-save-status ${hasUnsavedChanges ? 'dirty' : 'saved'}`} role="status">
          {hasUnsavedChanges ? 'Unsaved changes' : `Saved v${latest.version_number}`}{!hasUnsavedChanges && approvalStatus ? <small> · {approvalStatus.toLowerCase().replace(/_/g, ' ')}</small> : null}</span>}
        {!canManage && <small className="permission-note">Your account cannot create or edit scenario drafts.</small>}
      </div>
      {/* The next action sits directly beneath Save and is never hidden: when it is unavailable the reasons are listed beside it. */}
      {!(selected && latest) ? <p className="scenario-next-hint">After you save, the next action appears here: {isOperator ? 'Submit for review, then Deploy to Mission Control once a General approves that version.' : 'Deploy to Mission Control.'}</p>
        : <div className="scenario-next-action" role="group" aria-label="Next action">
          <div className="scenario-next-main">
            <button className="button-primary" type="button" disabled={!nextAction.enabled || busy || loading || activationBusy}
              aria-describedby={nextAction.reasons.length ? 'scenario-next-reasons' : undefined}
              onClick={() => (nextAction.kind === 'deploy' ? void deployToMissionControl() : submitForReview())}>
              {nextAction.kind === 'deploy' ? <CheckCircle2 size={14}/> : <Send size={14}/>} {nextAction.label}</button>
            <small>{nextAction.kind === 'deploy'
              ? 'Hands this saved version and its routes to Mission Control and opens it. Nothing is armed, flown or uploaded to PX4.'
              : 'Sends this exact saved version to a General for review. You can deploy it once it is approved.'}</small>
          </div>
          {nextAction.reasons.length > 0 && <div className="scenario-next-reasons" id="scenario-next-reasons">
            <b>UNAVAILABLE BECAUSE</b><ul aria-label="Why the next action is unavailable">{nextAction.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
          </div>}
          <div className={`scenario-fleet-status ${fleetState}`} aria-label="Fleet verification">
            <span><b>FLEET VERIFICATION</b><i className={`fleet-pill ${fleetState}`}>{FLEET_VERIFICATION_LABEL[fleetState]}</i></span>
            <small>{fleetState === 'stale' ? `Last verified ${Math.round((clock - fleetCheck.at) / 1000)} s ago; a result older than ${FLEET_FRESH_MS / 1000} s does not count.` : fleetCheck.detail}
              {' '}Deployment needs every selected aircraft running, sending telemetry and present in Gazebo. The backend checks this again when you deploy.</small>
            <button className="button-secondary" type="button" disabled={!editable || busy || activationBusy || !plan.required_uav_count}
              onClick={() => void activateRequestedFleet(plan.required_uav_count ?? undefined)}>{activationBusy ? 'ACTIVATING AND VERIFYING…' : 'ACTIVATE AND VERIFY'}</button>
            {activationError && <small className="scenario-fleet-error" role="alert">{activationError}{activationFailure ? ` (${activationFailure.status ? `HTTP ${activationFailure.status} · ` : ''}${activationFailure.code}; the per-check list is in Section A)` : ''}</small>}
            {startupNote && <small className="scenario-startup-note" role="note">{startupNote}</small>}
          </div>
          {!hasUnsavedChanges && <SimulationLocationNotice scenarioId={selected.id} versionId={latest.id} canRelocate={editable} active={active}
            onRelocated={() => void checkFleet()}/>}
        </div>}
    </section>
    </>}
    {loading && <p className="planner-empty">Loading saved scenarios and current PX4 fleet data…</p>}
    {!scenarios.length && !loading && <div className="scenario-library-empty"><CheckCircle2 size={16}/>No saved scenarios yet. The current configuration can be saved as a draft.</div>}
    <aside className={`scenario-assistant ${assistantOpen ? 'expanded' : ''}`} aria-label="Scenario Lab assistant">
      {assistantOpen ? <>
        <header><div><span className="eyebrow">CONTEXTUAL ASSISTANT</span><b>Scenario Lab</b></div><button type="button" aria-label="Collapse assistant" onClick={() => setAssistantOpen(false)}><X size={17}/></button></header>
        <div className="scenario-assistant-messages" aria-live="polite">{chat.map((item, index) => <p className={item.role} key={`${index}-${item.role}`}>{item.content}</p>)}{!chat.length && <p className="assistant-hint">Ask about the current draft, give a form command, or pick an option below. Options never use AI.</p>}
          <AssistantOptionBar options={buildAssistantOptions(helpContext)} disabled={assistantBusy || Boolean(pendingAssistantAction)} canManage={editable}
            onSelect={message => void submitAssistantMessage(message, true)}/></div>
        {pendingAssistantAction && <div className="scenario-assistant-confirm"><span>{pendingAssistantAction.kind === 'fleet' ? `Activate ${pendingAssistantAction.count} UAVs?` : `Apply ${pendingAssistantAction.ops.length} proposed change(s)?`}</span><button type="button" disabled={assistantBusy} onClick={() => void confirmAssistantAction()}>CONFIRM</button><button type="button" disabled={assistantBusy} onClick={cancelAssistantAction}>CANCEL</button></div>}
        <form onSubmit={event => { event.preventDefault(); void submitAssistantMessage() }}><input aria-label="Message Scenario Lab assistant" value={assistantInput} maxLength={500} disabled={assistantBusy || Boolean(pendingAssistantAction)} onChange={event => setAssistantInput(event.target.value)} placeholder="Ask or configure a field…"/><button type="submit" disabled={assistantBusy || Boolean(pendingAssistantAction) || !assistantInput.trim()} aria-label="Send message">{assistantBusy ? <RefreshCw size={15}/> : <Send size={15}/>}</button></form>
      </> : <button className="scenario-assistant-launcher" type="button" onClick={() => setAssistantOpen(true)} aria-label="Open Scenario Lab assistant"><MessageCircle size={18}/><span>SCENARIO ASSISTANT</span></button>}
    </aside>
  </section>
}
