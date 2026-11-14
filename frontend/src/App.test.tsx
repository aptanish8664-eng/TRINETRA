import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { api } from './services/api'
import type { UserSession } from './services/auth'

vi.mock('./services/api', () => ({
  api: {
    health: vi.fn(),
    session: vi.fn(),
    commandDecisions: vi.fn(),
    researchModelInfo: vi.fn(async () => ({ available: false, formula: '', live_residual_inputs_available: false })), researchDecisions: vi.fn(async () => []), attackExperiments: vi.fn(async () => []), attackLabLimits: vi.fn(), scenarioList: vi.fn(async () => []),
  },
}))

vi.mock('gsap', () => ({ default: { registerPlugin: vi.fn() } }))
vi.mock('gsap/ScrollTrigger', () => ({ ScrollTrigger: {} }))
vi.mock('./components/AccessPortal', () => ({ AccessPortal: () => null }))
vi.mock('./components/CommandDecisionPanel', () => ({ CommandDecisionPanel: () => null }))
vi.mock('./components/Map2D', () => ({ Map2D: () => null }))
vi.mock('./components/ScenarioWorkspace', () => ({ ScenarioWorkspace: () => null }))
vi.mock('./components/Px4MissionControl', () => ({ Px4MissionControl: () => <div data-testid="px4-mission-control">PX4 telemetry view</div> }))

const generalSession: UserSession = {
  expires_at: '2026-10-27T00:00:00Z',
  csrf_token: 'test-csrf',
  permissions: [
    'mission.read_all',
    'mission.create',
    'simulation.control',
    'command.approve',
  ],
  user: {
    id: 'general-user',
    username: 'trinetra-general',
    full_name: 'TRINETRA General',
    email: null,
    role: 'GENERAL',
    account_status: 'APPROVED',
  },
}

const apiMock = vi.mocked(api)

describe('Mission Control routing', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: true,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })))
    window.history.replaceState({}, '', '/mission-control')
    apiMock.health.mockResolvedValue({ status: 'ready', active_simulations: 0, database: 'sqlite' })
    apiMock.session.mockResolvedValue(generalSession)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('renders Mission Control from a direct link and preserves the session while navigating', async () => {
    const { unmount } = render(<App />)

    expect(await screen.findByTestId('px4-mission-control')).toBeTruthy()
    expect(window.location.pathname).toBe('/mission-control')
    expect(screen.queryByText('The backend verifies credentials and returns a revocable session.')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /^Research$/ }))
    expect(await screen.findByRole('heading', { name: /^Research$/ })).toBeTruthy()
    expect(window.location.pathname).toBe('/research')

    fireEvent.click(screen.getByRole('button', { name: /^Mission Control$/ }))
    expect(await screen.findByTestId('px4-mission-control')).toBeTruthy()
    expect(window.location.pathname).toBe('/mission-control')
    expect(apiMock.session).toHaveBeenCalledTimes(1)

    unmount()
    window.history.replaceState({}, '', '/mission-control')
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('px4-mission-control')).toBeTruthy())
  })

  it('keeps Operator mission access while hiding General-only navigation', async () => {
    apiMock.session.mockResolvedValue({
      ...generalSession,
      permissions: ['mission.read_own', 'mission.create', 'simulation.control'],
      user: { ...generalSession.user, id: 'operator-user', role: 'OPERATOR' },
    })

    render(<App />)

    expect(await screen.findByTestId('px4-mission-control')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^GENERAL$/ })).toBeNull()
  })

  it('shows only the live PX4 dashboard in Mission Control and keeps the abstract simulation under Research', async () => {
    render(<App />)

    expect(await screen.findByTestId('px4-mission-control')).toBeTruthy()
    expect(window.location.pathname).toBe('/mission-control')
    expect(screen.queryByRole('tab', { name: 'ABSTRACT RESEARCH SIMULATION' })).toBeNull()
    expect(screen.queryByRole('tab', { name: 'LIVE PX4 TELEMETRY' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Build a repeatable mission scenario.' })).toBeNull()
    expect(screen.queryByText(/Deterministic software simulation/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /^Research$/ }))
    const offline = await screen.findByRole('region', { name: 'Offline research simulation' })
    expect(offline.textContent).toMatch(/NOT LIVE PX4/)
    expect(screen.queryByRole('heading', { name: 'Build a repeatable mission scenario.' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'OPEN SIMULATION' }))
    expect(await screen.findByRole('heading', { name: 'Build a repeatable mission scenario.' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'CREATE & DEPLOY FLEET' }).hasAttribute('disabled')).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: /^Mission Control$/ }))
    expect(await screen.findByTestId('px4-mission-control')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Build a repeatable mission scenario.' })).toBeNull()
  })

  it('shows the signed-in General display name in the General header', async () => {
    window.history.replaceState({}, '', '/general')

    render(<App />)

    expect(await screen.findByText('TRINETRA General · GENERAL')).toBeTruthy()
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    fireEvent.click(screen.getByRole('button', { name: 'RETURN TO HOME' }))
    expect(await screen.findByRole('heading', { name: /See the signal/ })).toBeTruthy()
    expect(window.location.pathname).toBe('/')
    expect(document.querySelector('.hero-image img')?.getAttribute('fetchpriority')).toBeNull()
    expect(consoleError.mock.calls.flat().join(' ')).not.toContain('fetchPriority')
    consoleError.mockRestore()
  })
})
