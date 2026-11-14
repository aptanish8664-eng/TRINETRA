import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { Px4MissionControl } from './Px4MissionControl'
import type { GazeboPoseSnapshot, Px4TelemetrySnapshot, Px4TelemetryVehicle } from '../types'
import type { MissionDeployment } from '../services/auth'
import type { MissionPlan } from './missionPlan'
import { api } from '../services/api'

vi.mock('../services/api', () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number) { super('API error'); this.status = status } },
  api: {
    px4Telemetry: vi.fn(), px4TelemetrySocket: vi.fn(), latestMissionDeployment: vi.fn(), simulationLocation: vi.fn(), relocateSimulation: vi.fn(),
    health: vi.fn(), fleetStatus: vi.fn(), gazeboPoses: vi.fn(),
    researchModelInfo: vi.fn(async () => ({ available: false, formula: '', live_residual_inputs_available: false })), researchDecisions: vi.fn(async () => []), recordDecision: vi.fn(),
  },
}))

type MapProps = { basemap?: string; vehicles: Array<{ id: string }>; selected: string | null; onSelect: (id: string) => void; overlay?: { areaOutline: unknown[]; routes: Array<{ uavId: string; color: string; geofence: { radius_m: number } | null; dropPoints: unknown[]; role: string | null; outbound: unknown[]; returnPath: unknown[] }> }; layers?: Record<string, boolean>; follow?: boolean }
const mapProps = vi.hoisted(() => ({ last: null as unknown, renders: 0 }))

vi.mock('./CesiumLiveMap', () => ({
  DEFAULT_LAYERS: { routes: true, geofence: true, trails: true, labels: true },
  CesiumLiveMap: (props: MapProps) => {
    mapProps.last = props
    mapProps.renders += 1
    return <><div data-testid="cesium-live-map">{props.vehicles.map(vehicle => vehicle.id).join(',')}</div><span data-testid="map-selected">{props.selected}</span><button onClick={() => props.onSelect('px4-2')}>Select PX4 2 on map</button></>
  },
}))

vi.mock('./GazeboPoseMirror', () => ({
  GazeboPoseMirror: ({ selectedModel, onSelect }: { selectedModel: string | null; onSelect: (name: string) => void }) =>
    <div data-testid="gazebo-pose-mirror"><span data-testid="gazebo-selected-model">{selectedModel}</span><button onClick={() => onSelect('x500_1')}>Select Gazebo x500_1</button></div>,
}))

const apiMock = vi.mocked(api)
const lastMap = () => mapProps.last as MapProps
let socket: { onopen: (() => void) | null; onmessage: ((event: MessageEvent) => void) | null; onerror: (() => void) | null; onclose: ((event: CloseEvent) => void) | null; close: ReturnType<typeof vi.fn> }

function vehicle(systemId: number, patch: Partial<Px4TelemetryVehicle> = {}): Px4TelemetryVehicle {
  return {
    id: `px4-${systemId}`, system_id: systemId, component_id: 1, source: 'px4_mavlink', status: 'connected', armed: false,
    heartbeat: { vehicle_type: 2, autopilot: 12, base_mode: 0, custom_mode: 0, flight_mode: 'POSCTL', system_status: 3, received_at: '2026-11-08T00:00:00Z' },
    position: { latitude: 12.9716 + systemId / 10000, longitude: 77.5946, altitude_m: 920, relative_altitude_m: 20, heading_deg: 91, valid: true, coordinate_frame: 'WGS84', source_message: 'GLOBAL_POSITION_INT', source_time_boot_ms: 1000, received_at: '2026-11-08T00:00:00Z' },
    velocity: { north_m_s: 1, east_m_s: 2, down_m_s: 0, ground_speed_m_s: 2.236, source_message: 'GLOBAL_POSITION_INT', received_at: '2026-11-08T00:00:00Z' },
    attitude: { roll_rad: 0, pitch_rad: 0, yaw_rad: 1.2, source_message: 'ATTITUDE', source_time_boot_ms: 1000, received_at: '2026-11-08T00:00:00Z' },
    gps_raw: { latitude: 12.9716, longitude: 77.5946, altitude_m: 920, fix_type: 3, valid: true, source_time_usec: 1000, eph_m: 1, epv_m: 1, satellites_visible: 12, received_at: '2026-11-08T00:00:00Z' },
    imu_raw: null, battery: { voltage_v: 16.2, current_a: 2, remaining_percent: 80, source_message: 'SYS_STATUS', received_at: '2026-11-08T00:00:00Z' },
    mission: null, threat_assessment: { status: 'not_available', reason: 'Not validated.' }, last_heartbeat_at: '2026-11-08T00:00:00Z',
    last_message_at: '2026-11-08T00:00:00Z', telemetry_age_seconds: 0.5, heartbeat_age_seconds: 0.5, flight_trail: [],
    ...patch,
  }
}

function snapshot(vehicles: Px4TelemetryVehicle[], transport = vehicles.length ? 'receiving' : 'awaiting_data'): Px4TelemetrySnapshot {
  return {
    source: 'px4_mavlink', generated_at: '2026-11-08T00:00:00Z',
    transport: { status: transport as 'receiving', datagrams_received: 10, frames_decoded: 42, invalid_datagrams: 0, last_datagram_at: '2026-11-08T00:00:00Z', last_datagram_age_seconds: 0.02 },
    capabilities: { gps_spoof_detection: 'not_validated_for_px4_telemetry', independent_navigation_estimate: 'not_integrated', telemetry_persistence: 'in_memory_only' },
    vehicles,
  }
}

const point = (lat: number, lon: number) => ({ latitude: lat, longitude: lon })
const wp = (seq: number, lat: number, lon: number, alt: number, action: string) => ({ seq, latitude: lat, longitude: lon, altitude_m: alt, speed_mps: 5, hover_s: 0, action })
const route = {
  generator: 'payload_dropoff', generator_version: 1, length_m: 1500, duration_s: 480, assumptions: 'flat',
  waypoints: [wp(0, 12.9717, 77.5946, 30, 'takeoff'), wp(1, 12.9737, 77.5956, 30, 'waypoint'), wp(2, 12.9737, 77.5956, 8, 'payload_release_point'), wp(3, 12.9717, 77.5946, 30, 'waypoint'), wp(4, 12.9717, 77.5946, 0, 'land')],
  events: [{ type: 'payload_released', aircraft_id: 'px4-1', waypoint_seq: 2, planned_time_s: 120, payload_category: 'medical_kit', simulated: true }],
}
function aircraftPlan(id: string, role: 'primary' | 'decoy' | 'standby', withRoute = true) {
  return {
    uav_id: id, role, priority: 1, reassignable: true, task_type: 'payload_dropoff', parameters: {}, start_location: point(12.9717, 77.5946),
    altitude_m: 30, speed_mps: 5, loiter_duration_s: 0, return_mode: 'return_to_start', route: withRoute ? route : null, confirmed: true,
  }
}
function deployment(aircraft = [aircraftPlan('px4-1', 'primary'), aircraftPlan('px4-2', 'decoy')]): MissionDeployment {
  const plan = {
    objectives: ['relief_supply_delivery'], description: '', operating_area: { kind: 'circle', centre: point(12.9727, 77.595), radius_m: 400, vertices: [] },
    required_uav_count: aircraft.length, max_duration_minutes: null, constraints: { max_distance_from_start_m: 1000, max_altitude_m: 100 }, notes: '',
    allow_multiple_primary: false, selected_uav_ids: aircraft.map(item => item.uav_id), activated_uav_ids: aircraft.map(item => item.uav_id),
    aircraft, workflow_stage: 'review', current_uav_index: 0,
  } as unknown as MissionPlan
  return {
    id: 'deployment-0001-abcdef', mission_id: 'mission-123456789abc', scenario_id: 's1', scenario_version_id: 'version-1234-5678', scenario_version_hash: 'abcdef0123456789',
    scenario_title: 'Coastal relief', execution_status: 'DEPLOYED_TO_MISSION_CONTROL_NOT_UPLOADED_TO_PX4', requested_uav_count: aircraft.length,
    activated_uav_count: aircraft.length, assigned_uav_count: aircraft.length, approval: null, validation: { passed: true, validated_at: '2026-11-12T00:00:00Z' },
    intent: { mission_plan: plan, schema_version: 2, mission_objectives: [], proposed_tasks: [], uav_role_suggestions: [], operating_area_notes: '', environment_notes: '', constraints: [], geographic_mission: null, uav_fault_assignments: [] },
  } as unknown as MissionDeployment
}
const poses = (models: string[]): GazeboPoseSnapshot => ({
  source: 'gazebo_pose_info', world: 'default', generated_at: '2026-11-08T00:00:00Z',
  transport: { status: 'receiving', datagrams_received: 3, invalid_datagrams: 0, last_datagram_at: null, last_datagram_age_seconds: 0.1 },
  models: models.map((model_name, index) => ({ model_name, position_m: { x: index, y: 2, z: 0.5 }, orientation_quaternion: { x: 0, y: 0, z: 0, w: 1 }, received_at: '2026-11-08T00:00:00Z', status: 'connected' as const, telemetry_age_seconds: 0.4, flight_trail: [] })),
})
const health = (extra: Record<string, unknown> = {}) => ({
  status: 'ready', active_simulations: 0, database: 'sqlite',
  components: { api: { status: 'available' }, px4: { status: 'receiving' }, gazebo: { status: 'receiving' }, ...extra },
}) as never

describe('Mission Control live dashboard', () => {
  beforeEach(() => {
    window.localStorage.clear()
    mapProps.last = null
    mapProps.renders = 0
    socket = { onopen: null, onmessage: null, onerror: null, onclose: null, close: vi.fn() }
    apiMock.px4Telemetry.mockResolvedValue(snapshot([vehicle(1)]))
    apiMock.px4TelemetrySocket.mockReturnValue(socket as unknown as WebSocket)
    apiMock.latestMissionDeployment.mockResolvedValue(null)
    apiMock.health.mockResolvedValue(health())
    apiMock.fleetStatus.mockResolvedValue({ capacity: 5, verified_count: 1, instances: [], manager_error: null, bridges: { telemetry: { status: 'running' }, pose: { status: 'running' } } } as never)
    apiMock.gazeboPoses.mockResolvedValue(poses(['x500_0']))
    apiMock.simulationLocation.mockResolvedValue({ mission_start: null, aircraft: [], max_distance_to_start_m: null, max_distance_to_area_m: null, position_known: false,
      relocation_recommended: false, far_from_mission_area: false, message: 'No aircraft of this plan reports a valid position, so its distance from the mission start is unknown.', thresholds: { relocation_notice_m: 1000, far_from_area_m: 50000, home_tolerance_m: 50 } } as never)
  })
  afterEach(() => { cleanup(); vi.clearAllMocks() })

  it('with no aircraft and no deployment shows honest empty states and draws nothing', async () => {
    apiMock.px4Telemetry.mockResolvedValue(snapshot([]))
    apiMock.gazeboPoses.mockResolvedValue(poses([]))
    render(<Px4MissionControl />)
    expect(await screen.findByText('No aircraft reported or deployed.')).toBeTruthy()
    expect(screen.getByText('No deployed mission.')).toBeTruthy()
    expect(screen.getByText('No reassignments yet.')).toBeTruthy()
    expect(screen.getByText('No transitions observed yet.')).toBeTruthy()
    expect(screen.getByText('No alerts from backend-reported state.')).toBeTruthy()
    expect(screen.getByText('NOT DEPLOYED')).toBeTruthy()
    expect(screen.getByTestId('cesium-live-map').textContent).toBe('')
    expect(lastMap().overlay?.routes).toEqual([])
    expect(lastMap().overlay?.areaOutline).toEqual([])
  })

  it('shows reported values on the aircraft card, "no data" for anything missing, and no flight controls', async () => {
    apiMock.px4Telemetry.mockResolvedValue(snapshot([vehicle(1), vehicle(2, { battery: null, gps_raw: null, velocity: null })]))
    render(<Px4MissionControl />)
    const first = await screen.findByRole('button', { name: 'Select aircraft px4-1' })
    expect(within(first).getByText('x500_0')).toBeTruthy()
    expect(within(first).getByText('80%')).toBeTruthy()
    expect(within(first).getByText('12.97170, 77.59460')).toBeTruthy()
    expect(within(first).getByText('fix 3 · 12 sats')).toBeTruthy()
    expect(within(first).getByText('91°')).toBeTruthy()
    const second = screen.getByRole('button', { name: 'Select aircraft px4-2' })
    expect(within(second).getAllByText('no data').length).toBeGreaterThanOrEqual(3)
    expect(within(second).getByText('not planned')).toBeTruthy()
    for (const control of screen.queryAllByRole('button')) expect(control.textContent ?? '').not.toMatch(/\barm\b|take.?off|upload|start mission|execute|command/i)
  })

  it('marks stale telemetry and raises a link alert only from the reported state', async () => {
    apiMock.px4Telemetry.mockResolvedValue(snapshot([
      vehicle(1, { telemetry_age_seconds: 12 }), vehicle(2, { status: 'stale', telemetry_age_seconds: 40 }),
    ]))
    render(<Px4MissionControl />)
    const first = await screen.findByRole('button', { name: 'Select aircraft px4-1' })
    expect(within(first).getByText('STALE')).toBeTruthy()
    expect(within(screen.getByRole('button', { name: 'Select aircraft px4-2' })).getByText('STALE')).toBeTruthy()
    const alerts = screen.getByRole('region', { name: 'Alerts' })
    expect(within(alerts).getByText(/px4-1 telemetry is 12\.0 s old/)).toBeTruthy()
    expect(within(alerts).getByText(/px4-2 link stale/)).toBeTruthy()
  })

  it('builds the map overlay from the deployed schema-2 plan with role colours, drop point and geofence', async () => {
    apiMock.px4Telemetry.mockResolvedValue(snapshot([vehicle(1), vehicle(2)]))
    apiMock.latestMissionDeployment.mockResolvedValue(deployment())
    render(<Px4MissionControl />)
    await screen.findByText('DEPLOYED TO MISSION CONTROL')
    await waitFor(() => expect(lastMap().overlay?.routes.length).toBe(2))
    const [primary, decoy] = lastMap().overlay!.routes
    expect(primary).toMatchObject({ uavId: 'px4-1', role: 'primary', color: '#55d9ff' })
    expect(decoy).toMatchObject({ uavId: 'px4-2', role: 'decoy', color: '#f5b94c' })
    expect(primary.dropPoints).toHaveLength(1)
    expect(primary.geofence?.radius_m).toBe(1000)
    expect(primary.outbound.length).toBe(3)
    expect(primary.returnPath.length).toBeGreaterThanOrEqual(2)
    expect(lastMap().overlay!.areaOutline.length).toBeGreaterThan(8)
    const status = screen.getByRole('region', { name: 'Mission status' })
    expect(within(status).getByText(/Deployed to Mission Control/)).toBeTruthy()
    expect(within(status).getByText(/Uploaded to PX4/).textContent).toMatch(/not available yet/)
    expect(within(status).getByText(/^Executing/).textContent).toMatch(/not available yet/)
    expect(within(status).getAllByText('Progress: not started')).toHaveLength(2)
    expect(screen.getByText(/not executing on PX4/)).toBeTruthy()
    expect(screen.getByText('Coastal relief')).toBeTruthy()
  })

  it('applies battery thresholds and shows no data when battery is not reported', async () => {
    apiMock.px4Telemetry.mockResolvedValue(snapshot([
      vehicle(1), vehicle(2, { battery: { voltage_v: 14, current_a: 1, remaining_percent: 30, source_message: 'SYS_STATUS', received_at: 'x' } }),
      vehicle(3, { battery: { voltage_v: 13, current_a: 1, remaining_percent: 10, source_message: 'SYS_STATUS', received_at: 'x' } }), vehicle(4, { battery: null }),
    ]))
    render(<Px4MissionControl />)
    const panel = await screen.findByRole('region', { name: 'Battery status' })
    const items = within(panel).getAllByRole('listitem')
    expect(items.map(item => item.className)).toEqual(['tone-ok', 'tone-warn', 'tone-critical', 'tone-none'])
    expect(within(items[3]).getByRole('img').getAttribute('aria-label')).toBe('px4-4 battery no data')
    const alerts = screen.getByRole('region', { name: 'Alerts' })
    expect(within(alerts).getByText('px4-3 battery 10%.')).toBeTruthy()
    expect(within(alerts).getByText('px4-2 battery 30%.')).toBeTruthy()
    expect(within(alerts).queryByText(/px4-1 battery/)).toBeNull()
  })

  it('keeps TRINETRA id, PX4 system id and Gazebo model aligned for two aircraft', async () => {
    apiMock.px4Telemetry.mockResolvedValue(snapshot([vehicle(1), vehicle(2)]))
    apiMock.gazeboPoses.mockResolvedValue(poses(['x500_1']))
    render(<Px4MissionControl />)
    const table = await screen.findByRole('table', { name: 'Gazebo pose per aircraft' })
    await waitFor(() => expect(within(table).getAllByRole('row')[2].textContent).toMatch(/px4-2x500_1yes0\.00, 2\.00, 0\.50/))
    expect(within(table).getAllByRole('row')[1].textContent).toMatch(/px4-1x500_0nono data/)
    expect(within(screen.getByRole('button', { name: 'Select aircraft px4-2' })).getByText('x500_1')).toBeTruthy()
    expect(screen.getByText(/native Gazebo window runs separately/i)).toBeTruthy()
    expect(screen.getByText(/Reserved for a future embedded view/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Select Gazebo x500_1' }))
    expect(screen.getByTestId('map-selected').textContent).toBe('px4-2')
    expect(screen.getByRole('region', { name: 'Details for px4-2' }).textContent).toMatch(/PX4 system 2 · Gazebo x500_1/)
  })

  it('selects from a card or the map, and follow/layers are passed to the map without recreating anything else', async () => {
    apiMock.px4Telemetry.mockResolvedValue(snapshot([vehicle(1), vehicle(2)]))
    render(<Px4MissionControl />)
    fireEvent.click(await screen.findByRole('button', { name: 'Select aircraft px4-2' }))
    expect(screen.getByTestId('map-selected').textContent).toBe('px4-2')
    expect(screen.getByTestId('gazebo-selected-model').textContent).toBe('x500_1')
    fireEvent.click(screen.getByRole('button', { name: 'Select aircraft px4-1' }))
    expect(screen.getByTestId('map-selected').textContent).toBe('px4-1')
    fireEvent.click(screen.getByRole('button', { name: /FOLLOW px4-1/ }))
    expect(lastMap().follow).toBe(true)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Routes' }))
    expect(lastMap().layers).toMatchObject({ routes: false, geofence: true, trails: true, labels: true })
    expect(screen.getByRole('list', { name: 'Legend' })).toBeTruthy()
  })

  it('shows only injected backend events in alerts and the session log, and acknowledge is client-side', async () => {
    apiMock.latestMissionDeployment.mockResolvedValue(deployment([aircraftPlan('px4-1', 'primary')]))
    apiMock.health.mockResolvedValue(health({ px4: { status: 'unavailable' }, default_sitl_startup: { status: 'DEGRADED', detail: 'bridges not verified' } }))
    apiMock.fleetStatus.mockResolvedValue({ capacity: 5, verified_count: 0, instances: [], manager_error: 'WSL fleet manager could not complete', bridges: { telemetry: { status: 'not_running' }, pose: { status: 'running' } } } as never)
    render(<Px4MissionControl />)
    const alerts = await screen.findByRole('region', { name: 'Alerts' })
    // The startup alert is explained in plain words: what it is, the reported reason, and that it is not a live reading.
    await waitFor(() => expect(within(alerts).getByText(/Fleet startup degraded: when the backend started.*Reported reason: bridges not verified.*not the current state/)).toBeTruthy())
    expect(within(alerts).getByText(/PX4 receiver is unavailable/)).toBeTruthy()
    expect(within(alerts).getByText(/WSL fleet manager: WSL fleet manager could not complete/)).toBeTruthy()
    expect(within(alerts).getByText(/PX4 telemetry bridge is not running/)).toBeTruthy()
    const log = screen.getByRole('region', { name: 'Flight log' })
    expect(log.textContent).toMatch(/SESSION LOG/)
    expect(within(log).getByText(/Deployment deployment-0 loaded into Mission Control/)).toBeTruthy()
    expect(within(log).getByText(/px4-1 telemetry appeared: connected/)).toBeTruthy()
    const before = within(alerts).getAllByRole('button', { name: /ACKNOWLEDGE/ }).length
    fireEvent.click(within(alerts).getAllByRole('button', { name: /ACKNOWLEDGE/ })[0])
    expect(within(alerts).getAllByRole('button', { name: /ACKNOWLEDGE/ }).length).toBe(before - 1)
    act(() => socket.onmessage?.({ data: JSON.stringify(snapshot([vehicle(1, { status: 'stale', telemetry_age_seconds: 30 })])) } as MessageEvent))
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Flight log' })).getByText('px4-1 link connected → stale.')).toBeTruthy())
  })

  describe('layout', () => {
    let css = ''
    beforeAll(async () => {
      // Read the stylesheet source directly: vitest does not process imported CSS, so the rules are asserted on their text.
      const fs: { readFileSync: (path: string, encoding: string) => string } = await import(/* @vite-ignore */ ['node', 'fs'].join(':'))
      css = fs.readFileSync('src/components/mission-control-dashboard.css', 'utf8') // tests run from the frontend directory
    })
    const rule = (selector: string) => css.split(/\r?\n/).find(line => line.startsWith(selector)) ?? ''

    it('places the panels in three named columns, with the threat monitor, flight log and Gazebo pose below', async () => {
      render(<Px4MissionControl />)
      await screen.findByTestId('cesium-live-map')
      const dashboard = screen.getByRole('region', { name: 'Live PX4 mission control' })
      const main = dashboard.querySelector('.mc-main') as HTMLElement
      const left = main.querySelector('.mc-rail-left') as HTMLElement
      const right = main.querySelector('.mc-rail-right') as HTMLElement
      const labels = (node: Element) => Array.from(node.children).map(child => child.getAttribute('aria-label'))
      expect(labels(left)).toEqual(['Active aircraft', 'System status', 'Battery status'])
      expect(labels(right)).toEqual(['Alerts', 'Mission status', 'Mission handoffs'])
      expect(main.contains(screen.getByRole('region', { name: 'Mission map' }))).toBe(true)
      // The selected-aircraft strip stays directly under the map, inside the map panel.
      const mapPanel = screen.getByRole('region', { name: 'Mission map' })
      expect(mapPanel.contains(screen.getByTestId('cesium-live-map'))).toBe(true)
      const lower = dashboard.querySelector('.mc-lower') as HTMLElement
      expect(labels(lower)).toEqual(['Flight log', 'Gazebo panel'])
      const order = Array.from(dashboard.children).map(child => child.className || child.getAttribute('aria-label'))
      expect(order.indexOf('mc-main')).toBeLessThan(order.findIndex(item => String(item).includes('threat-monitor')))
      expect(order.findIndex(item => String(item).includes('threat-monitor'))).toBeLessThan(order.indexOf('mc-lower'))
    })

    it('defines grid areas and a bounded map height instead of a content-driven one', () => {
      const main = rule('.mc-main{')
      expect(main).toContain('grid-template-areas:"left map right"')
      expect(main).toMatch(/height:calc\(var\(--mc-map-h\)/)
      expect(css).toMatch(/--mc-map-h:clamp\(420px,\s*60vh,\s*720px\)/)
      expect(rule('.mc-map-panel{')).toContain('grid-area:map')
      expect(rule('.mc-map-panel .cesium-live-map{')).toMatch(/flex:1 1 0;height:auto;min-height:260px/)
      expect(rule('.mc-rail{')).toContain('min-height:0')
      expect(rule('.mc-rail>.panel{')).toMatch(/flex:1 1 auto.*overflow:auto/)
      expect(rule('.mc-lower{')).toContain('repeat(2,minmax(0,1fr))')
      expect(css).not.toMatch(/\.mc-map-panel \.cesium-live-map\{height:max\(60vh,460px\)\}/)
    })

    it('stacks to one column below 1100px in the requested order', () => {
      const media = css.split(/\r?\n/).find(line => line.startsWith('@media(max-width:1100px)')) ?? ''
      expect(media).toContain('.mc-rail{display:contents}')
      const orders = ['Active aircraft', 'Alerts', 'Mission status', 'System status'].map(label => Number(media.split(`aria-label="${label}"]{order:`)[1]?.[0]))
      expect(orders).toEqual([2, 3, 4, 5])
      expect(media).toContain('.mc-map-panel{order:1')
      expect(media).toContain('.mc-lower{grid-template-columns:minmax(0,1fr)}')
    })
  })

  describe('basemap', () => {
    const basemapSelect = () => screen.getByLabelText('Basemap') as HTMLSelectElement

    it('shows Street as active on first load when nothing is stored', async () => {
      render(<Px4MissionControl />)
      await screen.findByTestId('cesium-live-map')
      expect(basemapSelect().value).toBe('street')
      expect(lastMap().basemap).toBe('street')
    })

    it('honours a stored choice, keeps satellite selectable and remembers the new choice', async () => {
      window.localStorage.setItem('trinetra.basemap', 'satellite')
      render(<Px4MissionControl />)
      await screen.findByTestId('cesium-live-map')
      expect(basemapSelect().value).toBe('satellite')
      expect(lastMap().basemap).toBe('satellite')
      fireEvent.change(basemapSelect(), { target: { value: 'street' } })
      expect(lastMap().basemap).toBe('street')
      expect(window.localStorage.getItem('trinetra.basemap')).toBe('street')
      fireEvent.change(basemapSelect(), { target: { value: 'satellite' } })
      expect(lastMap().basemap).toBe('satellite')
    })

    it('ignores an invalid stored value and falls back to street', async () => {
      window.localStorage.setItem('trinetra.basemap', 'globe-of-doom')
      render(<Px4MissionControl />)
      await screen.findByTestId('cesium-live-map')
      expect(basemapSelect().value).toBe('street')
    })
  })

  it('shows the deployed schema-2 mission by name and version number with its routes, and the deploy confirmation', async () => {
    apiMock.latestMissionDeployment.mockResolvedValue({ ...deployment(), scenario_version_number: 3, latest_version_number: 3, schema_version: 2, legacy: false, superseded: false } as MissionDeployment)
    const dismiss = vi.fn()
    render(<Px4MissionControl notice="Deployed Coastal relief version 3 to Mission Control. Nothing was armed, flown or uploaded to PX4." onDismissNotice={dismiss} />)
    const header = await screen.findByLabelText('Mission header')
    await waitFor(() => expect(within(header).getByRole('heading', { name: 'Coastal relief' })).toBeTruthy())
    expect(within(header).getByText(/Version 3 · schema 2 · 2 generated routes on the map · hash abcdef012345/)).toBeTruthy()
    expect(within(header).queryByText(/legacy/i)).toBeNull()
    await waitFor(() => expect(lastMap().overlay?.routes).toHaveLength(2))
    expect(screen.getByText(/Deployed Coastal relief version 3 to Mission Control/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'DISMISS' }))
    expect(dismiss).toHaveBeenCalledOnce()
    await waitFor(() => expect(apiMock.simulationLocation).toHaveBeenCalledWith('s1', 'version-1234-5678'))
  })

  it('says clearly when the latest deployment is legacy, links to Scenario Lab, and offers no relocation', async () => {
    const legacy = deployment()
    const intent = { ...legacy.intent, mission_plan: null, schema_version: 1 }
    apiMock.latestMissionDeployment.mockResolvedValue({ ...legacy, intent, legacy: true, schema_version: 1, scenario_version_number: 1, mission_start: null } as unknown as MissionDeployment)
    const open = vi.fn()
    render(<Px4MissionControl onOpenScenarioLab={open} canManageFleet />)
    const header = await screen.findByLabelText('Mission header')
    const note = await within(header).findByText(/The latest deployment is a legacy \(schema 1\) mission/)
    expect(note.textContent).toMatch(/no generated routes, so none are drawn/)
    expect(within(header).getByText(/Version 1 · legacy \(schema 1\), no generated routes/)).toBeTruthy()
    fireEvent.click(within(header).getByRole('button', { name: 'Open Scenario Lab' }))
    expect(open).toHaveBeenCalledOnce()
    expect(lastMap().overlay?.routes ?? []).toHaveLength(0)
    expect(apiMock.simulationLocation).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: 'Mission location' })).toBeNull()
  })

  it('with no deployment points to Deploy to Mission Control in Scenario Lab', async () => {
    const open = vi.fn()
    render(<Px4MissionControl onOpenScenarioLab={open} />)
    const header = await screen.findByLabelText('Mission header')
    expect(within(header).getByText('Use Deploy to Mission Control in Scenario Lab to load a validated mission here.')).toBeTruthy()
    fireEvent.click(within(header).getByRole('button', { name: 'Open Scenario Lab' }))
    expect(open).toHaveBeenCalledOnce()
  })

  it('shows the far-from-mission notice with the move action only to accounts that may manage the fleet', async () => {
    apiMock.latestMissionDeployment.mockResolvedValue(deployment())
    apiMock.simulationLocation.mockResolvedValue({
      mission_start: { latitude: 12.9717, longitude: 77.5946, altitude_m: 488, uav_id: 'px4-1', role: 'primary', definition: 'Take-off point of the primary aircraft.' },
      aircraft: [], max_distance_to_start_m: 7_594_000, max_distance_to_area_m: 7_593_600, position_known: true, relocation_recommended: true, far_from_mission_area: true,
      message: 'The aircraft is about 7594 km from the mission area. The simulation is running at a different location than this mission. Use Move simulation to mission start.',
      thresholds: { relocation_notice_m: 1000, far_from_area_m: 50000, home_tolerance_m: 50 } } as never)
    const view = render(<Px4MissionControl canManageFleet />)
    const region = await screen.findByRole('region', { name: 'Mission location' })
    expect((await within(region).findByRole('alert')).textContent).toMatch(/about 7594 km from the mission area/)
    expect(within(region).getByRole('button', { name: /MOVE SIMULATION TO MISSION START/ })).toBeTruthy()
    expect(apiMock.relocateSimulation).not.toHaveBeenCalled()  // nothing restarts without the press and the confirmation
    view.unmount()
    render(<Px4MissionControl />)
    const readOnly = await screen.findByRole('region', { name: 'Mission location' })
    await within(readOnly).findByRole('alert')
    expect(within(readOnly).queryByRole('button', { name: /MOVE SIMULATION TO MISSION START/ })).toBeNull()
  })
})
