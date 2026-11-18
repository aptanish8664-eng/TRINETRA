/** Quick mission, the assistant front door, draft safety, approval refresh and the activate-and-verify rule (mocked API and map). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ScenarioWorkspace } from './ScenarioWorkspace'
import { api } from '../services/api'
import type { ScenarioConfig, ScenarioDetail, ScenarioIntent } from '../services/auth'
import type { Px4TelemetryVehicle } from '../types'
import type { MissionPlan } from './missionPlan'
import { EMPTY_PLAN } from './missionPlan'
import type { ParseResult, ParseState, QuickRequest, QuickResult } from './quickMission'

vi.mock('../services/api', () => ({
  api: {
    scenarioList: vi.fn(), myScenarioRequests: vi.fn(), px4Telemetry: vi.fn(), createScenario: vi.fn(), updateScenario: vi.fn(), submitScenario: vi.fn(),
    activateFleet: vi.fn(), scenarioAssistant: vi.fn(), previewRoute: vi.fn(), fleetStatus: vi.fn(), deployScenario: vi.fn(), scenarioDetail: vi.fn(), health: vi.fn(),
    planningLimits: vi.fn(), simulationLocation: vi.fn(), relocateSimulation: vi.fn(), quickMission: vi.fn(), parseMission: vi.fn(), missionDeployments: vi.fn(),
  },
}))
vi.mock('./PlanMapEditor', () => ({ PlanMapEditor: () => <div>plan map</div> }))
let waypointClicks = 0
vi.mock('./MissionPlannerMap', () => ({
  MissionPlannerMap: ({ onStartChange, onPlacePicked, onAddAircraftWaypoint, mode }: { onStartChange: (p: { latitude: number; longitude: number }) => void; onPlacePicked?: (p: { latitude: number; longitude: number }, label: string) => void
    onAddAircraftWaypoint: (id: string, p: { latitude: number; longitude: number }) => void; mode: string }) =>
    <div><span>map mode {mode}</span>
      <button type="button" onClick={() => onStartChange({ latitude: 12.97, longitude: 77.59 })}>CLICK THE MAP</button>
      <button type="button" onClick={() => { waypointClicks += 1; onAddAircraftWaypoint('quick', { latitude: Math.round((12.97 + waypointClicks / 1000) * 1e6) / 1e6, longitude: 77.59 }) }}>ADD A WAYPOINT</button>
      <button type="button" onClick={() => onPlacePicked?.({ latitude: 12.98, longitude: 77.6 }, 'Cubbon Park')}>PICK SEARCH RESULT</button></div>,
}))
vi.setConfig({ testTimeout: 30000 })

const apiMock = vi.mocked(api)
const config: ScenarioConfig = {
  uav_count: 4, task_count: 10, seed: 21, duration: 180, spoofing_type: 'none', target_uav: 'UAV-02',
  spoof_start: 15, spoof_duration: 35, spoof_magnitude: 3, response_policy: 'adaptive', detector_threshold: null,
}
const vehicle = (id: number): Px4TelemetryVehicle => ({
  id: `px4-${id}`, system_id: id, component_id: 1, source: 'px4_mavlink', status: 'connected', armed: false, heartbeat: null,
  position: { latitude: 12.9716, longitude: 77.5946, altitude_m: 0, relative_altitude_m: 0, heading_deg: null, valid: true, coordinate_frame: 'WGS84', source_message: 'GLOBAL_POSITION_INT', source_time_boot_ms: 1, received_at: 'x' },
  velocity: null, attitude: null, gps_raw: null, imu_raw: null, battery: null, mission: null,
  threat_assessment: { status: 'not_available', reason: '' }, last_heartbeat_at: 'x', last_message_at: 'x', telemetry_age_seconds: 0, heartbeat_age_seconds: 0, flight_trail: [],
}) as Px4TelemetryVehicle

const typeOf = (id: string, label: string, where: string, start: string, task = 'x') => ({ id, label, summary: `${label}.`, size_label: where === 'centre' ? 'Area radius (m)' : '', task_type: task, objectives: [], where, start })
const RULES = {
  types: [typeOf('area_survey', 'Area survey', 'centre', 'where'), typeOf('search_spiral', 'Search spiral', 'centre', 'where'), typeOf('relief_delivery', 'Relief delivery', 'destination', 'aircraft'),
    typeOf('perimeter_patrol', 'Perimeter patrol', 'centre', 'where'), typeOf('waypoint_route', 'Waypoint route', 'points', 'aircraft'), typeOf('waypoints_then_drop', 'Waypoints then drop', 'points', 'aircraft')],
  defaults: { speed_mps: 8, altitude_m: 50, size_m: 300, drone_count: 1 }, size_range_m: [50, 5000], max_drones: 5, rules: ['Speed defaults to the highest safe speed.'], speed_reason: 'The detector baseline was validated only at 8 m/s.',
  research_speed_mps: 8, extra_roles: ['standby', 'decoy', 'standby', 'decoy'], far_aircraft_m: 50000, point_limit: 20, decoy_offset_m: 25,
}

/** A deterministic stand-in for the backend: the same request gives the same plan, so equal drafts mean equal requests. */
const fakeQuick = (raw: QuickRequest): QuickResult => {
  // Like the backend: the plan has `drone_count` aircraft (running ids first, then px4-N), UAV-1 primary and the rest decoy unless a row or the older `roles` says otherwise.
  const ids = Array.from({ length: raw.drone_count }, (_, index) => raw.aircraft_ids[index] ?? `px4-${index + 1}`)
  const roleAt = (index: number) => raw.aircraft?.[index]?.role ?? (index === 0 ? 'primary' : (raw.roles ?? [])[index - 1] ?? 'decoy')
  const request = { ...raw, aircraft_ids: ids }
  const route = { generator: 'x', generator_version: 1, assumptions: '', events: [], length_m: 5857, duration_s: 732, waypoints: [
    { seq: 0, latitude: request.where!.latitude, longitude: request.where!.longitude, altitude_m: 50, speed_mps: request.speed_mps ?? 8, hover_s: 0, action: 'takeoff' as const }] }
  const plan = {
    ...EMPTY_PLAN, objectives: ['search_rescue'], operating_area: { kind: 'circle', centre: request.where!, radius_m: request.size_m ?? 300, vertices: [] },
    required_uav_count: request.aircraft_ids.length, selected_uav_ids: request.aircraft_ids, activated_uav_ids: request.aircraft_ids,
    constraints: { max_distance_from_start_m: 450, max_altitude_m: 80 }, max_duration_minutes: 18, workflow_stage: 'review',
    auto_limits: { max_distance_from_start_m: true, max_altitude_m: true, max_duration_minutes: true },
    aircraft: request.aircraft_ids.map((id, index) => ({ uav_id: id, role: roleAt(index), priority: index === 0 ? 1 : 2, reassignable: true, task_type: 'expanding_square_search',
      parameters: { datum: request.where!, track_spacing_m: 30, search_radius_m: 400 }, start_location: request.where!, altitude_m: request.altitude_m ?? 50, speed_mps: request.speed_mps ?? 8,
      loiter_duration_s: 0, return_mode: 'return_to_start', route, confirmed: true }))      // the backend confirms every aircraft of a quick plan (each route comes from a documented rule),
  } as unknown as MissionPlan
  return { plan, suggested_name: `Search spiral · ${request.place_name ?? `${request.where!.latitude.toFixed(4)}, ${request.where!.longitude.toFixed(4)}`}`, warnings: [], derived: {},
    summary: { mission_type: request.mission_type, label: 'Search spiral', area: { kind: 'circle', radius_m: request.size_m ?? 300, centre: request.where! }, start: request.where!, drones: request.aircraft_ids.length,
      aircraft_ids: request.aircraft_ids, speed_mps: request.speed_mps ?? 8, altitude_m: request.altitude_m ?? 50, estimated_s: 732, length_m: 5857, waypoints: 30, track_spacing_m: 30,
      limits: { max_distance_from_start_m: 450, max_altitude_m: 80, max_duration_minutes: 18 }, confirmed: true, roles: request.aircraft_ids.map((_, index) => roleAt(index)),
      speed_info: { speed_mps: request.speed_mps ?? 15.5, mode: request.speed_mps ? 'typed' : 'derived', derived_speed_mps: 15.5, research_speed_mps: 8, above_validated: (request.speed_mps ?? 15.5) > 8, limited_by: 'corner',
        ceiling_mps: 90, corner_limit_mps: 15.8, tightest_corner_radius_m: 25, lateral_accel_mps2: 10, acceptance_radius_m: 25 },
      explanation: request.aircraft_ids.map(id => ({ uav_id: id, role: 'primary', task_type: 'expanding_square_search', steps: ['Take off and climb to 50 m.', 'Fly an expanding square spiral (5.5 km, about 6 min).', 'Total 5.86 km, about 6.3 min, 30 waypoints.'] })),
      points: request.points ?? [], drop_index: request.drop_index ?? null,
      aircraft: ids.map((id, index) => ({ uav_id: id, name: raw.aircraft?.[index]?.name ?? `UAV-${index + 1}`, role: roleAt(index), task_type: 'x', length_m: 1, duration_s: 1, waypoints: 2,
        line: `${raw.aircraft?.[index]?.name ?? `UAV-${index + 1}`} (${roleAt(index)}) line` })),
      fingerprint: `fp:${request.mission_type}:${request.where!.latitude}:${request.where!.longitude}:${request.size_m ?? ''}:${request.speed_mps ?? ''}` } }
}

let store: ScenarioDetail | null = null
const stored = (intent: ScenarioIntent, number: number, title: string): ScenarioDetail => ({
  id: 'scenario-1', owner_user_id: 'u', title, created_at: 't', updated_at: 't',
  versions: [{ id: `version-${number}`, version_number: number, config, intent, config_hash: `hash-${number}`, created_at: 't', sandbox_run_id: null, approval: null }],
})
const lastRequest = (): QuickRequest => { const calls = apiMock.quickMission.mock.calls; return calls[calls.length - 1][0] }
const parse = (patch: Omit<Partial<ParseResult>, 'state'> & { state?: Partial<ParseState> }): ParseResult => ({
  intent: 'mission', understood: [], question: null, complete: false, unsupported: null, notes: [], refused: null, ...patch,
  state: { mission_type: null, size_m: null, drone_count: null, speed_mps: null, altitude_m: null, where_source: null, place_query: null, asked: null, ...(patch.state ?? {}) },
})

/** Renders the Quick Mission page by default (`mode: 'lab'` renders Scenario Lab: Sections A-C and no quick panel). */
function renderLab(props: { isOperator?: boolean; onMissionDeployed?: () => void; userId?: string; mode?: 'lab' | 'quick'; quickScenarioId?: string | null; sharedScenarioId?: string | null
  sharedVersion?: number | null; onOpenInLab?: (id: string) => void; onOpenQuickMission?: () => void } = {}) {
  return render(<ScenarioWorkspace userId={props.userId ?? 'quick-user'} config={config} onLoadConfig={vi.fn()} canManage isOperator={props.isOperator} onMissionDeployed={props.onMissionDeployed ?? vi.fn()}
    mode={props.mode ?? 'quick'} quickScenarioId={props.quickScenarioId} sharedScenarioId={props.sharedScenarioId} sharedVersion={props.sharedVersion} onOpenInLab={props.onOpenInLab} onOpenQuickMission={props.onOpenQuickMission}/>)
}
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }))
/** The rules and the verified fleet arrive asynchronously; wait for both before using the panel. */
const TYPE_IDS: [RegExp, string][] = [[/Search spiral/, 'search_spiral'], [/Relief delivery/, 'relief_delivery'], [/Area survey/, 'area_survey'], [/Waypoint route/, 'waypoint_route'], [/Waypoints then drop/, 'waypoints_then_drop']]
const pickType = async (name: string | RegExp) => {
  const id = TYPE_IDS.find(([pattern]) => pattern.test(String(name instanceof RegExp ? name.source : name)))![1]
  fireEvent.change(await screen.findByLabelText('Mission type'), { target: { value: id } })
  await screen.findByText(/verified aircraft available/)
}
const lastReply = () => Array.from(document.querySelectorAll('.scenario-assistant-messages p.assistant')).pop()?.textContent ?? ''
const say = (text: string) => {
  fireEvent.change(screen.getByLabelText('Message Scenario Lab assistant'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
}
const nameInput = () => screen.getByLabelText(/SCENARIO TITLE|Quick mission name/) as HTMLInputElement
const titleValue = () => nameInput().value

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear(); store = null
  apiMock.scenarioList.mockImplementation(async () => (store ? [{ ...store, latest_version: store.versions[0] }] : []) as never)
  apiMock.myScenarioRequests.mockResolvedValue([])
  apiMock.px4Telemetry.mockResolvedValue({ vehicles: [vehicle(1), vehicle(2)] } as never)
  apiMock.fleetStatus.mockResolvedValue({ capacity: 5, verified_count: 2, manager_error: null,
    instances: [1, 2].map(id => ({ aircraft_id: `px4-${id}`, instance_number: id - 1, system_id: id, gazebo_model: `x500_${id - 1}`, verified: true })) } as never)
  apiMock.planningLimits.mockResolvedValue({ schema_version: 2, limits: { max_flight_s: 1200, max_flight_distance_m: 15000 }, quick: RULES } as never)
  apiMock.quickMission.mockImplementation(async request => fakeQuick(request))
  apiMock.createScenario.mockImplementation(async (title, _config, intent) => (store = stored(intent!, 1, title)))
  apiMock.updateScenario.mockImplementation(async (_id, title, _config, intent) => (store = stored(intent!, (store?.versions[0].version_number ?? 0) + 1, title)))
  apiMock.deployScenario.mockResolvedValue({ id: 'deployment-1', scenario_id: 'scenario-1' } as never)
  apiMock.scenarioDetail.mockImplementation(async () => store as never)
  apiMock.health.mockResolvedValue({ status: 'ready', components: {} } as never)
  apiMock.simulationLocation.mockRejectedValue(new Error('not needed'))
  apiMock.scenarioAssistant.mockRejectedValue(new Error('AI disabled in tests'))
  apiMock.missionDeployments.mockResolvedValue({ deployment: null, execution: null, execution_deployment: null })
  apiMock.activateFleet.mockImplementation(async count => ({ required_uav_count: count, activated_uav_count: count, assigned_uav_count: 0, request_id: 'r', verified: true,
    vehicles: Array.from({ length: count }, (_, i) => ({ aircraft_id: `px4-${i + 1}`, instance_number: i, system_id: i + 1, gazebo_model: `x500_${i}`, px4_status: 'connected', gazebo_status: 'connected', verified: true })) }))
})
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers() })

async function quickSpiralAtClick() {
  renderLab()
  await pickType(/Search spiral/)
  click('CLICK THE MAP')
  await screen.findByLabelText('Primary aircraft steps')
}

describe('Quick mission panel', () => {
  it('fills the whole draft from a type and one click, with a suggested name and nothing typed', async () => {
    await quickSpiralAtClick()
    expect(apiMock.quickMission).toHaveBeenCalledTimes(1)
    expect(apiMock.quickMission.mock.calls[0][0]).toMatchObject({ mission_type: 'search_spiral', where: { latitude: 12.97, longitude: 77.59 }, where_source: 'map', drone_count: 1, aircraft_ids: ['px4-1'] })
    expect(titleValue()).toBe('Search spiral · 12.9700, 77.5900')
    const summary = screen.getByLabelText('What the aircraft will do').textContent ?? ''
    expect(summary).toContain('Take off and climb to 50 m.')
    expect(summary).toContain('max distance 450 m')
    expect(apiMock.createScenario).not.toHaveBeenCalled()      // filling never saves
    expect(apiMock.deployScenario).not.toHaveBeenCalled()
  })

  it('a name the user typed is kept', async () => {
    renderLab()
    fireEvent.change(await screen.findByLabelText('Quick mission name'), { target: { value: 'My search' } })
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    expect(titleValue()).toBe('My search')
  })

  it('uses the aircraft position, a picked search result and the drone count; destination types refuse the aircraft position', async () => {
    renderLab()
    await pickType(/Search spiral/)
    await waitFor(() => expect((screen.getByRole('button', { name: /USE THE AIRCRAFT/ }) as HTMLButtonElement).disabled).toBe(false))
    click(/USE THE AIRCRAFT/)
    await screen.findByLabelText('Primary aircraft steps')
    expect(lastRequest()).toMatchObject({ where_source: 'aircraft', where: { latitude: 12.9716, longitude: 77.5946 } })
    click('PICK SEARCH RESULT')
    await waitFor(() => expect(lastRequest()).toMatchObject({ where_source: 'place', place_name: 'Cubbon Park' }))
    await waitFor(() => expect(titleValue()).toBe('Search spiral · Cubbon Park'))
    fireEvent.change(screen.getByLabelText('Number of aircraft'), { target: { value: '2' } })
    await waitFor(() => expect(lastRequest()).toMatchObject({ drone_count: 2, aircraft_ids: ['px4-1', 'px4-2'] }))
    await pickType(/Relief delivery/)
    expect(screen.queryByRole('button', { name: /USE THE AIRCRAFT/ })).toBeNull()
  })

  it('never offers more aircraft than are verified and offers to start one when none is', async () => {
    apiMock.fleetStatus.mockResolvedValue({ capacity: 5, verified_count: 0, manager_error: null, instances: [] } as never)
    renderLab()
    expect(await screen.findByText(/No verified aircraft is running/)).toBeTruthy()
    click(/START AND VERIFY 1 AIRCRAFT/)
    await waitFor(() => expect(apiMock.activateFleet).toHaveBeenCalledWith(1, undefined))
  })

  it('shows a refusal from the backend in words and leaves the draft untouched', async () => {
    apiMock.quickMission.mockRejectedValue(Object.assign(new Error('x'), { detail: { issues: [{ code: 'speed_out_of_range', message: 'Speed must be between 1 and 90 m/s.' }] } }))
    renderLab()
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    expect(await screen.findByText(/Speed must be between 1 and 90 m\/s\./)).toBeTruthy()
    expect(screen.queryByLabelText('Primary aircraft steps')).toBeNull()
  })

  it('save and deploy saves the exact draft, deploys that version and never starts a flight', async () => {
    const deployed = vi.fn()
    renderLab({ onMissionDeployed: deployed })
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    await waitFor(() => expect((screen.getByRole('button', { name: /SAVE AND DEPLOY TO MISSION CONTROL/ }) as HTMLButtonElement).disabled).toBe(false))
    click(/SAVE AND DEPLOY TO MISSION CONTROL/)
    await waitFor(() => expect(deployed).toHaveBeenCalledTimes(1))
    expect(apiMock.createScenario).toHaveBeenCalledTimes(1)
    expect(apiMock.deployScenario).toHaveBeenCalledWith('scenario-1', 'version-1')
    const intent = apiMock.createScenario.mock.calls[0][2] as ScenarioIntent
    expect(intent.schema_version).toBe(2)
    expect(intent.mission_plan!.aircraft[0].route).not.toBeNull()
    expect(Object.keys(apiMock).some(name => /start|execute/i.test(name))).toBe(false)
  })

  it('an Operator cannot save-and-deploy a new draft: the reason says a General must approve the saved version', async () => {
    renderLab({ isOperator: true })
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    const button = screen.getByRole('button', { name: /SAVE AND DEPLOY TO MISSION CONTROL/ }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(screen.getByText(/Operators can deploy only a version a General has approved/)).toBeTruthy()
    click(/SAVE DRAFT/)
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledTimes(1))
  })

  it('the saved version carries the quick source: type, place, size, aircraft count, speed, altitude, name and the fingerprint', async () => {
    await quickSpiralAtClick()
    click(/SAVE DRAFT/)
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledTimes(1))
    const intent = apiMock.createScenario.mock.calls[0][2] as ScenarioIntent
    expect(intent.quick_source).toMatchObject({ mission_type: 'search_spiral', where: { latitude: 12.97, longitude: 77.59 }, where_source: 'map', drone_count: 1, speed_mps: null, altitude_m: null,   // auto stays auto: the typed values are stored, not the derived ones
      name: 'Search spiral · 12.9700, 77.5900', fingerprint: 'fp:search_spiral:12.97:77.59::' })
  })

  it('FINE-TUNE IN SCENARIO LAB is enabled only for a saved mission and opens that scenario', async () => {
    const open = vi.fn()
    renderLab({ onOpenInLab: open })
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    expect((screen.getByRole('button', { name: 'FINE-TUNE IN SCENARIO LAB' }) as HTMLButtonElement).disabled).toBe(true)
    click(/SAVE DRAFT/)
    await waitFor(() => expect((screen.getByRole('button', { name: 'FINE-TUNE IN SCENARIO LAB' }) as HTMLButtonElement).disabled).toBe(false))
    click('FINE-TUNE IN SCENARIO LAB')
    expect(open).toHaveBeenCalledWith('scenario-1')
  })

  const quickStored = (withSource: boolean) => {
    store = stored({ mission_objectives: [], mission_description: '', proposed_tasks: [], uav_role_suggestions: [], operating_area_notes: '', environment_notes: '', constraints: [], geographic_mission: null,
      schema_version: 2, mission_plan: { ...EMPTY_PLAN } as MissionPlan, uav_fault_assignments: [],
      ...(withSource ? { quick_source: { mission_type: 'search_spiral', where: { latitude: 12.95, longitude: 77.55 }, where_source: 'place', place_name: 'Cubbon Park', size_m: 450, drone_count: 1, speed_mps: 9, altitude_m: 40, name: 'My stored quick', fingerprint: 'fp' } } : {}) } as ScenarioIntent, 3, 'Stored quick')
  }

  it('the Quick Mission deep link restores the stored values exactly and a save is a new version of that scenario', async () => {
    quickStored(true)
    renderLab({ quickScenarioId: 'scenario-1' })
    await waitFor(() => expect(apiMock.quickMission).toHaveBeenCalled())
    expect(lastRequest()).toMatchObject({ mission_type: 'search_spiral', where: { latitude: 12.95, longitude: 77.55 }, where_source: 'place', place_name: 'Cubbon Park', size_m: 450, speed_mps: 9, altitude_m: 40, drone_count: 1 })
    await screen.findByLabelText('Primary aircraft steps')
    expect(titleValue()).toBe('My stored quick')
    click(/SAVE CHANGES \(NEW VERSION\)|SAVE DRAFT/)
    await waitFor(() => expect(apiMock.updateScenario).toHaveBeenCalledTimes(1))
    expect(apiMock.createScenario).not.toHaveBeenCalled()
    expect(apiMock.updateScenario.mock.calls[0][0]).toBe('scenario-1')
  })

  it('a scenario made by hand (no quick source) says so instead of inventing quick values', async () => {
    quickStored(false)
    renderLab({ quickScenarioId: 'scenario-1' })
    expect(await screen.findByText(/built in Scenario Lab, so Quick Mission can.t edit it without losing detail/)).toBeTruthy()
    expect(apiMock.quickMission).not.toHaveBeenCalled()
  })

  it('Scenario Lab shows Sections A-C without an Advanced collapse, no quick panel, and a link to Quick Mission', async () => {
    const link = vi.fn()
    renderLab({ mode: 'lab', onOpenQuickMission: link })
    expect(await screen.findByText('SECTION A')).toBeTruthy()
    expect(screen.getByText('SECTION B')).toBeTruthy(); expect(screen.getByText('SECTION C')).toBeTruthy()
    expect(document.querySelector('details.advanced-draft')).toBeNull()
    expect(screen.queryByText('QUICK MISSION', { selector: '.quick-mission .eyebrow' })).toBeNull()
    expect(document.querySelector('.quick-mission')).toBeNull()
    click('Create a quick mission')
    expect(link).toHaveBeenCalledTimes(1)
  })

  it('Scenario Lab opens a chosen older version as a new draft based on it', async () => {
    const intentOf = (description: string) => ({ mission_objectives: [], mission_description: '', proposed_tasks: [], uav_role_suggestions: [], operating_area_notes: '', environment_notes: '', constraints: [],
      geographic_mission: null, schema_version: 2, mission_plan: { ...EMPTY_PLAN, description } as MissionPlan, uav_fault_assignments: [] }) as ScenarioIntent
    const older = stored(intentOf('the first version'), 1, 'Two versions').versions[0]
    const newest = stored(intentOf('the second version'), 2, 'Two versions').versions[0]
    store = { ...stored(intentOf('x'), 2, 'Two versions'), versions: [newest, older] }
    renderLab({ mode: 'lab', sharedScenarioId: 'scenario-1', sharedVersion: 1 })
    expect(await screen.findByText(/Opened version 1 as a new draft based on it\. Saving creates version 3/)).toBeTruthy()
    expect((screen.getByLabelText(/DESCRIPTION \(optional\)/) as HTMLTextAreaElement).value).toBe('the first version')
  })
})

/** Scenario Lab has no quick panel: its draft is filled by the assistant, through the same derivation. */
async function labFilledViaAssistant() {
  apiMock.parseMission.mockResolvedValue(parse({ complete: true, state: { mission_type: 'search_spiral', size_m: 300, drone_count: 1, where_source: 'aircraft' } }))
  renderLab({ mode: 'lab' })
  await waitFor(() => expect(apiMock.px4Telemetry).toHaveBeenCalled())
  await new Promise(resolve => setTimeout(resolve, 80))
  click('Open Scenario Lab assistant')
  say('search around here')
  await waitFor(() => expect(lastReply()).toContain('Here is the plan'))
  click('CONFIRM')
  await screen.findByText('SCENARIO INPUTS READY FOR DRAFT REVIEW')
}

describe('Auto and typed limits', () => {
  const preview = (limits: object, warnings: object[] = []) => ({ route: fakeQuick({ mission_type: 'search_spiral', where: { latitude: 1, longitude: 1 }, where_source: 'map', drone_count: 1, aircraft_ids: ['px4-1'] }).plan.aircraft[0].route!,
    summary: { length_m: 5000, duration_s: 600, waypoints: 20 }, warnings, limits })

  it('generating the route again re-derives the auto limits and keeps a limit the user typed exactly', async () => {
    await labFilledViaAssistant()
    expect(screen.getAllByText(/auto \(450 m\)/).length).toBeGreaterThan(0)
    fireEvent.change(screen.getByLabelText('SPEED (m/s) *'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText(/MAX DISTANCE FROM START/), { target: { value: '777' } })
    apiMock.previewRoute.mockResolvedValue(preview({ max_distance_from_start_m: 777, max_altitude_m: 80, max_duration_minutes: 12, auto: { max_distance_from_start_m: false, max_altitude_m: true, max_duration_minutes: true } },
      [{ code: 'start_outside_area', message: 'The start is 120 m outside the operating area. The plan is still valid.' }]) as never)
    click(/GENERATE \/ PREVIEW ROUTE/)
    await screen.findByLabelText('Route preview')
    expect(apiMock.previewRoute.mock.calls[0][0]).toMatchObject({ auto_limits: { max_distance_from_start_m: false, max_altitude_m: true, max_duration_minutes: true }, constraints: { max_distance_from_start_m: 777 } })
    expect((screen.getByLabelText(/MAX DISTANCE FROM START/) as HTMLInputElement).value).toBe('777')
    expect(screen.getAllByText(/auto \(80 m\)/).length).toBeGreaterThan(0)
    expect(screen.getByText(/typed \(enforced exactly\)/)).toBeTruthy()
    expect((screen.getByLabelText(/MAX DURATION/) as HTMLInputElement).value).toBe('12')
    expect(screen.getByText(/Warning: The start is 120 m outside the operating area/)).toBeTruthy()      // visible warning, not a block
  })

  it('a route longer than the aircraft default is a warning, not a blocker, when the user raised the maximum duration', async () => {
    const longRun = (request: QuickRequest) => { const result = fakeQuick(request); result.plan.aircraft[0].route!.duration_s = 3000; result.plan.max_duration_minutes = 100; return result }
    apiMock.quickMission.mockImplementation(async request => longRun(request))
    await labFilledViaAssistant()
    expect(screen.getByText(/Warning \(you raised the maximum duration, so this does not block\)/)).toBeTruthy()
  })
})

describe('Scenario assistant as the front door', () => {
  const openAssistant = () => click('Open Scenario Lab assistant')

  it('plans "search 300 m around here with one drone" with no typing beyond the sentence, then CONFIRM fills the same draft', async () => {
    apiMock.parseMission.mockResolvedValue(parse({ understood: ['Search spiral'], complete: true, state: { mission_type: 'search_spiral', size_m: 300, drone_count: 1, where_source: 'aircraft' } }))
    renderLab()
    await screen.findByText(/verified aircraft available/)
    openAssistant()
    say('search 300 m around here with one drone')
    await waitFor(() => expect(lastReply()).toContain('Here is the plan'))
    expect(lastReply()).toContain('radius 300 m')
    expect(screen.getByText('Fill the draft with this mission?')).toBeTruthy()
    expect(apiMock.createScenario).not.toHaveBeenCalled()
    expect(apiMock.deployScenario).not.toHaveBeenCalled()
    click('CONFIRM')
    await screen.findByLabelText('Primary aircraft steps')
    expect(lastReply()).toContain('I do not save, deploy or start anything')
    expect(apiMock.createScenario).not.toHaveBeenCalled()
    const request = lastRequest()
    expect(request).toMatchObject({ mission_type: 'search_spiral', size_m: 300, where_source: 'aircraft', aircraft_ids: ['px4-1'], drone_count: 1 })
  })

  it('asks one question at a time with buttons; the map click answers the where question', async () => {
    apiMock.parseMission
      .mockResolvedValueOnce(parse({ question: { id: 'where', text: 'Where should the search spiral be?', choices: [{ id: 'where:map', label: 'Click the place on the map' }, { id: 'where:aircraft', label: "Use the aircraft's position" }] },
        state: { mission_type: 'search_spiral', asked: 'where' } }))
      .mockResolvedValueOnce(parse({ complete: true, state: { mission_type: 'search_spiral', where_source: 'map' } }))
    renderLab()
    await screen.findByText(/verified aircraft available/)
    openAssistant()
    say('I want a search spiral')
    await waitFor(() => expect(lastReply()).toContain('Where should the search spiral be?'))
    const choices = within(screen.getByRole('group', { name: 'Answer options' })).getAllByRole('button')
    expect(choices.map(item => item.textContent)).toEqual(['Click the place on the map', "Use the aircraft's position"])
    fireEvent.click(choices[0])
    await waitFor(() => expect(lastReply()).toContain('Click the place on the Quick mission map'))
    expect(screen.getByText(/map mode set_start/)).toBeTruthy()
    expect(apiMock.parseMission.mock.calls[1][0]).toMatchObject({ choice: 'where:map', state: { mission_type: 'search_spiral' } })
    expect(apiMock.quickMission).not.toHaveBeenCalled()
    click('CLICK THE MAP')                                    // the one click that answers it
    await waitFor(() => expect(lastReply()).toContain('Here is the plan'))
    expect(apiMock.quickMission.mock.calls[0][0]).toMatchObject({ where_source: 'map', where: { latitude: 12.97, longitude: 77.59 } })
    expect(apiMock.createScenario).not.toHaveBeenCalled()
  })

  it('answers unsupported requests honestly with the closest type, and refuses to save, deploy or start', async () => {
    apiMock.parseMission
      .mockResolvedValueOnce(parse({ intent: 'unsupported', unsupported: { reason: 'Following a moving target is not supported.', closest: 'waypoint_route' },
        question: { id: 'closest', text: 'Use Waypoint route instead?', choices: [{ id: 'closest:waypoint_route', label: 'Use Waypoint route' }, { id: 'cancel', label: 'Never mind' }] } }))
      .mockResolvedValueOnce(parse({ intent: 'refused', refused: 'I only fill the mission draft. Saving, deploying and starting are your own buttons.' }))
    renderLab()
    await screen.findByText(/verified aircraft available/)
    openAssistant()
    say('follow the car in front')
    await waitFor(() => expect(lastReply()).toContain('Not supported: Following a moving target is not supported.'))
    click('Never mind')
    await waitFor(() => expect(lastReply()).toContain('Nothing was changed'))
    say('save it and deploy')
    await waitFor(() => expect(lastReply()).toContain('your own buttons'))
    for (const name of ['createScenario', 'updateScenario', 'deployScenario', 'submitScenario', 'activateFleet'] as const) expect(apiMock[name]).not.toHaveBeenCalled()
    expect(apiMock.quickMission).not.toHaveBeenCalled()
  })

  it('only the two planning endpoints are used: parity with the Quick panel, request for request', async () => {
    const inputs = { mission_type: 'search_spiral', size_m: 300, where_source: 'aircraft', aircraft_ids: ['px4-1'], drone_count: 1 }
    // panel
    const first = renderLab()
    await pickType(/Search spiral/)
    await waitFor(() => expect((screen.getByRole('button', { name: /USE THE AIRCRAFT/ }) as HTMLButtonElement).disabled).toBe(false))
    click(/USE THE AIRCRAFT/)
    await screen.findByLabelText('Primary aircraft steps')
    click(/SAVE DRAFT/)
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledTimes(1))
    const panelRequest = lastRequest()
    const panelSaved = apiMock.createScenario.mock.calls[0]
    first.unmount(); localStorage.clear(); sessionStorage.clear(); store = null
    // assistant
    apiMock.parseMission.mockResolvedValue(parse({ complete: true, state: { mission_type: 'search_spiral', size_m: null, drone_count: 1, where_source: 'aircraft' } }))
    renderLab()
    await screen.findByText(/verified aircraft available/)
    openAssistant()
    say('search around here')
    await waitFor(() => expect(lastReply()).toContain('Here is the plan'))
    click('CONFIRM')
    await screen.findByLabelText('Primary aircraft steps')
    click(/SAVE DRAFT/)
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledTimes(2))
    const assistantRequest = lastRequest()
    expect(assistantRequest).toMatchObject(inputs && { mission_type: 'search_spiral', where_source: 'aircraft', aircraft_ids: ['px4-1'] })
    expect(assistantRequest).toEqual(panelRequest)
    const assistantSaved = apiMock.createScenario.mock.calls[1]
    expect(assistantSaved[0]).toBe(panelSaved[0])                        // same suggested name
    expect(assistantSaved[2]).toEqual(panelSaved[2])                     // same stored draft
  })

  it('typed set commands still work first and are not sent to the mission parser', async () => {
    renderLab()
    await screen.findByText(/verified aircraft available/)
    openAssistant()
    say('what is the speed limit?')
    await waitFor(() => expect(apiMock.parseMission).toHaveBeenCalled())
    apiMock.parseMission.mockClear()
    say('set altitude to 30')
    await waitFor(() => expect(lastReply().length).toBeGreaterThan(0))
    expect(apiMock.parseMission).not.toHaveBeenCalled()
  })
})

describe('Draft safety', () => {
  it('keeps an unsaved draft across a refresh, says so, and Discard throws it away', async () => {
    const first = renderLab()
    fireEvent.change(await screen.findByLabelText(/SCENARIO TITLE|Quick mission name/), { target: { value: 'Half typed mission' } })
    await waitFor(() => expect(localStorage.getItem('trinetra-unsaved-draft:quick-user:new')).toContain('Half typed mission'), { timeout: 3000 })
    first.unmount()
    renderLab()
    expect(await screen.findByText(/Restored your unsaved draft/)).toBeTruthy()
    expect(titleValue()).toBe('Half typed mission')
    click('DISCARD')
    await waitFor(() => expect(titleValue()).toBe(''))
    expect(screen.queryByText(/Restored your unsaved draft/)).toBeNull()
    expect(localStorage.getItem('trinetra-unsaved-draft:quick-user:new')).toBeNull()
  })

  it('a draft belongs to its user: another account on this browser sees nothing', async () => {
    const first = renderLab()
    fireEvent.change(await screen.findByLabelText(/SCENARIO TITLE|Quick mission name/), { target: { value: 'Private draft' } })
    await waitFor(() => expect(localStorage.getItem('trinetra-unsaved-draft:quick-user:new')).not.toBeNull(), { timeout: 3000 })
    first.unmount()
    renderLab({ userId: 'someone-else' })
    await screen.findByLabelText(/SCENARIO TITLE|Quick mission name/)
    expect(titleValue()).toBe('')
    expect(screen.queryByText(/Restored your unsaved draft/)).toBeNull()
  })

  it('saving removes the stored draft, and signing out removes every stored draft of that user', async () => {
    renderLab()
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    await waitFor(() => expect(localStorage.getItem('trinetra-unsaved-draft:quick-user:new')).not.toBeNull(), { timeout: 3000 })
    click(/SAVE DRAFT/)
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalled())
    await waitFor(() => expect(localStorage.getItem('trinetra-unsaved-draft:quick-user:new')).toBeNull(), { timeout: 3000 })
    fireEvent.change(nameInput(), { target: { value: 'Edited after saving' } })
    await waitFor(() => expect(localStorage.getItem('trinetra-unsaved-draft:quick-user:scenario-1')).not.toBeNull(), { timeout: 3000 })
    act(() => { window.dispatchEvent(new Event('trinetra:logout')) })
    expect(localStorage.getItem('trinetra-unsaved-draft:quick-user:scenario-1')).toBeNull()
  })

  it('warns before leaving the page only while there are unsaved changes', async () => {
    renderLab()
    const leave = () => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented }
    await screen.findByLabelText(/SCENARIO TITLE|Quick mission name/)
    expect(leave()).toBe(false)
    fireEvent.change(nameInput(), { target: { value: 'x' } })
    await waitFor(() => expect(leave()).toBe(true))
  })

  it('a saved version without a route says so when it is opened, instead of a bare "No route generated yet"', async () => {
    const plan = { ...EMPTY_PLAN, aircraft: [{ uav_id: 'px4-1', role: 'primary', priority: 1, reassignable: true, task_type: 'waypoint_route', parameters: {}, start_location: null,
      altitude_m: 30, speed_mps: 8, loiter_duration_s: 0, return_mode: 'return_to_start', route: null, confirmed: false }], selected_uav_ids: ['px4-1'] } as unknown as MissionPlan
    store = stored({ mission_objectives: [], mission_description: '', proposed_tasks: [], uav_role_suggestions: [], operating_area_notes: '', environment_notes: '', constraints: [], geographic_mission: null,
      schema_version: 2, mission_plan: plan, uav_fault_assignments: [] } as ScenarioIntent, 3, 'Draft without a route')
    renderLab({ mode: 'lab' })
    await waitFor(() => expect(screen.getByLabelText('SAVED SCENARIO')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('SAVED SCENARIO'), { target: { value: 'scenario-1' } })
    expect(await screen.findByText(/Loaded Draft without a route · version 3\. This version was saved without a route for px4-1/)).toBeTruthy()
    expect(screen.getByText(/No route generated yet\. Press GENERATE/)).toBeTruthy()
  })
})

describe('Operator approval without reopening', () => {
  const pendingStore = (status: string) => {
    store = stored({ mission_objectives: [], mission_description: '', proposed_tasks: [], uav_role_suggestions: [], operating_area_notes: '', environment_notes: '', constraints: [], geographic_mission: null,
      schema_version: 2, mission_plan: { ...EMPTY_PLAN } as MissionPlan, uav_fault_assignments: [] } as ScenarioIntent, 1, 'Needs approval')
    store.versions[0].approval = { id: 'a1', status, reason: null, submitted_at: 't', reviewed_at: null, reviewed_by: null } as never
  }

  it('re-reads a pending review every ~12 s, shows the decision, and stops polling once decided', async () => {
    pendingStore('PENDING')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderLab({ mode: 'lab', isOperator: true })
    await waitFor(() => expect(screen.getByLabelText('SAVED SCENARIO')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('SAVED SCENARIO'), { target: { value: 'scenario-1' } })
    await waitFor(() => expect(screen.getAllByText(/PENDING/).length).toBeGreaterThan(0))
    apiMock.scenarioList.mockClear()
    await act(async () => { await vi.advanceTimersByTimeAsync(9_000) })
    expect(apiMock.scenarioList).not.toHaveBeenCalled()                              // no polling storm
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000) })
    expect(apiMock.scenarioList).toHaveBeenCalledTimes(1)
    pendingStore('APPROVED')
    await act(async () => { await vi.advanceTimersByTimeAsync(12_000) })
    expect(apiMock.scenarioList).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(apiMock.scenarioList).toHaveBeenCalledTimes(2)                            // decided: polling stopped
    expect(await screen.findAllByText(/approved/i)).toBeTruthy()
  })

  it('does not poll while the tab is hidden', async () => {
    pendingStore('PENDING')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderLab({ mode: 'lab', isOperator: true })
    await waitFor(() => expect(screen.getByLabelText('SAVED SCENARIO')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('SAVED SCENARIO'), { target: { value: 'scenario-1' } })
    await waitFor(() => expect(screen.getAllByText(/PENDING/).length).toBeGreaterThan(0))
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    try {
      apiMock.scenarioList.mockClear()
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
      expect(apiMock.scenarioList).not.toHaveBeenCalled()
    } finally { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }) }
  })
})

describe('Activate and verify is a repair, not a step', () => {
  it('ticking an aircraft that is already verified live makes the counts match without pressing ACTIVATE AND VERIFY', async () => {
    renderLab({ mode: 'lab' })
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /px4-1/ })).toBeTruthy())
    await waitFor(() => expect((screen.getByRole('checkbox', { name: /px4-1/ }) as HTMLInputElement).disabled).toBe(false))
    fireEvent.click(screen.getByRole('checkbox', { name: /px4-1/ }))
    await waitFor(() => expect((screen.getByLabelText(/HOW MANY UAVS ARE REQUIRED/) as HTMLInputElement).value).toBe('1'))
    expect(apiMock.activateFleet).not.toHaveBeenCalled()
    expect(screen.queryByText(/Requested, activated and assigned UAV counts must match/)).toBeNull()
  })

  it('when the backend refuses a deploy because the fleet is not verified, one click verifies and deploys the same version', async () => {
    const deployed = vi.fn()
    renderLab({ onMissionDeployed: deployed })
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    apiMock.deployScenario.mockRejectedValueOnce(Object.assign(new Error('Fleet is not verified.'), { status: 503, detail: { code: 'fleet_not_verified' } }))
    await waitFor(() => expect((screen.getByRole('button', { name: /SAVE AND DEPLOY TO MISSION CONTROL/ }) as HTMLButtonElement).disabled).toBe(false))
    click(/SAVE AND DEPLOY TO MISSION CONTROL/)
    expect(await screen.findByText(/was not deployed: Fleet is not verified\./)).toBeTruthy()
    click('VERIFY THE FLEET AND DEPLOY')
    await waitFor(() => expect(deployed).toHaveBeenCalledTimes(1))
    expect(apiMock.activateFleet).toHaveBeenCalledTimes(1)
    expect(apiMock.deployScenario).toHaveBeenCalledTimes(2)
    expect(apiMock.deployScenario.mock.calls[1]).toEqual(['scenario-1', 'version-1'])
  })
})

describe('A flight in progress', () => {
  it('Scenario Lab says a mission is running and that saving or deploying here does not affect it', async () => {
    apiMock.missionDeployments.mockResolvedValue({ deployment: null, execution: { id: 'e1', deployment_id: 'd1', aircraft_id: 'px4-1', state: 'executing', active: true, finished_at: null },
      execution_deployment: { id: 'd1', scenario_title: 'Harbour search', scenario_version_number: 4 } as never })
    renderLab()
    expect(await screen.findByText(/A mission is running \(Harbour search v4\)\. Saving or deploying here will not affect it\./)).toBeTruthy()
  })
})

describe('One map per page', () => {
  it('Scenario Lab mounts one planning map for the area and the aircraft, and Quick Mission has its own single map', async () => {
    renderLab({ mode: 'lab' })
    await screen.findByLabelText(/SCENARIO TITLE|Quick mission name/)
    expect(screen.getAllByText('plan map')).toHaveLength(1)
    expect(screen.queryByText(/map mode/)).toBeNull()
    cleanup()
    renderLab()
    await screen.findByLabelText(/SCENARIO TITLE|Quick mission name/)
    expect(screen.queryByText('plan map')).toBeNull()
    expect(screen.getAllByText(/map mode/)).toHaveLength(1)
  })
})


describe('Quick Mission page: the new type, several clicks, roles, speed and the quick source', () => {
  beforeEach(() => { waypointClicks = 0 })

  it('starts empty: no place and no aircraft position is chosen silently, and nothing is planned', async () => {
    renderLab()
    expect(await screen.findByText('Choose a mission type first.')).toBeTruthy()
    await pickType(/Search spiral/)
    expect(screen.getByText('Click the place on the map')).toBeTruthy()
    await new Promise(resolve => setTimeout(resolve, 500))
    expect(apiMock.quickMission).not.toHaveBeenCalled()
    expect(screen.getByText('Pick a mission type and a place to see the plan.')).toBeTruthy()
  })

  it('"Waypoints then drop": three clicks, undo and clear, a marked drop, and the explanation lists the steps', async () => {
    renderLab()
    await pickType(/Waypoints then drop/)
    expect(screen.getByText('Click the place on the map (the first waypoint).')).toBeTruthy()
    for (let i = 0; i < 3; i += 1) click('ADD A WAYPOINT')
    await waitFor(() => expect(lastRequest().points).toHaveLength(3))
    expect(lastRequest()).toMatchObject({ mission_type: 'waypoints_then_drop', where_source: 'map' })
    expect(lastRequest().drop_index).toBeNull()
    expect(screen.getByText('3 points clicked. Click again to add more.')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Drop at'), { target: { value: '1' } })
    await waitFor(() => expect(lastRequest().drop_index).toBe(1))
    click(/UNDO LAST POINT/)
    await waitFor(() => expect(lastRequest().points).toHaveLength(2))
    expect(await screen.findByLabelText('Primary aircraft steps')).toBeTruthy()
    click(/CLEAR POINTS/)
    await waitFor(() => expect(screen.getByText('Pick a mission type and a place to see the plan.')).toBeTruthy())
    expect(screen.queryByLabelText('Drop at')).toBeNull()
  })

  it('a waypoint route also takes several clicks; switching type keeps the first place', async () => {
    renderLab()
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    await pickType(/Waypoint route/)
    await waitFor(() => expect(lastRequest()).toMatchObject({ mission_type: 'waypoint_route', points: [{ latitude: 12.97, longitude: 77.59 }] }))
    click('ADD A WAYPOINT')
    await waitFor(() => expect(lastRequest().points).toHaveLength(2))
  })

  it('shows the derived speed with its rule, the badge above 8 m/s, and one click to the research speed and back to auto', async () => {
    renderLab()
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    expect((screen.getByLabelText('Speed') as HTMLInputElement).value).toBe('15.5')
    expect(screen.getByText(/auto \(derived\)/)).toBeTruthy()
    expect(screen.getByText(/Highest safe speed for this route: the tightest corner \(radius 25 m, lateral acceleration 10 m\/s²\)/)).toBeTruthy()
    expect(screen.getByText('Threat monitor is validated only at 8 m/s', { exact: false })).toBeTruthy()
    click(/RESEARCH SPEED \(8 m\/s\)/)
    await waitFor(() => expect(lastRequest().speed_mps).toBe(8))
    await waitFor(() => expect(screen.queryByText('Threat monitor is validated only at 8 m/s', { exact: false })).toBeNull())
    expect(screen.getByText(/You chose 8 m\/s; the highest safe speed here is 15.5 m\/s/)).toBeTruthy()
    click('USE AUTO')
    await waitFor(() => expect(lastRequest().speed_mps).toBeNull())
  })

  it('any UAV count with a role per aircraft (UAV-1 primary, the rest decoy by default), sent to the backend', async () => {
    apiMock.fleetStatus.mockResolvedValue({ capacity: 5, verified_count: 3, manager_error: null,
      instances: [1, 2, 3].map(id => ({ aircraft_id: `px4-${id}`, instance_number: id - 1, system_id: id, gazebo_model: `x500_${id - 1}`, verified: true })) } as never)
    apiMock.px4Telemetry.mockResolvedValue({ vehicles: [vehicle(1), vehicle(2), vehicle(3)] } as never)
    renderLab()
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    fireEvent.change(screen.getByLabelText('Number of aircraft'), { target: { value: '3' } })
    await waitFor(() => expect(lastRequest()).toMatchObject({ drone_count: 3, aircraft_ids: ['px4-1', 'px4-2', 'px4-3'], roles: [] }))
    expect((screen.getByLabelText('Role of aircraft 1') as HTMLSelectElement).value).toBe('primary')
    expect((screen.getByLabelText('Role of aircraft 2') as HTMLSelectElement).value).toBe('decoy')
    expect((screen.getByLabelText('Role of aircraft 3') as HTMLSelectElement).value).toBe('decoy')
    fireEvent.change(screen.getByLabelText('Role of aircraft 2'), { target: { value: 'standby' } })
    await waitFor(() => expect(lastRequest().aircraft?.map(row => row.role)).toEqual([null, 'standby', null]))        // only what the user chose is sent
    expect(screen.getByText(/Needs 3 verified aircraft to fly; multi-aircraft execution is not available yet/)).toBeTruthy()
  })

  it('an aircraft far from the chosen place gets the amber relocation note from the backend', async () => {
    apiMock.quickMission.mockImplementation(async request => ({ ...fakeQuick(request), warnings: [{ code: 'aircraft_far', message: 'The aircraft is 8200 km from this place. The mission starts near the place; use "Move simulation to mission start".' }] }))
    renderLab()
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    expect(await screen.findByText(/The aircraft is 8200 km from this place/)).toBeTruthy()
    expect(document.querySelector('.quick-note')).toBeTruthy()
  })

  it('the quick source round trip keeps the clicked places, the marked drop, the roles and the aircraft count', async () => {
    apiMock.fleetStatus.mockResolvedValue({ capacity: 5, verified_count: 2, manager_error: null,
      instances: [1, 2].map(id => ({ aircraft_id: `px4-${id}`, instance_number: id - 1, system_id: id, gazebo_model: `x500_${id - 1}`, verified: true })) } as never)
    renderLab()
    await pickType(/Waypoints then drop/)
    click('ADD A WAYPOINT'); click('ADD A WAYPOINT')
    await waitFor(() => expect(lastRequest().points).toHaveLength(2))
    fireEvent.change(screen.getByLabelText('Number of aircraft'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('Role of aircraft 2'), { target: { value: 'decoy' } })
    fireEvent.change(screen.getByLabelText('Drop at'), { target: { value: '0' } })
    await waitFor(() => expect(lastRequest()).toMatchObject({ drop_index: 0, drone_count: 2 }))
    expect(lastRequest().aircraft?.[1]?.role).toBe('decoy')
    await screen.findByLabelText('Primary aircraft steps')
    click(/SAVE DRAFT/)
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledTimes(1))
    const intent = apiMock.createScenario.mock.calls[0][2] as ScenarioIntent
    expect(intent.quick_source).toMatchObject({ mission_type: 'waypoints_then_drop', drop_index: 0, drone_count: 2, roles: ['decoy'], points: [{ latitude: 12.971, longitude: 77.59 }, { latitude: 12.972, longitude: 77.59 }] })
    expect(intent.quick_source?.aircraft?.map(row => row.role)).toEqual([null, 'decoy'])
    // reopen it from the stored source
    cleanup(); apiMock.quickMission.mockClear()
    store = stored(intent, 1, 'Round trip')
    renderLab({ quickScenarioId: 'scenario-1' })
    await waitFor(() => expect(apiMock.quickMission).toHaveBeenCalled())
    expect(lastRequest()).toMatchObject({ mission_type: 'waypoints_then_drop', drop_index: 0, drone_count: 2, points: [{ latitude: 12.971, longitude: 77.59 }, { latitude: 12.972, longitude: 77.59 }] })
    expect(lastRequest().aircraft?.[1]?.role).toBe('decoy')
  })
})


describe('Open a saved mission (Quick Mission)', () => {
  const qs = (patch: Record<string, unknown> = {}) => ({ mission_type: 'search_spiral', where: { latitude: 12.95, longitude: 77.55 }, where_source: 'place', place_name: 'Cubbon Park', size_m: 450, drone_count: 1,
    speed_mps: null, altitude_m: null, name: 'Cubbon search', fingerprint: 'fp', ...patch })
  const entry = (id: string, title: string, intentPatch: Record<string, unknown>, version = 2, approval: ScenarioDetail['versions'][number]['approval'] = null, aircraft = 1) => ({
    id, owner_user_id: 'u', title, created_at: 't', updated_at: '2026-11-01T10:00:00Z',
    latest_version: { id: `${id}-v${version}`, version_number: version, config, config_hash: 'h', created_at: 't', sandbox_run_id: null, approval,
      intent: { mission_objectives: [], mission_description: '', proposed_tasks: [], uav_role_suggestions: [], operating_area_notes: '', environment_notes: '', constraints: [], geographic_mission: null, schema_version: 2,
        // A quick-made mission stores exactly the plan the quick planner makes from its values; a Lab mission has its own.
        mission_plan: (intentPatch.quick_source ? fakeQuick({ mission_type: 'search_spiral', where: { latitude: 12.95, longitude: 77.55 }, where_source: 'place', size_m: 450, drone_count: 1, aircraft_ids: ['px4-1'], speed_mps: null, altitude_m: null, place_name: 'Cubbon Park' } as QuickRequest).plan
          : { ...EMPTY_PLAN, aircraft: Array.from({ length: aircraft }, (_, i) => ({ uav_id: `px4-${i + 1}`, role: i === 0 ? 'primary' : 'decoy', route: null })) }) as unknown as MissionPlan,
        uav_fault_assignments: [], ...intentPatch } as ScenarioIntent } })
  const quickEntry = () => entry('s-quick', 'Cubbon search', { quick_source: qs() }, 3)
  const labEntry = () => entry('s-lab', 'Harbour patrol (Lab)', {}, 4, null, 2)
  const approvedEntry = () => entry('s-approved', 'Approved ridge', { quick_source: qs({ name: 'Approved ridge', fingerprint: 'fp2' }) }, 2,
    { id: 'a1', status: 'APPROVED', reason: null, submitted_at: 't', reviewed_at: 't', reviewed_by: 'g' } as never)
  const list = (...items: ReturnType<typeof entry>[]) => { apiMock.scenarioList.mockImplementation(async () => items as never) }
  const pickOne = (title: string) => fireEvent.click(within(screen.getByRole('list', { name: 'Saved missions' })).getByRole('button', { name: new RegExp(title) }))

  it('lists each saved mission with name, type, place, aircraft, status, version and last change, and the search and status filter narrow it', async () => {
    list(quickEntry(), labEntry(), approvedEntry())
    renderLab()
    const rows = await screen.findByRole('list', { name: 'Saved missions' })
    expect(within(rows).getAllByRole('listitem')).toHaveLength(3)
    expect(rows.textContent).toMatch(/Cubbon search.*Search spiral · Cubbon Park · 1 aircraft.*draft · v3 · changed/)
    expect(rows.textContent).toMatch(/Harbour patrol \(Lab\).*Scenario Lab mission.*2 aircraft/)
    fireEvent.change(screen.getByLabelText('Search saved missions'), { target: { value: 'harbour' } })
    expect(within(rows).getAllByRole('listitem')).toHaveLength(1)
    fireEvent.change(screen.getByLabelText('Search saved missions'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Filter saved missions by status'), { target: { value: 'APPROVED' } })
    expect(within(rows).getAllByRole('listitem')).toHaveLength(1)
    expect(rows.textContent).toMatch(/Approved ridge/)
  })

  it('shows the same list to a General and an Operator (the backend decides which scenarios each account may list)', async () => {
    list(quickEntry(), labEntry())
    for (const isOperator of [false, true]) {
      renderLab({ isOperator })
      expect(within(await screen.findByRole('list', { name: 'Saved missions' })).getAllByRole('listitem')).toHaveLength(2)
      cleanup()
    }
  })

  it('opening a quick-made mission fills the form exactly (auto stays auto) and says which version is being edited', async () => {
    list(quickEntry())
    renderLab()
    await screen.findByRole('list', { name: 'Saved missions' })
    await screen.findByText(/verified aircraft available/)
    pickOne('Cubbon search')
    await waitFor(() => expect(apiMock.quickMission).toHaveBeenCalled())
    expect(lastRequest()).toMatchObject({ mission_type: 'search_spiral', where: { latitude: 12.95, longitude: 77.55 }, where_source: 'place', place_name: 'Cubbon Park', size_m: 450, speed_mps: null, altitude_m: null, drone_count: 1 })
    expect((screen.getByLabelText('Mission type') as HTMLSelectElement).value).toBe('search_spiral')
    expect((await screen.findByLabelText('Editing')).textContent).toMatch(/Editing: Cubbon search, version 3/)
    expect(titleValue()).toBe('Cubbon search')
    expect(document.body.textContent).toMatch(/SPEED \(m\/s\) auto \(derived\)/)
  })

  it('a Scenario Lab mission is never opened for editing: the notice appears, and nothing is saved', async () => {
    list(labEntry())
    const openLab = vi.fn()
    renderLab({ onOpenInLab: openLab })
    await screen.findByRole('list', { name: 'Saved missions' })
    pickOne('Harbour patrol')
    const notice = await screen.findByLabelText('Built in Scenario Lab')
    expect(notice.textContent).toMatch(/This mission was built in Scenario Lab, so Quick Mission can.t edit it without losing detail\./)
    expect(screen.queryByLabelText('Mission type')).toBeNull()
    fireEvent.click(within(notice).getByRole('button', { name: 'OPEN IN SCENARIO LAB' }))
    expect(openLab).toHaveBeenCalledWith('s-lab')
    expect(apiMock.createScenario).not.toHaveBeenCalled(); expect(apiMock.updateScenario).not.toHaveBeenCalled()
  })

  it('"Use as a starting point" fills a new draft, saves it as a NEW mission and never touches the Lab mission', async () => {
    list(labEntry())
    renderLab()
    await screen.findByRole('list', { name: 'Saved missions' })
    await screen.findByText(/verified aircraft available/)
    pickOne('Harbour patrol')
    fireEvent.click(within(await screen.findByLabelText('Built in Scenario Lab')).getByRole('button', { name: /USE AS A STARTING POINT/ }))
    expect(titleValue()).toBe('Harbour patrol (Lab) (copy)')
    expect(screen.queryByLabelText('Built in Scenario Lab')).toBeNull()
    expect(screen.queryByLabelText('Editing')).toBeNull()
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    click(/SAVE DRAFT/)
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledTimes(1))
    expect(apiMock.createScenario.mock.calls[0][0]).toBe('Harbour patrol (Lab) (copy)')
    expect(apiMock.updateScenario).not.toHaveBeenCalled()
  })

  it('Save changes writes a new version of the opened mission; Save as a new mission creates another one and leaves the first alone', async () => {
    list(quickEntry())
    renderLab({ quickScenarioId: 's-quick' })
    await screen.findByLabelText('Primary aircraft steps')
    fireEvent.change(screen.getByLabelText('Size'), { target: { value: '600' } })
    await waitFor(() => expect(lastRequest().size_m).toBe(600))
    click(/SAVE CHANGES \(NEW VERSION\)/)
    await waitFor(() => expect(apiMock.updateScenario).toHaveBeenCalledTimes(1))
    expect(apiMock.updateScenario.mock.calls[0][0]).toBe('s-quick')
    expect(apiMock.createScenario).not.toHaveBeenCalled()
    cleanup(); vi.clearAllMocks()
    list(quickEntry())
    apiMock.createScenario.mockImplementation(async (title, _c, intent) => stored(intent!, 1, title))
    renderLab({ quickScenarioId: 's-quick' })
    await screen.findByLabelText('Primary aircraft steps')
    click(/SAVE AS A NEW MISSION/)
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledTimes(1))
    expect(apiMock.createScenario.mock.calls[0][0]).toBe('Cubbon search (copy)')
    expect(apiMock.updateScenario).not.toHaveBeenCalled()
  })

  it('Deploy follows Scenario Lab: an Operator needs an approved version, with the same wording; an approved one deploys that exact version', async () => {
    list(quickEntry(), approvedEntry())
    renderLab({ isOperator: true, quickScenarioId: 's-quick' })
    await screen.findByLabelText('Primary aircraft steps')
    const blocked = screen.getByRole('button', { name: /DEPLOY TO MISSION CONTROL/ }) as HTMLButtonElement
    expect(blocked.disabled).toBe(true)
    expect(blocked.title).toMatch(/Operators need General approval of this exact version: submit it for review first\./)
    cleanup(); vi.clearAllMocks(); list(quickEntry(), approvedEntry())
    const deployed = vi.fn()
    apiMock.scenarioDetail.mockResolvedValue({ id: 's-approved', owner_user_id: 'u', title: 'Approved ridge', created_at: 't', updated_at: 't', versions: [approvedEntry().latest_version] } as never)
    renderLab({ isOperator: true, quickScenarioId: 's-approved', onMissionDeployed: deployed })
    await screen.findByLabelText('Primary aircraft steps')
    const ok = screen.getByRole('button', { name: /^DEPLOY TO MISSION CONTROL$/ }) as HTMLButtonElement
    await waitFor(() => expect(ok.disabled).toBe(false))
    fireEvent.click(ok)
    await waitFor(() => expect(apiMock.deployScenario).toHaveBeenCalledWith('s-approved', 's-approved-v2'))
    expect(apiMock.updateScenario).not.toHaveBeenCalled()
  })

  it('the General page "Edit in Quick Mission" (the deep link) opens the same selection as picking it in the list', async () => {
    list(quickEntry())
    renderLab({ quickScenarioId: 's-quick' })
    expect((await screen.findByLabelText('Editing')).textContent).toMatch(/Editing: Cubbon search, version 3/)
    await waitFor(() => expect(apiMock.quickMission).toHaveBeenCalled())
    const viaLink = lastRequest()
    cleanup(); vi.clearAllMocks(); list(quickEntry())
    renderLab()
    await screen.findByText(/verified aircraft available/)
    pickOne('Cubbon search')
    await screen.findByLabelText('Editing')
    await waitFor(() => expect(apiMock.quickMission).toHaveBeenCalled())
    expect(lastRequest()).toEqual(viaLink)
  })

  it('warns before unsaved changes are thrown away: KEEP EDITING keeps them, DISCARD AND OPEN opens the other mission', async () => {
    list(quickEntry(), approvedEntry())
    renderLab({ quickScenarioId: 's-quick' })
    await screen.findByLabelText('Primary aircraft steps')
    fireEvent.change(screen.getByLabelText('Size'), { target: { value: '700' } })
    await waitFor(() => expect(lastRequest().size_m).toBe(700))
    await waitFor(() => expect((screen.getByLabelText('Editing')).textContent).toMatch(/unsaved changes/))
    pickOne('Approved ridge')
    const warning = await screen.findByLabelText('Unsaved changes')
    expect(warning.textContent).toMatch(/changes that are not saved/)
    fireEvent.click(within(warning).getByRole('button', { name: 'KEEP EDITING' }))
    expect(screen.queryByLabelText('Unsaved changes')).toBeNull()
    expect(screen.getByLabelText('Editing').textContent).toMatch(/Cubbon search/)
    pickOne('Approved ridge')
    fireEvent.click(within(await screen.findByLabelText('Unsaved changes')).getByRole('button', { name: 'DISCARD AND OPEN' }))
    await waitFor(() => expect(screen.getByLabelText('Editing').textContent).toMatch(/Editing: Approved ridge, version 2/))
  })

  it('the assistant lists the saved missions and fills the form from one by name, without saving or deploying anything', async () => {
    list(quickEntry(), labEntry())
    renderLab()
    await screen.findByRole('list', { name: 'Saved missions' })
    await screen.findByText(/verified aircraft available/)
    click('Open Scenario Lab assistant')
    say('which saved missions do I have')
    await waitFor(() => expect(lastReply()).toMatch(/You have 2 saved missions: Cubbon search \(v3\), Harbour patrol \(Lab\) \(v4\)/))
    say('open the saved mission cubbon search')
    await waitFor(() => expect(lastReply()).toMatch(/Opened Cubbon search \(version 3\) in the form\. I did not save or deploy anything\./))
    expect((await screen.findByLabelText('Editing')).textContent).toMatch(/Editing: Cubbon search, version 3/)
    say('open the saved mission harbour patrol')
    await waitFor(() => expect(lastReply()).toMatch(/built in Scenario Lab, so Quick Mission can't edit it/))
    say('open the saved mission nothing like it')
    await waitFor(() => expect(lastReply()).toMatch(/I found no saved mission called "nothing like it"/))
    expect(apiMock.createScenario).not.toHaveBeenCalled(); expect(apiMock.updateScenario).not.toHaveBeenCalled(); expect(apiMock.deployScenario).not.toHaveBeenCalled()
  })

  it('Clear returns to an empty form and the opened mission is not changed', async () => {
    list(quickEntry())
    renderLab({ quickScenarioId: 's-quick' })
    await screen.findByLabelText('Primary aircraft steps')
    fireEvent.change(screen.getByLabelText('Size'), { target: { value: '650' } })
    await waitFor(() => expect(lastRequest().size_m).toBe(650))
    await new Promise(resolve => setTimeout(resolve, 700))                                      // the unsaved draft is kept locally after 400 ms
    click(/^CLEAR$/)
    await waitFor(() => expect(screen.queryByLabelText('Editing')).toBeNull())
    expect(screen.queryByText(/Restored your unsaved draft/)).toBeNull()                       // Clear throws the kept draft away too
    expect((screen.getByLabelText('Mission type') as HTMLSelectElement).value).toBe('')
    expect(apiMock.updateScenario).not.toHaveBeenCalled(); expect(apiMock.createScenario).not.toHaveBeenCalled()
  })
})

describe('Quick Mission with any number of aircraft', () => {
  const setCount = (value: string) => fireEvent.change(screen.getByLabelText('Number of aircraft'), { target: { value } })
  const rowsShown = () => within(screen.getByRole('list', { name: 'Aircraft list' })).getAllByRole('listitem')

  it.each([1, 2, 5, 12])('plans %i aircraft with one row each, whatever is running', async count => {
    await quickSpiralAtClick()                                                    // two aircraft run in this fixture
    setCount(String(count))
    await waitFor(() => expect(lastRequest().drone_count).toBe(count))
    await waitFor(() => expect(rowsShown()).toHaveLength(count))
    expect((screen.getByLabelText('Role of aircraft 1') as HTMLSelectElement).value).toBe('primary')
    if (count > 1) expect((screen.getByLabelText(`Role of aircraft ${count}`) as HTMLSelectElement).value).toBe('decoy')
    await waitFor(() => expect(rowsShown()[count - 1].textContent).toContain(`UAV-${count} (${count === 1 ? 'primary' : 'decoy'}) line`))   // the plain line per aircraft
    expect(screen.queryByRole('alert')).toBeNull()                                 // planning is never blocked by what runs or by the capacity
  })

  it('asks "Did you mean 100 aircraft?" once above 50 (a typo guard, not a limit), then plans 100 rows', async () => {
    await quickSpiralAtClick()
    setCount('100')
    const guard = await screen.findByRole('alertdialog', { name: 'Check the number of aircraft' })
    expect(guard.textContent).toMatch(/Did you mean 100 aircraft\?/)
    expect(lastRequest().drone_count).toBe(1)                                      // nothing is planned until the answer
    fireEvent.click(within(guard).getByRole('button', { name: 'NO, KEEP 1' }))
    expect(screen.queryByRole('alertdialog', { name: 'Check the number of aircraft' })).toBeNull()
    expect((screen.getByLabelText('Number of aircraft') as HTMLInputElement).value).toBe('1')
    setCount('100')
    fireEvent.click(within(await screen.findByRole('alertdialog', { name: 'Check the number of aircraft' })).getByRole('button', { name: 'YES, 100 AIRCRAFT' }))
    await waitFor(() => expect(lastRequest().drone_count).toBe(100))
    await waitFor(() => expect(rowsShown()).toHaveLength(100))
    setCount('3'); await waitFor(() => expect(lastRequest().drone_count).toBe(3))
    setCount('100')                                                                // the same number is not asked about twice
    await waitFor(() => expect(lastRequest().drone_count).toBe(100))
    expect(screen.queryByRole('alertdialog', { name: 'Check the number of aircraft' })).toBeNull()
    setCount('50'); await waitFor(() => expect(lastRequest().drone_count).toBe(50))   // 50 and below never ask
  })

  it('changing the count adds or removes rows without losing what was typed in the other rows', async () => {
    await quickSpiralAtClick()
    setCount('3')
    await waitFor(() => expect(rowsShown()).toHaveLength(3))
    fireEvent.change(screen.getByLabelText('Name of aircraft 2'), { target: { value: 'East' } })
    fireEvent.change(screen.getByLabelText('Role of aircraft 2'), { target: { value: 'standby' } })
    fireEvent.click(screen.getByLabelText('Advanced settings of aircraft 3'))
    fireEvent.change(screen.getByLabelText('Altitude of aircraft 3'), { target: { value: '70' } })
    fireEvent.change(screen.getByLabelText('Speed of aircraft 3'), { target: { value: '6' } })
    fireEvent.change(screen.getByLabelText('Start offset of aircraft 3'), { target: { value: '20' } })
    await waitFor(() => expect(lastRequest().aircraft).toEqual([
      { name: null, role: null, altitude_m: null, speed_mps: null, start_offset_m: null },
      { name: 'East', role: 'standby', altitude_m: null, speed_mps: null, start_offset_m: null },
      { name: null, role: null, altitude_m: 70, speed_mps: 6, start_offset_m: 20 }]))
    setCount('5')
    await waitFor(() => expect(rowsShown()).toHaveLength(5))
    expect((screen.getByLabelText('Name of aircraft 2') as HTMLInputElement).value).toBe('East')
    expect((screen.getByLabelText('Altitude of aircraft 3') as HTMLInputElement).value).toBe('70')
    setCount('2')
    await waitFor(() => expect(rowsShown()).toHaveLength(2))
    expect((screen.getByLabelText('Role of aircraft 2') as HTMLSelectElement).value).toBe('standby')
    setCount('3')                                                                  // the removed row comes back exactly as typed
    await waitFor(() => expect(rowsShown()).toHaveLength(3))
    expect((screen.getByLabelText('Altitude of aircraft 3') as HTMLInputElement).value).toBe('70')
    expect((screen.getByLabelText('Start offset of aircraft 3') as HTMLInputElement).value).toBe('20')
  })

  it('saves the rows in the quick source and restores them when the mission is reopened (typed stays typed, auto stays auto)', async () => {
    await quickSpiralAtClick()
    setCount('2')
    await waitFor(() => expect(rowsShown()).toHaveLength(2))
    fireEvent.change(screen.getByLabelText('Name of aircraft 2'), { target: { value: 'Wing' } })
    fireEvent.click(screen.getByLabelText('Advanced settings of aircraft 2'))
    fireEvent.change(screen.getByLabelText('Altitude of aircraft 2'), { target: { value: '65' } })
    await waitFor(() => expect(lastRequest().aircraft?.[1]).toMatchObject({ name: 'Wing', altitude_m: 65 }))
    await screen.findByLabelText('Primary aircraft steps')
    click(/SAVE DRAFT/)
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledTimes(1))
    const intent = apiMock.createScenario.mock.calls[0][2] as ScenarioIntent
    expect(intent.quick_source?.drone_count).toBe(2)
    expect(intent.quick_source?.aircraft).toEqual([
      { name: null, role: null, altitude_m: null, speed_mps: null, start_offset_m: null },
      { name: 'Wing', role: null, altitude_m: 65, speed_mps: null, start_offset_m: null }])
    cleanup(); apiMock.quickMission.mockClear()
    store = stored(intent, 1, 'Two aircraft')
    renderLab({ quickScenarioId: 'scenario-1' })
    await waitFor(() => expect(apiMock.quickMission).toHaveBeenCalled())
    await waitFor(() => expect(rowsShown()).toHaveLength(2))
    expect((screen.getByLabelText('Name of aircraft 2') as HTMLInputElement).value).toBe('Wing')
    expect((screen.getByLabelText('Altitude of aircraft 2') as HTMLInputElement).value).toBe('65')
    expect((screen.getByLabelText('Name of aircraft 1') as HTMLInputElement).value).toBe('')            // an untyped name stays the default (placeholder UAV-1)
    expect(lastRequest().aircraft?.[1]).toMatchObject({ name: 'Wing', role: null, altitude_m: 65, speed_mps: null })
  })

  it('when a deploy needs more aircraft than this machine runs, the plain sentence stays with "activate the first M" and "deploy as a plan only"', async () => {
    const sentence = 'This mission has 12 aircraft, this machine runs up to 5 reliably. You can start the first 5 aircraft, activate fewer, or still deploy as a plan only.'
    const deployed = vi.fn()
    apiMock.deployScenario.mockImplementation(async (_id, _version, mode) => {
      if (mode === 'plan_only') return { id: 'deployment-plan', scenario_id: 'scenario-1', plan_only: true } as never
      throw Object.assign(new Error(sentence), { status: 409, detail: { code: 'capacity_exceeded', message: sentence, capacity: 5, required_uav_count: 12 } })
    })
    renderLab({ onMissionDeployed: deployed })
    await pickType(/Search spiral/)
    click('CLICK THE MAP')
    await screen.findByLabelText('Primary aircraft steps')
    setCount('12')
    await waitFor(() => expect(lastRequest().drone_count).toBe(12))
    const deploy = screen.getByRole('button', { name: /SAVE AND DEPLOY TO MISSION CONTROL/ }) as HTMLButtonElement
    await waitFor(() => expect(deploy.disabled).toBe(false))                       // 12 planned aircraft with 2 running do not block the button
    fireEvent.click(deploy)
    expect(await screen.findByText(new RegExp(sentence.slice(0, 60)))).toBeTruthy()
    expect(apiMock.createScenario).toHaveBeenCalledTimes(1)                         // the mission was saved; nothing was dropped
    expect(deployed).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'ACTIVATE THE FIRST 5 AIRCRAFT' }))
    await waitFor(() => expect(apiMock.activateFleet).toHaveBeenCalledWith(5))
    fireEvent.click(screen.getByRole('button', { name: 'DEPLOY AS A PLAN ONLY' }))
    await waitFor(() => expect(apiMock.deployScenario).toHaveBeenLastCalledWith('scenario-1', 'version-1', 'plan_only'))
    await waitFor(() => expect(deployed).toHaveBeenCalledTimes(1))
    expect(deployed.mock.calls[0][1]).toMatch(/Deployed as a plan only: no aircraft was checked or started/)
  })

  it('the assistant changes one aircraft role on the form ("make UAV-7 standby") and never saves or deploys', async () => {
    await quickSpiralAtClick()
    setCount('12')
    await waitFor(() => expect(rowsShown()).toHaveLength(12))
    click('Open Scenario Lab assistant')
    say('make UAV-7 standby')
    await waitFor(() => expect(lastReply()).toMatch(/UAV-7 is now standby in the draft\..*Nothing was saved, deployed or started\./))
    expect((screen.getByLabelText('Role of aircraft 7') as HTMLSelectElement).value).toBe('standby')
    await waitFor(() => expect(lastRequest().aircraft?.[6]?.role).toBe('standby'))
    say('make UAV-40 decoy')
    await waitFor(() => expect(lastReply()).toMatch(/This mission has 12 aircraft, so there is no UAV-40/))
    expect(apiMock.createScenario).not.toHaveBeenCalled(); expect(apiMock.deployScenario).not.toHaveBeenCalled()
  })
})
