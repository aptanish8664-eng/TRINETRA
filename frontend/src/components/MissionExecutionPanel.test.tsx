import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MissionExecutionPanel, clock } from './MissionExecutionPanel'
import type { ExecutionEvent, ExecutionPreflight, ExecutionView } from '../services/api'
import type { MissionDeployment } from '../services/auth'
import type { AircraftRow } from './missionControlModel'
import { IN_THE_AIR, disarmState } from './missionControlModel'

const apiMock = vi.hoisted(() => ({
  latestExecution: vi.fn(), executionPreflight: vi.fn(), executionEvents: vi.fn(), startExecution: vi.fn(),
  pauseExecution: vi.fn(), resumeExecution: vi.fn(), abortExecution: vi.fn(), disarmAircraft: vi.fn(),
}))
vi.mock('../services/api', () => ({ api: apiMock }))

const CONFIRMATION = 'This runs your route in the PX4/Gazebo simulation. The aircraft will arm, take off and fly the plan.'
const deployment = { id: 'deployment-1', scenario_id: 's1', scenario_version_id: 'v1', scenario_title: 'Coastal relief', scenario_version_hash: 'h', mission_id: 's1',
  execution_status: 'x', legacy: false, schema_version: 2, intent: { schema_version: 2, mission_plan: { aircraft: [] } } } as unknown as MissionDeployment
const CHECK_KEYS = ['plan_valid', 'role', 'single_aircraft', 'fleet_verified', 'managed_instance', 'telemetry_fresh', 'gps_fix', 'disarmed_landed', 'at_mission_start', 'battery', 'no_execution_running', 'endurance']
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
const panel = (props: Partial<Parameters<typeof MissionExecutionPanel>[0]> = {}) =>
  render(<MissionExecutionPanel deployment={deployment} rows={[]} activePollMs={25} idlePollMs={25} {...props}/>)

beforeEach(() => {
  apiMock.latestExecution.mockResolvedValue(null)
  apiMock.executionPreflight.mockResolvedValue(preflight())
  apiMock.executionEvents.mockResolvedValue({ execution_id: 'e'.repeat(32), state: 'executing', events: [] })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('MissionExecutionPanel', () => {
  it('GENERAL: Start is enabled directly; the other controls are visible but disabled with their reasons', async () => {
    panel()
    await waitFor(() => expect(button(/START IN SIMULATION/).disabled).toBe(false))
    for (const name of [/^PAUSE$/, /^RESUME$/, /ABORT \(RETURN TO LAUNCH\)/]) expect(button(name).disabled).toBe(true)   // never hidden
    expect(reasons()).toEqual({ Pause: 'Nothing is running.', Resume: 'Nothing is running.', Abort: 'Nothing is running.', Disarm: 'No telemetry from the aircraft, so its armed state is unknown.' })
    expect(screen.getByLabelText('Role rule').textContent).toBe('Role rule: General accounts deploy and start directly.')
    const lifecycle = screen.getByRole('list', { name: 'Mission lifecycle' })
    expect(Array.from(lifecycle.querySelectorAll('li')).map(item => item.textContent)).toEqual(['Deployed to Mission Control', 'Uploaded to PX4not started', 'Executingnot started'])
    expect(apiMock.executionPreflight).toHaveBeenCalledWith('deployment-1')
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


describe('Disarm', () => {
  const row = (vehicle: Record<string, unknown> | null, id = 'px4-1'): AircraftRow => ({
    id, systemId: 1, gazeboModel: 'x500_0', pose: null, stale: false, plan: { uav_id: id, role: 'primary' } as AircraftRow['plan'],
    vehicle: vehicle && { id, system_id: 1, status: 'connected', ...vehicle } as unknown as AircraftRow['vehicle'],
  })
  const ground = { armed: true, position: { relative_altitude_m: 0.1 }, velocity: { ground_speed_m_s: 0 } }
  const air = { armed: true, position: { relative_altitude_m: 30 }, velocity: { ground_speed_m_s: 8 } }
  const run = (state: string, active: boolean) => ({ active, state, aircraft_id: 'px4-1' })

  it('disarmState follows the backend rule: armed + on the ground + nothing flying it', () => {
    expect(disarmState(row(ground), null)).toMatchObject({ allowed: true, armedOnGround: true })
    expect(disarmState(row(air), null)).toMatchObject({ allowed: false, reason: IN_THE_AIR, armedOnGround: false })
    expect(IN_THE_AIR).toBe('In the air: abort (return to launch) first; it disarms itself after landing.')
    expect(disarmState(row({ ...ground, velocity: { ground_speed_m_s: 3 } }), null).allowed).toBe(false)          // low but moving
    expect(disarmState(row({ ...ground, armed: false }), null).reason).toBe('The aircraft is already disarmed.')
    expect(disarmState(row({ ...ground, status: 'stale' }), null).reason).toMatch(/No telemetry/)
    expect(disarmState(row({ armed: true, position: {} }), null).reason).toMatch(/altitude is unknown/)
    expect(disarmState(null, null).allowed).toBe(false)
    expect(disarmState(row(ground), run('returning', true))).toMatchObject({ allowed: false, reason: IN_THE_AIR })      // a run is flying it
    expect(disarmState(row(ground), run('uploading', true)).reason).toBe('A run is uploading on this aircraft. Wait for it, or abort it first.')
    expect(disarmState(row(ground), run('aborted', false)).allowed).toBe(true)
  })

  it('in the air: the button is visible but disabled, with the abort-first reason', async () => {
    apiMock.latestExecution.mockResolvedValue(execution({ state: 'returning', stage: 'executing' }))
    panel({ rows: [row(air)] })
    await waitFor(() => expect(reasons().Disarm).toBe(IN_THE_AIR))
    expect(button(/^DISARM$/).disabled).toBe(true)
    expect(screen.queryByLabelText('Armed on the ground')).toBeNull()
  })

  it('a finished run with the aircraft still armed on the ground shows "Armed on the ground" and disarms after the in-page confirmation', async () => {
    apiMock.latestExecution.mockResolvedValue(finished('aborted', 'Aborted by Test General.'))
    apiMock.disarmAircraft.mockResolvedValue({ aircraft_id: 'px4-1', disarmed: true, armed: false, forced: false, px4_messages: [] })
    panel({ rows: [row(ground)] })
    const notice = await screen.findByLabelText('Armed on the ground')
    expect(notice.textContent).toMatch(/Armed on the ground.*still reports it armed/)
    expect(button(/^DISARM$/).disabled).toBe(false)
    fireEvent.click(within(notice).getByRole('button', { name: /DISARM…/ }))
    const confirmation = await screen.findByRole('dialog', { name: 'Confirm disarm' })
    expect(confirmation.textContent).toMatch(/normal disarm \(not forced\)/)
    expect(apiMock.disarmAircraft).not.toHaveBeenCalled()                    // nothing is sent before the confirmation
    fireEvent.click(within(confirmation).getByRole('button', { name: 'CANCEL' }))
    expect(apiMock.disarmAircraft).not.toHaveBeenCalled()
    fireEvent.click(button(/^DISARM$/))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Confirm disarm' })).getByRole('button', { name: 'CONFIRM DISARM' }))
    await waitFor(() => expect(apiMock.disarmAircraft).toHaveBeenCalledWith('px4-1'))
    expect(await screen.findByText(/px4-1 is disarmed \(normal disarm, not forced\)/)).toBeTruthy()
  })

  it('a refusal from PX4 is shown with its reason and the aircraft stays armed', async () => {
    apiMock.latestExecution.mockResolvedValue(finished('completed'))
    apiMock.disarmAircraft.mockRejectedValue(Object.assign(new Error('PX4 refused to disarm (DENIED). PX4 said: Disarming denied: not landed'), { status: 409, detail: { code: 'disarm_refused' } }))
    panel({ rows: [row(ground)] })
    fireEvent.click(await screen.findByRole('button', { name: /^DISARM$/ }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Confirm disarm' })).getByRole('button', { name: 'CONFIRM DISARM' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/PX4 refused to disarm \(DENIED\)\. PX4 said: Disarming denied: not landed \(HTTP 409 · disarm_refused\)/)
  })

  it('no notice while disarmed, and no notice while a run is still active', async () => {
    apiMock.latestExecution.mockResolvedValue(finished('completed'))
    const { unmount } = panel({ rows: [row({ ...ground, armed: false })] })
    await waitFor(() => expect(reasons().Disarm).toBe('The aircraft is already disarmed.'))
    expect(screen.queryByLabelText('Armed on the ground')).toBeNull()
    unmount()
    apiMock.latestExecution.mockResolvedValue(execution({ state: 'executing' }))
    panel({ rows: [row(ground)] })
    await waitFor(() => expect(reasons().Disarm).toBe(IN_THE_AIR))
    expect(screen.queryByLabelText('Armed on the ground')).toBeNull()
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
