import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Assessment, Replay, ReplayEvaluation } from './attackLabTypes'
import { ThreatReplayPanel } from './ThreatReplayPanel'
import { findGroundTruthKeys, threatSessionStore } from './threatSession'

const apiMock = vi.hoisted(() => ({
  researchModelInfo: vi.fn(), attackExperiments: vi.fn(), researchDecisions: vi.fn(), createReplay: vi.fn(),
  assessThreat: vi.fn(), finishReplay: vi.fn(), replayEvaluation: vi.fn(), recordDecision: vi.fn(),
}))
vi.mock('../services/api', () => ({ api: apiMock }))

const SAMPLES = 60
const series = (value: number[]) => Array.from({ length: SAMPLES }, () => value)
const replay: Replay = {
  replay_id: 'r1', experiment_name: 'Drift test', experiment_id: 'e1', hz: 2, duration_s: 30, min_battery_pct: 40, candidate_roles: ['standby'], source: 'offline_replay',
  aircraft: [{ aircraft_id: 'px4-1', role: 'primary', reassignable: false, t: Array.from({ length: SAMPLES }, (_, i) => i / 2),
    gps_position: series([1, 2, 30]), gps_velocity: series([1, 0, 0]), imu_velocity: series([1, 0, 0]), battery_pct: Array.from({ length: SAMPLES }, () => 90) }],
}
const assessment: Assessment = {
  aircraft_id: 'px4-1', anomaly_confidence: 0.6, severity: 0.5, mission_impact: 0.4, threat_score: 35, tier: 'Medium', threat_score_is_probability: false,
  evidence: [{ feature: 'res_pos_w10', value: 20, reference: 5, unit: 'm', times_normal: 4, text: 'Position residual is 4x normal.' }],
  actions: [{ action: 'continue_monitor', label: 'Continue monitoring', reason: 'Keep watching', tradeoff: 'none', eligible: true, rank: 1, rank_score: 1 }],
  handover_candidates: [], t: 10, model_id: 'm1',
}
const evaluation: ReplayEvaluation = {
  experiment_name: 'Drift test',
  attacks: [{ target_aircraft: 'px4-1', type: 'drift', start_s: 12, duration_s: 8, magnitude: 1, direction_deg: null }],
  aircraft: [{ aircraft_id: 'px4-1', role: 'primary', attacked: true, attack_start_s: 12, detection_latency_s: 3, detected: true, false_alarms: 0, false_alarms_per_hour: 0,
    max_gps_position_error_m: 8, mean_gps_position_error_m: 4, max_tier: 'High', mission_completion_pct: null, handover_time_s: null }],
  not_measured: { mission_completion_pct: 'needs the experiment runner' },
}

beforeEach(() => {
  apiMock.researchModelInfo.mockResolvedValue({ available: true, model_id: 'm1', formula: 'f', live_residual_inputs_available: false })
  apiMock.attackExperiments.mockResolvedValue([{ id: 'e1', name: 'Drift test', status: 'ready' }])
  apiMock.researchDecisions.mockResolvedValue([])
  apiMock.createReplay.mockResolvedValue(replay)
  apiMock.assessThreat.mockResolvedValue(assessment)
  apiMock.finishReplay.mockResolvedValue({ finished: true })
  apiMock.replayEvaluation.mockResolvedValue(evaluation)
  apiMock.recordDecision.mockImplementation(async (payload: Record<string, unknown>) => ({ id: 'd1', username: 'op', created_at: new Date().toISOString(), executed: false, handover_candidate: null, ...payload }))
})
afterEach(() => { cleanup(); threatSessionStore.publish(null); vi.clearAllMocks() })

async function startReplay() {
  render(<ThreatReplayPanel canRecord/>)
  const select = await screen.findByLabelText('Experiment')
  await screen.findByText('model m1')
  fireEvent.change(select, { target: { value: 'e1' } })
  fireEvent.click(screen.getByRole('button', { name: /START REPLAY/ }))
}

describe('ThreatReplayPanel', () => {
  it('renders, needs an experiment first, and states that it is offline and not live PX4', async () => {
    render(<ThreatReplayPanel canRecord/>)
    expect(await screen.findByText(/OFFLINE REPLAY · SYNTHETIC · NOT LIVE PX4/)).toBeTruthy()
    expect((screen.getByRole('button', { name: /START REPLAY/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('runs a replay, hides the attack schedule while it plays, records a decision, and shows the evaluation only after finishing', async () => {
    await startReplay()
    await waitFor(() => expect(apiMock.createReplay).toHaveBeenCalledWith('e1'))
    expect(await screen.findByRole('meter', {}, { timeout: 8000 })).toBeTruthy()

    // During the replay: no evaluation, no schedule text, evaluation endpoint untouched, requests carry no ground truth.
    expect(screen.queryByLabelText('Evaluation summary')).toBeNull()
    expect(document.body.textContent).not.toMatch(/Scheduled:|drift on px4-1/)
    expect(apiMock.replayEvaluation).not.toHaveBeenCalled()
    expect(apiMock.finishReplay).not.toHaveBeenCalled()
    for (const [payload] of apiMock.assessThreat.mock.calls) expect(findGroundTruthKeys(payload)).toEqual([])
    expect(JSON.stringify(apiMock.assessThreat.mock.calls[0][0])).not.toMatch(/attack|schedule|label|truth/i)
    expect(findGroundTruthKeys(threatSessionStore.get())).toEqual([])

    // Record a decision in the page (no native dialog).
    const confirmSpy = vi.spyOn(window, 'confirm')
    fireEvent.click(await screen.findByRole('button', { name: /Continue monitoring/ }))
    fireEvent.click(screen.getByText('CONFIRM AND RECORD'))
    await waitFor(() => expect(apiMock.recordDecision).toHaveBeenCalledTimes(1))
    expect(apiMock.recordDecision.mock.calls[0][0]).toMatchObject({ aircraft_id: 'px4-1', action: 'continue_monitor', source: 'offline_replay', source_name: 'Drift test' })
    expect(await screen.findByText(/The aircraft was not commanded/)).toBeTruthy()
    expect(confirmSpy).not.toHaveBeenCalled()

    // After the replay ends: finish is called first, then the evaluation (with the schedule) appears.
    expect(await screen.findByLabelText('Evaluation summary', {}, { timeout: 15000 })).toBeTruthy()
    expect(apiMock.finishReplay).toHaveBeenCalledWith('r1')
    expect(apiMock.finishReplay.mock.invocationCallOrder[0]).toBeLessThan(apiMock.replayEvaluation.mock.invocationCallOrder[0])
    expect(screen.getByText(/Scheduled: drift on px4-1 from \+12 s for 8 s/)).toBeTruthy()
    expect(screen.getByText(/yes, detected/)).toBeTruthy()
  }, 40000)

  it('rejects a replay payload that contains ground-truth fields', async () => {
    apiMock.createReplay.mockResolvedValue({ ...replay, attack_schedule: [{ start_s: 12 }] })
    await startReplay()
    expect(await screen.findByText(/rejected because it contained ground-truth fields/)).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
    expect(apiMock.assessThreat).not.toHaveBeenCalled()
  })
})
