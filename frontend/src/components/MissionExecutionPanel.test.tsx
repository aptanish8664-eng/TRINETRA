import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MissionExecutionPanel, clock } from './MissionExecutionPanel'
import type { ExecutionEvent, ExecutionPreflight, ExecutionView } from '../services/api'
import type { MissionDeployment } from '../services/auth'
import type { AircraftRow } from './missionControlModel'
import { landState } from './missionControlModel'

const apiMock = vi.hoisted(() => ({
  latestExecution: vi.fn(), executionPreflight: vi.fn(), executionEvents: vi.fn(), startExecution: vi.fn(),
  attackLabLive: vi.fn(), fleetExecutions: vi.fn(), pauseExecution: vi.fn(), resumeExecution: vi.fn(), abortExecution: vi.fn(), landHere: vi.fn(),
}))
vi.mock('../services/api', () => ({ api: apiMock }))

const CONFIRMATION = 'This runs your route in the PX4/Gazebo simulation. The aircraft will arm, take off and fly the plan.'
const deployment = { id: 'deployment-1', scenario_id: 's1', scenario_version_id: 'v1', scenario_title: 'Coastal relief', scenario_version_hash: 'h', mission_id: 's1',
  execution_status: 'x', legacy: false, schema_version: 2, intent: { schema_version: 2, mission_plan: { aircraft: [] } } } as unknown as MissionDeployment
const CHECK_KEYS = ['plan_valid', 'role', 'aircraft_count', 'fleet_verified', 'managed_instance', 'telemetry_fresh', 'gps_fix', 'disarmed_landed', 'at_mission_start', 'battery', 'no_execution_running', 'endurance']
const preflight = (patch: Partial<ExecutionPreflight> = {}, failing: Record<string, string> = {}): ExecutionPreflight => ({
  deployment_id: 'deployment-1', aircraft_id: 'px4-1', can_start: Object.keys(failing).length === 0, confirmation: CONFIRMATION,
  role_rule: { allowed: true, code: 'general', message: 'General accounts deploy and start directly.' },
  checks: CHECK_KEYS.map(key => ({ key, label: key.replace(/_/g, ' '), ok: !(key in failing), detail: failing[key] ?? `${key} is fine.`, ...(key === 'at_mission_start' && key in failing ? { offer_relocation: true } : {}) })),
  blocking: Object.values(failing), estimated_duration_s: 233, route_waypoints: 6, ...patch,
})
const idleControls = (state: string) => ({
  pause: { allowed: false, reason: `Pause is possible while executing; the run is ${state}.` },
  resume: { allowed: false, reason: `Resume is possible while paused; the run is ${state}.` },
  abort: { allowed: true, reason: null },
})
const execution = (patch: Partial<ExecutionView> = {}): ExecutionView => ({
  id: 'e'.repeat(32), deployment_id: 'deployment-1', scenario_id: 's1', scenario_version_id: 'v1', aircraft_id: 'px4-1', state: 'executing', stage: 'executing',
  reason: '', active: true, started_by_name: 'Test General', started_by_role: 'GENERAL', created_at: 't', updated_at: 't', finished_at: null,
  uploaded: true, armed: true, px4_mode: 'Mission', route_total: 6, route_reached: 2, route_current: 3, percent: 33.3, elapsed_s: 114, estimated_s: 233,
  px4_heartbeat_age_s: 0.4, px4_messages: [], simulation_only: true,
  controls: { pause: { allowed: true, reason: null }, resume: { allowed: false, reason: 'Resume is possible while paused; the run is executing.' }, abort: { allowed: true, reason: null } },
  ...patch,
})
const finished = (state: 'completed' | 'aborted' | 'failed' | 'link_lost', reason = '') => execution({
  state, stage: state, active: false, reason, percent: state === 'completed' ? 100 : 33.3, route_reached: state === 'completed' ? 6 : 2, route_current: state === 'completed' ? 6 : 3, armed: false,
  controls: { pause: { allowed: false, reason: `The run is already ${state}.` }, resume: { allowed: false, reason: `The run is already ${state}.` }, abort: { allowed: false, reason: `The run is already ${state}.` } },
})
const button = (name: RegExp | string) => screen.getByRole('button', { name }) as HTMLButtonElement
const reasons = () => Object.fromEntries(Array.from(document.querySelectorAll('[aria-label="Why controls are unavailable"] li')).map(item => [item.querySelector('b')!.textContent, item.textContent!.slice(item.querySelector('b')!.textContent!.length)]))
const restartSpy = vi.fn()
const panel = (props: Partial<Parameters<typeof MissionExecutionPanel>[0]> = {}) =>
  render(<MissionExecutionPanel deployment={deployment} rows={[]} activePollMs={25} idlePollMs={25} onRestart={restartSpy} {...props}/>)

beforeEach(() => {
  apiMock.attackLabLive.mockResolvedValue({ armed: 0, run_active: false, experiments_in_run: 0, note: '' })
  apiMock.latestExecution.mockResolvedValue(null)
  apiMock.executionPreflight.mockResolvedValue(preflight())
  apiMock.executionEvents.mockResolvedValue({ execution_id: 'e'.repeat(32), state: 'executing', events: [] })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('MissionExecutionPanel', () => {
  it('GENERAL: Start is enabled directly; the other controls are visible but disabled with their reasons', async () => {
    panel()
    await waitFor(() => expect(button(/START IN SIMULATION/).disabled).toBe(false), { timeout: 5000 })
    for (const name of [/^PAUSE$/, /^RESUME$/, /ABORT \(RETURN TO LAUNCH\)/]) expect(button(name).disabled).toBe(true)   // never hidden
    expect(reasons()).toEqual({ Pause: 'Nothing is running.', Resume: 'Nothing is running.', Abort: 'Nothing is running.', 'Land here': 'No telemetry from the aircraft, so its armed state is unknown.' })
    expect(screen.getByLabelText('Role rule').textContent).toBe('Role rule: General accounts deploy and start directly.')
    const lifecycle = screen.getByRole('list', { name: 'Mission lifecycle' })
    expect(Array.from(lifecycle.querySelectorAll('li')).map(item => item.textContent)).toEqual(['Deployed to Mission Control', 'Uploaded to PX4not started', 'Executingnot started'])
    expect(apiMock.executionPreflight).toHaveBeenCalledWith('deployment-1')
  })

  it('shows how many experiments are armed (a count only) and when they are running', async () => {
    apiMock.attackLabLive.mockResolvedValue({ armed: 2, run_active: false, experiments_in_run: 0, note: '' })
    panel()
    const note = await screen.findByRole('status', { name: 'Experiments armed' }, { timeout: 5000 })
    expect(note.textContent).toBe('2 experiments armed: they run automatically when this mission starts.')
    expect(note.textContent).not.toMatch(/drift|offset|jump|px4-/i)
  })

  it('lists every started aircraft with its own run and its own controls (a standby stays on the ground)', async () => {
    const running = execution({ state: 'executing', stage: 'executing', controls: { pause: { allowed: true, reason: null }, resume: { allowed: false, reason: 'Resume is possible while paused; the run is executing.' }, abort: { allowed: true, reason: null }, land: { allowed: true, reason: null } } })
    apiMock.fleetExecutions.mockResolvedValue({ deployment_id: 'deployment-1', simulation_only: true, on_the_ground: [{ aircraft_id: 'px4-3', role: 'standby' }],
      executions: [{ ...running, aircraft_id: 'px4-1', role: 'primary' }, { ...running, id: 'f'.repeat(32), aircraft_id: 'px4-2', role: 'decoy', state: 'paused' }] })
    apiMock.pauseExecution.mockResolvedValue(running)
    panel()
    const table = await screen.findByRole('region', { name: 'Aircraft runs' }, { timeout: 5000 })
    expect(within(table).getAllByRole('listitem')).toHaveLength(3)
    expect(table.textContent).toContain('Not started: stays on the ground until it is needed.')
    fireEvent.click(within(table).getByRole('button', { name: 'PAUSE px4-1' }))
    await waitFor(() => expect(apiMock.pauseExecution).toHaveBeenCalledWith(running.id))
    expect((within(table).getByRole('button', { name: 'RESUME px4-1' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('Start needs the in-page confirmation with the exact sentence and the pre-start checklist; nothing starts before START NOW', async () => {
    const nativeConfirm = vi.spyOn(window, 'confirm')
    const connecting = execution({ state: 'connecting', stage: 'starting', uploaded: false, armed: false, percent: 0, route_reached: 0, route_current: 1, elapsed_s: null, controls: idleControls('connecting') })
    apiMock.startExecution.mockImplementation(async () => { apiMock.latestExecution.mockResolvedValue(connecting); return connecting })
    panel()
    await waitFor(() => expect(button(/START IN SIMULATION/).disabled).toBe(false))
    fireEvent.click(button(/START IN SIMULATION/))
    const dialog = await screen.findByRole('dialog', { name: 'Confirm start in simulation' })
    expect(within(dialog).getByText(CONFIRMATION)).toBeTruthy()
    const rows = within(within(dialog).getByRole('list', { name: 'Pre-start checklist' })).getAllByRole('listitem')
    expect(rows).toHaveLength(12)
    expect(rows.every(row => row.className === 'pass' && /PASS/.test(row.textContent ?? ''))).toBe(true)
    expect(apiMock.startExecution).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'CANCEL' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(apiMock.startExecution).not.toHaveBeenCalled()
    fireEvent.click(button(/START IN SIMULATION/))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'START NOW' }))
    await waitFor(() => expect(apiMock.startExecution).toHaveBeenCalledWith('deployment-1'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByRole('status', { name: 'Execution state' }).textContent).toBe('Connecting to PX4')
    expect(button(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(false)   // abort is available as soon as a run exists
    expect(nativeConfirm).not.toHaveBeenCalled()
  })

  it('OPERATOR before approval: Start stays disabled with the exact backend reason beside it, and the checklist shows the failing role rule', async () => {
    const message = "Operators need a General's approval of this exact version. Submit version 1 for review in Scenario Lab."
    apiMock.executionPreflight.mockResolvedValue(preflight({ role_rule: { allowed: false, code: 'approval_required', message } }, { role: message }))
    panel()
    await waitFor(() => expect(reasons().Start).toBe(message))
    expect(button(/START IN SIMULATION/).disabled).toBe(true)
    expect(screen.getByLabelText('Role rule').textContent).toBe(`Role rule: ${message}`)
    fireEvent.click(button('VIEW PRE-START CHECKS'))
    const dialog = await screen.findByRole('dialog')
    expect((within(dialog).getByRole('button', { name: 'START NOW' }) as HTMLButtonElement).disabled).toBe(true)
    const failing = within(dialog).getAllByRole('listitem').filter(row => row.className === 'fail')
    expect(failing).toHaveLength(1)
    expect(failing[0].textContent).toContain('FAIL')
    expect(failing[0].textContent).toContain(message)
    expect(within(dialog).getByText('Start is disabled: 1 check(s) above must pass first.')).toBeTruthy()
    expect(apiMock.startExecution).not.toHaveBeenCalled()
  })

  it('shows the pending and superseded reasons, and counts further failing checks', async () => {
    const pending = "Version 1 is waiting for a General's review. Start stays disabled until it is approved."
    apiMock.executionPreflight.mockResolvedValue(preflight({ role_rule: { allowed: false, code: 'approval_pending', message: pending } },
      { role: pending, battery: 'Battery 31%.', at_mission_start: 'The aircraft is 7378.2 km from the mission start (limit 50 m). Use Move simulation to mission start.' }))
    panel()
    await waitFor(() => expect(reasons().Start).toBe(`${pending} (+2 more in the pre-start checks)`))
    fireEvent.click(button('VIEW PRE-START CHECKS'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Use Move simulation to mission start\. The action is in the Mission location panel above\./)).toBeTruthy()
    expect(within(dialog).getByText('Battery 31%.')).toBeTruthy()
  })

  it('while executing: progress, waypoint n of N, elapsed against the estimate, Pause enabled and Abort always available', async () => {
    apiMock.latestExecution.mockResolvedValue(execution())
    const returning = execution({ state: 'returning', px4_mode: 'Return', reason: 'aborted by the operator', controls: idleControls('returning') })
    apiMock.abortExecution.mockImplementation(async () => { apiMock.latestExecution.mockResolvedValue(returning); return returning })
    panel()
    expect((await screen.findByRole('status', { name: 'Execution state' })).textContent).toBe('Executing')
    const bar = screen.getByRole('progressbar', { name: 'Mission progress' })
    expect(bar.getAttribute('aria-valuenow')).toBe('33')
    const lifecycle = Array.from(screen.getByRole('list', { name: 'Mission lifecycle' }).querySelectorAll('li')).map(item => item.textContent)
    expect(lifecycle).toEqual(['Deployed to Mission Control', 'Uploaded to PX4mission accepted by PX4 (simulation)', 'Executingwaypoint 3 of 6 · 33% · 1:54 of about 3:53'])
    expect(screen.getByText(/PX4 mode Mission · armed · started by Test General \(GENERAL\)/)).toBeTruthy()
    expect(button(/START IN SIMULATION/).disabled).toBe(true)
    expect(reasons()).toMatchObject({ Start: 'A run of this mission is in progress.', Resume: 'Resume is possible while paused; the run is executing.' })
    expect(button(/^PAUSE$/).disabled).toBe(false)
    const abort = button(/ABORT \(RETURN TO LAUNCH\)/)
    expect(abort.disabled).toBe(false)
    fireEvent.click(abort)   // no confirmation step in front of the safety action
    await waitFor(() => expect(apiMock.abortExecution).toHaveBeenCalledWith('e'.repeat(32)))
    await waitFor(() => expect(screen.getByRole('status', { name: 'Execution state' }).textContent).toBe('Returning to launch'))
    expect(button(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(false)
    expect(apiMock.executionPreflight).not.toHaveBeenCalled()   // no pre-start polling while a run is active
  })

  it('pause and resume call their endpoints and the buttons follow the backend state', async () => {
    const paused = execution({ state: 'paused', px4_mode: 'Hold', controls: { pause: { allowed: false, reason: 'Pause is possible while executing; the run is paused.' }, resume: { allowed: true, reason: null }, abort: { allowed: true, reason: null } } })
    apiMock.latestExecution.mockResolvedValueOnce(execution()).mockResolvedValue(paused)
    apiMock.pauseExecution.mockResolvedValue(paused)
    apiMock.resumeExecution.mockResolvedValue(execution())
    panel({ activePollMs: 100000 })
    await waitFor(() => expect(button(/^PAUSE$/).disabled).toBe(false))
    fireEvent.click(button(/^PAUSE$/))
    await waitFor(() => expect(apiMock.pauseExecution).toHaveBeenCalledWith('e'.repeat(32)))
    await waitFor(() => expect(screen.getByRole('status', { name: 'Execution state' }).textContent).toBe('Paused (Hold)'))
    expect(button(/^PAUSE$/).disabled).toBe(true)
    expect(reasons().Pause).toBe('Pause is possible while executing; the run is paused.')
    expect(button(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(false)
    fireEvent.click(button(/^RESUME$/))
    await waitFor(() => expect(apiMock.resumeExecution).toHaveBeenCalledWith('e'.repeat(32)))
    await waitFor(() => expect(screen.getByRole('status', { name: 'Execution state' }).textContent).toBe('Executing'))
  })

  it.each([
    ['completed', '', 'Completed', 'done'],
    ['aborted', 'returned to launch and disarmed', 'Aborted', 'bad'],
    ['failed', 'PX4 answered the arm command with MAV_RESULT_TEMPORARILY_REJECTED.', 'Failed', 'bad'],
    ['link_lost', 'The mission executor process stopped before the run finished.', 'Link lost', 'bad'],
  ] as const)('final state %s is shown with its reason and frees Start', async (state, reason, label, tone) => {
    apiMock.latestExecution.mockResolvedValue(finished(state, reason))
    panel()
    expect((await screen.findByRole('status', { name: 'Execution state' })).textContent).toBe(label)
    const last = Array.from(screen.getByRole('list', { name: 'Mission lifecycle' }).querySelectorAll('li')).pop()!
    expect(last.className).toBe(tone)
    expect(last.textContent).toBe(`${label}${reason}`)
    expect(button(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(true)
    expect(reasons().Abort).toBe(`The run is already ${state}.`)
    await waitFor(() => expect(button(/START IN SIMULATION/).disabled).toBe(false))   // a finished run does not block the next one
    if (state === 'completed') expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('100')
  })

  it('a lost link during a run is shown as Link lost, not as executing, and Abort stays available', async () => {
    apiMock.latestExecution.mockResolvedValue(execution({ state: 'link_lost', stage: 'link_lost', reason: 'no PX4 heartbeat for 10 s', controls: idleControls('link_lost') }))
    panel()
    expect((await screen.findByRole('status', { name: 'Execution state' })).textContent).toBe('Link lost')
    expect(screen.getByText('no PX4 heartbeat for 10 s')).toBeTruthy()
    expect(screen.getByRole('list', { name: 'Mission lifecycle' }).textContent).not.toMatch(/Executingwaypoint/)
    expect(button(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(false)
  })

  it('shows the structured error when the start is refused and keeps the checklist current', async () => {
    apiMock.startExecution.mockRejectedValue(Object.assign(new Error('The aircraft is armed.'), { status: 409, detail: { code: 'preflight_failed' } }))
    panel()
    await waitFor(() => expect(button(/START IN SIMULATION/).disabled).toBe(false))
    fireEvent.click(button(/START IN SIMULATION/))
    apiMock.executionPreflight.mockResolvedValue(preflight({}, { disarmed_landed: 'The aircraft is armed.' }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'START NOW' }))
    expect((await screen.findByRole('alert')).textContent).toBe('The aircraft is armed. (HTTP 409 · preflight_failed)')
    await waitFor(() => expect(reasons().Start).toBe('The aircraft is armed.'))
  })

  it('delivers each backend event once to the flight log, asking only for newer ones', async () => {
    const event = (id: number, type: string, message: string): ExecutionEvent => ({ id, execution_id: 'e'.repeat(32), source: 'executor', type, message, data: {}, actor_name: null, occurred_at: `2026-11-14T03:0${id}:00Z` })
    apiMock.latestExecution.mockResolvedValue(execution())
    apiMock.executionEvents.mockImplementation(async (_id: string, after = 0) => ({ execution_id: 'e'.repeat(32), state: 'executing',
      events: [event(1, 'uploaded', 'Mission uploaded to PX4: 7 items acknowledged.'), event(2, 'armed', 'PX4 reports armed.'), event(3, 'payload_released_simulated', 'payload_released (simulated) at waypoint 3 of 6. No release command was sent.')].filter(item => item.id > after) }))
    const onEvents = vi.fn()
    panel({ onEvents })
    await waitFor(() => expect(apiMock.executionEvents.mock.calls.length).toBeGreaterThan(2))
    expect(onEvents).toHaveBeenCalledTimes(1)
    expect(onEvents.mock.calls[0][0].map((item: ExecutionEvent) => item.type)).toEqual(['uploaded', 'armed', 'payload_released_simulated'])
    expect(apiMock.executionEvents.mock.calls[0]).toEqual(['e'.repeat(32), 0])
    expect(apiMock.executionEvents.mock.calls[1]).toEqual(['e'.repeat(32), 3])
  })

  it('a run of another deployment blocks Start with that reason and is not shown as this mission', async () => {
    apiMock.latestExecution.mockResolvedValue(execution({ deployment_id: 'other-deployment' }))
    panel()
    await waitFor(() => expect(reasons().Start).toBe('A run of another deployment is in progress on this aircraft.'))
    expect(screen.queryByRole('status', { name: 'Execution state' })).toBeNull()
    expect(button(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(false)   // the running aircraft can still be brought home from here
  })

  it('with no deployment shows the empty state and no controls; clock formats minutes and seconds', async () => {
    render(<MissionExecutionPanel deployment={null} rows={[]} activePollMs={25} idlePollMs={25}/>)
    expect(await screen.findByText('No deployed mission.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /START IN SIMULATION/ })).toBeNull()
    expect(apiMock.executionPreflight).not.toHaveBeenCalled()
    expect([clock(0), clock(59.6), clock(233), clock(null)]).toEqual(['0:00', '1:00', '3:53', '—'])
  })

  it('never shows attack information', async () => {
    apiMock.latestExecution.mockResolvedValue(execution())
    panel()
    await screen.findByRole('status', { name: 'Execution state' })
    expect(document.body.textContent?.toLowerCase()).not.toMatch(/attack|spoof|drift|experiment/)
  })
})


describe('Land here and Restart at mission start', () => {
  const row = (vehicle: Record<string, unknown> | null, id = 'px4-1'): AircraftRow => ({
    id, systemId: 1, gazeboModel: 'x500_0', pose: null, stale: false, plan: { uav_id: id, role: 'primary' } as AircraftRow['plan'],
    vehicle: vehicle && { id, system_id: 1, status: 'connected', ...vehicle } as unknown as AircraftRow['vehicle'],
  })
  const ground = { armed: true, position: { relative_altitude_m: 0.1 }, velocity: { ground_speed_m_s: 0 } }
  const air = { armed: true, position: { relative_altitude_m: 30 }, velocity: { ground_speed_m_s: 8 } }
  const run = (state: string, active: boolean) => ({ active, state, aircraft_id: 'px4-1' })
  const landed = (name: string, value: number, age = 0.5) => ({ landed_state: { value, name, age_s: age, received_at: 't' } })

  it('landState: allowed whenever the aircraft is connected and armed, in the air or on the ground, with or without a run', () => {
    expect(landState(row(ground), null)).toMatchObject({ allowed: true, armedOnGround: true, phase: 'ground' })
    expect(landState(row(air), null)).toMatchObject({ allowed: true, armedOnGround: false, phase: 'air' })
    expect(landState(row(air), run('returning', true)).allowed).toBe(true)              // after Abort the aircraft can be landed at once
    expect(landState(row(air), run('executing', true)).allowed).toBe(true)
    expect(landState(row(ground), run('uploading', true)).allowed).toBe(true)
    expect(landState(row({ ...ground, armed: false }), null)).toMatchObject({ allowed: false, reason: 'Already disarmed', phase: 'disarmed' })
    expect(landState(row({ ...ground, status: 'stale' }), null).reason).toMatch(/No telemetry/)
    expect(landState(row({ armed: null, position: {} }), null).reason).toBe('PX4 has not reported whether the aircraft is armed.')
    expect(landState(null, null).allowed).toBe(false)
  })

  it('landState: PX4\'s landed state wins; altitude and speed are only the fallback and the view says which was used', () => {
    const far = row({ ...air, ...landed('on_ground', 1) })                              // 30 m "up" is the flat-world offset far from home
    expect(landState(far, null)).toMatchObject({ onGround: true, source: 'px4_landed_state', phase: 'ground', landedState: 'on_ground' })
    expect(landState(row({ ...ground, ...landed('in_air', 2) }), null)).toMatchObject({ onGround: false, source: 'px4_landed_state' })
    expect(landState(row({ ...ground, ...landed('on_ground', 1, 30) }), null)).toMatchObject({ onGround: true, source: 'altitude_speed_fallback' })      // stale: fallback
    expect(landState(row({ ...ground, velocity: { ground_speed_m_s: 3 } }), null)).toMatchObject({ onGround: false, source: 'altitude_speed_fallback' })     // low but moving
    expect(landState(row({ armed: true, position: {} }), null)).toMatchObject({ allowed: true, onGround: null, source: null })
  })

  it('landState: landing is recognised from the run, PX4\'s landing state or the Land flight mode, and then the button is not offered again', () => {
    expect(landState(row(air), run('landing', true))).toMatchObject({ landing: true, allowed: false, reason: 'The aircraft is already landing.', phase: 'landing' })
    expect(landState(row({ ...air, ...landed('landing', 4) }), null).landing).toBe(true)
    expect(landState(row({ ...air, heartbeat: { flight_mode: 'AUTO_LAND' } }), null).landing).toBe(true)
  })

  it('in the air the button is enabled and the confirmation says what it does: lands here, disarms, no return to launch', async () => {
    apiMock.latestExecution.mockResolvedValue(execution({ state: 'returning', stage: 'executing' }))
    panel({ rows: [row(air)] })
    await waitFor(() => expect(button(/^LAND HERE$/).disabled).toBe(false))
    expect(button(/^LAND HERE$/).title).toBe('Lands at the current position and disarms')
    expect(screen.getByText(/Abort = return to launch · Land here = land at the current position and disarm · Restart = reset the simulation to the mission start/)).toBeTruthy()
    fireEvent.click(button(/^LAND HERE$/))
    const dialog = await screen.findByRole('dialog', { name: 'Confirm land here' })
    expect(dialog.textContent).toMatch(/lands the aircraft where it is now and disarms it\. It does not return to launch and it is not a motor kill/)
    expect(apiMock.landHere).not.toHaveBeenCalled()                                     // nothing is sent before the confirmation
    fireEvent.click(within(dialog).getByRole('button', { name: 'CANCEL' }))
    expect(apiMock.landHere).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog', { name: 'Confirm land here' })).toBeNull()
  })

  it('Land here sends one request for the aircraft, shows the landing progress, then "Landed and disarmed at hh:mm"', async () => {
    apiMock.latestExecution.mockResolvedValue(null)
    apiMock.landHere.mockResolvedValue({ aircraft_id: 'px4-1', already_landing: false, mode: 'air', state: 'landing', simulation_only: true })
    const view = panel({ rows: [row(air)] })
    await waitFor(() => expect(button(/^LAND HERE$/).disabled).toBe(false))
    fireEvent.click(button(/^LAND HERE$/))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Confirm land here' })).getByRole('button', { name: 'CONFIRM LAND HERE' }))
    await waitFor(() => expect(apiMock.landHere).toHaveBeenCalledWith('px4-1'))
    expect(apiMock.landHere).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('px4-1 is landing at its current position.')).toBeTruthy()
    expect((await screen.findByRole('status', { name: 'Landing status' })).textContent).toMatch(/Landing… 30 m/)
    view.rerender(<MissionExecutionPanel deployment={deployment} rows={[row({ ...air, ...landed('landing', 4), position: { relative_altitude_m: 0.5 } })]} activePollMs={25} idlePollMs={25} onRestart={restartSpy}/>)
    expect((await screen.findByRole('status', { name: 'Landing status' })).textContent).toMatch(/touching down/)
    view.rerender(<MissionExecutionPanel deployment={deployment} rows={[row({ ...ground, armed: false })]} activePollMs={25} idlePollMs={25} onRestart={restartSpy}/>)
    expect((await screen.findByRole('status', { name: 'Landing status' })).textContent).toMatch(/Landed and disarmed at \d{1,2}[:.]\d{2}/)
  })

  it('while the aircraft is landing the button shows LANDING… and cannot be pressed again', async () => {
    apiMock.latestExecution.mockResolvedValue(execution({ state: 'landing', stage: 'executing' }))
    panel({ rows: [row(air)] })
    await waitFor(() => expect(button(/LANDING…/).disabled).toBe(true))
    expect((await screen.findByRole('status', { name: 'Execution state' })).textContent).toBe('Landing at the current position')
    await waitFor(() => expect(reasons()['Land here']).toBe('The aircraft is already landing.'))
  })

  it('armed on the ground: the confirmation says normal disarm, and a finished run still armed on the ground gets a notice with LAND HERE…', async () => {
    apiMock.latestExecution.mockResolvedValue(finished('aborted', 'Aborted by Test General.'))
    apiMock.landHere.mockResolvedValue({ aircraft_id: 'px4-1', already_landing: false, mode: 'ground', state: 'disarmed', simulation_only: true })
    panel({ rows: [row(ground)] })
    const notice = await screen.findByLabelText('Armed on the ground')
    expect(notice.textContent).toMatch(/Armed on the ground.*still reports it armed/)
    fireEvent.click(within(notice).getByRole('button', { name: /LAND HERE…/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Confirm land here' })
    expect(dialog.textContent).toMatch(/armed on the ground: this sends a normal disarm \(not forced\)/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'CONFIRM LAND HERE' }))
    await waitFor(() => expect(apiMock.landHere).toHaveBeenCalledWith('px4-1'))
    expect(await screen.findByText(/px4-1 was armed on the ground: normal disarm sent \(not forced\)\./)).toBeTruthy()
  })

  it('a refusal from PX4 is shown with its exact reason and the values', async () => {
    apiMock.latestExecution.mockResolvedValue(execution())
    apiMock.landHere.mockRejectedValue(Object.assign(new Error('PX4 answered Land with MAV_RESULT_DENIED (PX4 said: Land denied: no position estimate); values used: AUTO LAND at relative altitude 30.0 m'), { status: 409, detail: { code: 'land_refused' } }))
    panel({ rows: [row(air)] })
    fireEvent.click(await screen.findByRole('button', { name: /^LAND HERE$/ }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Confirm land here' })).getByRole('button', { name: 'CONFIRM LAND HERE' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/MAV_RESULT_DENIED \(PX4 said: Land denied: no position estimate\); values used: AUTO LAND at relative altitude 30\.0 m \(HTTP 409 · land_refused\)/)
  })

  it('disarmed: disabled with "Already disarmed"; no notice while a run is still active', async () => {
    apiMock.latestExecution.mockResolvedValue(finished('completed'))
    const { unmount } = panel({ rows: [row({ ...ground, armed: false })] })
    await waitFor(() => expect(reasons()['Land here']).toBe('Already disarmed'))
    expect(button(/^LAND HERE$/).disabled).toBe(true)
    expect(screen.queryByLabelText('Armed on the ground')).toBeNull()
    unmount()
    apiMock.latestExecution.mockResolvedValue(execution({ state: 'executing' }))
    panel({ rows: [row(ground)] })
    await waitFor(() => expect(button(/^LAND HERE$/).disabled).toBe(false))
    expect(screen.queryByLabelText('Armed on the ground')).toBeNull()
  })

  it('the final state of a landed-in-place run reads "Aborted: landed in place"', async () => {
    apiMock.latestExecution.mockResolvedValue(finished('aborted', 'landed in place'))
    panel()
    const lifecycle = await screen.findByRole('list', { name: 'Mission lifecycle' })
    await waitFor(() => expect(lifecycle.textContent).toMatch(/Aborted: landed in place/))
    expect(lifecycle.textContent).not.toMatch(/Aborted: landed in placelanded in place/)
  })

  it('Restart at mission start is enabled in every state: idle, armed on the ground, flying; the dialog is opened by the page', async () => {
    for (const [latest, vehicle] of [[null, null], [finished('completed'), ground], [execution(), air], [execution({ state: 'paused' }), air]] as const) {
      apiMock.latestExecution.mockResolvedValue(latest)
      restartSpy.mockClear()
      const view = panel({ rows: vehicle ? [row(vehicle)] : [] })
      await waitFor(() => expect(button(/RESTART AT MISSION START/).disabled).toBe(false))
      expect(button(/RESTART AT MISSION START/).title).toMatch(/restarts PX4 and Gazebo.*a flight in progress ends/)
      fireEvent.click(button(/RESTART AT MISSION START/))
      expect(restartSpy).toHaveBeenCalledTimes(1)
      view.unmount()
    }
  })

  it('Restart is disabled only without a deployed mission or for a legacy deployment, with the reason', async () => {
    panel({ deployment: null })
    expect(screen.getByText('No deployed mission.')).toBeTruthy()                       // nothing to restart at: no controls at all
    expect(screen.queryByRole('button', { name: /RESTART AT MISSION START/ })).toBeNull()
    cleanup()
    panel({ deployment: { ...deployment, legacy: true } as unknown as MissionDeployment })
    await waitFor(() => expect(button(/RESTART AT MISSION START/).disabled).toBe(true))
  })
})


describe('walkthrough fixes: another deployment\'s finished run', () => {
  it('a finished run of a different deployment does not put "already aborted" beside this mission\'s buttons', async () => {
    apiMock.latestExecution.mockResolvedValue({ ...finished('aborted'), deployment_id: 'some-other-deployment' })
    panel()
    await waitFor(() => expect(reasons().Pause).toBe('Nothing is running.'))
    expect(reasons()).toMatchObject({ Resume: 'Nothing is running.', Abort: 'Nothing is running.' })
    expect(document.body.textContent).not.toMatch(/already aborted/)
  })

  it('an ACTIVE run of another deployment still offers Abort (the aircraft is flying)', async () => {
    apiMock.latestExecution.mockResolvedValue({ ...execution(), deployment_id: 'some-other-deployment' })
    panel()
    await waitFor(() => expect(button(/ABORT \(RETURN TO LAUNCH\)/).disabled).toBe(false))
    expect(reasons().Start).toBe('A run of another deployment is in progress on this aircraft.')
  })
})
