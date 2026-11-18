/** No native browser dialog is used anywhere: every confirmation is an in-page form that works when dialogs are suppressed. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AccessPortal } from './AccessPortal'
import { CommandDecisionPanel } from './CommandDecisionPanel'
import { PlanMapEditor } from './PlanMapEditor'
import { SimulationLocationNotice } from './SimulationLocationNotice'
import { api } from '../services/api'
import { EMPTY_PLAN, type AircraftPlan, type MissionPlan } from './missionPlan'

vi.mock('../services/api', () => ({
  api: {
    generalUsers: vi.fn(), pendingScenarioApprovals: vi.fn(), generalAudit: vi.fn(), decideRegistration: vi.fn(), setUserRole: vi.fn(), setUserActive: vi.fn(),
    commandDecisions: vi.fn(), reviewCommandDecision: vi.fn(), simulationLocation: vi.fn(), relocateSimulation: vi.fn(),
  },
}))
vi.mock('gsap', () => ({ default: { context: vi.fn(() => ({ revert: vi.fn() })) } }))
vi.mock('framer-motion', () => ({ motion: { section: 'section' } }))
vi.mock('./MissionPlannerMap', () => ({ MissionPlannerMap: () => <div>planner map</div> }))

const apiMock = vi.mocked(api)
const general = { expires_at: 'x', csrf_token: 'c', permissions: [], user: { id: 'g1', username: 'boss', full_name: 'Boss General', email: null, role: 'GENERAL', account_status: 'APPROVED' } }
const person = (id: string, status: string, role: string | null) => ({ id, username: `user-${id}`, full_name: `Person ${id}`, email: null, role, account_status: status, requested_role: 'OPERATOR', created_at: '2026-11-15T08:00:00Z', registration_note: null })

let prompt: ReturnType<typeof vi.spyOn>
let confirm: ReturnType<typeof vi.spyOn>
beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
  prompt = vi.spyOn(window, 'prompt').mockImplementation(() => { throw new Error('native prompt used') })
  confirm = vi.spyOn(window, 'confirm').mockImplementation(() => { throw new Error('native confirm used') })
  apiMock.pendingScenarioApprovals.mockResolvedValue([])
  apiMock.generalAudit.mockResolvedValue({ events: [], total: 0 } as never)
  apiMock.decideRegistration.mockResolvedValue({} as never)
  apiMock.setUserRole.mockResolvedValue({} as never)
  apiMock.setUserActive.mockResolvedValue({} as never)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.clearAllMocks() })

const portal = (users: unknown[]) => {
  apiMock.generalUsers.mockResolvedValue(users as never)
  return render(<AccessPortal page="general" userSession={general as never} onPageChange={vi.fn()} onExit={vi.fn()} onAuthenticated={vi.fn()} apiReady streamConnected={false} hasActiveRun={false}/>)
}

describe('General account management without native dialogs', () => {
  it('rejecting a registration asks for the optional reason in the page and sends nothing until confirmed', async () => {
    portal([person('p1', 'PENDING', null)])
    fireEvent.click(await screen.findByRole('button', { name: 'REJECT' }))
    const form = screen.getByRole('group', { name: 'Confirm account change' })
    expect(form.textContent).toContain('Reject the registration of user-p1?')
    expect(apiMock.decideRegistration).not.toHaveBeenCalled()
    fireEvent.change(form.querySelector('textarea')!, { target: { value: 'Not a project member.' } })
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM' }))
    await waitFor(() => expect(apiMock.decideRegistration).toHaveBeenCalledWith('p1', 'reject', undefined, 'Not a project member.'))
    expect(prompt).not.toHaveBeenCalled()
  })

  it('cancelling a rejection sends nothing; rejecting without a reason is allowed (the reason is optional)', async () => {
    portal([person('p1', 'PENDING', null)])
    fireEvent.click(await screen.findByRole('button', { name: 'REJECT' }))
    fireEvent.click(screen.getByRole('button', { name: 'CANCEL' }))
    expect(screen.queryByRole('group', { name: 'Confirm account change' })).toBeNull()
    expect(apiMock.decideRegistration).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'REJECT' }))
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM' }))
    await waitFor(() => expect(apiMock.decideRegistration).toHaveBeenCalledWith('p1', 'reject', undefined, undefined))
  })

  it('changing a role says sessions will be revoked and needs a confirmation click', async () => {
    portal([person('p2', 'APPROVED', 'OPERATOR')])
    fireEvent.change(await screen.findByLabelText('ROLE'), { target: { value: 'GENERAL' } })
    fireEvent.click(screen.getByRole('button', { name: 'SAVE ROLE' }))
    expect(screen.getByRole('group', { name: 'Confirm account change' }).textContent).toContain("Change user-p2's role to GENERAL? Their active sessions will be revoked.")
    expect(apiMock.setUserRole).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM' }))
    await waitFor(() => expect(apiMock.setUserRole).toHaveBeenCalledWith('p2', 'GENERAL'))
    expect(confirm).not.toHaveBeenCalled()
  })

  it('deactivating and activating an account are confirmed in the page', async () => {
    portal([person('p3', 'APPROVED', 'OPERATOR'), person('p4', 'DEACTIVATED', 'OPERATOR')])
    fireEvent.click(await screen.findByRole('button', { name: 'DEACTIVATE' }))
    expect(screen.getByText('Deactivate user-p3?')).toBeTruthy()
    expect(apiMock.setUserActive).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM' }))
    await waitFor(() => expect(apiMock.setUserActive).toHaveBeenCalledWith('p3', false))
    fireEvent.click(await screen.findByRole('button', { name: 'ACTIVATE' }))
    expect(screen.getByText('Activate user-p4?')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM' }))
    await waitFor(() => expect(apiMock.setUserActive).toHaveBeenCalledWith('p4', true))
    expect(confirm).not.toHaveBeenCalled()
  })
})

describe('command rejection without a native prompt', () => {
  const request = { id: 'c1', requester_id: 'other', requester_username: 'other', reviewer_id: null, run_id: 'run-1', command: 'stop', payload: {}, status: 'pending', created_at: '2026-11-15T10:00:00Z', decided_at: null, decision_reason: null }
  it('asks for the reason in the page, requires it, and sends the decision only after confirming', async () => {
    apiMock.commandDecisions.mockResolvedValue([request] as never)
    apiMock.reviewCommandDecision.mockResolvedValue({ id: 'c1', status: 'rejected' } as never)
    render(<CommandDecisionPanel runId="run-1" currentUserId="me" canRequest canApprove refreshKey={0} onExecuted={vi.fn()}/>)
    fireEvent.click(await screen.findByRole('button', { name: 'REJECT' }))
    const confirmButton = screen.getByRole('button', { name: 'CONFIRM REJECTION' }) as HTMLButtonElement
    expect(confirmButton.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText(/REASON FOR REJECTING/), { target: { value: 'Not needed now.' } })
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM REJECTION' }))
    await waitFor(() => expect(apiMock.reviewCommandDecision).toHaveBeenCalledWith('c1', 'reject', 'Not needed now.'))
    expect(prompt).not.toHaveBeenCalled()
  })
})

describe('sign-in page after the server ended the session', () => {
  const login = (sessionEnded?: boolean) => render(<AccessPortal page="login" userSession={null} onPageChange={vi.fn()} onExit={vi.fn()} onAuthenticated={vi.fn()} apiReady streamConnected={false} hasActiveRun={false} sessionEnded={sessionEnded}/>)
  it('says the session ended only when it did', () => {
    login(true)
    expect(screen.getByText(/Your session ended/).closest('.access-session-ended')).toBeTruthy()
    cleanup()
    login(false)
    expect(screen.queryByText(/Your session ended/)).toBeNull()
  })
})

describe('map hints', () => {
  const aircraft = (start: AircraftPlan['start_location']): AircraftPlan => ({ uav_id: 'px4-1', role: 'primary', priority: 1, reassignable: true, task_type: 'waypoint_route', parameters: {}, start_location: start,
    altitude_m: 30, speed_mps: 8, loiter_duration_s: 0, return_mode: 'return_to_start', route: null, confirmed: false })
  const editor = (start: AircraftPlan['start_location']) => <PlanMapEditor scope="aircraft" plan={{ ...EMPTY_PLAN } as MissionPlan} aircraft={aircraft(start)} onAreaChange={vi.fn()} onAircraftChange={vi.fn()}/>

  it('"Click the map to set the start" shows while there is no start and disappears once it is set', () => {
    const view = render(editor(null))
    fireEvent.click(screen.getByRole('button', { name: 'SET START' }))
    expect(screen.getByText('Click the map to set the start.')).toBeTruthy()
    view.rerender(editor({ latitude: 12.97, longitude: 77.59 }))
    expect(screen.queryByText('Click the map to set the start.')).toBeNull()
  })
})

describe('location notice in flight', () => {
  const where = { scenario_id: 's', scenario_version_id: 'v', max_distance_to_start_m: 0, max_distance_to_area_m: 0, position_known: true, mission_start: null, aircraft: [],
    relocation_recommended: false, far_from_mission_area: false, message: 'The aircraft is 0 m from the mission start.', thresholds: { relocation_notice_m: 1000, far_from_area_m: 50000, home_tolerance_m: 50 } }
  it('hides the plain distance line while a flight runs, and shows it before one', async () => {
    apiMock.simulationLocation.mockResolvedValue(where as never)
    const view = render(<SimulationLocationNotice scenarioId="s" versionId="v" canRelocate inFlight/>)
    await waitFor(() => expect(apiMock.simulationLocation).toHaveBeenCalled())
    expect(screen.queryByText(/0 m from the mission start/)).toBeNull()
    view.rerender(<SimulationLocationNotice scenarioId="s" versionId="v" canRelocate/>)
    expect(await screen.findByText(/0 m from the mission start/)).toBeTruthy()
  })
  it('a far aircraft still gets the explicit relocation notice during the switch (not in flight)', async () => {
    apiMock.simulationLocation.mockResolvedValue({ ...where, max_distance_to_start_m: 3400, relocation_recommended: true, message: 'The aircraft is 3.4 km from the mission start.',
      mission_start: { latitude: 12.97, longitude: 77.59, altitude_m: 0, uav_id: 'px4-1', role: 'primary', definition: 'x' } } as never)
    render(<SimulationLocationNotice scenarioId="s" versionId="v" canRelocate/>)
    expect(await screen.findByRole('button', { name: /RESTART AT MISSION START/ })).toBeTruthy()
    expect(apiMock.relocateSimulation).not.toHaveBeenCalled()                       // never without the confirmation
  })
})
