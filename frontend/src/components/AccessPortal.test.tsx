import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AccessPortal } from './AccessPortal'
import { api } from '../services/api'

vi.mock('../services/api', () => ({
  api: {
    register: vi.fn(),
    registrationStatus: vi.fn(),
    login: vi.fn(),
    generalUsers: vi.fn(),
    pendingScenarioApprovals: vi.fn(),
    generalAudit: vi.fn(),
    reviewScenario: vi.fn(),
    generalScenarios: vi.fn(),
  },
}))

vi.mock('gsap', () => ({
  default: {
    context: vi.fn(() => ({ revert: vi.fn() })),
  },
}))

vi.mock('framer-motion', () => ({
  motion: { section: 'section' },
}))

const apiMock = vi.mocked(api)

describe('registration receipt and status', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
    apiMock.register.mockResolvedValue({ application_id: 'application-123', status: 'PENDING' })
    apiMock.registrationStatus.mockResolvedValue({
      application_id: 'application-123',
      status: 'PENDING',
      rejection_message: null,
      assigned_role: null,
    })
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('shows the issued application ID and lets the applicant check its status', async () => {
    render(<AccessPortal
      page="register"
      onPageChange={vi.fn()}
      onExit={vi.fn()}
      onAuthenticated={vi.fn()}
      userSession={null}
      apiReady={true}
      streamConnected={false}
      hasActiveRun={false}
    />)

    fireEvent.change(screen.getByRole('textbox', { name: 'Full name' }), { target: { value: 'Vanashree' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Username' }), { target: { value: 'TRINETRA-OPERATOR' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'StrongPassword123' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'StrongPassword123' } })
    fireEvent.click(screen.getByRole('button', { name: /SUBMIT REGISTRATION/ }))

    expect(await screen.findByText('application-123')).toBeTruthy()
    expect(apiMock.register).toHaveBeenCalledWith(expect.objectContaining({ email: null }))
    expect(screen.getByText('YOUR REGISTRATION ID')).toBeTruthy()
    expect(screen.getByText(/STATUS:/).textContent).toContain('PENDING')
    expect((screen.getByRole('textbox', { name: 'Application ID' }) as HTMLInputElement).value).toBe('application-123')
    expect(apiMock.registrationStatus).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'CHECK' }))
    await waitFor(() => expect(apiMock.registrationStatus).toHaveBeenCalledWith('application-123'))
    expect(await screen.findByText(/STATUS:/)).toBeTruthy()
  })
})


describe('sign-in and scenario review (walkthrough fixes)', () => {
  const props = { onPageChange: vi.fn(), onExit: vi.fn(), onAuthenticated: vi.fn(), apiReady: true, streamConnected: false, hasActiveRun: false }
  beforeEach(() => { vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true }))) })
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks() })

  it('after the server refuses the credentials the stage list stops saying "in progress" until the user changes them', async () => {
    apiMock.login.mockRejectedValue(new Error('Invalid username or password'))
    render(<AccessPortal page="login" userSession={null} {...props}/>)
    fireEvent.change(screen.getByRole('textbox', { name: 'Username' }), { target: { value: 'walkgeneral' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong-password-1' } })
    expect(screen.getByText('IN PROGRESS')).toBeTruthy()
    fireEvent.click(screen.getAllByRole('button', { name: /SIGN IN/ }).pop()!)
    expect(await screen.findByText('Invalid username or password')).toBeTruthy()
    expect(screen.getByText('CHECK DETAILS')).toBeTruthy()
    expect(screen.queryByText('IN PROGRESS')).toBeNull()
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'another-try-1' } })
    expect(screen.getByText('IN PROGRESS')).toBeTruthy()
  })

  const general = { expires_at: 'x', csrf_token: 'c', permissions: [], user: { id: 'u1', username: 'walkgeneral', full_name: 'Walk General', email: null, role: 'GENERAL', account_status: 'APPROVED' } }
  const request = {
    id: 'r1', scenario_id: 's1', scenario_title: 'Operator route', scenario_version_id: 'v1', version_number: 1, status: 'PENDING', reason: null, reviewer_name: null,
    submitted_at: '2026-11-15T08:00:00Z', reviewed_at: null, sandbox_run_id: null, latest_version: true, config_hash: 'c9e85c7675b9db14aa', owner_user_id: 'o1', owner_name: 'Walk Operator',
    config: { uav_count: 4, seed: 21 },
    intent: { schema_version: 2, uav_fault_assignments: [], mission_plan: { objectives: ['custom_mission'], aircraft: [{ uav_id: 'px4-1', role: 'primary', task_type: 'waypoint_route', altitude_m: 30, speed_mps: 8,
      route: { waypoints: [1, 2, 3, 4, 5, 6], length_m: 1540, duration_s: 193 } }] } },
  }
  const renderGeneral = () => {
    apiMock.generalUsers.mockResolvedValue([])
    apiMock.pendingScenarioApprovals.mockResolvedValue([request] as never)
    apiMock.generalAudit.mockResolvedValue({ events: [], total: 0 } as never)
    apiMock.reviewScenario.mockResolvedValue({} as never)
    const view = render(<AccessPortal page="general" userSession={general as never} {...props}/>)
    fireEvent.click(screen.getByRole('button', { name: 'Scenario reviews' }))      // the General control center is tabbed
    return view
  }

  it('the review note is asked in the page (no native prompt) and the decision is sent only after confirming', async () => {
    const prompt = vi.spyOn(window, 'prompt')
    renderGeneral()
    fireEvent.click(await screen.findByRole('button', { name: 'APPROVE VERSION' }))
    const form = screen.getByRole('group', { name: 'Review note' })
    expect(prompt).not.toHaveBeenCalled()
    expect(apiMock.reviewScenario).not.toHaveBeenCalled()
    const note = form.querySelector('textarea') as HTMLTextAreaElement
    expect(note.value).toBe('Approved after review.')
    fireEvent.change(note, { target: { value: '' } })
    expect((screen.getByRole('button', { name: 'CONFIRM APPROVAL' }) as HTMLButtonElement).disabled).toBe(true)      // a note is required
    expect(screen.getByText('A note is required.')).toBeTruthy()
    fireEvent.change(note, { target: { value: 'Route checked against the area.' } })
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM APPROVAL' }))
    await waitFor(() => expect(apiMock.reviewScenario).toHaveBeenCalledWith('r1', 'approve', 'Route checked against the area.'))
  })

  it('the control center is tabbed (users, scenario reviews, saved scenarios, audit log) and Saved scenarios loads every user scenario', async () => {
    apiMock.generalScenarios.mockResolvedValue([])
    renderGeneral()
    const tabs = screen.getByRole('navigation', { name: 'General control center sections' })
    expect(Array.from(tabs.querySelectorAll('button')).map(item => item.textContent)).toEqual(['Users and requests', 'Scenario reviews', 'Saved scenarios', 'Audit log'])
    fireEvent.click(screen.getByRole('button', { name: 'Saved scenarios' }))
    await waitFor(() => expect(apiMock.generalScenarios).toHaveBeenCalled())
    expect(screen.getByRole('region', { name: 'Saved scenarios' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'APPROVE VERSION' })).toBeNull()
  })

  it('cancel closes the form without a decision, and rejecting asks for a reason', async () => {
    renderGeneral()
    fireEvent.click(await screen.findByRole('button', { name: 'REJECT' }))
    expect((screen.getByRole('group', { name: 'Review note' }).querySelector('textarea') as HTMLTextAreaElement).value).toBe('')
    expect(screen.getByRole('button', { name: 'CONFIRM REJECTION' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'CANCEL' }))
    expect(screen.queryByRole('group', { name: 'Review note' })).toBeNull()
    expect(apiMock.reviewScenario).not.toHaveBeenCalled()
  })

  it('the reviewer sees the mission plan (aircraft, task, waypoints, length, duration) before the legacy simulator fields', async () => {
    renderGeneral()
    const summary = await screen.findByLabelText('Mission summary')
    expect(summary.textContent).toContain('custom mission')
    expect(summary.textContent).toContain('px4-1 · primary · waypoint route · 30 m at 8 m/s · 6 waypoints · 1.54 km · about 3.2 min')
    expect(screen.getByText('SIMULATOR CONFIGURATION (legacy fields)')).toBeTruthy()
  })
})
