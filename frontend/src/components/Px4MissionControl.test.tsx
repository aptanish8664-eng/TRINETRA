import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Px4MissionControl } from './Px4MissionControl'
import type { Px4TelemetrySnapshot } from '../types'
import { api } from '../services/api'

vi.mock('../services/api', () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number) { super('API error'); this.status = status } },
  api: { px4Telemetry: vi.fn(), px4TelemetrySocket: vi.fn() },
}))

const selectionCallbacks = vi.hoisted(() => ({
  px4: [] as Array<(id: string) => void>,
  gazebo: [] as Array<(name: string) => void>,
}))

vi.mock('./CesiumLiveMap', () => ({
  CesiumLiveMap: ({ vehicles, onSelect }: { vehicles: Array<{ id: string }>; onSelect: (id: string) => void }) => {
    selectionCallbacks.px4.push(onSelect)
    return <><div data-testid="cesium-live-map">{vehicles.map(vehicle => vehicle.id).join(',')}</div><button onClick={() => onSelect('px4-2')}>Select PX4 2</button></>
  },
}))

vi.mock('./GazeboPoseMirror', () => ({
  GazeboPoseMirror: ({ selectedModel, onSelect }: { selectedModel: string | null; onSelect: (name: string) => void }) => {
    selectionCallbacks.gazebo.push(onSelect)
    return <div data-testid="gazebo-pose-mirror"><span>Gazebo live pose mirror</span><span data-testid="gazebo-selected-model">{selectedModel}</span><button onClick={() => onSelect('x500_1')}>Select Gazebo x500_1</button></div>
  },
}))

const apiMock = vi.mocked(api)
let socket: { onopen: (() => void) | null; onmessage: ((event: MessageEvent) => void) | null; onerror: (() => void) | null; onclose: ((event: CloseEvent) => void) | null; close: ReturnType<typeof vi.fn> }

function snapshot(systemIds: number[]): Px4TelemetrySnapshot {
  return {
    source: 'px4_mavlink', generated_at: '2026-11-08T00:00:00Z',
    transport: { status: systemIds.length ? 'receiving' : 'awaiting_data', datagrams_received: 10, frames_decoded: 42, invalid_datagrams: 0, last_datagram_at: '2026-11-08T00:00:00Z', last_datagram_age_seconds: 0.02 },
    capabilities: { gps_spoof_detection: 'not_validated_for_px4_telemetry', independent_navigation_estimate: 'not_integrated', telemetry_persistence: 'in_memory_only' },
    vehicles: systemIds.map(system_id => ({
      id: `px4-${system_id}`, system_id, component_id: 1, source: 'px4_mavlink', status: 'connected', armed: false,
      heartbeat: { vehicle_type: 2, autopilot: 12, base_mode: 0, custom_mode: 0, flight_mode: 'POSCTL', system_status: 3, received_at: '2026-11-08T00:00:00Z' },
      position: { latitude: 12.9716 + system_id / 10000, longitude: 77.5946, altitude_m: 920, relative_altitude_m: 20, heading_deg: 91, valid: true, coordinate_frame: 'WGS84', source_message: 'GLOBAL_POSITION_INT', source_time_boot_ms: 1000, received_at: '2026-11-08T00:00:00Z' },
      velocity: { north_m_s: 1, east_m_s: 2, down_m_s: 0, ground_speed_m_s: 2.236, source_message: 'GLOBAL_POSITION_INT', received_at: '2026-11-08T00:00:00Z' },
      attitude: { roll_rad: 0, pitch_rad: 0, yaw_rad: 1.2, source_message: 'ATTITUDE', source_time_boot_ms: 1000, received_at: '2026-11-08T00:00:00Z' },
      gps_raw: { latitude: 12.9716, longitude: 77.5946, altitude_m: 920, fix_type: 3, valid: true, source_time_usec: 1000, eph_m: 1, epv_m: 1, satellites_visible: 12, received_at: '2026-11-08T00:00:00Z' },
      imu_raw: null, battery: { voltage_v: 16.2, current_a: 2, remaining_percent: 80, source_message: 'SYS_STATUS', received_at: '2026-11-08T00:00:00Z' },
      mission: null, threat_assessment: { status: 'not_available', reason: 'Not validated.' }, last_heartbeat_at: '2026-11-08T00:00:00Z', last_message_at: '2026-11-08T00:00:00Z', telemetry_age_seconds: 0.02, heartbeat_age_seconds: 0.02,
      flight_trail: [],
    })),
  }
}

describe('PX4 mission telemetry view', () => {
  beforeEach(() => {
    selectionCallbacks.px4.length = 0
    selectionCallbacks.gazebo.length = 0
    socket = { onopen: null, onmessage: null, onerror: null, onclose: null, close: vi.fn() }
    apiMock.px4Telemetry.mockResolvedValue(snapshot([1]))
    apiMock.px4TelemetrySocket.mockReturnValue(socket as unknown as WebSocket)
  })

  afterEach(() => { cleanup(); vi.clearAllMocks() })

  it('renders only backend-provided vehicle state and applies live socket updates', async () => {
    render(<Px4MissionControl />)

    expect(await screen.findByText('12.971700, 77.594600')).toBeTruthy()
    expect(screen.getByText('HIGHRES_IMU NOT RECEIVED')).toBeTruthy()
    expect(screen.getByText('80% / 16.20 V')).toBeTruthy()
    expect(screen.getByTestId('cesium-live-map').textContent).toBe('px4-1')
    expect(screen.getByText('Gazebo live pose mirror')).toBeTruthy()
    expect(screen.getByTestId('gazebo-selected-model').textContent).toBe('x500_0')

    await waitFor(() => expect(apiMock.px4TelemetrySocket).toHaveBeenCalledOnce())
    act(() => socket.onmessage?.({ data: JSON.stringify(snapshot([1, 2])) } as MessageEvent))

    expect(await screen.findByText('px4-1,px4-2')).toBeTruthy()
    expect(screen.getAllByText('2 / 2').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('NOT VALIDATED')).toBeTruthy()
  })

  it('keeps PX4 details, map selection and Gazebo model selection paired', async () => {
    apiMock.px4Telemetry.mockResolvedValue(snapshot([1, 2, 3]))
    render(<Px4MissionControl />)

    expect(await screen.findByText('12.971700, 77.594600')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Select Gazebo x500_1' }))

    expect(screen.getByTestId('gazebo-selected-model').textContent).toBe('x500_1')
    expect(screen.getByText('12.971800, 77.594600')).toBeTruthy()
    expect(screen.getByRole('button', { name: /PX4 SYS 2/ }).getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: /PX4 SYS 3/ }))
    expect(screen.getByTestId('gazebo-selected-model').textContent).toBe('x500_2')
    expect(screen.getByText('12.971900, 77.594600')).toBeTruthy()
  })

  it('keeps viewer selection callbacks stable across live telemetry updates', async () => {
    render(<Px4MissionControl />)

    expect(await screen.findByText('12.971700, 77.594600')).toBeTruthy()
    await waitFor(() => expect(apiMock.px4TelemetrySocket).toHaveBeenCalledOnce())
    act(() => socket.onmessage?.({ data: JSON.stringify(snapshot([1, 2])) } as MessageEvent))
    act(() => socket.onmessage?.({ data: JSON.stringify(snapshot([1, 2, 3])) } as MessageEvent))

    expect(new Set(selectionCallbacks.px4).size).toBe(1)
    expect(new Set(selectionCallbacks.gazebo).size).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Select PX4 2' }))
    expect(screen.getByText('12.971800, 77.594600')).toBeTruthy()
    expect(screen.getByTestId('gazebo-selected-model').textContent).toBe('x500_1')
  })

  it('does not display the MAVLink no-mission sentinel as a waypoint count', async () => {
    const noMission = snapshot([1])
    noMission.vehicles[0].mission = {
      current_sequence: 0,
      total_sequence: null,
      mission_state: 1,
      received_at: '2026-11-08T00:00:00Z',
    }
    apiMock.px4Telemetry.mockResolvedValue(noMission)
    render(<Px4MissionControl />)

    expect(await screen.findByText('PX4 reports no mission loaded.')).toBeTruthy()
    expect(screen.getByText('NO MISSION LOADED')).toBeTruthy()
    expect(screen.queryByText('1 / 65535')).toBeNull()
  })

  it('labels PX4 attitude and renders only the reported raw sensor fields', async () => {
    const withSensors = snapshot([1])
    withSensors.vehicles[0].imu_raw = {
      acceleration_m_s2: [0.1, -0.2, 9.8],
      angular_rate_rad_s: [0.01, 0.02, -0.03],
      magnetic_field_gauss: [0.2, 0.1, -0.4],
      temperature_c: 24,
      fields_updated: 7,
      source_time_usec: 123456,
      received_at: '2026-11-08T00:00:01Z',
    }
    apiMock.px4Telemetry.mockResolvedValue(withSensors)
    render(<Px4MissionControl />)

    expect(await screen.findByText('0.00 rad / 0.00 rad / 1.20 rad')).toBeTruthy()
    expect(screen.getByText('0.10 / -0.20 / 9.80')).toBeTruthy()
    expect(screen.getByText('0.010 / 0.020 / -0.030')).toBeTruthy()
    expect(screen.getByText('0.200 / 0.100 / -0.400')).toBeTruthy()
    expect(screen.getByText(/Fields updated 0x7 · source 123456/)).toBeTruthy()
    expect(screen.getByText(/GPS VALID FIX \/ IMU RECEIVED/)).toBeTruthy()
    expect(screen.getByText('NOT CONNECTED TO LIVE PX4')).toBeTruthy()
  })
})
