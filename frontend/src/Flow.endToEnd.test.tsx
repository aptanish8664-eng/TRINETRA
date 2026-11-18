/**
 * Acceptance walk through the real App: Scenario Lab -> save -> deploy -> Mission Control -> Attack Lab -> back.
 *
 * The real ScenarioWorkspace, Px4MissionControl, AttackLab and App navigation run against an in-memory fake of the
 * backend that enforces the same rules (immutable versions, latest-version-only deploy, operator approval, pinned
 * experiments). Only the map widgets, the WebSocket and the threat monitor are replaced.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import type { MissionDeployment, ScenarioDetail, ScenarioIntent, ScenarioVersion, UserSession } from './services/auth'
import type { AircraftPlan, GeneratedRoute, MissionPlan, OperatingArea } from './components/missionPlan'
import { ATTACK_TYPE_SPECS, PRESET_SPECS, type Experiment, type ExperimentInput } from './components/attackLabTypes'

const START = { latitude: 12.9716, longitude: 77.5946 }
const ZURICH = { latitude: 47.397971, longitude: 8.546164 }

// --- in-memory backend ------------------------------------------------------------------------------------------
const backend = vi.hoisted(() => {
  const state = {
    role: 'GENERAL' as 'GENERAL' | 'OPERATOR',
    scenarios: [] as { id: string; title: string; versions: unknown[] }[],
    deployments: [] as { id: string; scenario_id: string; scenario_version_id: string; at: number }[],
    experiments: [] as Record<string, unknown>[],
    aircraftAt: { latitude: 47.397971, longitude: 8.546164 },
    mapOverlay: null as unknown,
    relocations: 0,
    clock: 0,
    execution: null as null | Record<string, unknown>,
    executionEvents: [] as Record<string, unknown>[],
  }
  return state
})
type StoredVersion = ScenarioVersion
const scenario = (id: string) => backend.scenarios.find(item => item.id === id) as { id: string; title: string; versions: StoredVersion[] } | undefined
const conflict = (code: string, message: string) => Object.assign(new Error(message), { status: 409, detail: { code, message } })
const metres = (a: { latitude: number; longitude: number }, b: { latitude: number; longitude: number }) =>
  Math.hypot((a.latitude - b.latitude) * 111_320, (a.longitude - b.longitude) * 111_320 * Math.cos((a.latitude * Math.PI) / 180))
const detail = (item: { id: string; title: string; versions: StoredVersion[] }): ScenarioDetail =>
  ({ id: item.id, owner_user_id: 'u', title: item.title, created_at: 't', updated_at: 't', versions: [...item.versions] })
const planOf = (version: StoredVersion) => version.intent.mission_plan as MissionPlan
const approve = (scenarioId: string) => {
  const version = scenario(scenarioId)!.versions[0]
  version.approval = { ...(version.approval as object), status: 'APPROVED' } as never
}
const deploymentView = (record: { id: string; scenario_id: string; scenario_version_id: string }): MissionDeployment => {
  const owner = scenario(record.scenario_id)!
  const version = owner.versions.find(item => item.id === record.scenario_version_id)!
  const primary = planOf(version).aircraft.find(item => item.role === 'primary')!
  return {
    id: record.id, mission_id: owner.id, scenario_id: owner.id, scenario_version_id: version.id, scenario_version_hash: version.config_hash,
    scenario_title: owner.title, execution_status: 'DEPLOYED_TO_MISSION_CONTROL_NOT_UPLOADED_TO_PX4', requested_uav_count: 1, activated_uav_count: 1,
    assigned_uav_count: 1, approval: null, validation: { passed: true, validated_at: 't' }, intent: version.intent,
    scenario_version_number: version.version_number, latest_version_number: owner.versions[0].version_number, schema_version: 2, legacy: false,
    superseded: owner.versions[0].id !== version.id,
    mission_start: { ...primary.start_location!, altitude_m: 488, uav_id: primary.uav_id, role: 'primary', definition: 'Take-off point of the primary aircraft.' },
  }
}
const vehicle = () => ({
  id: 'px4-1', system_id: 1, component_id: 1, source: 'px4_mavlink', status: 'connected', armed: false, heartbeat: null,
  position: { ...backend.aircraftAt, altitude_m: 488, relative_altitude_m: 0, heading_deg: 0, valid: true, coordinate_frame: 'WGS84', source_message: 'GLOBAL_POSITION_INT', source_time_boot_ms: 1, received_at: 'x' },
  velocity: null, attitude: null, gps_raw: null, imu_raw: null, battery: null, mission: null, threat_assessment: { status: 'not_available', reason: '' },
  last_heartbeat_at: 'x', last_message_at: 'x', telemetry_age_seconds: 0.2, heartbeat_age_seconds: 0.2, flight_trail: [],
})
const saveVersion = (id: string | null, title: string, intent: ScenarioIntent): ScenarioDetail => {
  const owner = id ? scenario(id)! : { id: `scenario-${backend.scenarios.length + 1}`, title, versions: [] as StoredVersion[] }
  if (!id) backend.scenarios.push(owner)
  owner.title = title
  const number = owner.versions.length + 1
  owner.versions.unshift({ id: `${owner.id}-v${number}`, version_number: number, config: {} as never, intent: JSON.parse(JSON.stringify(intent)), config_hash: `hash-${owner.id}-${number}`, created_at: 't', sandbox_run_id: null, approval: null } as StoredVersion)
  return detail(owner)
}
const experimentView = (record: Record<string, unknown>): Experiment => ({
  ...(record as unknown as Experiment), executed: false, note: 'not executed',
  superseded: scenario(record.scenario_id as string)!.versions[0].id !== record.scenario_version_id,
})

vi.mock('./services/api', () => ({
  ApiError: class ApiError extends Error { status: number; constructor(message: string, status: number) { super(message); this.status = status } },
  api: {
    health: vi.fn(async () => ({ status: 'ready', active_simulations: 0, database: 'sqlite', components: { px4: { status: 'receiving' }, gazebo: { status: 'receiving' }, default_sitl_startup: { status: 'READY' } } })),
    session: vi.fn(),
    scenarioList: vi.fn(async () => backend.scenarios.map(item => ({ ...detail(item as never), latest_version: (item.versions as StoredVersion[])[0] }))),
    scenarioDetail: vi.fn(async (id: string) => detail(scenario(id)!)),
    myScenarioRequests: vi.fn(async () => []),
    px4Telemetry: vi.fn(async () => ({ source: 'px4_mavlink', generated_at: 't', transport: { status: 'receiving' }, capabilities: {}, vehicles: [vehicle()] })),
    px4TelemetrySocket: vi.fn(() => ({ close: vi.fn(), onopen: null, onmessage: null, onerror: null, onclose: null })),
    gazeboPoses: vi.fn(async () => ({ source: 'gazebo_pose_info', world: 'default', generated_at: 't', transport: { status: 'receiving' }, models: [] })),
    fleetStatus: vi.fn(async () => ({ capacity: 5, verified_count: 1, manager_error: null, bridges: { telemetry: { status: 'running' }, pose: { status: 'running' } },
      instances: [{ aircraft_id: 'px4-1', instance_number: 0, system_id: 1, gazebo_model: 'x500_0', verified: true }] })),
    activateFleet: vi.fn(async (count: number) => ({ required_uav_count: count, activated_uav_count: count, assigned_uav_count: 0, request_id: 'r', verified: true,
      vehicles: [{ aircraft_id: 'px4-1', instance_number: 0, system_id: 1, gazebo_model: 'x500_0', px4_status: 'connected', gazebo_status: 'connected', verified: true }] })),
    planningLimits: vi.fn(async () => ({ schema_version: 2, limits: { max_flight_s: 1200, max_flight_distance_m: 15000 } })),
    previewRoute: vi.fn(async (plan: MissionPlan, uavId: string) => {
      const aircraft = plan.aircraft.find(item => item.uav_id === uavId)!
      const points = [aircraft.start_location!, ...(aircraft.parameters.points ?? []), aircraft.start_location!]
      const route: GeneratedRoute = { generator: 'waypoint_route', generator_version: 1, length_m: 900, duration_s: 240, assumptions: 'flat', events: [],
        waypoints: points.map((point, seq) => ({ seq, ...point, altitude_m: seq === points.length - 1 ? 0 : 30, speed_mps: 5, hover_s: 0, action: seq === 0 ? 'takeoff' as const : seq === points.length - 1 ? 'land' as const : 'waypoint' as const })) }
      return { route, summary: { length_m: 900, duration_s: 240, waypoints: route.waypoints.length, limit_duration_s: 1200, limit_length_m: 15000 } }
    }),
    createScenario: vi.fn(async (title: string, _config: unknown, intent: ScenarioIntent) => saveVersion(null, title, intent)),
    updateScenario: vi.fn(async (id: string, title: string, _config: unknown, intent: ScenarioIntent) => saveVersion(id, title, intent)),
    submitScenario: vi.fn(async (id: string, versionId: string) => {
      const version = scenario(id)!.versions.find(item => item.id === versionId)!
      version.approval = { id: `approval-${versionId}`, status: 'PENDING', reason: null, submitted_at: 't', reviewed_at: null, reviewed_by: null }
      return { id: `approval-${versionId}`, status: 'PENDING' }
    }),
    deployScenario: vi.fn(async (id: string, versionId: string) => {
      const owner = scenario(id)!
      if (owner.versions[0].id !== versionId) throw conflict('version_superseded', 'Only the latest immutable scenario version can be deployed.')
      if (backend.role === 'OPERATOR' && owner.versions[0].approval?.status !== 'APPROVED') throw conflict('approval_required', 'Operator deployment requires approval of this exact immutable scenario version.')
      let record = backend.deployments.find(item => item.scenario_version_id === versionId)
      if (!record) { record = { id: `deployment-${backend.deployments.length + 1}`, scenario_id: id, scenario_version_id: versionId, at: 0 }; backend.deployments.push(record) }
      record.at = ++backend.clock
      return deploymentView(record)
    }),
    latestMissionDeployment: vi.fn(async () => {
      const latest = [...backend.deployments].sort((a, b) => b.at - a.at)[0]
      return latest ? deploymentView(latest) : null
    }),
    missionDeployments: vi.fn(async () => {
      const latest = [...backend.deployments].sort((a, b) => b.at - a.at)[0]
      return { deployment: latest ? deploymentView(latest) : null, execution: null, execution_deployment: null }
    }),
    simulationLocation: vi.fn(async (id: string, versionId: string) => {
      const primary = planOf(scenario(id)!.versions.find(item => item.id === versionId)!).aircraft.find(item => item.role === 'primary')!
      const distance = metres(backend.aircraftAt, primary.start_location!)
      const far = distance > 50_000
      return {
        scenario_id: id, scenario_version_id: versionId, max_distance_to_start_m: distance, max_distance_to_area_m: distance, position_known: true,
        mission_start: { ...primary.start_location!, altitude_m: 488, uav_id: primary.uav_id, role: 'primary', definition: 'Take-off point of the primary aircraft.' },
        aircraft: [], relocation_recommended: distance > 1000, far_from_mission_area: far,
        message: far ? `The aircraft is about ${(distance / 1000).toFixed(0)} km from the mission area. The simulation is running at a different location than this mission. Use Restart at mission start.`
          : `The aircraft is ${distance.toFixed(0)} m from the mission start.`,
        thresholds: { relocation_notice_m: 1000, far_from_area_m: 50_000, home_tolerance_m: 50 },
      }
    }),
    relocateSimulation: vi.fn(async (id: string, versionId: string) => {
      const primary = planOf(scenario(id)!.versions.find(item => item.id === versionId)!).aircraft.find(item => item.role === 'primary')!
      backend.aircraftAt = { ...primary.start_location! }
      backend.relocations += 1
      return { relocated: true, verified: true, mission_start: { ...primary.start_location!, altitude_m: 488, uav_id: 'px4-1', role: 'primary', definition: '' }, max_distance_to_start_m: 0.4, commands_sent_to_aircraft: false,
        note: 'The PX4 and Gazebo simulation was restarted at the mission start. No arm, take-off or mission command was sent.',
        checks: { px4: { status: 'ok', detail: '1 of 1 PX4 SITL process(es) present.' }, verification: { status: 'ok', detail: '1 of 1 UAV(s) have PX4 telemetry and a Gazebo pose.' }, simulation_home: { status: 'ok', detail: 'Every aircraft reports a position within 50 m of the mission start.' } } }
    }),
    // --- Restart at mission start (the page's shared restart dialog): the fake backend moves the aircraft to the deployment's mission start ---
    startRestart: vi.fn(async (deploymentId: string, purpose: 'restart' | 'switch') => {
      const record = backend.deployments.find(item => item.id === deploymentId)!
      const owner = scenario(record.scenario_id)!
      const primary = planOf(owner.versions.find(item => item.id === record.scenario_version_id)!).aircraft.find(item => item.role === 'primary')!
      backend.aircraftAt = { ...primary.start_location! }
      backend.relocations += 1
      return { id: 'job-1', status: 'succeeded', purpose, deployment_id: deploymentId, reason: 'simulation restarted', failure: null, can_retry: false, elapsed_s: 19.5,
        steps: ['managed', 'executor', 'stop_sim', 'start_sim', 'bridges', 'telemetry', 'gazebo_pose', 'position', 'orphans'].map(key => ({ key, label: key, status: 'ok', detail: '' })),
        started_at: 't', finished_at: 't', started_by_name: 'Test', simulation_only: true,
        result: { mission_start: primary.start_location, max_distance_to_start_m: 0.4, armed: false, ended_executions: [], killed_processes: [] } }
    }),
    restartJob: vi.fn(),
    latestRestart: vi.fn(async () => null),
    deploymentRoleRule: vi.fn(async () => ({ allowed: true, code: 'general', message: 'General accounts deploy and start directly.' })),
    // --- Start in simulation: a fake executor with the backend's rules (role, approval, one run at a time) ---
    executionPreflight: vi.fn(async (deploymentId: string) => {
      const record = backend.deployments.find(item => item.id === deploymentId)!
      const owner = scenario(record.scenario_id)!
      const version = owner.versions.find(item => item.id === record.scenario_version_id)!
      const superseded = owner.versions[0].id !== version.id
      const approved = backend.role === 'GENERAL' || version.approval?.status === 'APPROVED'
      const primary = planOf(version).aircraft.find(item => item.role === 'primary')!
      const distance = metres(backend.aircraftAt, primary.start_location!)
      const failing: [string, string][] = []
      if (superseded) failing.push(['plan_valid', `Version ${version.version_number} was superseded by version ${owner.versions[0].version_number}. Deploy the latest version before starting.`])
      if (!approved) failing.push(['role', `Operators need a General's approval of this exact version. Submit version ${version.version_number} for review in Scenario Lab.`])
      if (distance > 50) failing.push(['at_mission_start', `The aircraft is ${(distance / 1000).toFixed(1)} km from the mission start (limit 50 m). Use Restart at mission start.`])
      if (backend.execution?.active) failing.push(['no_execution_running', `An execution is already ${backend.execution.state} on px4-1.`])
      const keys = ['plan_valid', 'role', 'aircraft_count', 'fleet_verified', 'managed_instance', 'telemetry_fresh', 'gps_fix', 'disarmed_landed', 'at_mission_start', 'battery', 'no_execution_running', 'endurance']
      return { deployment_id: deploymentId, aircraft_id: 'px4-1', can_start: failing.length === 0, blocking: failing.map(item => item[1]),
        confirmation: 'This runs your route in the PX4/Gazebo simulation. The aircraft will arm, take off and fly the plan.',
        role_rule: approved ? { allowed: true, code: backend.role === 'GENERAL' ? 'general' : 'approved', message: backend.role === 'GENERAL' ? 'General accounts deploy and start directly.' : `A General approved version ${version.version_number}.` }
          : { allowed: false, code: 'approval_required', message: failing.find(item => item[0] === 'role')![1] },
        checks: keys.map(key => ({ key, label: key.replace(/_/g, ' '), ok: !failing.some(item => item[0] === key), detail: failing.find(item => item[0] === key)?.[1] ?? 'ok', ...(key === 'at_mission_start' && distance > 50 ? { offer_relocation: true } : {}) })) }
    }),
    latestExecution: vi.fn(async () => backend.execution),
    executionEvents: vi.fn(async (_id: string, after = 0) => ({ execution_id: _id, state: backend.execution?.state, events: backend.executionEvents.filter(item => (item.id as number) > after) })),
    startExecution: vi.fn(async (deploymentId: string) => {
      const record = backend.deployments.find(item => item.id === deploymentId)!
      setExecution({ id: 'e'.repeat(32), deployment_id: deploymentId, scenario_id: record.scenario_id, scenario_version_id: record.scenario_version_id, state: 'connecting', stage: 'starting', uploaded: false, armed: false, percent: 0, route_reached: 0, route_current: 1, elapsed_s: null })
      logExecution('user', 'start_requested', `Test ${backend.role} (${backend.role}) started the mission in simulation.`)
      return backend.execution
    }),
    pauseExecution: vi.fn(async () => { setExecution({ state: 'paused', px4_mode: 'Hold' }); logExecution('executor', 'paused', 'Paused: the aircraft holds its position.'); return backend.execution }),
    resumeExecution: vi.fn(async () => { setExecution({ state: 'executing', px4_mode: 'Mission' }); logExecution('executor', 'resumed', 'Resumed: the aircraft continues the mission.'); return backend.execution }),
    abortExecution: vi.fn(async () => { setExecution({ state: 'returning', px4_mode: 'Return', reason: 'aborted by the operator' }); logExecution('executor', 'return_to_launch', 'Return to launch commanded.'); return backend.execution }),
    attackLabLimits: vi.fn(async () => ({
      name: 'Attack Lab', executes: false, max_attacks: 10, duration_s: { min: 1, max: 3600 }, start_s: { min: 0, max: 86400 }, seed: { min: 0, max: 2147483647 },
      handover: { min_battery_pct: 40, roles: ['standby', 'decoy'] }, presets: PRESET_SPECS,
      types: ATTACK_TYPE_SPECS.map(item => ({ type: item.value, label: item.label, summary: item.summary, uses_direction: item.usesDirection, magnitude: { label: 'Magnitude', unit: item.unit, min: 0.1, max: 500, default: 1 } })),
    })),
    attackLabMissions: vi.fn(async () => {
      const current = [...backend.deployments].sort((a, b) => b.at - a.at)[0]
      return { legacy_only_count: 0, missions: backend.scenarios.map(item => ({
        scenario_id: item.id, title: item.title,
        versions: (item.versions as StoredVersion[]).map((version, index) => {
          const aircraft = planOf(version).aircraft.map(entry => ({ uav_id: entry.uav_id, role: entry.role, reassignable: entry.reassignable, duration_s: entry.route?.duration_s ?? null }))
          return { id: version.id, version_number: version.version_number, config_hash: version.config_hash, created_at: 't', latest: index === 0, superseded: index !== 0,
            deployed: backend.deployments.some(entry => entry.scenario_version_id === version.id), deployed_at: null, in_mission_control: current?.scenario_version_id === version.id,
            aircraft, estimated_duration_s: 240, area_summary: 'circle, radius 400 m', objectives: [], presets: { presets: [], hidden: [] } }
        }),
      })) }
    }),
    attackExperiments: vi.fn(async () => backend.experiments.map(experimentView)),
    attackLabResults: vi.fn(async () => []),
    attackLabLive: vi.fn(async () => ({ armed: 0, run_active: false, experiments_in_run: 0, note: '' })),
    implementExperiment: vi.fn(),
    exportRunRecords: vi.fn(),
    applyAttackPreset: vi.fn(async (presetId: string) => ({ preset_id: presetId, seed: 1, config: {
      attacks: presetId === 'baseline' ? [] : [{ target_aircraft: 'px4-1', type: 'drift', start_s: 72, duration_s: 96, magnitude: 0.5, direction_deg: null }],
      response_policy: 'operator_decides', allow_overlap: false, handover: { min_battery_pct: 40, candidate_roles: ['standby', 'decoy'] },
      measurements: { detection_latency: true, false_alarms: true, position_error: true, mission_completion: true, handover_time: true } } })),
    validateExperiment: vi.fn(async (input: ExperimentInput) => {
      const late = input.config.attacks.filter(item => item.start_s + item.duration_s > 240)
      return { valid: late.length === 0, issues: late.map(() => ({ code: 'attack_outside_route', message: 'The attack ends after the route.' })) }
    }),
    createExperiment: vi.fn(async (input: ExperimentInput) => {
      const record = { ...input, id: `experiment-${backend.experiments.length + 1}`, owner_user_id: 'u', config_hash: 'h', created_at: 't', updated_at: 't' }
      backend.experiments.push(record)
      return experimentView(record)
    }),
    updateExperiment: vi.fn(), deleteExperiment: vi.fn(), scenarioAssistant: vi.fn(async () => { throw new Error('AI disabled in tests') }),
  },
}))

vi.mock('gsap', () => ({ default: { registerPlugin: vi.fn() } }))
vi.mock('gsap/ScrollTrigger', () => ({ ScrollTrigger: {} }))
vi.mock('./components/AccessPortal', () => ({ AccessPortal: () => null }))
vi.mock('./components/ThreatMonitor', () => ({ ThreatMonitor: () => null }))
vi.mock('./components/GazeboPoseMirror', () => ({ GazeboPoseMirror: () => null }))
vi.mock('./components/CesiumLiveMap', () => ({
  DEFAULT_LAYERS: { routes: true, geofence: true, trails: true, labels: true },
  CesiumLiveMap: (props: { overlay?: unknown }) => { backend.mapOverlay = props.overlay; return <div data-testid="mission-map"/> },
}))
vi.mock('./components/PlanMapEditor', () => ({
  PlanMapEditor: ({ scope, aircraft, onAreaChange, onAircraftChange }: {
    scope: 'area' | 'aircraft' | 'both'; aircraft?: AircraftPlan
    onAreaChange: (area: OperatingArea | null) => void; onAircraftChange: (patch: Partial<AircraftPlan>) => void
  }) => <div>
    {scope !== 'aircraft' && <button type="button" onClick={() => onAreaChange({ kind: 'circle', centre: { latitude: 12.975, longitude: 77.596 }, radius_m: 400, vertices: [] })}>SET TEST AREA</button>}
    {aircraft && scope !== 'area' && <div>
      <button type="button" onClick={() => onAircraftChange({ start_location: { latitude: 12.9716, longitude: 77.5946 } })}>SET TEST START</button>
      <button type="button" onClick={() => onAircraftChange({ parameters: { ...aircraft?.parameters, points: [{ latitude: 12.973, longitude: 77.595 }] } })}>SET TEST POINTS</button>
    </div>}
  </div>,
}))

vi.setConfig({ testTimeout: 60000 })

import { api } from './services/api'
const apiMock = vi.mocked(api)
/** The fake executor's state, with the controls the backend derives from it. */
function setExecution(patch: Record<string, unknown>) {
  const next = { aircraft_id: 'px4-1', reason: '', started_by_name: `Test ${backend.role}`, started_by_role: backend.role, created_at: 't', updated_at: 't', finished_at: null,
    px4_mode: 'Mission', route_total: 3, estimated_s: 240, px4_heartbeat_age_s: 0.3, px4_messages: [], simulation_only: true, ...(backend.execution ?? {}), ...patch } as Record<string, unknown>
  const state = next.state as string
  const active = !['completed', 'aborted', 'failed'].includes(state)
  const done = `The run is already ${state}.`
  next.active = active
  next.stage = !active ? state : ['executing', 'paused', 'returning'].includes(state) ? 'executing' : next.uploaded ? 'uploaded' : 'starting'
  next.controls = {
    pause: state === 'executing' ? { allowed: true, reason: null } : { allowed: false, reason: active ? `Pause is possible while executing; the run is ${state}.` : done },
    resume: state === 'paused' ? { allowed: true, reason: null } : { allowed: false, reason: active ? `Resume is possible while paused; the run is ${state}.` : done },
    abort: active ? { allowed: true, reason: null } : { allowed: false, reason: done },
  }
  backend.execution = next
}
function logExecution(source: string, type: string, message: string) {
  backend.executionEvents.push({ id: backend.executionEvents.length + 1, execution_id: 'e'.repeat(32), source, type, message, data: {}, actor_name: null, occurred_at: new Date(Date.UTC(2026, 10, 14, 3, 0, backend.executionEvents.length)).toISOString() })
}
/** What PX4 and the executor would report next (the real sequence is covered by the backend tests and the live trial). */
function simulate(step: 'fly' | 'progress' | 'land') {
  if (step === 'fly') {
    setExecution({ state: 'executing', uploaded: true, armed: true, elapsed_s: 5 })
    logExecution('executor', 'uploaded', 'Mission uploaded to PX4: 4 items acknowledged.')
    logExecution('executor', 'armed', 'PX4 reports armed.')
  } else if (step === 'progress') {
    setExecution({ route_reached: 2, route_current: 3, percent: 66.7, elapsed_s: 150 })
    logExecution('executor', 'takeoff_complete', 'Take-off complete (waypoint 1 of 3).')
    logExecution('executor', 'waypoint_reached', 'Reached waypoint 2 of 3.')
  } else {
    setExecution({ state: 'completed', armed: false, route_reached: 3, route_current: 3, percent: 100, elapsed_s: 262 })
    logExecution('executor', 'completed', 'Mission completed: the aircraft landed and disarmed.')
  }
}

const session = (role: 'GENERAL' | 'OPERATOR'): UserSession => ({
  expires_at: '2027-01-01T00:00:00Z', csrf_token: 'csrf',
  permissions: role === 'GENERAL' ? ['mission.read_all', 'mission.read_own', 'scenario.create', 'scenario.review'] : ['mission.read_own', 'scenario.create'],
  user: { id: `${role.toLowerCase()}-user`, username: role.toLowerCase(), full_name: `Test ${role}`, email: null, role, account_status: 'APPROVED' },
})

const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }))
const openTab = (name: 'Scenario Lab' | 'Attack Lab' | 'Mission Control') =>
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name }))
const nextAction = () => screen.getByRole('group', { name: 'Next action' })
const nextButton = (name: RegExp) => within(nextAction()).getByRole('button', { name }) as HTMLButtonElement
const saveStatus = () => document.querySelector('.scenario-save-status')?.textContent ?? null
const reasons = () => Array.from(nextAction().querySelectorAll('[aria-label="Why the next action is unavailable"] li')).map(item => item.textContent)
const overlay = () => backend.mapOverlay as { routes: { uavId: string; waypoints: unknown[] }[] } | null

async function planMission(title: string) {
  fireEvent.change(await screen.findByLabelText('SCENARIO TITLE'), { target: { value: title } })
  fireEvent.click(screen.getByRole('checkbox', { name: /Custom Mission/ }))
  click('SET TEST AREA')
  fireEvent.change(screen.getByLabelText(/MAX DISTANCE FROM START/), { target: { value: '1500' } })
  fireEvent.change(screen.getByLabelText(/MAX ALTITUDE/), { target: { value: '100' } })
  fireEvent.change(screen.getByLabelText(/HOW MANY UAVS ARE REQUIRED/), { target: { value: '1' } })
  click(/ACTIVATE AND VERIFY PX4/)
  expect(await screen.findByText(/Verified 1 PX4\/Gazebo instances/)).toBeTruthy()
  fireEvent.change(screen.getByLabelText(/TASK TYPE/), { target: { value: 'waypoint_route' } })
  click('SET TEST START')
  click('SET TEST POINTS')
  fireEvent.change(screen.getByLabelText('ALTITUDE (m) *'), { target: { value: '30' } })
  fireEvent.change(screen.getByLabelText('SPEED (m/s) *'), { target: { value: '5' } })
  click(/GENERATE \/ PREVIEW ROUTE/)
  await screen.findByLabelText('Route preview')          // the backend generated the route
  click(/CONFIRM ALL AIRCRAFT MISSIONS/)
}

describe('end-to-end flow through the real App', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    window.scrollTo = vi.fn() as never
    sessionStorage.clear(); localStorage.clear(); window.localStorage.clear()
    Object.assign(backend, { role: 'GENERAL', scenarios: [], deployments: [], experiments: [], aircraftAt: { ...ZURICH }, mapOverlay: null, relocations: 0, clock: 0, execution: null, executionEvents: [] })
    window.history.replaceState({}, '', '/scenario-lab')
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks() })

  it('GENERAL: plan -> save -> deploy -> Mission Control -> Attack Lab -> new version -> superseded notice -> redeploy', async () => {
    apiMock.session.mockResolvedValue(session('GENERAL'))
    const confirmSpy = vi.spyOn(window, 'confirm')
    render(<App />)

    // Scenario Lab: three sections only, and the review carries exactly one line about attacks.
    await planMission('Flow mission')
    expect(Array.from(document.querySelectorAll('.scenario-lab-section > header > .eyebrow')).map(item => item.textContent)).toEqual(['SECTION A', 'SECTION B', 'SECTION C'])
    expect(screen.getByRole('heading', { name: 'Review and validation' })).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/SECTION D|Section D/)
    expect(screen.getByText(/GPS attack experiments are designed in the/).textContent).toBe('GPS attack experiments are designed in the Attack Lab after saving.')
    // Before the first save the next action is announced, not hidden.
    expect(screen.getByText(/After you save, the next action appears here: Deploy to Mission Control\./)).toBeTruthy()

    click('SAVE SCENARIO DRAFT')
    await waitFor(() => expect(nextButton(/DEPLOY TO MISSION CONTROL/).disabled).toBe(false))
    expect(saveStatus()).toBe('Saved v1')
    expect(document.body.textContent).not.toMatch(/NEXT ACTION|Nothing blocks this action/)
    expect(within(nextAction()).queryByText('READY')).toBeNull()
    expect(within(nextAction()).queryByText('UNAVAILABLE BECAUSE')).toBeNull()  // reasons appear only while the button is disabled
    expect(within(screen.getByLabelText('Fleet verification')).getByText('VERIFIED')).toBeTruthy()
    // The aircraft is in Zurich and the mission is in Bengaluru: an amber notice, not a blocker.
    const location = await within(nextAction()).findByRole('alert')
    expect(location.textContent).toMatch(/DIFFERENT LOCATION THAN THIS MISSION/)
    expect(nextButton(/DEPLOY TO MISSION CONTROL/).disabled).toBe(false)

    // Deploy: the latest version is refetched first, then Mission Control opens by itself with a confirmation.
    click(/DEPLOY TO MISSION CONTROL/)
    await waitFor(() => expect(window.location.pathname).toBe('/mission-control'))
    expect(apiMock.scenarioDetail).toHaveBeenCalledWith('scenario-1')
    expect(apiMock.deployScenario).toHaveBeenCalledWith('scenario-1', 'scenario-1-v1')
    expect(await screen.findByText(/Deployed Flow mission version 1 to Mission Control\. Nothing was armed, flown or uploaded to PX4\./)).toBeTruthy()
    const header = await screen.findByLabelText('Mission header')
    await waitFor(() => expect(within(header).getByRole('heading', { name: 'Flow mission' })).toBeTruthy())
    expect(within(header).getByText(/Version 1 · schema 2 · 1 generated route on the map/)).toBeTruthy()
    await waitFor(() => expect(overlay()?.routes).toHaveLength(1))
    expect(overlay()!.routes[0]).toMatchObject({ uavId: 'px4-1' })
    expect(overlay()!.routes[0].waypoints).toHaveLength(3)

    // Start in simulation is visible but disabled while the aircraft is not at the mission start, with that exact reason.
    const mission = await screen.findByRole('region', { name: 'Mission status' })
    const control = (name: RegExp) => within(mission).getByRole('button', { name }) as HTMLButtonElement
    const controlReasons = () => Object.fromEntries(Array.from(mission.querySelectorAll('[aria-label="Why controls are unavailable"] li')).map(item => [item.querySelector('b')!.textContent, item.textContent!.slice(item.querySelector('b')!.textContent!.length)]))
    await waitFor(() => expect(controlReasons().Start).toMatch(/km from the mission start \(limit 50 m\)\. Use Restart at mission start\./))
    expect(control(/START IN SIMULATION/).disabled).toBe(true)
    expect(within(mission).getByLabelText('Role rule').textContent).toBe('Role rule: General accounts deploy and start directly.')

    // Mission Control: the simulation is far away. Moving it needs a press and an in-page confirmation, and commands no flight.
    const missionLocation = await screen.findByRole('region', { name: 'Mission location' })
    expect((await within(missionLocation).findByRole('alert')).textContent).toMatch(/about \d+ km from the mission area/)
    fireEvent.click(within(missionLocation).getByRole('button', { name: /RESTART AT MISSION START/ }))
    expect(apiMock.startRestart).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog', { name: 'Confirm restart at mission start' })
    expect(dialog.textContent).toMatch(/This restarts PX4 and Gazebo at the mission start \(about 20 s\)\. A flight in progress ends\./)
    fireEvent.click(within(dialog).getByRole('button', { name: 'RESTART NOW' }))
    await waitFor(() => expect(apiMock.startRestart).toHaveBeenCalledWith('deployment-1', 'restart'))
    expect((await screen.findByRole('dialog', { name: 'Restart progress' })).textContent).toMatch(/0\.4 m from the mission start · disarmed · ready/)
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Restart progress' })).getByRole('button', { name: 'CLOSE' }))
    await waitFor(() => expect(within(missionLocation).queryByRole('alert')).toBeNull())

    // Start in simulation: enabled once the aircraft is at the mission start; it needs the in-page confirmation with the checklist.
    fireEvent.click(within(mission).getByRole('button', { name: 'VIEW PRE-START CHECKS' }))   // forces a fresh read after the move
    const checks = await within(mission).findByRole('dialog', { name: 'Confirm start in simulation' })
    await waitFor(() => expect((within(checks).getByRole('button', { name: 'START NOW' }) as HTMLButtonElement).disabled).toBe(false))
    expect(within(checks).getByText('This runs your route in the PX4/Gazebo simulation. The aircraft will arm, take off and fly the plan.')).toBeTruthy()
    expect(within(within(checks).getByRole('list', { name: 'Pre-start checklist' })).getAllByRole('listitem')).toHaveLength(12)
    expect(apiMock.startExecution).not.toHaveBeenCalled()
    fireEvent.click(within(checks).getByRole('button', { name: 'START NOW' }))
    await waitFor(() => expect(apiMock.startExecution).toHaveBeenCalledWith('deployment-1'))
    expect((await within(mission).findByRole('status', { name: 'Execution state' })).textContent).toBe('Connecting to PX4')
    expect(control(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(false)
    simulate('fly')
    await waitFor(() => expect(within(mission).getByRole('status', { name: 'Execution state' }).textContent).toBe('Executing'), { timeout: 6000 })
    expect(within(mission).getByText(/Uploaded to PX4/).textContent).toMatch(/mission accepted by PX4/)
    simulate('progress')
    await waitFor(() => expect(within(mission).getByText(/waypoint 3 of 3 · 67% · 2:30 of about 4:00/)).toBeTruthy(), { timeout: 6000 })
    expect(within(mission).getByRole('progressbar', { name: 'Mission progress' }).getAttribute('aria-valuenow')).toBe('67')
    fireEvent.click(control(/^PAUSE$/))
    await waitFor(() => expect(within(mission).getByRole('status', { name: 'Execution state' }).textContent).toBe('Paused (Hold)'), { timeout: 6000 })
    expect(control(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(false)   // abort stays available while paused
    fireEvent.click(control(/^RESUME$/))
    await waitFor(() => expect(within(mission).getByRole('status', { name: 'Execution state' }).textContent).toBe('Executing'), { timeout: 6000 })
    simulate('land')
    await waitFor(() => expect(within(mission).getByRole('status', { name: 'Execution state' }).textContent).toBe('Completed'), { timeout: 6000 })
    expect(control(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(true)
    const flightLog = screen.getByRole('region', { name: 'Flight log' })
    for (const text of ['Test GENERAL (GENERAL) started the mission in simulation.', 'Mission uploaded to PX4: 4 items acknowledged.', 'PX4 reports armed.', 'Take-off complete (waypoint 1 of 3).',
      'Reached waypoint 2 of 3.', 'Paused: the aircraft holds its position.', 'Resumed: the aircraft continues the mission.', 'Mission completed: the aircraft landed and disarmed.']) {
      await waitFor(() => expect(within(flightLog).getAllByText(text)).toHaveLength(1), { timeout: 6000 })
    }
    expect(confirmSpy).not.toHaveBeenCalled()  // no native dialog anywhere

    // Attack Lab: the saved version is listed, preselected, and marked latest and deployed.
    openTab('Attack Lab')
    const versionSelect = await screen.findByRole('combobox', { name: 'Version' }) as HTMLSelectElement
    await waitFor(() => expect(versionSelect.value).toBe('scenario-1-v1'))
    expect((screen.getByRole('combobox', { name: 'Mission' }) as HTMLSelectElement).selectedOptions[0].textContent).toBe('Flow mission · 1 version')
    expect(versionSelect.selectedOptions[0].textContent).toBe('v1 · latest · deployed · in Mission Control')
    const summary = screen.getByLabelText('Mission summary')
    expect(summary.textContent).toMatch(/px4-1 primary/)
    expect(summary.textContent).toMatch(/4\.0 min \(240 s\)/)
    expect(summary.textContent).toMatch(/circle, radius 400 m/)

    // Create and validate an experiment from a preset.
    fireEvent.change(screen.getByPlaceholderText('Name this experiment'), { target: { value: 'Slow drift run' } })
    fireEvent.click(screen.getByRole('button', { name: /^Slow drift/ }))
    await waitFor(() => expect(apiMock.applyAttackPreset).toHaveBeenCalledWith('slow_drift', 'scenario-1', 'scenario-1-v1', 1))
    expect(await screen.findByRole('group', { name: 'Attack 1' })).toBeTruthy()
    const readiness = screen.getByRole('region', { name: 'Readiness checklist' })
    expect(Array.from(readiness.querySelectorAll('li')).map(item => [item.querySelector('b')?.textContent, item.className])).toEqual([
      ['Mission pinned', 'ok'], ['Attack valid', 'ok'], ['Within duration', 'ok'], ['Battery threshold', 'ok']])
    click('VALIDATE')
    expect(await screen.findByText('Valid: this experiment passes every backend check.')).toBeTruthy()
    click(/SAVE DRAFT/)
    const saved = await screen.findByRole('listitem', { name: 'Experiment Slow drift run' })
    expect(saved.textContent).toMatch(/Slow drift/)
    expect(saved.textContent).toMatch(/Flow mission/)
    expect(saved.textContent).toMatch(/Pinned versionv1/)
    expect(saved.textContent).not.toMatch(/Greyed/)
    expect(apiMock.createExperiment.mock.calls[0][0]).toMatchObject({ scenario_version_id: 'scenario-1-v1', preset_id: 'slow_drift', config: { response_policy: 'operator_decides' } })

    // Back to Scenario Lab through the link: the same scenario is open. Edit, and the next action explains why it is unavailable.
    click(/Open in Scenario Lab/)
    await waitFor(() => expect(window.location.pathname).toBe('/scenario-lab'))
    expect((screen.getByLabelText('SCENARIO TITLE') as HTMLInputElement).value).toBe('Flow mission')
    fireEvent.change(screen.getByLabelText(/NOTES \(optional/), { target: { value: 'second version' } })
    expect(nextButton(/DEPLOY TO MISSION CONTROL/).disabled).toBe(true)
    expect(reasons()).toEqual(['Unsaved changes'])
    expect(saveStatus()).toBe('Unsaved changes')
    click(/SAVE NEW IMMUTABLE VERSION/)
    await waitFor(() => expect(saveStatus()).toBe('Saved v2'))
    await waitFor(() => expect(nextButton(/DEPLOY TO MISSION CONTROL/).disabled).toBe(false))
    expect(reasons()).toEqual([])

    // The experiment stays pinned to v1 and now carries the superseded notice; v2 is latest and not deployed yet.
    openTab('Attack Lab')
    const card = await screen.findByRole('listitem', { name: 'Experiment Slow drift run' })
    expect(card.textContent).toMatch(/Greyed: a newer version of this mission exists, so this experiment is pinned to an older one\./)
    expect(card.textContent).toMatch(/RE-PIN TO LATEST/)
    const pinned = within(card).getByRole('button', { name: 'Open experiment Slow drift run' })
    const versions = await screen.findByRole('combobox', { name: 'Version' }) as HTMLSelectElement
    await waitFor(() => expect(Array.from(versions.options).map(option => option.textContent)).toEqual([
      'Choose an exact version', 'v2 · latest · not deployed', 'v1 · superseded · deployed · in Mission Control']))
    expect(versions.value).toBe('scenario-1-v2')
    expect(screen.getByText('This version is not deployed. You can define the experiment now and run it after the version is deployed.')).toBeTruthy()
    fireEvent.click(pinned)
    expect(await screen.findByText(/A newer version \(v2\) of this mission exists\. An experiment saved here stays pinned to v1, a superseded version\./)).toBeTruthy()

    // Redeploy: Mission Control shows the new version.
    openTab('Mission Control')
    expect(await screen.findByText(/A newer version \(v2\) of this scenario has been saved but not deployed/)).toBeTruthy()
    openTab('Scenario Lab')
    await waitFor(() => expect(nextButton(/DEPLOY TO MISSION CONTROL/).disabled).toBe(false))
    click(/DEPLOY TO MISSION CONTROL/)
    await waitFor(() => expect(window.location.pathname).toBe('/mission-control'))
    expect(apiMock.deployScenario).toHaveBeenLastCalledWith('scenario-1', 'scenario-1-v2')
    expect(await screen.findByText(/Deployed Flow mission version 2 to Mission Control/)).toBeTruthy()
    const redeployed = await screen.findByLabelText('Mission header')
    await waitFor(() => expect(within(redeployed).getByText(/Version 2 · schema 2 · 1 generated route on the map/)).toBeTruthy())
    expect(screen.queryByText(/has been saved but not deployed/)).toBeNull()
    expect(within(redeployed).queryByText(/legacy/i)).toBeNull()
    expect(backend.relocations).toBe(1)  // the simulation was moved once, by the explicit action only
  })

  it('OPERATOR: submit for review -> General approval -> deploy -> Mission Control; Attack Lab is read-only', async () => {
    backend.role = 'OPERATOR'
    backend.aircraftAt = { ...START }
    apiMock.session.mockResolvedValue(session('OPERATOR'))
    render(<App />)
    await planMission('Operator mission')
    expect(screen.getByText(/the next action appears here: Submit for review, then Deploy to Mission Control once a General approves/)).toBeTruthy()
    click('SAVE SCENARIO DRAFT')

    // The operator's next action is Submit for review; deploying is not offered until that exact version is approved.
    await waitFor(() => expect(nextButton(/SUBMIT FOR REVIEW/).disabled).toBe(false))
    expect(within(nextAction()).queryByRole('button', { name: /DEPLOY TO MISSION CONTROL/ })).toBeNull()
    expect(await within(nextAction()).findByText(/The aircraft is 0 m from the mission start\./)).toBeTruthy()
    click(/SUBMIT FOR REVIEW/)
    await waitFor(() => expect(apiMock.submitScenario).toHaveBeenCalledWith('scenario-1', 'scenario-1-v1'))
    await waitFor(() => expect(reasons()).toEqual(['Already submitted: waiting for General review']))
    expect(nextButton(/SUBMIT FOR REVIEW/).disabled).toBe(true)
    expect(saveStatus()).toBe('Saved v1 · pending')

    approve('scenario-1')  // the General approves in the General console
    click('Refresh scenarios')
    await waitFor(() => expect(nextButton(/DEPLOY TO MISSION CONTROL/).disabled).toBe(false))
    expect(saveStatus()).toBe('Saved v1 · approved')
    click(/DEPLOY TO MISSION CONTROL/)
    await waitFor(() => expect(window.location.pathname).toBe('/mission-control'))
    const header = await screen.findByLabelText('Mission header')
    await waitFor(() => expect(within(header).getByRole('heading', { name: 'Operator mission' })).toBeTruthy())
    expect(within(header).getByText(/Version 1 · schema 2/)).toBeTruthy()
    expect(await within(screen.getByRole('region', { name: 'Mission location' })).findByText(/The aircraft is 0 m from the mission start\./)).toBeTruthy()
    expect(within(screen.getByRole('region', { name: 'Mission location' })).queryByRole('button', { name: /RESTART AT MISSION START/ })).toBeNull()      // no location warning, so no button in the notice
    // The approved version can be started by the operator; the role rule says why.
    const mission = await screen.findByRole('region', { name: 'Mission status' })
    await waitFor(() => expect((within(mission).getByRole('button', { name: /START IN SIMULATION/ }) as HTMLButtonElement).disabled).toBe(false))
    expect(within(mission).getByLabelText('Role rule').textContent).toBe('Role rule: A General approved version 1.')
    fireEvent.click(within(mission).getByRole('button', { name: /START IN SIMULATION/ }))
    fireEvent.click(within(await within(mission).findByRole('dialog')).getByRole('button', { name: 'START NOW' }))
    await waitFor(() => expect(apiMock.startExecution).toHaveBeenCalledWith('deployment-1'))
    fireEvent.click(await within(mission).findByRole('button', { name: /ABORT \(RETURN TO LAUNCH\)/ }))
    await waitFor(() => expect(within(mission).getByRole('status', { name: 'Execution state' }).textContent).toBe('Returning to launch'), { timeout: 6000 })

    openTab('Attack Lab')
    expect(await screen.findByText('Operator accounts can view experiments but not create or edit them.')).toBeTruthy()
    const version = await screen.findByRole('combobox', { name: 'Version' }) as HTMLSelectElement
    await waitFor(() => expect(version.selectedOptions[0].textContent).toBe('v1 · latest · deployed · in Mission Control'))
    expect(screen.queryByRole('button', { name: /SAVE DRAFT/ })).toBeNull()
  })

  it('a version saved elsewhere is detected before deploying: nothing is deployed and the reason is shown', async () => {
    apiMock.session.mockResolvedValue(session('GENERAL'))
    render(<App />)
    await planMission('Raced mission')
    click('SAVE SCENARIO DRAFT')
    await waitFor(() => expect(nextButton(/DEPLOY TO MISSION CONTROL/).disabled).toBe(false))
    // Another session saves a different version 2 of the same scenario.
    const first = scenario('scenario-1')!.versions[0]
    saveVersion('scenario-1', 'Raced mission', { ...first.intent, mission_plan: { ...planOf(first), notes: 'changed elsewhere' } })
    click(/DEPLOY TO MISSION CONTROL/)
    expect((await screen.findByText(/Version superseded: version 2 is now the latest and differs from what is shown here\. Nothing was deployed\./)).closest('[role="alert"]')).toBeTruthy()
    expect(apiMock.deployScenario).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe('/scenario-lab')
    await waitFor(() => expect(reasons()).toEqual(['Unsaved changes', 'Version superseded']))
    expect(nextButton(/DEPLOY TO MISSION CONTROL/).disabled).toBe(true)
  })
})
