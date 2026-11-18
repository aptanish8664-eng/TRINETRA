import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActionRow, Assessment, LiveAircraft, LiveThreat, ResponseOption, Tier } from './attackLabTypes'
import { ThreatCard } from './ThreatCard'
import { ThreatMonitor } from './ThreatMonitor'
import { findGroundTruthKeys, sessionFreshness, threatSessionStore, type ThreatSession } from './threatSession'

const apiMock = vi.hoisted(() => ({
  researchModelInfo: vi.fn(), researchDecisions: vi.fn(), recordDecision: vi.fn(), researchLive: vi.fn(), threatResponseOptions: vi.fn(), respondToThreat: vi.fn(),
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

const RULE = { min_battery_pct: 40, enforced_by: 'server' as const, message: "A handover is refused when the receiving aircraft's battery is below 40%." }
const RATES = { GPS_RAW_INT: 10.3, LOCAL_POSITION_NED: 25.7, HIGHRES_IMU: 25.8 }
function liveAircraft(patch: Partial<LiveAircraft> = {}): LiveAircraft {
  return { aircraft_id: 'px4-1', armed: true, role: 'primary', role_source: 'deployed_plan', state: 'live', link: { status: 'connected', telemetry_age_s: 0.1, heartbeat_age_s: 0.5 },
    flight_mode: 'MISSION', rates_hz: RATES, missing_inputs: [], assessment: { ...assessment('Low', 3), source: 'live_telemetry', computed_at: '2026-11-14T10:00:00Z' }, assessment_age_s: 0,
    warming_up: null, data_quality: { clock: 'px4_boot_clock', sample_hz: 2, gps_delay_s: 0.04, velocity: 'interval_averaged', window_samples: 120, window_seconds: 60, velocity_reference: 'px4_estimator_velocity', velocity_reference_note: 'n', flags: [], estimator: null, vibration: null },
    ...patch }
}
const live = (...aircraft: LiveAircraft[]): LiveThreat => ({ source: 'live_telemetry', generated_at: '2026-11-14T10:00:00Z', model_available: true, flight_running: aircraft.some(item => item.armed), update_interval_s: 1, handover_rule: RULE, aircraft })

beforeEach(() => {
  apiMock.researchLive.mockResolvedValue(live())
  apiMock.researchModelInfo.mockResolvedValue({ available: true, formula: 'f', live_residual_inputs_available: false })
  apiMock.researchDecisions.mockResolvedValue([])
  apiMock.threatResponseOptions.mockRejectedValue(new Error('not offered in this test'))
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

  it('idle: one slim "Waiting for a flight" line, no empty box and no invented data', async () => {
    const { container } = render(<ThreatMonitor {...props()}/>)
    expect(await screen.findByText('Waiting for a flight.')).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
    expect(container.querySelector('.threat-monitor.idle')).toBeTruthy()
    expect(container.querySelector('.mc-empty')).toBeNull()
    expect(screen.queryByText(/Last offline replay/)).toBeNull()
  })

  it('idle with an earlier offline replay: offers a link to it instead of showing it', async () => {
    act(() => threatSessionStore.publish(session('Medium', 45, 'finished')))
    render(<ThreatMonitor {...props()}/>)
    const link = await screen.findByRole('button', { name: /Last offline replay: Drift test/ })
    expect(screen.queryByRole('meter')).toBeNull()
    fireEvent.click(link)
    expect((await screen.findAllByText(/Source: offline replay \(Drift test\)/)).length).toBeGreaterThan(0)
  })

  it('live: shows the backend assessment for a flying aircraft, Low and calm, with the measured input rates', async () => {
    apiMock.researchLive.mockResolvedValue(live(liveAircraft()))
    const handlers = props()
    render(<ThreatMonitor {...handlers}/>)
    expect(await screen.findByText('No threat detected')).toBeTruthy()
    expect(screen.queryByText('Waiting for a flight.')).toBeNull()
    expect(screen.getAllByText(/Source: live telemetry/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/GPS 10 Hz · velocity 26 Hz · IMU 26 Hz/).length).toBeGreaterThan(0)
    expect(screen.getByRole('meter').getAttribute('aria-valuenow')).toBe('3')
    expect(handlers.onTierTransitions).not.toHaveBeenCalled()
  })

  it('live: records a decision as live telemetry and reads the handover rule from the backend', async () => {
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ assessment: { ...assessment('High', 70), source: 'live_telemetry', computed_at: 'x' } })))
    render(<ThreatMonitor {...props()}/>)
    fireEvent.click(await screen.findByText(/Hand over to standby/))
    expect(screen.getByText(RULE.message)).toBeTruthy()
    fireEvent.click(screen.getByText('CONFIRM AND RECORD'))
    await waitFor(() => expect(apiMock.recordDecision).toHaveBeenCalled())
    expect(apiMock.recordDecision.mock.calls[0][0]).toMatchObject({ aircraft_id: 'px4-1', action: 'handover_to_standby', handover_candidate: 'px4-2', source: 'live_telemetry', tier: 'High', threat_score: 70 })
  })

  it('live: a refused handover shows the backend reason and does not claim it was recorded', async () => {
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ assessment: { ...assessment('High', 70), source: 'live_telemetry', computed_at: 'x' } })))
    apiMock.recordDecision.mockRejectedValueOnce(new Error('Handover refused: px4-2 battery 20% is below the 40% minimum.'))
    render(<ThreatMonitor {...props()}/>)
    fireEvent.click(await screen.findByText(/Hand over to standby/))
    fireEvent.click(screen.getByText('CONFIRM AND RECORD'))
    expect(await screen.findByText(/battery 20% is below the 40% minimum/)).toBeTruthy()
    expect(screen.queryByText(/The aircraft was not commanded/)).toBeNull()
  })

  it('live: collecting data, then a missing required input is named in amber', async () => {
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ state: 'warming_up', assessment: null, data_quality: null, warming_up: { seconds_collected: 4, seconds_needed: 10 } })))
    const { unmount } = render(<ThreatMonitor {...props()}/>)
    expect(await screen.findByText(/Collecting data: 4 of 10 s/)).toBeTruthy()
    unmount()
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ state: 'inputs_missing', assessment: null, data_quality: null, missing_inputs: [
      { input: 'estimator_velocity', title: 'Estimator velocity (LOCAL_POSITION_NED)', meaning: "PX4's velocity estimate is not arriving.", detail: 'last LOCAL_POSITION_NED was 6.0 s ago', required: true }] })))
    render(<ThreatMonitor {...props()}/>)
    const alert = await screen.findByText(/INPUT MISSING/)
    expect(alert.closest('.threat-live-note')?.classList.contains('amber')).toBe(true)
    expect(screen.getByText(/Estimator velocity \(LOCAL_POSITION_NED\)/)).toBeTruthy()
    expect(screen.getByText(/last LOCAL_POSITION_NED was 6.0 s ago/)).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
  })

  it('live: an optional missing input keeps the score and says what is missing', async () => {
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ missing_inputs: [{ input: 'imu', title: 'IMU (HIGHRES_IMU)', meaning: 'Raw IMU samples are not arriving.', detail: 'x', required: false }] })))
    render(<ThreatMonitor {...props()}/>)
    expect(await screen.findByRole('meter')).toBeTruthy()
    expect(screen.getByText(/Input missing \(score still computed\)/)).toBeTruthy()
  })

  it('live: a lost link marks the aircraft stale with the age and hides the old score', async () => {
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ state: 'link_lost', link: { status: 'disconnected', telemetry_age_s: 42, heartbeat_age_s: 42 }, assessment_age_s: 44 })))
    render(<ThreatMonitor {...props()}/>)
    expect(await screen.findByText('STALE')).toBeTruthy()
    expect(screen.getByText(/Link lost in flight: no telemetry for 42 s/)).toBeTruthy()
    expect(screen.getByText(/is 44 s old and is not updating/)).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
  })

  it('live: an aircraft that landed shows LANDED with its last assessment and age, not STALE or link lost', async () => {
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ state: 'landed', armed: false, landed_age_s: 35, assessment_age_s: 36,
      link: { status: 'disconnected', telemetry_age_s: 20, heartbeat_age_s: 20 } })))
    render(<ThreatMonitor {...props()}/>)
    expect(await screen.findByText('LANDED')).toBeTruthy()
    expect(screen.queryByText('STALE')).toBeNull()
    expect(screen.getByText(/Landed 35 s ago \(the link has since dropped\)\. The last assessment \(Low, score 3\) is 36 s old and is not updating\./)).toBeTruthy()
    expect(screen.queryByText(/Link lost/)).toBeNull()
    expect(screen.queryByRole('meter')).toBeNull()
    expect(screen.queryByRole('group', { name: /Response options/ })).toBeNull()
    expect(screen.queryByText('Waiting for a flight.')).toBeNull()
  })

  it('live: aircraft with nothing to show (idle, offline) leave the slim waiting line', async () => {
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ state: 'idle', armed: false, assessment: null, data_quality: null }),
      liveAircraft({ aircraft_id: 'px4-2', state: 'offline', armed: true, assessment: null, data_quality: null, link: { status: 'disconnected', telemetry_age_s: 4000, heartbeat_age_s: 4000 } })))
    render(<ThreatMonitor {...props()}/>)
    expect(await screen.findByText('Waiting for a flight.')).toBeTruthy()
    expect(screen.queryByText('STALE')).toBeNull()
  })

  it('handover candidates show "unavailable" for unknown values and are never offered on them', async () => {
    const candidates = [
      { aircraft_id: 'px4-2', role: 'standby', battery_pct: 85, distance_to_task_m: null, workload: null, eligible: true, ineligible_reason: null, score: 0.44, distance_known: false },
      { aircraft_id: 'px4-3', role: 'standby', battery_pct: null, distance_to_task_m: null, workload: null, eligible: false, ineligible_reason: 'battery unavailable: no live battery reading', score: 0.1, battery_known: false },
    ]
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ assessment: { ...assessment('High', 70, { handover_candidates: candidates }), source: 'live_telemetry', computed_at: 'x' } })))
    render(<ThreatMonitor {...props()}/>)
    fireEvent.click(await screen.findByText(/Hand over to standby/))
    const known = screen.getByLabelText(/px4-2/) as HTMLInputElement
    const unknown = screen.getByLabelText(/px4-3/) as HTMLInputElement
    expect(known.closest('label')?.textContent).toMatch(/battery 85% · distance to the task unavailable/)
    expect(unknown.closest('label')?.textContent).toMatch(/battery unavailable · distance to the task unavailable .*not eligible: battery unavailable/)
    expect(unknown.disabled).toBe(true)
    expect(known.checked).toBe(true)
    expect(document.body.textContent).not.toMatch(/\b0 m from the task/)
  })

  it('live: one alert per tier change, not per poll', async () => {
    const handlers = props()
    apiMock.researchLive.mockResolvedValue(live(liveAircraft()))
    render(<ThreatMonitor {...handlers}/>)
    await screen.findByText('No threat detected')
    apiMock.researchLive.mockResolvedValue(live(liveAircraft({ assessment: { ...assessment('High', 70), source: 'live_telemetry', computed_at: 'later' } })))
    await waitFor(() => expect(handlers.onTierTransitions).toHaveBeenCalledTimes(1), { timeout: 3000 })
    await new Promise(resolve => setTimeout(resolve, 1300))
    expect(handlers.onTierTransitions).toHaveBeenCalledTimes(1)
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
    expect(screen.queryByRole('meter')).toBeNull()                       // an old replay is not shown as current
    fireEvent.click(await screen.findByRole('button', { name: /Last offline replay: Drift test \(stopped\)/ }))
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


describe('ThreatMonitor real responses (simulator only)', () => {
  const props = () => ({ canRecord: true, onTierTransitions: vi.fn(), onTiers: vi.fn() })
  const options = (patch: Record<string, { enabled?: boolean; reason?: string | null }> = {}) => ({ aircraft_id: 'px4-1', simulation_only: true, note: '', options: [
    { action: 'acknowledge', label: 'Acknowledge and continue', meaning: 'm', enabled: true, reason: null },
    { action: 'hold', label: 'Hold (pause the mission)', meaning: 'Pauses the mission.', ...{ enabled: true, reason: null, ...patch.hold } },
    { action: 'resume', label: 'Resume the mission', meaning: 'm', ...{ enabled: false, reason: 'Resume is possible while paused; the run is executing.', ...patch.resume } },
    { action: 'return_to_launch', label: 'Return to launch', meaning: 'm', enabled: true, reason: null },
    { action: 'land_here', label: 'Land here', meaning: 'm', enabled: true, reason: null },
    { action: 'restart', label: 'Restart the simulation at the mission start', meaning: 'm', enabled: true, reason: null, runs_in_ui: true },
    { action: 'handover_to_standby', label: 'Hand over to a standby aircraft', meaning: 'm', enabled: false, reason: 'Needs more than one active aircraft.', multi_aircraft: true },
  ] })
  const highRow = () => live(liveAircraft({ assessment: { ...assessment('High', 70), source: 'live_telemetry', computed_at: 'x' } }))

  it('shows each response with its reason when disabled, and runs Hold only after confirmation', async () => {
    apiMock.researchLive.mockResolvedValue(highRow())
    apiMock.threatResponseOptions.mockResolvedValue(options())
    apiMock.respondToThreat.mockResolvedValue({ aircraft_id: 'px4-1', action: 'hold', outcome: 'executed', executed: true, message: 'The mission was paused (hold).', tier: 'High', threat_score: 70, evidence: [], simulation_only: true })
    render(<ThreatMonitor {...props()}/>)
    const group = await screen.findByRole('group', { name: 'Respond to px4-1' })
    expect((within(group).getByRole('button', { name: 'Resume the mission' }) as HTMLButtonElement).disabled).toBe(true)
    expect(group.textContent).toContain('Hand over to a standby aircraft: Needs more than one active aircraft.')
    expect(screen.queryByRole('group', { name: 'Response options for px4-1' })).toBeNull()      // the recorded-only list is replaced
    fireEvent.click(within(group).getByRole('button', { name: 'Hold (pause the mission)' }))
    expect(apiMock.respondToThreat).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: /^CONFIRM: HOLD/ }))
    await waitFor(() => expect(apiMock.respondToThreat).toHaveBeenCalledWith({ aircraft_id: 'px4-1', action: 'hold', confirm: true }))
    expect(await screen.findByText(/The mission was paused \(hold\)\. \(High, score 70\)/)).toBeTruthy()
  })

  it('acknowledge runs at once, restart opens the page dialog, and a refused command shows the reason', async () => {
    apiMock.researchLive.mockResolvedValue(highRow())
    apiMock.threatResponseOptions.mockResolvedValue(options())
    apiMock.respondToThreat.mockResolvedValueOnce({ aircraft_id: 'px4-1', action: 'acknowledge', outcome: 'acknowledged', executed: false, message: 'No command was sent.', tier: 'High', threat_score: 70, evidence: [], simulation_only: true })
    apiMock.respondToThreat.mockRejectedValueOnce(new Error('The aircraft is landing at its current position.'))
    const onRestart = vi.fn()
    render(<ThreatMonitor {...props()} onRestart={onRestart}/>)
    const group = await screen.findByRole('group', { name: 'Respond to px4-1' })
    fireEvent.click(within(group).getByRole('button', { name: 'Acknowledge and continue' }))
    await waitFor(() => expect(apiMock.respondToThreat).toHaveBeenCalledWith({ aircraft_id: 'px4-1', action: 'acknowledge', confirm: true }))
    fireEvent.click(within(group).getByRole('button', { name: 'Restart the simulation at the mission start' }))
    expect(onRestart).toHaveBeenCalledTimes(1)
    fireEvent.click(within(group).getByRole('button', { name: 'Return to launch' }))
    fireEvent.click(await screen.findByRole('button', { name: /^CONFIRM: RETURN TO LAUNCH/ }))
    expect(await screen.findByText('The aircraft is landing at its current position.')).toBeTruthy()
  })

  it('handover asks which standby aircraft, lists the ones that cannot be used with the reason, and sends the choice', async () => {
    apiMock.researchLive.mockResolvedValue(highRow())
    const base = options() as unknown as { options: ResponseOption[] }
    base.options = base.options.map(item => item.action === 'handover_to_standby' ? { ...item, enabled: true, reason: null, candidates: [
      { aircraft_id: 'px4-2', role: 'standby', battery_pct: 90, eligible: true, reason: null },
      { aircraft_id: 'px4-3', role: 'standby', battery_pct: 20, eligible: false, reason: 'battery 20% is below the 40% minimum' }] } : item)
    apiMock.threatResponseOptions.mockResolvedValue(base)
    apiMock.respondToThreat.mockResolvedValue({ aircraft_id: 'px4-1', action: 'handover_to_standby', outcome: 'executed', executed: true, message: 'px4-1 was sent back to launch and px4-2 started its own planned route.', tier: 'High', threat_score: 70, evidence: [], simulation_only: true })
    render(<ThreatMonitor {...props()}/>)
    const group = await screen.findByRole('group', { name: 'Respond to px4-1' })
    fireEvent.click(within(group).getByRole('button', { name: 'Hand over to a standby aircraft' }))
    const dialog = await screen.findByRole('group', { name: 'Confirm response' })
    expect((within(dialog).getByRole('radio', { name: /px4-3/ }) as HTMLInputElement).disabled).toBe(true)
    expect(dialog.textContent).toContain('not available: battery 20% is below the 40% minimum')
    fireEvent.click(within(dialog).getByRole('button', { name: /^CONFIRM: HAND OVER/ }))
    await waitFor(() => expect(apiMock.respondToThreat).toHaveBeenCalledWith({ aircraft_id: 'px4-1', action: 'handover_to_standby', confirm: true, candidate: 'px4-2' }))
  })

  it('shows a visible alert for High as well as Critical and the sound can be switched off', async () => {
    apiMock.researchLive.mockResolvedValue(highRow())
    render(<ThreatMonitor {...props()}/>)
    const banner = await screen.findByRole('alert')
    expect(banner.textContent).toMatch(/HIGH.*px4-1: threat tier High/)
    const sound = screen.getByRole('checkbox', { name: 'Alert sound' }) as HTMLInputElement
    expect(sound.checked).toBe(true)
    fireEvent.click(sound)
    expect(sound.checked).toBe(false)
    expect(localStorage.getItem('trinetra.threatSound')).toBe('off')
    localStorage.removeItem('trinetra.threatSound')
  })
})
