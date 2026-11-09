import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ScenarioWorkspace } from './ScenarioWorkspace'
import { api } from '../services/api'
import type { ScenarioConfig, ScenarioDetail, ScenarioIntent, ScenarioSummary } from '../services/auth'

vi.mock('../services/api', () => ({
  api: {
    scenarioList: vi.fn(),
    myScenarioRequests: vi.fn(),
    createScenario: vi.fn(),
    updateScenario: vi.fn(),
    submitScenario: vi.fn(),
  },
}))

const apiMock = vi.mocked(api)
const config: ScenarioConfig = {
  uav_count: 4, task_count: 10, seed: 21, duration: 180, spoofing_type: 'none',
  target_uav: 'UAV-02', spoof_start: 15, spoof_duration: 35, spoof_magnitude: 3,
  response_policy: 'adaptive', detector_threshold: null,
}
const emptyIntent: ScenarioIntent = {
  mission_objectives: [], proposed_tasks: [], uav_role_suggestions: [],
  operating_area_notes: '', environment_notes: '', constraints: [], geographic_mission: null,
  uav_fault_assignments: [],
}
const defaultFaultAssignments = Array.from({ length: config.uav_count }, (_, index) => ({
  uav_id: `UAV-${String(index + 1).padStart(2, '0')}`,
  fault_type: 'none' as const,
  spoof_start: 15,
  spoof_duration: 35,
  spoof_magnitude: 3,
}))

function scenarioDetail(intent: ScenarioIntent): ScenarioDetail {
  const version = {
    id: 'version-1', version_number: 1, config, intent, config_hash: 'hash-1',
    created_at: '2026-11-07T00:00:00Z', sandbox_run_id: null, approval: null,
  }
  return {
    id: 'scenario-1', owner_user_id: 'operator-1', title: 'Mission intent test',
    created_at: version.created_at, updated_at: version.created_at, versions: [version],
  }
}

describe('ScenarioWorkspace mission intent', () => {
  beforeEach(() => {
    apiMock.scenarioList.mockResolvedValue([])
    apiMock.myScenarioRequests.mockResolvedValue([])
    apiMock.createScenario.mockResolvedValue(scenarioDetail(emptyIntent))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('saves edited mission intent with the simulator configuration', async () => {
    render(<ScenarioWorkspace config={config} onLoadConfig={vi.fn()} onRunCreated={vi.fn()} canManage />)
    fireEvent.change(await screen.findByLabelText('SCENARIO TITLE'), { target: { value: 'Intent draft' } })
    fireEvent.change(screen.getByLabelText('MISSION OBJECTIVES'), { target: { value: 'Inspect abstract markers\nMaintain continuity' } })
    fireEvent.change(screen.getByLabelText('PROPOSED TASK NOTES'), { target: { value: 'Review task alpha' } })
    fireEvent.change(screen.getByLabelText('ABSTRACT AREA NOTES'), { target: { value: 'Abstract grid sector east' } })
    fireEvent.click(screen.getByRole('button', { name: 'SAVE NEW DRAFT' }))

    await waitFor(() => expect(apiMock.createScenario).toHaveBeenCalledWith('Intent draft', config, {
      ...emptyIntent,
      mission_objectives: ['Inspect abstract markers', 'Maintain continuity'],
      proposed_tasks: [{ description: 'Review task alpha', task_reference: null, assigned_uav_id: null, priority: 1 }],
      operating_area_notes: 'Abstract grid sector east',
      uav_fault_assignments: defaultFaultAssignments,
    }))
  })

  it('loads versioned intent and submits edits as a new version', async () => {
    const intent = { ...emptyIntent, mission_objectives: ['Original objective'] }
    const detail = scenarioDetail(intent)
    const summary: ScenarioSummary = {
      id: detail.id, owner_user_id: detail.owner_user_id, title: detail.title,
      created_at: detail.created_at, updated_at: detail.updated_at, latest_version: detail.versions[0],
    }
    apiMock.scenarioList.mockResolvedValue([summary])
    apiMock.updateScenario.mockResolvedValue(detail)
    render(<ScenarioWorkspace config={config} onLoadConfig={vi.fn()} onRunCreated={vi.fn()} canManage />)
    fireEvent.change(await screen.findByLabelText('SAVED SCENARIO'), { target: { value: detail.id } })
    const objectives = await screen.findByLabelText('MISSION OBJECTIVES')
    expect((objectives as HTMLTextAreaElement).value).toBe('Original objective')
    fireEvent.change(objectives, { target: { value: 'Revised objective' } })
    fireEvent.click(screen.getByRole('button', { name: 'SAVE NEW VERSION' }))
    await waitFor(() => expect(apiMock.updateScenario).toHaveBeenCalledWith(detail.id, detail.title, config, {
      ...emptyIntent, mission_objectives: ['Revised objective'], uav_fault_assignments: defaultFaultAssignments,
    }))
  })

  it('loads the shared scenario selected by Mission Planner', async () => {
    const detail = scenarioDetail({ ...emptyIntent, mission_objectives: ['Shared mission'] })
    const summary: ScenarioSummary = {
      id: detail.id, owner_user_id: detail.owner_user_id, title: detail.title,
      created_at: detail.created_at, updated_at: detail.updated_at, latest_version: detail.versions[0],
    }
    const onLoadConfig = vi.fn()
    const onScenarioSelected = vi.fn()
    apiMock.scenarioList.mockResolvedValue([summary])
    render(<ScenarioWorkspace
      config={config}
      onLoadConfig={onLoadConfig}
      onRunCreated={vi.fn()}
      canManage
      sharedScenarioId={detail.id}
      onScenarioSelected={onScenarioSelected}
    />)

    await waitFor(() => expect((screen.getByLabelText('SAVED SCENARIO') as HTMLSelectElement).value).toBe(detail.id))
    expect((await screen.findByLabelText('MISSION OBJECTIVES') as HTMLTextAreaElement).value).toBe('Shared mission')
    await waitFor(() => expect(onLoadConfig).toHaveBeenCalledWith(config))
    expect(onScenarioSelected).toHaveBeenCalledWith(detail.id)
  })

  it('keeps multiple per-UAV fault profiles reviewable but blocks abstract execution', async () => {
    const intent: ScenarioIntent = {
      ...emptyIntent,
      uav_fault_assignments: [
        { uav_id: 'UAV-01', fault_type: 'sudden', spoof_start: 0, spoof_duration: 5, spoof_magnitude: 1 },
        { uav_id: 'UAV-02', fault_type: 'noise', spoof_start: 0, spoof_duration: 5, spoof_magnitude: 0 },
      ],
    }
    const detail = scenarioDetail(intent)
    const summary: ScenarioSummary = {
      id: detail.id, owner_user_id: detail.owner_user_id, title: detail.title,
      created_at: detail.created_at, updated_at: detail.updated_at, latest_version: detail.versions[0],
    }
    apiMock.scenarioList.mockResolvedValue([summary])
    render(<ScenarioWorkspace config={config} onLoadConfig={vi.fn()} onRunCreated={vi.fn()} canManage />)

    fireEvent.change(await screen.findByLabelText('SAVED SCENARIO'), { target: { value: detail.id } })

    expect(await screen.findByText(/supports one active GPS fault at a time/)).toBeTruthy()
    expect((screen.getByRole('button', { name: /RUN SANDBOX/ }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: /EXECUTE APPROVED VERSION/ })).toBeNull()
  })

  it('submits the selected immutable version for General approval', async () => {
    const detail = scenarioDetail({ ...emptyIntent, mission_objectives: ['Preserve mission continuity'] })
    const summary: ScenarioSummary = {
      id: detail.id, owner_user_id: detail.owner_user_id, title: detail.title,
      created_at: detail.created_at, updated_at: detail.updated_at, latest_version: detail.versions[0],
    }
    apiMock.scenarioList.mockResolvedValue([summary])
    apiMock.submitScenario.mockResolvedValue({ id: 'approval-1', status: 'PENDING' })
    render(<ScenarioWorkspace config={config} onLoadConfig={vi.fn()} onRunCreated={vi.fn()} canManage />)

    fireEvent.change(await screen.findByLabelText('SAVED SCENARIO'), { target: { value: detail.id } })
    fireEvent.click(screen.getByRole('button', { name: 'SUBMIT VERSION' }))

    await waitFor(() => expect(apiMock.submitScenario).toHaveBeenCalledWith(detail.id, 'version-1'))
    expect((await screen.findByRole('status')).textContent).toContain('Version 1 submitted for General review.')
  })

  it('preserves geographic intent and prevents abstract sandbox execution', async () => {
    const geographicIntent: ScenarioIntent = {
      ...emptyIntent,
      geographic_mission: {
        objective: 'area_survey',
        start_location: { latitude: 12, longitude: 77 },
        destination: null,
        survey_zones: [{
          id: 'zone-1', name: 'North area',
          vertices: [
            { latitude: 12, longitude: 77 },
            { latitude: 13, longitude: 77 },
            { latitude: 12.5, longitude: 78 },
          ],
        }],
        uav_assignments: [{
          uav_id: 'px4-7', role: 'area_survey', task: 'Survey north', zone_id: 'zone-1',
        }],
        constraints: [], ai_recommendations: [],
      },
    }
    const detail = scenarioDetail(geographicIntent)
    const summary: ScenarioSummary = {
      id: detail.id, owner_user_id: detail.owner_user_id, title: detail.title,
      created_at: detail.created_at, updated_at: detail.updated_at, latest_version: detail.versions[0],
    }
    apiMock.scenarioList.mockResolvedValue([summary])
    render(<ScenarioWorkspace config={config} onLoadConfig={vi.fn()} onRunCreated={vi.fn()} canManage />)

    fireEvent.change(await screen.findByLabelText('SAVED SCENARIO'), { target: { value: detail.id } })

    expect(await screen.findByText(/Geographic mission data is preserved/)).toBeTruthy()
    expect((screen.getByRole('button', { name: /RUN SANDBOX/ }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: /EXECUTE APPROVED VERSION/ })).toBeNull()
  })
})
