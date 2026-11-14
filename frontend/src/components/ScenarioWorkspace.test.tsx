import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ScenarioWorkspace } from './ScenarioWorkspace'
import { api } from '../services/api'
import type { ScenarioConfig, ScenarioDetail, ScenarioIntent } from '../services/auth'
import type { Px4TelemetrySnapshot, Px4TelemetryVehicle } from '../types'
import type { AircraftPlan, GeneratedRoute, MissionPlan, OperatingArea } from './missionPlan'

vi.mock('../services/api', () => ({
  api: {
    scenarioList: vi.fn(), myScenarioRequests: vi.fn(), px4Telemetry: vi.fn(), createScenario: vi.fn(),
    updateScenario: vi.fn(), submitScenario: vi.fn(), activateFleet: vi.fn(), scenarioAssistant: vi.fn(),
    previewRoute: vi.fn(), fleetStatus: vi.fn(), deployScenario: vi.fn(),
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
      <button type="button" onClick={() => onAircraftChange({ parameters: { ...aircraft?.parameters, points: [{ latitude: 12.973, longitude: 77.595 }, { latitude: 12.974, longitude: 77.596 }] } })}>SET TEST POINTS</button>
      <button type="button" onClick={() => onAircraftChange({ parameters: { ...aircraft?.parameters, drop_point: { latitude: 12.974, longitude: 77.596 } } })}>SET TEST DROP POINT</button>
    </div>,
}))

const apiMock = vi.mocked(api)
const config: ScenarioConfig = {
  uav_count: 4, task_count: 10, seed: 21, duration: 180, spoofing_type: 'none',
  target_uav: 'UAV-02', spoof_start: 15, spoof_duration: 35, spoof_magnitude: 3,
  response_policy: 'adaptive', detector_threshold: null,
}
const vehicle: Px4TelemetryVehicle = {
  id: 'px4-1', system_id: 1, component_id: 1, source: 'px4_mavlink', status: 'connected', armed: false, heartbeat: null,
  position: {
    latitude: 12.9716, longitude: 77.5946, altitude_m: 0, relative_altitude_m: 0, heading_deg: null, valid: true,
    coordinate_frame: 'WGS84', source_message: 'GLOBAL_POSITION_INT', source_time_boot_ms: 100, received_at: '2026-11-09T00:00:00Z',
  },
  velocity: null, attitude: null, gps_raw: null, imu_raw: null, battery: null, mission: null,
  threat_assessment: { status: 'not_available', reason: 'No validated threat assessment.' },
  last_heartbeat_at: '2026-11-09T00:00:00Z', last_message_at: '2026-11-09T00:00:00Z',
  telemetry_age_seconds: 0, heartbeat_age_seconds: 0, flight_trail: [],
}
const fleet: Px4TelemetrySnapshot = {
  source: 'px4_mavlink', generated_at: '2026-11-09T00:00:00Z',
  transport: { status: 'receiving', datagrams_received: 2, frames_decoded: 2, invalid_datagrams: 0, last_datagram_at: '2026-11-09T00:00:00Z', last_datagram_age_seconds: 0 },
  capabilities: { gps_spoof_detection: 'not_available', independent_navigation_estimate: 'not_available', telemetry_persistence: 'not_available' },
  vehicles: [vehicle],
}
const route: GeneratedRoute = {
  generator: 'waypoint_route', generator_version: 1, length_m: 1234.5, duration_s: 300, assumptions: 'Flat-earth tangent plane assumption.',
  waypoints: [
    { seq: 0, latitude: 12.9716, longitude: 77.5946, altitude_m: 30, speed_mps: 5, hover_s: 0, action: 'takeoff' },
    { seq: 1, latitude: 12.973, longitude: 77.595, altitude_m: 30, speed_mps: 5, hover_s: 0, action: 'waypoint' },
    { seq: 2, latitude: 12.9716, longitude: 77.5946, altitude_m: 0, speed_mps: 5, hover_s: 0, action: 'land' },
  ],
  events: [],
}
const dropRoute: GeneratedRoute = {
  ...route, generator: 'payload_dropoff',
  waypoints: [...route.waypoints.slice(0, 2), { seq: 2, latitude: 12.973, longitude: 77.595, altitude_m: 8, speed_mps: 5, hover_s: 20, action: 'payload_release_point' }, { ...route.waypoints[2], seq: 3 }],
  events: [{ type: 'payload_released', aircraft_id: 'px4-1', waypoint_seq: 2, planned_time_s: 61.5, payload_category: 'medical_kit', simulated: true }],
}
const legacyIntent: ScenarioIntent = {
  mission_objectives: ['Infrastructure Inspection'], mission_description: 'legacy description', proposed_tasks: [], uav_role_suggestions: [],
  operating_area_notes: '', environment_notes: '', constraints: [], uav_fault_assignments: [],
  geographic_mission: {
    objective: 'inspection', objectives: ['inspection'], mission_description: 'legacy description', start_location: null, destination: null, survey_zones: [], constraints: [], ai_recommendations: [],
    uav_assignments: [{ uav_id: 'px4-1', role: 'Inspection', task: 'Inspect roof', zone_ids: [], waypoints: [{ latitude: 1, longitude: 2 }], constraints: [], confirmed: true } as never],
  } as never,
}
const saved = (intent: ScenarioIntent): ScenarioDetail => ({
  id: 'scenario-1', owner_user_id: 'operator-1', title: 'Mission intent test', created_at: '2026-11-09T00:00:00Z', updated_at: '2026-11-09T00:00:00Z',
  versions: [{ id: 'version-1', version_number: 1, config, intent, config_hash: 'hash-1', created_at: '2026-11-09T00:00:00Z', sandbox_run_id: null, approval: null }],
})

const countInput = () => screen.getByLabelText(/HOW MANY UAVS ARE REQUIRED/) as HTMLInputElement
const openAssistant = (userId = 'test-user') => {
  render(<ScenarioWorkspace userId={userId} config={config} onLoadConfig={vi.fn()} canManage />)
  fireEvent.click(screen.getByRole('button', { name: 'Open Scenario Lab assistant' }))
}
const say = (text: string) => {
  fireEvent.change(screen.getByLabelText('Message Scenario Lab assistant'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
}
const lastReply = () => {
  const messages = Array.from(document.querySelectorAll('.scenario-assistant-messages p.assistant'))
  return messages[messages.length - 1]?.textContent ?? ''
}
const mutatingApis = () => [apiMock.activateFleet, apiMock.createScenario, apiMock.updateScenario, apiMock.submitScenario, apiMock.previewRoute]
const activateOne = async () => {
  fireEvent.change(countInput(), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: /ACTIVATE AND VERIFY PX4/ }))
  expect(await screen.findByText(/Verified 1 PX4\/Gazebo instances/)).toBeTruthy()
  apiMock.activateFleet.mockClear()
}
const fillWaypointAircraft = () => {
  fireEvent.click(screen.getByRole('checkbox', { name: /Custom Mission/ }))
  fireEvent.change(screen.getByLabelText(/TASK TYPE/), { target: { value: 'waypoint_route' } })
  fireEvent.click(screen.getByRole('button', { name: 'SET TEST START' }))
  fireEvent.click(screen.getByRole('button', { name: 'SET TEST POINTS' }))
  fireEvent.change(screen.getByLabelText('ALTITUDE (m) *'), { target: { value: '30' } })
  fireEvent.change(screen.getByLabelText('SPEED (m/s) *'), { target: { value: '5' } })
}

describe('ScenarioWorkspace (schema 2 mission plan)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    apiMock.scenarioList.mockResolvedValue([])
    apiMock.myScenarioRequests.mockResolvedValue([])
    apiMock.px4Telemetry.mockResolvedValue(fleet)
    apiMock.createScenario.mockImplementation(async (title, _config, intent) => ({ ...saved(intent!), title }))
    apiMock.updateScenario.mockImplementation(async (_id, title, _config, intent) => ({ ...saved(intent!), title }))
    apiMock.activateFleet.mockResolvedValue({
      required_uav_count: 1, activated_uav_count: 1, assigned_uav_count: 0, request_id: 'test-request', verified: true,
      vehicles: [{ aircraft_id: 'px4-1', instance_number: 0, system_id: 1, gazebo_model: 'x500_0', px4_status: 'connected', gazebo_status: 'connected', verified: true }],
    })
    apiMock.previewRoute.mockResolvedValue({ route, summary: { length_m: route.length_m, duration_s: route.duration_s, waypoints: 3 } })
    apiMock.scenarioAssistant.mockRejectedValue(Object.assign(new Error('The OpenAI service is unavailable.'), { detail: { code: 'provider_unavailable' } }))
  })
  afterEach(() => { cleanup(); vi.clearAllMocks() })

  it('Sections A and B expose only fields something consumes', async () => {
    render(<ScenarioWorkspace userId="fields" config={config} onLoadConfig={vi.fn()} canManage />)
    for (const label of [/MAX DISTANCE FROM START/, /MAX ALTITUDE/, /MAX DURATION/, /DESCRIPTION \(optional\)/, /NOTES \(optional/, /HOW MANY UAVS/]) {
      expect(await screen.findByLabelText(label)).toBeTruthy()
    }
    expect(screen.getByRole('checkbox', { name: /Relief Supply Delivery/ })).toBeTruthy()
    for (const gone of ['MISSION AREA', 'OPERATIONAL CONSTRAINTS', 'MISSION DURATION (minutes)']) expect(screen.queryByLabelText(gone)).toBeNull()
    await activateOne()
    for (const label of [/ROLE/, /PRIORITY/, /TASK TYPE/, 'ALTITUDE (m) *', 'SPEED (m/s) *', /LOITER AT DESTINATION/, /RETURN MODE/]) {
      expect(screen.getByLabelText(label)).toBeTruthy()
    }
    for (const gone of ['INDIVIDUAL OBJECTIVE', 'INDIVIDUAL TASK', 'DATA REQUIREMENTS', 'COMPLETION CONDITION', 'EXPECTED RESULT', 'RETURN COMPLETION CONDITION', 'DESTINATION ACTION']) {
      expect(screen.queryByLabelText(gone)).toBeNull()
    }
    expect(screen.getByLabelText(/ROLE/).querySelectorAll('option')).toHaveLength(4)
  })

  it('shows parameters only for the chosen task type and a fixed payload allowlist', async () => {
    render(<ScenarioWorkspace userId="params" config={config} onLoadConfig={vi.fn()} canManage />)
    fireEvent.click(await screen.findByRole('checkbox', { name: /Area Surveillance/ }))
    await activateOne()
    expect(screen.queryByLabelText(/LINE SPACING/)).toBeNull()
    fireEvent.change(screen.getByLabelText(/TASK TYPE/), { target: { value: 'coverage_survey' } })
    expect(screen.getByLabelText(/LINE SPACING/)).toBeTruthy()
    expect(screen.queryByLabelText(/DROP ALTITUDE/)).toBeNull()
    expect(Array.from((screen.getByLabelText(/TASK TYPE/) as HTMLSelectElement).options).map(option => option.value)).not.toContain('payload_dropoff')
    fireEvent.click(screen.getByRole('checkbox', { name: /Relief Supply Delivery/ }))
    fireEvent.change(screen.getByLabelText(/TASK TYPE/), { target: { value: 'payload_dropoff' } })
    expect(screen.queryByLabelText(/LINE SPACING/)).toBeNull()
    for (const label of [/DROP ALTITUDE/, /HOVER DURATION/, /PAYLOAD CATEGORY/]) expect(screen.getByLabelText(label)).toBeTruthy()
    const options = Array.from((screen.getByLabelText(/PAYLOAD CATEGORY/) as HTMLSelectElement).options).map(option => option.value)
    expect(options).toEqual(['', 'relief_supplies', 'medical_kit', 'flotation_device'])
  })

  it('generates a backend route, previews it, and clears it when route inputs change', async () => {
    render(<ScenarioWorkspace userId="route" config={config} onLoadConfig={vi.fn()} canManage />)
    await screen.findByRole('button', { name: 'SET TEST AREA' })
    await activateOne()
    const generate = () => screen.getByRole('button', { name: /GENERATE \/ PREVIEW ROUTE/ }) as HTMLButtonElement
    expect(generate().disabled).toBe(true)
    fillWaypointAircraft()
    expect(generate().disabled).toBe(false)
    expect((screen.getByRole('button', { name: /CONFIRM ALL AIRCRAFT MISSIONS/ }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(generate())
    const preview = await screen.findByLabelText('Route preview')
    expect(preview.textContent).toMatch(/3 waypoints · 1\.23 km · about 5\.0 min/)
    const request = apiMock.previewRoute.mock.calls[0]
    expect(request[1]).toBe('px4-1')
    expect((request[0] as MissionPlan).aircraft[0].task_type).toBe('waypoint_route')
    expect((screen.getByRole('button', { name: /CONFIRM ALL AIRCRAFT MISSIONS/ }) as HTMLButtonElement).disabled).toBe(false)
    fireEvent.change(screen.getByLabelText('ALTITUDE (m) *'), { target: { value: '40' } })
    expect(screen.queryByLabelText('Route preview')).toBeNull()
    expect((screen.getByRole('button', { name: /CONFIRM ALL AIRCRAFT MISSIONS/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows structured route rejections from the backend', async () => {
    apiMock.previewRoute.mockRejectedValueOnce(Object.assign(new Error('Altitude 150 m exceeds the maximum 100 m.'), {
      detail: { code: 'route_invalid', message: 'x', issues: [{ code: 'altitude_over_limit', message: 'Altitude 150 m exceeds the maximum 100 m.' }, { code: 'route_too_far', message: 'A waypoint is 3000 m from the start.' }] },
    }))
    render(<ScenarioWorkspace userId="issues" config={config} onLoadConfig={vi.fn()} canManage />)
    await screen.findByRole('button', { name: 'SET TEST AREA' })
    await activateOne()
    fillWaypointAircraft()
    fireEvent.click(screen.getByRole('button', { name: /GENERATE \/ PREVIEW ROUTE/ }))
    const list = await screen.findByLabelText('Route issues')
    expect(within(list).getAllByRole('listitem').map(item => item.textContent)).toEqual(['Altitude 150 m exceeds the maximum 100 m.', 'A waypoint is 3000 m from the start.'])
    expect(screen.queryByLabelText('Route preview')).toBeNull()
  })

  it('saves schema_version 2 with the generated waypoints and roles, and Section C shows the stored waypoints', async () => {
    render(<ScenarioWorkspace userId="save" config={config} onLoadConfig={vi.fn()} canManage />)
    fireEvent.change(await screen.findByLabelText('SCENARIO TITLE'), { target: { value: 'Route plan' } })
    fireEvent.click(await screen.findByRole('button', { name: 'SET TEST AREA' }))
    await activateOne()
    fillWaypointAircraft()
    fireEvent.click(screen.getByRole('button', { name: /GENERATE \/ PREVIEW ROUTE/ }))
    await screen.findByLabelText('Route preview')
    const table = screen.getByLabelText('px4-1 stored waypoints')
    expect(within(table).getAllByRole('row')).toHaveLength(4)
    expect(within(table).getByText('takeoff')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'SAVE SCENARIO DRAFT' }))
    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledOnce())
    const intent = apiMock.createScenario.mock.calls[0][2]!
    expect(intent.schema_version).toBe(2)
    expect(intent.geographic_mission).toBeNull()
    const aircraft = intent.mission_plan!.aircraft[0]
    expect(aircraft.role).toBe('primary')
    expect(aircraft.route!.waypoints).toEqual(route.waypoints)
    expect(intent.mission_plan!.operating_area).toMatchObject({ kind: 'circle', radius_m: 400 })
    expect(intent.mission_plan!.required_uav_count).toBe(1)
  })

  it('lists backend validation issues when saving is rejected', async () => {
    apiMock.createScenario.mockRejectedValueOnce(Object.assign(new Error('The route is outside the area.'), {
      detail: { code: 'mission_plan_invalid', message: 'x', issues: [{ code: 'route_mismatch', uav_id: 'px4-1', message: 'The stored route does not match the plan parameters; regenerate it.' }] },
    }))
    render(<ScenarioWorkspace userId="save-fail" config={config} onLoadConfig={vi.fn()} canManage />)
    fireEvent.change(await screen.findByLabelText('SCENARIO TITLE'), { target: { value: 'Bad plan' } })
    fireEvent.click(screen.getByRole('button', { name: 'SAVE SCENARIO DRAFT' }))
    const list = await screen.findByLabelText('Validation issues')
    expect(list.textContent).toContain('px4-1: The stored route does not match the plan parameters; regenerate it.')
  })

  it('previews the simulated payload event without any release command', async () => {
    apiMock.previewRoute.mockResolvedValueOnce({ route: dropRoute, summary: { length_m: 900, duration_s: 200, waypoints: 4 } })
    render(<ScenarioWorkspace userId="drop" config={config} onLoadConfig={vi.fn()} canManage />)
    fireEvent.click(await screen.findByRole('checkbox', { name: /Relief Supply Delivery/ }))
    await activateOne()
    fireEvent.change(screen.getByLabelText(/TASK TYPE/), { target: { value: 'payload_dropoff' } })
    fireEvent.click(screen.getByRole('button', { name: 'SET TEST START' }))
    fireEvent.click(screen.getByRole('button', { name: 'SET TEST DROP POINT' }))
    fireEvent.change(screen.getByLabelText(/DROP ALTITUDE/), { target: { value: '8' } })
    fireEvent.change(screen.getByLabelText(/HOVER DURATION/), { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText(/PAYLOAD CATEGORY/), { target: { value: 'medical_kit' } })
    fireEvent.change(screen.getByLabelText('ALTITUDE (m) *'), { target: { value: '30' } })
    fireEvent.change(screen.getByLabelText('SPEED (m/s) *'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: /GENERATE \/ PREVIEW ROUTE/ }))
    const preview = await screen.findByLabelText('Route preview')
    expect(preview.textContent).toMatch(/Simulated payload_released \(medical kit\) at \+61\.5 s — logged event only, no release command is sent/)
  })

  it('opens legacy schema-1 versions read-only without rewriting them', async () => {
    apiMock.scenarioList.mockResolvedValue([{ ...saved(legacyIntent), latest_version: saved(legacyIntent).versions[0] }] as never)
    render(<ScenarioWorkspace userId="legacy" config={config} onLoadConfig={vi.fn()} canManage />)
    await screen.findByRole('option', { name: /Mission intent test/ })
    fireEvent.change(screen.getByLabelText('SAVED SCENARIO'), { target: { value: 'scenario-1' } })
    const banner = await screen.findByLabelText('Legacy version')
    expect(banner.textContent).toMatch(/READ-ONLY/)
    expect(banner.textContent).toContain('px4-1 · Inspection · Inspect roof · 1 stored waypoint(s)')
    expect(screen.queryByText('SECTION A')).toBeNull()
    expect(screen.queryByRole('button', { name: /SAVE/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open Scenario Lab assistant' }))
    say('is everything ready?')
    await waitFor(() => expect(lastReply()).toMatch(/legacy \(schema 1\) version. It is read-only/))
    for (const mocked of mutatingApis()) expect(mocked).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'START A NEW SCHEMA-2 DRAFT' }))
    expect(await screen.findByText('SECTION A')).toBeTruthy()
    expect((screen.getByLabelText('SCENARIO TITLE') as HTMLInputElement).value).toBe('Mission intent test (schema 2)')
    expect((screen.getByRole('checkbox', { name: /Infrastructure Inspection/ }) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText(/DESCRIPTION \(optional\)/) as HTMLTextAreaElement).value).toBe('legacy description')
    expect(apiMock.updateScenario).not.toHaveBeenCalled()  // the legacy version is never rewritten
  })

  describe('assistant on the new model', () => {
    it('readiness mirrors the Section C list and missing-field answers use the new fields', async () => {
      openAssistant('assistant-a')
      say('So everything is ready?')
      await waitFor(() => expect(lastReply()).toMatch(/Not ready yet/))
      const reviewItems = Array.from(document.querySelectorAll('.scenario-review-validation li')).map(item => item.textContent ?? '')
      expect(reviewItems.length).toBeGreaterThan(0)
      for (const item of reviewItems) expect(lastReply()).toContain(item)
      await activateOne()
      fireEvent.change(screen.getByLabelText(/ROLE/), { target: { value: 'primary' } })
      say('what is left for UAV 1?')
      await waitFor(() => expect(lastReply()).toMatch(/still needs/))
      for (const label of ['Task type', 'Start location (map)', 'Cruise altitude (m)', 'Speed (m/s)']) expect(lastReply()).toContain(label)
      expect(lastReply()).not.toMatch(/INDIVIDUAL OBJECTIVE|DATA REQUIREMENTS|COMPLETION CONDITION/)
      expect(lastReply()).toContain('Say "set altitude to 30"')
    })

    it('explains task types including the simulated payload drop-off, and Section C (review)', async () => {
      openAssistant('assistant-b')
      say('what is payload_dropoff?')
      await waitFor(() => expect(lastReply()).toMatch(/simulated payload_released event/))
      expect(lastReply()).toMatch(/No release command is sent/)
      say('explain section C')
      await waitFor(() => expect(lastReply()).toMatch(/Attack Lab/))
      expect(lastReply()).toMatch(/review and validation/)
      expect(lastReply()).toMatch(/Attack Lab after saving/)
    })

    it('typed set commands only propose; CONFIRM applies through the plan and clears the route', async () => {
      render(<ScenarioWorkspace userId="assistant-c" config={config} onLoadConfig={vi.fn()} canManage />)
      await screen.findByRole('button', { name: 'SET TEST AREA' })
      await activateOne()
      fillWaypointAircraft()
      fireEvent.click(screen.getByRole('button', { name: /GENERATE \/ PREVIEW ROUTE/ }))
      await screen.findByLabelText('Route preview')
      fireEvent.click(screen.getByRole('button', { name: 'Open Scenario Lab assistant' }))
      say('set altitude to 45')
      fireEvent.click(await screen.findByRole('button', { name: 'CONFIRM' }).then(button => button))
      await waitFor(() => expect((screen.getByLabelText('ALTITUDE (m) *') as HTMLInputElement).value).toBe('45'))
      expect(screen.queryByLabelText('Route preview')).toBeNull()
      say('set task type of UAV 1 to coverage survey')
      await waitFor(() => expect(lastReply()).toMatch(/not allowed for the selected objectives/))
      expect(screen.queryByRole('button', { name: 'CONFIRM' })).toBeNull()
      fireEvent.click(screen.getByRole('checkbox', { name: /Area Surveillance/ }))
      say('set task type of UAV 1 to coverage survey')
      expect(await screen.findByRole('button', { name: 'CONFIRM' })).toBeTruthy()
      expect((screen.getByLabelText(/TASK TYPE/) as HTMLSelectElement).value).toBe('waypoint_route')
      fireEvent.click(screen.getByRole('button', { name: 'CONFIRM' }))
      await waitFor(() => expect((screen.getByLabelText(/TASK TYPE/) as HTMLSelectElement).value).toBe('coverage_survey'))
    })

    it('rejects parameters that do not belong to the aircraft task type', async () => {
      openAssistant('assistant-d')
      await activateOne()
      say('set drop altitude of UAV 1 to 8')
      await waitFor(() => expect(lastReply()).toMatch(/does not apply|choose the task type first/))
      expect(screen.queryByRole('button', { name: 'CONFIRM' })).toBeNull()
    })

    it('chips are context aware, never call the AI, and input chips only propose', async () => {
      openAssistant('chips')
      const chipNames = () => Array.from(document.querySelectorAll('[aria-label="Assistant options"] button')).map(button => button.textContent ?? '')
      expect(chipNames()).toEqual(['Is everything ready?', "What's still missing?", 'Summarize my mission', 'What can you do?',
        'Explain Section A', 'Set mission objective', 'Set max duration', 'Set UAV count'])
      expect(Array.from((() => { fireEvent.click(screen.getByRole('button', { name: 'Set mission objective' })); return screen.getByLabelText('Mission objective') as HTMLSelectElement })().options).map(option => option.value)).toContain('Relief Supply Delivery')
      fireEvent.click(screen.getByRole('button', { name: 'Set max duration' }))
      fireEvent.change(screen.getByLabelText('Max duration (minutes)'), { target: { value: '25' } })
      fireEvent.click(screen.getByRole('button', { name: 'PROPOSE' }))
      expect(await screen.findByRole('button', { name: 'CONFIRM' })).toBeTruthy()
      expect((screen.getByLabelText(/MAX DURATION/) as HTMLInputElement).value).toBe('')
      expect(Array.from(document.querySelectorAll('[aria-label="Assistant options"] button')).every(button => (button as HTMLButtonElement).disabled)).toBe(true)
      fireEvent.click(screen.getByRole('button', { name: 'CONFIRM' }))
      await waitFor(() => expect((screen.getByLabelText(/MAX DURATION/) as HTMLInputElement).value).toBe('25'))
      await activateOne()
      fireEvent.change(screen.getByLabelText(/ROLE/), { target: { value: 'decoy' } })
      await waitFor(() => expect(chipNames()).toContain('Set task type'))
      expect(chipNames()).toEqual(expect.arrayContaining(['Explain a field', 'Explain a task type', "What's missing for UAV 1?", 'Summarize UAV 1', 'Set altitude / speed / loiter', 'Set role']))
      for (const name of ['Is everything ready?', 'Summarize UAV 1']) fireEvent.click(screen.getByRole('button', { name }))
      await waitFor(() => expect(lastReply()).toMatch(/UAV 1 \(px4-1\)/))
      expect(apiMock.scenarioAssistant).not.toHaveBeenCalled()
    })

    it('sends only unrecognised text to the model and drops invented actions', async () => {
      apiMock.scenarioAssistant.mockResolvedValueOnce({
        reply: 'Deploying now.', source: 'ai', dropped_actions: 0,
        actions: [{ op: 'deploy' }, { op: 'set_assignment_field', uav_index: 1, field: 'start_location', value: '1,2' }],
      })
      openAssistant('assistant-e')
      await activateOne()
      say('thoughts on my plan?')
      await waitFor(() => expect(lastReply()).toContain('Deploying now.'))
      expect(lastReply()).toMatch(/ignored a proposed change/)
      expect(screen.queryByRole('button', { name: 'CONFIRM' })).toBeNull()
      const snapshot = apiMock.scenarioAssistant.mock.calls[0][0].snapshot as { aircraft: { start_selected: boolean }[] }
      expect(snapshot.aircraft[0].start_selected).toBe(false)
      expect(JSON.stringify(apiMock.scenarioAssistant.mock.calls[0][0])).not.toMatch(/12\.97/)
    })

    it('explains quota/billing and transient rate limits differently, and the rule-based assistant keeps working', async () => {
      openAssistant('assistant-quota')
      apiMock.scenarioAssistant.mockRejectedValueOnce(Object.assign(new Error('billing'), { detail: { code: 'provider_insufficient_quota' } }))
      say('tell me a joke')
      await waitFor(() => expect(lastReply()).toMatch(/no remaining credits or billing is not enabled/))
      expect(lastReply()).toMatch(/waiting will not help/)
      apiMock.scenarioAssistant.mockRejectedValueOnce(Object.assign(new Error('slow down'), { detail: { code: 'provider_rate_limited' } }))
      say('tell me another joke')
      await waitFor(() => expect(lastReply()).toMatch(/rate limiting requests right now\. This is temporary/))
      expect(lastReply()).not.toMatch(/credits/)
      say('explain section C')
      await waitFor(() => expect(lastReply()).toMatch(/Attack Lab|abstract-simulator-only/))
      say('is everything ready?')
      await waitFor(() => expect(lastReply()).toMatch(/Not ready yet/))
    })

    it('a pending proposal blocks new input; CANCEL changes nothing; double CONFIRM applies once', async () => {
      let release: (value: Awaited<ReturnType<typeof api.activateFleet>>) => void = () => undefined
      apiMock.activateFleet.mockReturnValueOnce(new Promise(resolve => { release = resolve }))
      openAssistant('assistant-f')
      say('I need 1 UAV.')
      const confirm = await screen.findByRole('button', { name: 'CONFIRM' })
      expect((screen.getByLabelText('Message Scenario Lab assistant') as HTMLInputElement).disabled).toBe(true)
      expect(countInput().value).toBe('')
      fireEvent.click(confirm)
      fireEvent.click(confirm)
      expect(apiMock.activateFleet).toHaveBeenCalledTimes(1)
      release({
        required_uav_count: 1, activated_uav_count: 1, assigned_uav_count: 0, request_id: 'r', verified: true,
        vehicles: [{ aircraft_id: 'px4-1', instance_number: 0, system_id: 1, gazebo_model: 'x500_0', px4_status: 'connected', gazebo_status: 'connected', verified: true }],
      })
      expect(await screen.findByText(/Fleet activation verified/)).toBeTruthy()
      say('I need 3 UAVs.')
      fireEvent.click(await screen.findByRole('button', { name: 'CANCEL' }))
      expect(countInput().value).toBe('1')
      expect(apiMock.activateFleet).toHaveBeenCalledTimes(1)
    })

    it('keeps conversations in memory only: empty after remount, kept across collapse, cleared on logout', async () => {
      sessionStorage.setItem('trinetra-scenario-chat:memory:legacy', JSON.stringify([{ role: 'user', content: 'old chat' }]))
      openAssistant('memory')
      expect(screen.queryByText('old chat')).toBeNull()
      expect(sessionStorage.getItem('trinetra-scenario-chat:memory:legacy')).toBeNull()
      say('explain section C')
      await waitFor(() => expect(lastReply()).toMatch(/Attack Lab/))
      fireEvent.click(screen.getByRole('button', { name: 'Collapse assistant' }))
      fireEvent.click(screen.getByRole('button', { name: 'Open Scenario Lab assistant' }))
      expect(lastReply()).toMatch(/Attack Lab/)
      expect(JSON.stringify(Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.getItem(sessionStorage.key(index) ?? '')))).not.toContain('Attack Lab tab')
      window.dispatchEvent(new Event('trinetra:logout'))
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Collapse assistant' })).toBeNull())
      cleanup()
      openAssistant('memory')
      expect(document.querySelectorAll('.scenario-assistant-messages p.assistant').length).toBe(0)
    })
  })
})
