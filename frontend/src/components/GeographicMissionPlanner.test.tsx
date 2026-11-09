import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { GeographicMissionObjective, GeographicPoint, GeographicUavAssignment, ScenarioDetail, ScenarioIntent } from '../services/auth'
import type { AIStatus, GeographicMissionProposal } from '../services/ai'
import type { Px4TelemetrySnapshot, Px4TelemetryVehicle } from '../types'
import { GeographicMissionPlanner } from './GeographicMissionPlanner'
import { api } from '../services/api'

vi.mock('../services/api', () => ({
  api: {
    aiStatus: vi.fn(),
    px4Telemetry: vi.fn(),
    scenarioList: vi.fn(),
    createScenario: vi.fn(),
    updateScenario: vi.fn(),
    submitScenario: vi.fn(),
    planGeographicMission: vi.fn(),
  },
}))

vi.mock('./MissionPlannerMap', () => ({
  MissionPlannerMap: ({
    mode, activeAssignmentId, onStartChange, onAddVertex, onAircraftLocationChange, onAddAircraftWaypoint,
  }: {
    mode: string
    activeAssignmentId: string
    onStartChange: (point: GeographicPoint) => void
    onAddVertex: (point: GeographicPoint) => void
    onAircraftLocationChange: (uavId: string, location: 'start_location' | 'destination', point: GeographicPoint) => void
    onAddAircraftWaypoint: (uavId: string, point: GeographicPoint) => void
  }) => <div aria-label="Mock Cesium map">
    <button type="button" onClick={() => onStartChange({ latitude: 12.9716, longitude: 77.5946 })}>MAP START</button>
    {mode === 'draw_zone' && <button type="button" onClick={() => onAddVertex({ latitude: 12.9, longitude: 77.5 })}>MAP VERTEX A</button>}
    {mode === 'draw_zone' && <button type="button" onClick={() => onAddVertex({ latitude: 13.0, longitude: 77.5 })}>MAP VERTEX B</button>}
    {mode === 'draw_zone' && <button type="button" onClick={() => onAddVertex({ latitude: 12.95, longitude: 77.6 })}>MAP VERTEX C</button>}
    {mode === 'set_aircraft_start' && <button type="button" onClick={() => onAircraftLocationChange(activeAssignmentId, 'start_location', { latitude: 12.98, longitude: 77.59 })}>MAP AIRCRAFT START</button>}
    {mode === 'set_aircraft_destination' && <button type="button" onClick={() => onAircraftLocationChange(activeAssignmentId, 'destination', { latitude: 13.02, longitude: 77.62 })}>MAP AIRCRAFT DESTINATION</button>}
    {mode === 'add_aircraft_waypoint' && <button type="button" onClick={() => onAddAircraftWaypoint(activeAssignmentId, { latitude: 13, longitude: 77.61 })}>MAP AIRCRAFT WAYPOINT</button>}
  </div>,
}))

const apiMock = vi.mocked(api)
const aircraft: Px4TelemetryVehicle = {
  id: 'px4-7', system_id: 7, component_id: 1, source: 'px4_mavlink',
  status: 'connected', armed: false, heartbeat: null,
  position: {
    latitude: 12.9716, longitude: 77.5946, altitude_m: 0, relative_altitude_m: 0,
    heading_deg: null, valid: true, coordinate_frame: 'WGS84',
    source_message: 'GLOBAL_POSITION_INT', source_time_boot_ms: 100, received_at: '2026-11-09T00:00:00Z',
  },
  velocity: null, attitude: null, gps_raw: null, imu_raw: null, battery: null, mission: null,
  threat_assessment: { status: 'not_available', reason: 'No validated threat assessment.' },
  last_heartbeat_at: '2026-11-09T00:00:00Z', last_message_at: '2026-11-09T00:00:00Z',
  telemetry_age_seconds: 0, heartbeat_age_seconds: 0, flight_trail: [],
}
const fleet: Px4TelemetrySnapshot = {
  source: 'px4_mavlink', generated_at: '2026-11-09T00:00:00Z',
  transport: {
    status: 'receiving', datagrams_received: 2, frames_decoded: 2, invalid_datagrams: 0,
    last_datagram_at: '2026-11-09T00:00:00Z', last_datagram_age_seconds: 0,
  },
  capabilities: {
    gps_spoof_detection: 'not_available',
    independent_navigation_estimate: 'not_available',
    telemetry_persistence: 'not_available',
  },
  vehicles: [aircraft],
}
const aiStatus: AIStatus = {
  available: true, provider: 'openai', model: 'test-model', reason: null,
  remaining_requests: 4, remaining_output_tokens: 1000,
}
const recommendation: GeographicMissionProposal = {
  summary: 'Review these suggested assignments.',
  assignments: [{
    uav_id: 'px4-7', role: 'area_survey', task: 'Survey Zone 1', zone_id: 'zone-test',
  }],
  missing_information: [], constraints: [], warnings: [],
}
const intent: ScenarioIntent = {
  mission_objectives: ['Area surveying'], proposed_tasks: [], uav_role_suggestions: [],
  operating_area_notes: '', environment_notes: '', constraints: [],
  uav_fault_assignments: [],
  geographic_mission: {
    objective: 'area_survey' as GeographicMissionObjective,
    start_location: { latitude: 12.9716, longitude: 77.5946 },
    destination: null,
    survey_zones: [{
      id: 'zone-test', name: 'Zone 1',
      vertices: [
        { latitude: 12.9, longitude: 77.5 },
        { latitude: 13.0, longitude: 77.5 },
        { latitude: 12.95, longitude: 77.6 },
      ],
    }],
    uav_assignments: [{
      uav_id: 'px4-7', role: 'area_survey', task: 'Survey Zone 1', zone_id: 'zone-test',
    } as GeographicUavAssignment],
    constraints: [], ai_recommendations: [],
  },
}
const saved: ScenarioDetail = {
  id: 'mission-1', owner_user_id: 'operator-1', title: 'North area survey',
  created_at: '2026-11-09T00:00:00Z', updated_at: '2026-11-09T00:00:00Z',
  versions: [{
    id: 'version-1', version_number: 1,
    config: {
      uav_count: 2, task_count: 1, seed: 1, duration: 1, spoofing_type: 'none',
      target_uav: 'UAV-01', spoof_start: 0, spoof_duration: 1, spoof_magnitude: 0,
      response_policy: 'baseline', detector_threshold: null,
    },
    intent, config_hash: 'hash-1', created_at: '2026-11-09T00:00:00Z', approval: null,
  }],
}

describe('GeographicMissionPlanner', () => {
  beforeEach(() => {
    apiMock.scenarioList.mockResolvedValue([])
    apiMock.px4Telemetry.mockResolvedValue(fleet)
    apiMock.aiStatus.mockResolvedValue(aiStatus)
    apiMock.createScenario.mockResolvedValue(saved)
    apiMock.planGeographicMission.mockResolvedValue(recommendation)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('uses map selections, actual PX4 aircraft, and saves a validated geographic draft', async () => {
    render(<GeographicMissionPlanner signedIn canManage />)
    expect(await screen.findByText('1 PX4 AIRCRAFT AVAILABLE')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'SELECT START' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP START' }))
    fireEvent.click(screen.getByRole('button', { name: 'DRAW SURVEY ZONE' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP VERTEX A' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP VERTEX B' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP VERTEX C' }))
    fireEvent.click(screen.getByRole('button', { name: /FINISH ZONE/ }))
    fireEvent.click(screen.getByRole('button', { name: 'ADD AVAILABLE AIRCRAFT' }))
    fireEvent.change(screen.getByPlaceholderText("Describe this aircraft's task"), { target: { value: 'Survey northern boundary' } })
    fireEvent.click(screen.getByRole('button', { name: 'MAP START FOR px4-7' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP AIRCRAFT START' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP DESTINATION FOR px4-7' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP AIRCRAFT DESTINATION' }))
    fireEvent.click(screen.getByRole('button', { name: 'ADD WAYPOINT FOR px4-7' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP AIRCRAFT WAYPOINT' }))
    fireEvent.change(screen.getByLabelText('MISSION NAME'), { target: { value: 'North area survey' } })
    expect(screen.getByText('GEOGRAPHIC INPUTS VALIDATED')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /SAVE MISSION DRAFT/ }))

    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledOnce())
    const [, config, savedIntent] = apiMock.createScenario.mock.calls[0]
    expect(config.spoofing_type).toBe('none')
    expect(savedIntent?.geographic_mission?.start_location).toEqual({ latitude: 12.9716, longitude: 77.5946 })
    expect(savedIntent?.geographic_mission?.survey_zones[0].vertices).toHaveLength(3)
    expect(savedIntent?.geographic_mission?.uav_assignments[0].uav_id).toBe('px4-7')
    expect(savedIntent?.geographic_mission?.uav_assignments[0].start_location).toEqual({ latitude: 12.98, longitude: 77.59 })
    expect(savedIntent?.geographic_mission?.uav_assignments[0].destination).toEqual({ latitude: 13.02, longitude: 77.62 })
    expect(savedIntent?.geographic_mission?.uav_assignments[0].waypoints).toEqual([{ latitude: 13, longitude: 77.61 }])
    expect(await screen.findByText(/Saving does not execute a flight mission/)).toBeTruthy()
  })

  it('requests AI advice without sending geographic coordinates and requires an explicit apply action', async () => {
    render(<GeographicMissionPlanner signedIn canManage />)
    await screen.findByText('1 PX4 AIRCRAFT AVAILABLE')
    fireEvent.click(screen.getByRole('button', { name: 'ADD AVAILABLE AIRCRAFT' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP START FOR px4-7' }))
    fireEvent.click(screen.getByRole('button', { name: 'MAP AIRCRAFT START' }))
    fireEvent.change(screen.getByLabelText('MISSION DESCRIPTION'), { target: { value: 'Survey the north area.' } })
    fireEvent.click(screen.getByRole('button', { name: /REQUEST RECOMMENDATIONS/ }))

    await waitFor(() => expect(apiMock.planGeographicMission).toHaveBeenCalledWith({
      objective: 'area_survey', description: 'Survey the north area.', zones: [],
    }))
    expect(await screen.findByText('ADVISORY PROPOSAL')).toBeTruthy()
    expect(apiMock.createScenario).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'APPLY TO EDITABLE DRAFT FOR REVIEW' }))
    expect(await screen.findByText(/copied into the editable mission/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'CLEAR AIRCRAFT START' })).toBeTruthy()
  })

  it('continues and restores a bounded conversation, and blocks applying incomplete recommendations', async () => {
    apiMock.planGeographicMission
      .mockResolvedValueOnce({
        ...recommendation,
        missing_information: ['Which survey area should be prioritized?'],
      })
      .mockResolvedValueOnce(recommendation)
    const firstRender = render(<GeographicMissionPlanner signedIn canManage userId="operator-1"/>)
    await screen.findByText('1 PX4 AIRCRAFT AVAILABLE')
    fireEvent.change(screen.getByLabelText('MISSION DESCRIPTION'), { target: { value: 'Plan an area survey.' } })
    fireEvent.click(screen.getByRole('button', { name: /REQUEST RECOMMENDATIONS/ }))

    await waitFor(() => expect(apiMock.planGeographicMission).toHaveBeenCalledOnce())
    expect(screen.getByRole('button', { name: 'APPLY TO EDITABLE DRAFT FOR REVIEW' }).hasAttribute('disabled')).toBe(true)
    fireEvent.change(screen.getByLabelText('Answer the follow-up question'), { target: { value: 'Prioritize the northern section.' } })
    fireEvent.click(screen.getByRole('button', { name: /CONTINUE CONVERSATION/ }))

    await waitFor(() => expect(apiMock.planGeographicMission).toHaveBeenCalledTimes(2))
    expect(apiMock.planGeographicMission.mock.calls[1][0].description).toContain(
      'Operator: Plan an area survey.\nAssistant: Review these suggested assignments.\nFollow-up questions:\n- Which survey area should be prioritized?\nOperator: Prioritize the northern section.',
    )
    expect(await screen.findByText('ADVISORY PROPOSAL')).toBeTruthy()
    await waitFor(() => {
      const savedConversation = JSON.parse(window.sessionStorage.getItem('trinetra-geographic-conversation:operator-1') ?? '{}')
      expect(savedConversation.messages).toHaveLength(4)
    })

    firstRender.unmount()
    render(<GeographicMissionPlanner signedIn canManage userId="operator-1"/>)
    expect(await screen.findByRole('log', { name: 'Geographic planning conversation' })).toBeTruthy()
    expect(screen.getByText(/Prioritize the northern section/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'NEW CONVERSATION' }))
    expect(screen.queryByRole('log', { name: 'Geographic planning conversation' })).toBeNull()
    await waitFor(() => expect(window.sessionStorage.getItem('trinetra-geographic-conversation:operator-1')).toContain('"messages":[]'))
  })

  it('stops at eight user turns and requires a new conversation', async () => {
    render(<GeographicMissionPlanner signedIn canManage userId="operator-2"/>)
    await screen.findByText('1 PX4 AIRCRAFT AVAILABLE')
    for (let turn = 1; turn <= 8; turn += 1) {
      fireEvent.change(screen.getByLabelText('MISSION DESCRIPTION'), { target: { value: `Planning detail ${turn}.` } })
      fireEvent.click(screen.getByRole('button', { name: /REQUEST RECOMMENDATIONS|CONTINUE CONVERSATION/ }))
      await waitFor(() => expect(apiMock.planGeographicMission).toHaveBeenCalledTimes(turn))
    }
    expect(await screen.findByText(/reached its eight-turn limit/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'CONTINUE CONVERSATION' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'NEW CONVERSATION' }).hasAttribute('disabled')).toBe(false)
  })

  it('loads the shared saved scenario and reports selections back to Scenario Lab', async () => {
    const summary = {
      id: saved.id, owner_user_id: saved.owner_user_id, title: saved.title,
      created_at: saved.created_at, updated_at: saved.updated_at, latest_version: saved.versions[0],
    }
    const onLoadConfig = vi.fn()
    const onScenarioSelected = vi.fn()
    apiMock.scenarioList.mockResolvedValue([summary])
    render(<GeographicMissionPlanner
      signedIn
      canManage
      sharedScenarioId={saved.id}
      onLoadConfig={onLoadConfig}
      onScenarioSelected={onScenarioSelected}
    />)

    await screen.findByText('1 PX4 AIRCRAFT AVAILABLE')
    expect((screen.getByLabelText('Saved geographic mission') as HTMLSelectElement).value).toBe(saved.id)
    expect((screen.getByLabelText('MISSION NAME') as HTMLInputElement).value).toBe(saved.title)
    expect(onLoadConfig).toHaveBeenCalledWith(saved.versions[0].config)
    expect(onScenarioSelected).toHaveBeenCalledWith(saved.id)
  })

  it('surfaces unavailable PX4 telemetry instead of inventing a fleet', async () => {
    apiMock.px4Telemetry.mockRejectedValue(new Error('PX4 telemetry receiver is not enabled'))
    render(<GeographicMissionPlanner signedIn canManage />)
    expect(await screen.findByText('PX4 telemetry receiver is not enabled')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'ADD AVAILABLE AIRCRAFT' }).hasAttribute('disabled')).toBe(true)
  })
})
