import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActionRow, Assessment, Tier } from './attackLabTypes'
import { ThreatCard } from './ThreatCard'
import { ThreatMonitor } from './ThreatMonitor'
import { findGroundTruthKeys, sessionFreshness, threatSessionStore, type ThreatSession } from './threatSession'

const apiMock = vi.hoisted(() => ({
  researchModelInfo: vi.fn(), researchDecisions: vi.fn(), recordDecision: vi.fn(),
}))
vi.mock('../services/api', () => ({ api: apiMock }))

const ACTIONS: ActionRow[] = [
  { action: 'continue_monitor', label: 'Continue monitoring', reason: 'Nothing unusual', tradeoff: 'none', eligible: true, rank: 1, rank_score: 1 },
  { action: 'handover_to_standby', label: 'Hand over to standby', reason: 'Primary may be spoofed', tradeoff: 'standby leaves its post', eligible: true, rank: 2, rank_score: 0.8 },
  { action: 'return_to_launch', label: 'Return to launch', reason: 'Abort', tradeoff: 'mission ends', eligible: false, rank: 3, rank_score: 0.1 },
]
function assessment(tier: Tier, score: number, overrides: Partial<Assessment> = {}): Assessment {
  return {
    aircraft_id: 'px4-1', anomaly_confidence: 0.7, severity: 0.5, mission_impact: 0.4, threat_score: score, tier, threat_score_is_probability: false,
    evidence: tier === 'Low' ? [] : [{ feature: 'res_pos_w10', value: 30, reference: 3, unit: 'm', times_normal: 10, text: 'Position residual is 10x normal.' }],
    actions: ACTIONS,
    handover_candidates: [
      { aircraft_id: 'px4-2', role: 'standby', battery_pct: 80, distance_to_task_m: 120, workload: 0, eligible: true, ineligible_reason: null, score: 0.9 },
      { aircraft_id: 'px4-3', role: 'standby', battery_pct: 20, distance_to_task_m: 80, workload: 0, eligible: false, ineligible_reason: 'battery below 40%', score: 0 },
    ],
    t: 10, model_id: 'm1', ...overrides,
  }
}
function session(tier: Tier, score = 60, state: ThreatSession['state'] = 'playing', updatedAt = Date.now()): ThreatSession {
  return { sourceName: 'Drift test', experimentId: 'e1', state, updatedAt, minBatteryPct: 40,
    aircraft: { 'px4-1': { aircraftId: 'px4-1', role: 'primary', assessment: assessment(tier, score), history: [10, 20, score], tiers: [tier], battery: 90, reassignable: false } } }
}

beforeEach(() => {
  apiMock.researchModelInfo.mockResolvedValue({ available: true, formula: 'f', live_residual_inputs_available: false })
  apiMock.researchDecisions.mockResolvedValue([])
  apiMock.recordDecision.mockImplementation(async (payload: Record<string, unknown>) => ({
    id: 'd1', username: 'op', created_at: new Date().toISOString(), executed: false, handover_candidate: null, ...payload,
  }))
})
afterEach(() => { cleanup(); act(() => threatSessionStore.publish(null)); vi.clearAllMocks() })

describe('ThreatCard', () => {
  const base = { aircraftId: 'px4-1', history: [10, 30], sourceLine: 'Source: offline replay (x)', canRecord: true, decisions: [], onConfirm: vi.fn(async () => undefined) }

  it.each(['Low', 'Medium', 'High', 'Critical'] as Tier[])('shows the %s tier with a score that is not called a probability', tier => {
    render(<ThreatCard {...base} assessment={assessment(tier, 55)} state="ok"/>)
    expect(screen.getByText(tier.toUpperCase())).toBeTruthy()
    expect(screen.getByText(/not a probability/i)).toBeTruthy()
    expect(screen.getByText('Spoofing confidence')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/probability of/i)
  })

  it('says "No threat detected" for a calm Low aircraft', () => {
    render(<ThreatCard {...base} assessment={assessment('Low', 3)} state="ok"/>)
    expect(screen.getByText('No threat detected')).toBeTruthy()
  })

  it('hides stale and missing assessments instead of showing old numbers', () => {
    const { rerender } = render(<ThreatCard {...base} assessment={assessment('High', 70)} state="stale"/>)
    expect(screen.getByText('STALE')).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
    rerender(<ThreatCard {...base} assessment={null} state="no_data"/>)
    expect(screen.getByText('NO DATA')).toBeTruthy()
  })

  it('lists ranked options, disables unavailable ones, and gives the handover candidate eligibility', () => {
    render(<ThreatCard {...base} assessment={assessment('High', 70)} state="ok"/>)
    const group = screen.getByRole('group', { name: /Response options/ })
    const labels = within(group).getAllByRole('button').map(button => button.textContent)
    expect(labels[0]).toMatch(/^1\. Continue/)
    expect(labels[2]).toMatch(/unavailable/)
    expect((within(group).getByText(/Return to launch/) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(within(group).getByText(/Hand over to standby/))
    expect(screen.getByText(/not eligible: battery below 40%/)).toBeTruthy()
    const low = screen.getByLabelText(/px4-3/) as HTMLInputElement
    expect(low.disabled).toBe(true)
  })

  it('confirms in the page (no native dialog) and records without commanding the aircraft', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    const alertSpy = vi.spyOn(window, 'alert')
    const onConfirm = vi.fn(async () => undefined)
    render(<ThreatCard {...base} onConfirm={onConfirm} assessment={assessment('High', 70)} state="ok"/>)
    fireEvent.click(screen.getByRole('button', { name: /Continue monitoring/ }))
    expect(screen.getByText('This records your decision. It does not command the aircraft yet.')).toBeTruthy()
    fireEvent.click(screen.getByText('CONFIRM AND RECORD'))
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith('continue_monitor', null))
    expect(await screen.findByText(/The aircraft was not commanded/)).toBeTruthy()
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(alertSpy).not.toHaveBeenCalled()
  })

  it('requires an eligible candidate for a handover decision', async () => {
    const onConfirm = vi.fn(async () => undefined)
    render(<ThreatCard {...base} onConfirm={onConfirm} assessment={assessment('High', 70)} state="ok"/>)
    fireEvent.click(screen.getByText(/Hand over to standby/))
    fireEvent.click(screen.getByText('CONFIRM AND RECORD'))
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith('handover_to_standby', 'px4-2'))
  })

  it('disables recording for accounts that cannot record', () => {
    render(<ThreatCard {...base} canRecord={false} assessment={assessment('High', 70)} state="ok"/>)
    const group = screen.getByRole('group', { name: /Response options/ })
    expect(within(group).getAllByRole('button').every(button => (button as HTMLButtonElement).disabled)).toBe(true)
  })
})

describe('ThreatMonitor', () => {
  const props = () => ({ canRecord: true, onTierTransitions: vi.fn(), onTiers: vi.fn() })

  it('shows the honest fallback and no invented data without a source', async () => {
    render(<ThreatMonitor {...props()}/>)
    expect(await screen.findByText(/Live residual inputs not available; showing offline replay only/)).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
  })

  it('renders the replay session with its source line', async () => {
    act(() => threatSessionStore.publish(session('Medium', 45)))
    render(<ThreatMonitor {...props()}/>)
    expect((await screen.findAllByText(/Source: offline replay \(Drift test\)/)).length).toBeGreaterThan(0)
    expect(screen.getByRole('meter').getAttribute('aria-valuenow')).toBe('45')
  })

  it('keeps a baseline aircraft Low and calm', async () => {
    act(() => threatSessionStore.publish(session('Low', 2)))
    const handlers = props()
    render(<ThreatMonitor {...handlers}/>)
    expect(await screen.findByText('No threat detected')).toBeTruthy()
    expect(handlers.onTierTransitions).not.toHaveBeenCalled()
  })

  it('alerts once per tier transition, not once per update', async () => {
    const handlers = props()
    act(() => threatSessionStore.publish(session('Low', 5)))
    render(<ThreatMonitor {...handlers}/>)
    await screen.findAllByText(/Source: offline replay/)
    act(() => threatSessionStore.publish(session('High', 70, 'playing', Date.now() + 1)))
    await waitFor(() => expect(handlers.onTierTransitions).toHaveBeenCalledTimes(1))
    act(() => threatSessionStore.publish(session('High', 72, 'playing', Date.now() + 2)))
    act(() => threatSessionStore.publish(session('High', 74, 'playing', Date.now() + 3)))
    expect(handlers.onTierTransitions).toHaveBeenCalledTimes(1)
    expect(handlers.onTierTransitions.mock.calls[0][0][0].message).toMatch(/Low → High/)
  })

  it('shows a Critical banner until it is acknowledged', async () => {
    act(() => threatSessionStore.publish(session('Critical', 92)))
    render(<ThreatMonitor {...props()}/>)
    const banner = await screen.findByRole('alert')
    expect(banner.textContent).toMatch(/CRITICAL/)
    fireEvent.click(within(banner).getByText('ACKNOWLEDGE'))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('marks an old session as STALE and hides its numbers', async () => {
    act(() => threatSessionStore.publish(session('High', 70, 'playing', Date.now() - 120_000)))
    render(<ThreatMonitor {...props()}/>)
    expect(await screen.findByText(/STALE: no recent update/)).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
  })

  it('records a decision through the API as an offline-replay decision', async () => {
    act(() => threatSessionStore.publish(session('High', 70)))
    render(<ThreatMonitor {...props()}/>)
    fireEvent.click(await screen.findByRole('button', { name: /Continue monitoring/ }))
    fireEvent.click(screen.getByText('CONFIRM AND RECORD'))
    await waitFor(() => expect(apiMock.recordDecision).toHaveBeenCalledTimes(1))
    expect(apiMock.recordDecision.mock.calls[0][0]).toMatchObject({ aircraft_id: 'px4-1', action: 'continue_monitor', source: 'offline_replay' })
  })

  it('rejects session data that contains ground-truth fields and never renders them', async () => {
    const bad = { ...session('High', 70), attack_schedule: [{ type: 'drift', start_s: 12 }] } as unknown as ThreatSession
    act(() => threatSessionStore.publish(bad))
    render(<ThreatMonitor {...props()}/>)
    expect(await screen.findByText(/rejected because it contained ground-truth fields/)).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
    expect(document.body.textContent).not.toMatch(/start_s|drift/)
  })
})

describe('threat session helpers', () => {
  it('detects ground-truth key names at any depth', () => {
    expect(findGroundTruthKeys({ a: { is_attacked: true } })).toHaveLength(1)
    expect(findGroundTruthKeys({ aircraft: [{ label: 1 }] })).toHaveLength(1)
    expect(findGroundTruthKeys(session('Low'))).toEqual([])
  })
  it('classifies freshness', () => {
    const now = Date.now()
    expect(sessionFreshness(null, now)).toBe('no_data')
    expect(sessionFreshness(session('Low', 1, 'playing', now), now)).toBe('ok')
    expect(sessionFreshness(session('Low', 1, 'playing', now - 60_000), now)).toBe('stale')
    expect(sessionFreshness(session('Low', 1, 'finished', now - 60_000), now)).toBe('ended')
  })
})
