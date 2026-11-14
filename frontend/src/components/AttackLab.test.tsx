import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AttackLab, computeReadiness } from './AttackLab'
import {
  ATTACK_FIELD_HELP, ATTACK_LAB_NAME, ATTACK_TYPE_SPECS, DEFAULT_CONFIG, GROUND_TRUTH_NOTE, HIDDEN_SCHEDULE_NOTE, PRESET_SPECS, presetsForPlan,
  type AttackLimits, type PickerMission, type PickerVersion,
} from './attackLabTypes'
import { buildAssessPayload } from './ThreatReplayPanel'
import { findGroundTruthKeys } from './threatSession'

const apiMock = vi.hoisted(() => ({
  attackLabLimits: vi.fn(), attackExperiments: vi.fn(), attackLabMissions: vi.fn(), applyAttackPreset: vi.fn(),
  validateExperiment: vi.fn(), createExperiment: vi.fn(), updateExperiment: vi.fn(), deleteExperiment: vi.fn(),
}))
vi.mock('../services/api', () => ({ api: apiMock }))

const limits: AttackLimits = {
  name: 'Attack Lab',
  types: ATTACK_TYPE_SPECS.map(item => ({ type: item.value, label: item.label, summary: item.summary, uses_direction: item.usesDirection, magnitude: { label: 'Magnitude', unit: item.unit, min: 1, max: 500, default: 50 } })),
  duration_s: { min: 1, max: 3600 }, start_s: { min: 0, max: 86400 }, seed: { min: 0, max: 2147483647 }, max_attacks: 8,
  handover: { min_battery_pct: 40, roles: ['standby', 'decoy'] }, presets: PRESET_SPECS, executes: false,
}
const version = (patch: Partial<PickerVersion> = {}): PickerVersion => ({
  id: 'v1', version_number: 1, config_hash: 'h', created_at: 't', latest: true, superseded: false, deployed: false, deployed_at: null, in_mission_control: false,
  aircraft: [{ uav_id: 'px4-1', role: 'primary', reassignable: true, duration_s: 240 }], estimated_duration_s: 240, area_summary: 'circle, radius 400 m',
  objectives: [], presets: { presets: [], hidden: [] }, ...patch,
})
const mission = (versions: PickerVersion[]): PickerMission => ({ scenario_id: 's1', title: 'Coastal plan', versions })

beforeEach(() => {
  apiMock.attackLabLimits.mockResolvedValue(limits)
  apiMock.attackExperiments.mockResolvedValue([])
  apiMock.attackLabMissions.mockResolvedValue({ missions: [], legacy_only_count: 0 })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('AttackLab', () => {
  it('uses the single product name and states software-only, attack-only and hidden schedule', async () => {
    render(<AttackLab canEdit/>)
    expect(await screen.findByRole('heading', { name: ATTACK_LAB_NAME })).toBeTruthy()
    expect(screen.getByText(/does not affect the aircraft/)).toBeTruthy()
    expect(screen.getByText(/not executed until the experiment runner exists/i)).toBeTruthy()
    expect(screen.getByText(/never changes an aircraft's role/)).toBeTruthy()
    expect(document.body.textContent).toContain(HIDDEN_SCHEDULE_NOTE)
    expect(document.body.textContent).toContain(GROUND_TRUTH_NOTE)
    await waitFor(() => expect(apiMock.attackLabLimits).toHaveBeenCalled())
  })

  it('has no weapon, strike or scripted-response wording and no response-policy selector', async () => {
    render(<AttackLab canEdit/>)
    await screen.findByRole('heading', { name: ATTACK_LAB_NAME })
    expect(document.body.textContent?.toLowerCase()).not.toMatch(/weapon|bomb|strike|munition|missile|scripted|benchmark/)
    expect(screen.queryByLabelText('Response policy')).toBeNull()
  })

  it('keeps the offline type/preset specs aligned with the backend ids, in the order the backend serves them', () => {
    expect(PRESET_SPECS.map(item => item.id)).toEqual(['baseline', 'slow_drift', 'rapid_jump', 'strong_offset', 'noise_burst', 'paired_drift'])
    expect(PRESET_SPECS.slice(0, 5).map(item => item.label)).toEqual(['Baseline (no attack)', 'Slow drift', 'Rapid jump', 'Strong offset', 'Noise burst'])
    expect(limits.types.map(item => item.type)).toEqual(ATTACK_TYPE_SPECS.map(item => item.value))
    expect(Object.keys(ATTACK_FIELD_HELP)).toEqual(['start', 'duration', 'magnitude', 'direction', 'seed'])
  })

  it('lists saved versions with latest / deployed / superseded marks, a run-after-deploy note and Open in Scenario Lab', async () => {
    apiMock.attackLabMissions.mockResolvedValue({ missions: [mission([version({ id: 'v2', version_number: 2 }), version({ id: 'v1', latest: false, superseded: true, deployed: true, in_mission_control: true })])], legacy_only_count: 0 })
    const open = vi.fn()
    render(<AttackLab canEdit onOpenScenarioLab={open}/>)
    fireEvent.change(await screen.findByLabelText('Mission'), { target: { value: 's1' } })
    const select = screen.getByLabelText('Version') as HTMLSelectElement
    expect(Array.from(select.options).map(option => option.textContent)).toEqual(['Choose an exact version', 'v2 · latest · not deployed', 'v1 · superseded · deployed · in Mission Control'])
    expect(select.value).toBe('v2')
    expect(screen.getByText(/can define the experiment now and run it after the version is deployed/)).toBeTruthy()
    fireEvent.change(select, { target: { value: 'v1' } })
    expect(screen.getByText(/A newer version \(v2\) of this mission exists/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Open in Scenario Lab/ }))
    expect(open).toHaveBeenCalledWith('s1')
  })

  it('keeps two-aircraft presets out of the list for a one-aircraft plan and says why', async () => {
    apiMock.attackLabMissions.mockResolvedValue({ missions: [mission([version()])], legacy_only_count: 0 })
    render(<AttackLab canEdit initialScenarioId="s1"/>)
    const presets = await screen.findByRole('region', { name: 'Presets' })
    await waitFor(() => expect(within(presets).getByRole('button', { name: /Slow drift/ }).hasAttribute('disabled')).toBe(false))
    expect(within(presets).queryByRole('button', { name: /Drift on two aircraft/ })).toBeNull()
    expect(within(presets).getByText(/Not listed for this plan: Drift on two aircraft — It attacks two aircraft at once, so the plan needs a second aircraft\./)).toBeTruthy()
    expect(presetsForPlan(2).offered).toHaveLength(6)
  })

  it('readiness reflects pinned mission, valid attack, duration and battery', () => {
    const draft = { name: 'x', scenario_id: 's1', scenario_version_id: 'v1', preset_id: null, seed: 1, status: 'draft' as const,
      config: { ...DEFAULT_CONFIG, attacks: [{ target_aircraft: 'px4-1', type: 'drift' as const, start_s: 200, duration_s: 100, magnitude: 1, direction_deg: null }] } }
    const items = computeReadiness(draft, version(), limits)
    expect(items.map(item => [item.key, item.ok])).toEqual([['mission', true], ['attack', true], ['duration', false], ['battery', true]])
    expect(items[2].detail).toMatch(/ends at 300 s but the route lasts 240 s/)
    expect(computeReadiness(draft, null, limits)[0].ok).toBe(false)
  })
})

describe('replay assessment payload', () => {
  const replay = {
    replay_id: 'r1', experiment_name: 'x', experiment_id: 'e1', hz: 1, duration_s: 100, min_battery_pct: 40, candidate_roles: ['standby'], source: 'offline_replay' as const,
    aircraft: ['px4-1', 'px4-2'].map((id, index) => ({
      aircraft_id: id, role: index ? 'standby' as const : 'primary' as const, reassignable: index === 1,
      t: Array.from({ length: 60 }, (_, i) => i), gps_position: Array.from({ length: 60 }, (_, i) => [i, index]),
      gps_velocity: Array.from({ length: 60 }, () => [1, 0]), imu_velocity: Array.from({ length: 60 }, () => [1, 0]), battery_pct: Array.from({ length: 60 }, () => 90),
    })),
  }
  it('carries sensor streams only: no schedule, label or attack fields', () => {
    const payload = buildAssessPayload(replay, 0, 40)
    expect(findGroundTruthKeys(payload)).toEqual([])
    expect(JSON.stringify(payload)).not.toMatch(/attack|schedule|label|truth/i)
    expect(payload.samples.length).toBeLessThanOrEqual(40)
    expect(payload.handover_candidates[0].aircraft_id).toBe('px4-2')
  })
})
