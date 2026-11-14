/** Front-end half of the Scenario Lab -> Mission Control workflow audit (mocked API, mocked map). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ScenarioWorkspace } from './ScenarioWorkspace'
import { api } from '../services/api'
import type { MissionDeployment, ScenarioConfig, ScenarioDetail, ScenarioIntent } from '../services/auth'
import type { Px4TelemetryVehicle } from '../types'
import type { AircraftPlan, GeneratedRoute, OperatingArea } from './missionPlan'

vi.mock('../services/api', () => ({
  api: {
    scenarioList: vi.fn(), myScenarioRequests: vi.fn(), px4Telemetry: vi.fn(), createScenario: vi.fn(), updateScenario: vi.fn(),
    submitScenario: vi.fn(), activateFleet: vi.fn(), scenarioAssistant: vi.fn(), previewRoute: vi.fn(), fleetStatus: vi.fn(), deployScenario: vi.fn(), scenarioDetail: vi.fn(), health: vi.fn(), planningLimits: vi.fn(),
    simulationLocation: vi.fn(), relocateSimulation: vi.fn(),
  },
}))
vi.mock('./PlanMapEditor', () => ({
  PlanMapEditor: ({ scope, aircraft, onAreaChange, onAircraftChange }: {
    scope: 'area' | 'aircraft'; aircraft?: AircraftPlan
    onAreaChange: (area: OperatingArea | null) => void; onAircraftChange: (patch: Partial<AircraftPlan>) => void
  }) => scope === 'area'
    ? <button type="button" onClick={() => onAreaChange({ kind: 'circle', centre: { latitude: 12.975, longitude: 77.596 }, radius_m: 400, vertices: [] })}>SET TEST AREA</button>
    : <div>
      <button type="button" onClick={() => onAircraftChange({ start_location: { latitude: 12.9716, longitude: 77.5946 } })}>SET TEST START</button>
      <button type="button" onClick={() => onAircraftChange({ parameters: { ...aircraft?.parameters, points: [{ latitude: 12.973, longitude: 77.595 }] } })}>SET TEST POINTS</button>
    </div>,
}))

// These tests drive whole multi-step workflows; give them headroom when the full suite runs in parallel.
vi.setConfig({ testTimeout: 40000 })

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
const route: GeneratedRoute = {
  generator: 'waypoint_route', generator_version: 1, length_m: 900, duration_s: 240, assumptions: 'flat', events: [],
  waypoints: [
    { seq: 0, latitude: 12.9716, longitude: 77.5946, altitude_m: 30, speed_mps: 5, hover_s: 0, action: 'takeoff' },
    { seq: 1, latitude: 12.973, longitude: 77.595, altitude_m: 30, speed_mps: 5, hover_s: 0, action: 'waypoint' },
    { seq: 2, latitude: 12.9716, longitude: 77.5946, altitude_m: 0, speed_mps: 5, hover_s: 0, action: 'land' },
  ],
}

let store: ScenarioDetail | null = null
const stored = (intent: ScenarioIntent, number: number, title: string): ScenarioDetail => ({
  id: 'scenario-1', owner_user_id: 'u', title, created_at: 't', updated_at: 't',
  versions: [{ id: `version-${number}`, version_number: number, config, intent, config_hash: `hash-${number}`, created_at: 't', sandbox_run_id: null, approval: null }],
})
const approve = () => { store!.versions[0].approval = { id: 'approval-1', status: 'APPROVED', reason: null, submitted_at: 't', reviewed_at: 't', reviewed_by: 'g' } as never }

const countInput = () => screen.getByLabelText(/HOW MANY UAVS ARE REQUIRED/) as HTMLInputElement
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }))
const lastReply = () => {
  const messages = Array.from(document.querySelectorAll('.scenario-assistant-messages p.assistant'))
  return messages[messages.length - 1]?.textContent ?? ''
}
const say = (text: string) => {
  fireEvent.change(screen.getByLabelText('Message Scenario Lab assistant'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
}

function renderLab(props: { isOperator?: boolean; onMissionDeployed?: (deployment: MissionDeployment) => void; onLoadConfig?: (value: ScenarioConfig) => void } = {}) {
  return render(<ScenarioWorkspace userId="workflow" config={config} onLoadConfig={props.onLoadConfig ?? vi.fn()} canManage
    isOperator={props.isOperator} onMissionDeployed={props.onMissionDeployed ?? vi.fn()} />)
}

async function sectionA(count: number) {
  fireEvent.change(await screen.findByLabelText('SCENARIO TITLE'), { target: { value: 'Workflow plan' } })
  fireEvent.click(screen.getByRole('checkbox', { name: /Custom Mission/ }))
  click('SET TEST AREA')
  fireEvent.change(screen.getByLabelText(/MAX DISTANCE FROM START/), { target: { value: '1500' } })
  fireEvent.change(screen.getByLabelText(/MAX ALTITUDE/), { target: { value: '100' } })
  fireEvent.change(countInput(), { target: { value: String(count) } })
  click(/ACTIVATE AND VERIFY PX4/)
  expect(await screen.findByText(new RegExp(`Verified ${count} PX4/Gazebo instances`))).toBeTruthy()
}

async function configureAircraft() {
  fireEvent.change(screen.getByLabelText(/TASK TYPE/), { target: { value: 'waypoint_route' } })
  click('SET TEST START')
  click('SET TEST POINTS')
  fireEvent.change(screen.getByLabelText('ALTITUDE (m) *'), { target: { value: '30' } })
  fireEvent.change(screen.getByLabelText('SPEED (m/s) *'), { target: { value: '5' } })
  click(/GENERATE \/ PREVIEW ROUTE/)
  await screen.findByLabelText('Route preview')
}

async function completeToReview(count: number) {
  await sectionA(count)
  for (let index = 0; index < count; index += 1) {
    await configureAircraft()
    click(index === count - 1 ? /CONFIRM ALL AIRCRAFT MISSIONS/ : /AND CONTINUE/)
  }
}

describe('Scenario Lab workflow (schema 2)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    store = null
    apiMock.scenarioList.mockImplementation(async () => (store ? [{ ...store, latest_version: store.versions[0] }] : []) as never)
    apiMock.myScenarioRequests.mockResolvedValue([])
    apiMock.px4Telemetry.mockResolvedValue({ vehicles: [vehicle(1), vehicle(2)] } as never)
    apiMock.fleetStatus.mockResolvedValue({ capacity: 5, verified_count: 2, manager_error: null,
      instances: [1, 2].map(id => ({ aircraft_id: `px4-${id}`, instance_number: id - 1, system_id: id, gazebo_model: `x500_${id - 1}`, verified: true })) } as never)
    apiMock.createScenario.mockImplementation(async (title, _config, intent) => (store = stored(intent!, 1, title)))
    apiMock.updateScenario.mockImplementation(async (_id, title, _config, intent) => (store = stored(intent!, (store?.versions[0].version_number ?? 0) + 1, title)))
    apiMock.activateFleet.mockImplementation(async count => ({
      required_uav_count: count, activated_uav_count: count, assigned_uav_count: 0, request_id: 'r', verified: true,
      vehicles: Array.from({ length: count }, (_, i) => ({ aircraft_id: `px4-${i + 1}`, instance_number: i, system_id: i + 1, gazebo_model: `x500_${i}`, px4_status: 'connected', gazebo_status: 'connected', verified: true })),
    }))
    apiMock.previewRoute.mockResolvedValue({ route, summary: { length_m: 900, duration_s: 240, waypoints: 3 } })
    apiMock.planningLimits.mockResolvedValue({ schema_version: 2, limits: { max_flight_s: 1200, max_flight_distance_m: 15000 } } as never)
    apiMock.scenarioDetail.mockImplementation(async () => store as never)
    apiMock.health.mockResolvedValue({ status: 'ready', components: {} } as never)
    apiMock.simulationLocation.mockRejectedValue(new Error('not needed here'))
    apiMock.deployScenario.mockResolvedValue({ id: 'deployment-1', scenario_id: 'scenario-1' } as never)
    apiMock.scenarioAssistant.mockRejectedValue(new Error('AI disabled in tests'))
  })
  afterEach(() => { cleanup(); vi.clearAllMocks() })

  it('walks one aircraft from a new draft to a deployed mission, with the assistant agreeing with Section C at every step', async () => {
    const deployed = vi.fn()
    renderLab({ onMissionDeployed: deployed })
    click('Open Scenario Lab assistant')
    await completeToReview(1)

    // Three sections only; the review (Section C) carries one line about the Attack Lab and no attack details.
    expect(document.querySelector('.scenario-fault-list')).toBeNull()
    expect(screen.queryByLabelText(/SUPPORTED SOFTWARE-ONLY FAULT/)).toBeNull()
    expect(screen.getByText(/GPS attack experiments are designed in the/).textContent).toBe('GPS attack experiments are designed in the Attack Lab after saving.')
    expect(document.body.textContent).not.toMatch(/Section D|SECTION D/)
    expect(document.body.textContent).not.toMatch(/UAV-0[1-4]/)
    // Section C shows the stored waypoints and an empty checklist; the assistant lists only the unsaved state.
    expect(within(screen.getByLabelText('px4-1 stored waypoints')).getAllByRole('row')).toHaveLength(4)
    expect(screen.getByText('SCENARIO INPUTS READY FOR DRAFT REVIEW')).toBeTruthy()
    say('is everything ready?')
    await waitFor(() => expect(lastReply()).toMatch(/Not ready yet[\s\S]*Save the draft first/))
    expect(lastReply().match(/•/g)).toHaveLength(1)
    expect(screen.queryByRole('button', { name: /DEPLOY TO MISSION CONTROL/ })).toBeNull()  // nothing saved yet
    expect(screen.getByText(/After you save, the next action appears here: Deploy to Mission Control\./)).toBeTruthy()

    click('SAVE SCENARIO DRAFT')
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledOnce())
    const intent = apiMock.createScenario.mock.calls[0][2]!
    expect(intent.schema_version).toBe(2)
    expect(intent.mission_plan!.workflow_stage).toBe('review')
    await waitFor(() => expect((screen.getByRole('button', { name: /DEPLOY TO MISSION CONTROL/ }) as HTMLButtonElement).disabled).toBe(false))
    say('can I deploy?')
    await waitFor(() => expect(lastReply()).toMatch(/no blockers/))

    click(/DEPLOY TO MISSION CONTROL/)
    await waitFor(() => expect(apiMock.deployScenario).toHaveBeenCalledWith('scenario-1', 'version-1'))
    await waitFor(() => expect(deployed).toHaveBeenCalledOnce())
    expect(apiMock.scenarioDetail).toHaveBeenCalledWith('scenario-1')  // the latest version is refetched before deploying
  })

  it('walks two aircraft in order straight to Review and keeps the checklist consistent', async () => {
    renderLab()
    await completeToReview(2)
    expect(document.querySelector('.scenario-fault-list')).toBeNull()
    expect(screen.getByText('SCENARIO INPUTS READY FOR DRAFT REVIEW')).toBeTruthy()
    expect(screen.getByText(/px4-2 - standby/)).toBeTruthy()
    expect(screen.getByText(/px4-1 - primary/)).toBeTruthy()
  })

  it('Section C is a pointer to the Attack Lab: confirming aircraft goes to Review and saves no fault profiles', async () => {
    const onLoadConfig = vi.fn()
    renderLab({ onLoadConfig })
    await completeToReview(1)
    expect(screen.getByText('SCENARIO INPUTS READY FOR DRAFT REVIEW')).toBeTruthy()
    expect(screen.getAllByText(/Attack Lab/).length).toBeGreaterThan(0)
    click('SAVE SCENARIO DRAFT')
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledOnce())
    expect(apiMock.createScenario.mock.calls[0][2]!.uav_fault_assignments).toEqual([])
    expect(onLoadConfig).not.toHaveBeenCalled()
  })

  it('editing route inputs or mission limits clears the route and the confirmation', async () => {
    renderLab()
    await sectionA(1)
    await configureAircraft()
    const confirm = () => screen.getByRole('button', { name: /CONFIRM ALL AIRCRAFT MISSIONS/ }) as HTMLButtonElement
    expect(confirm().disabled).toBe(false)
    fireEvent.change(screen.getByLabelText('SPEED (m/s) *'), { target: { value: '6' } })
    expect(screen.queryByLabelText('Route preview')).toBeNull()
    expect(confirm().disabled).toBe(true)
    click(/GENERATE \/ PREVIEW ROUTE/)
    await screen.findByLabelText('Route preview')
    fireEvent.change(screen.getByLabelText(/MAX ALTITUDE/), { target: { value: '60' } })
    expect(screen.queryByLabelText('Route preview')).toBeNull()
    expect(confirm().disabled).toBe(true)
  })

  it('flags a task type the selected objectives no longer allow, as the backend does', async () => {
    renderLab()
    click('Open Scenario Lab assistant')
    await sectionA(1)
    fireEvent.click(screen.getByRole('checkbox', { name: /Area Surveillance/ }))
    fireEvent.change(screen.getByLabelText(/TASK TYPE/), { target: { value: 'coverage_survey' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /Area Surveillance/ }))
    const checklist = document.querySelector('.scenario-review-validation')!.textContent ?? ''
    expect(checklist).toMatch(/px4-1: the Coverage survey task is not allowed for the selected objectives\./)
    say('what is missing?')
    await waitFor(() => expect(lastReply()).toBeTruthy())
    say('is everything ready?')
    await waitFor(() => expect(lastReply()).toContain('px4-1: the Coverage survey task is not allowed for the selected objectives.'))
  })

  const nextAction = () => screen.getByRole('group', { name: 'Next action' })
  const nextActionReady = () => screen.findByRole('group', { name: 'Next action' })
  const reasons = () => Array.from(nextAction().querySelectorAll('[aria-label="Why the next action is unavailable"] li')).map(item => item.textContent)

  it('shows "Saved v1" next to Save, no heading, READY tag or reasons while the deploy button is enabled', async () => {
    renderLab()
    await completeToReview(1)
    expect(document.querySelector('.scenario-save-status')).toBeNull()  // nothing saved yet
    click('SAVE SCENARIO DRAFT')
    const deploy = await within(await nextActionReady()).findByRole('button', { name: /DEPLOY TO MISSION CONTROL/ }) as HTMLButtonElement
    await waitFor(() => expect(deploy.disabled).toBe(false))
    expect(document.querySelector('.scenario-save-status')?.textContent).toBe('Saved v1')
    expect(document.body.textContent).not.toMatch(/NEXT ACTION|Nothing blocks this action/)
    expect(within(nextAction()).queryByText('READY')).toBeNull()
    expect(nextAction().querySelector('[aria-label="Why the next action is unavailable"]')).toBeNull()
    // the save status sits next to the Save button, the deploy button beneath it
    const save = screen.getByRole('button', { name: 'SAVE NEW IMMUTABLE VERSION' })
    expect(save.parentElement!.contains(document.querySelector('.scenario-save-status'))).toBe(true)
    expect(save.parentElement!.nextElementSibling).toBe(nextAction())
  })

  it('shows the estimated duration and length per aircraft next to the aircraft limit and blocks a route over it', async () => {
    apiMock.planningLimits.mockResolvedValue({ schema_version: 2, limits: { max_flight_s: 120, max_flight_distance_m: 500 } } as never)
    renderLab()
    await completeToReview(1)
    const note = screen.getByLabelText('px4-1 endurance')
    expect(note.textContent).toMatch(/Estimated 4\.0 min, 0\.90 km · aircraft limit 2 min, 0\.5 km/)
    expect(within(note).getByRole('alert').textContent).toContain('Route takes about 4 min; the aircraft limit is 2 min. Reduce the area, widen track spacing, or raise speed.')
    expect(within(note).getByRole('alert').textContent).toContain('Route is about 0.9 km long; the aircraft limit is 0.5 km.')
    const checklist = document.querySelector('.scenario-review-validation')!.textContent ?? ''
    expect(checklist).toMatch(/px4-1: Route takes about 4 min; the aircraft limit is 2 min/)
    expect(checklist).toMatch(/NEED ATTENTION/)
  })

  it('keeps the next action visible but disabled, with the exact reason, when there are unsaved edits', async () => {
    renderLab()
    await completeToReview(1)
    click('SAVE SCENARIO DRAFT')
    const deployButton = async () => await within(await nextActionReady()).findByRole('button', { name: /DEPLOY TO MISSION CONTROL/ }) as HTMLButtonElement
    await waitFor(async () => expect((await deployButton()).disabled).toBe(false))
    fireEvent.change(screen.getByLabelText(/MAX DURATION/), { target: { value: '20' } })
    expect((await deployButton()).disabled).toBe(true)
    // Changing a mission limit also clears the generated route, so the checklist reason is listed as well.
    expect(reasons()).toEqual(['Unsaved changes', 'Plan incomplete: 1 item(s) in the checklist above'])
    expect(document.querySelector('.scenario-save-status')?.textContent).toBe('Unsaved changes')
  })

  it('shows the fleet as NOT VERIFIED with a one-click Activate and verify, and keeps the deploy action disabled until it is verified', async () => {
    apiMock.fleetStatus.mockResolvedValue({ capacity: 5, verified_count: 0, instances: [], manager_error: null } as never)
    renderLab()
    await completeToReview(1)
    click('SAVE SCENARIO DRAFT')
    const deploy = await within(await nextActionReady()).findByRole('button', { name: /DEPLOY TO MISSION CONTROL/ }) as HTMLButtonElement
    const status = screen.getByLabelText('Fleet verification')
    await waitFor(() => expect(within(status).getByText('NOT VERIFIED')).toBeTruthy())
    expect(deploy.disabled).toBe(true)
    expect(reasons()).toEqual(['Fleet not freshly verified: run Activate and verify'])
    apiMock.fleetStatus.mockResolvedValue({ capacity: 5, verified_count: 1, manager_error: null, instances: [{ aircraft_id: 'px4-1', instance_number: 0, system_id: 1, gazebo_model: 'x500_0', verified: true }] } as never)
    fireEvent.click(within(status).getByRole('button', { name: 'ACTIVATE AND VERIFY' }))
    await waitFor(() => expect(within(status).getByText('VERIFIED')).toBeTruthy())
    expect(deploy.disabled).toBe(false)
    expect(reasons()).toEqual([])
  })

  it('refetches and retries once on a 409 latest-version conflict, and says so', async () => {
    const deployed = vi.fn()
    renderLab({ onMissionDeployed: deployed })
    await completeToReview(1)
    click('SAVE SCENARIO DRAFT')
    const button = await within(await nextActionReady()).findByRole('button', { name: /DEPLOY TO MISSION CONTROL/ })
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false))
    apiMock.deployScenario.mockRejectedValueOnce(Object.assign(new Error('Only the latest immutable scenario version can be deployed.'), { status: 409, detail: { code: 'version_superseded', message: 'Only the latest immutable scenario version can be deployed.' } }))
    fireEvent.click(button)
    await waitFor(() => expect(deployed).toHaveBeenCalledOnce())
    expect(apiMock.deployScenario).toHaveBeenCalledTimes(2)
    expect(deployed.mock.calls[0][1]).toMatch(/deploy was retried once with the latest version/)
  })

  it('shows the backend message after a second 409 and does not retry again', async () => {
    renderLab()
    await completeToReview(1)
    click('SAVE SCENARIO DRAFT')
    const button = await within(await nextActionReady()).findByRole('button', { name: /DEPLOY TO MISSION CONTROL/ })
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false))
    const conflict = () => Object.assign(new Error('Only the latest immutable scenario version can be deployed.'), { status: 409, detail: { code: 'version_superseded', message: 'x' } })
    apiMock.deployScenario.mockRejectedValueOnce(conflict()).mockRejectedValueOnce(conflict())
    const listCallsBefore = apiMock.scenarioList.mock.calls.length
    fireEvent.click(button)
    const alert = await screen.findByText(/Only the latest immutable scenario version can be deployed\. The deploy was retried once/)
    expect(alert.closest('[role="alert"]')).toBeTruthy()
    expect(apiMock.deployScenario).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(apiMock.scenarioList.mock.calls.length).toBeGreaterThan(listCallsBefore))
  })

  it('operator path: Submit for review tied to the saved version, deploy only when that exact version is approved, and a new version loses approval', async () => {
    const view = renderLab({ isOperator: true })
    await completeToReview(1)
    click('SAVE SCENARIO DRAFT')
    const submit = await within(await nextActionReady()).findByRole('button', { name: /SUBMIT FOR REVIEW/ })
    await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false))
    expect(screen.queryByRole('button', { name: /DEPLOY TO MISSION CONTROL/ })).toBeNull()
    fireEvent.click(submit)
    await waitFor(() => expect(apiMock.submitScenario).toHaveBeenCalledWith('scenario-1', 'version-1'))
    approve()
    click('Open Scenario Lab assistant')
    fireEvent.click(screen.getByRole('button', { name: 'Refresh scenarios' }))
    expect(await screen.findByRole('button', { name: /DEPLOY TO MISSION CONTROL/ })).toBeTruthy()
    fireEvent.change(screen.getByLabelText(/MAX DURATION/), { target: { value: '25' } })
    click(/SAVE NEW IMMUTABLE VERSION/)
    await waitFor(() => expect(apiMock.updateScenario).toHaveBeenCalledOnce())
    await waitFor(() => expect(screen.queryByRole('button', { name: /DEPLOY TO MISSION CONTROL/ })).toBeNull())
    say('can I deploy?')
    await waitFor(() => expect(lastReply()).toMatch(/General approval of this exact version/))
    view.unmount()
  })
})
