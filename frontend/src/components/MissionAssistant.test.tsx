import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MissionAssistant } from './MissionAssistant'
import { api } from '../services/api'
import type { AIStatus, MissionAssessment, MissionConversation, MissionProposalContent } from '../services/ai'
import type { ScenarioConfig, ScenarioDetail } from '../services/auth'
import type { MissionState } from '../types'

vi.mock('../services/api', () => ({
  api: {
    aiStatus: vi.fn(),
    aiConversation: vi.fn(),
    pendingAIProposals: vi.fn(),
    continueAIConversation: vi.fn(),
    createAIConversation: vi.fn(),
    saveAIConversationScenario: vi.fn(),
    assessMission: vi.fn(),
  },
}))

const apiMock = vi.mocked(api)
const config: ScenarioConfig = {
  uav_count: 4, task_count: 10, seed: 21, duration: 180, spoofing_type: 'none',
  target_uav: 'UAV-02', spoof_start: 15, spoof_duration: 35, spoof_magnitude: 3,
  response_policy: 'adaptive', detector_threshold: null,
}
const proposal: MissionProposalContent = {
  mission_objectives: ['Maintain abstract mission continuity'], required_information: [], proposed_tasks: [],
  uav_role_suggestions: [], waypoint_references: [], assumptions: [], constraints: [],
  assessment_explanations: ['Use deterministic run evidence'], clarification_questions: [],
}
const collectingConversation: MissionConversation = {
  conversation_id: 'conversation-1', status: 'collecting',
  messages: [
    { role: 'user', content: 'Plan an abstract search mission.' },
    { role: 'assistant', content: 'Should this remain a software-only abstract scenario?' },
  ],
  proposal: null, proposal_id: null, scenario_id: null, operator_config: config,
  expires_at: '2026-11-14T00:00:00Z',
}
const readyConversation: MissionConversation = {
  ...collectingConversation,
  status: 'ready_for_review',
  messages: [...collectingConversation.messages, { role: 'user', content: 'Yes, keep it abstract and software-only.' }],
  proposal, proposal_id: 'proposal-1',
}
const readyStatus: AIStatus = {
  available: true, provider: 'openai', model: 'test-model', reason: null,
  remaining_requests: 10, remaining_output_tokens: 1000,
}
const assessment: MissionAssessment = {
  run_id: 'run-private-to-user-a', tick: 7, simulation_status: 'running',
  validated_evidence: {
    coordinate_system: 'abstract units', run_id: 'run-private-to-user-a', tick: 7, simulation_status: 'running',
    validated_progress: { completed: 1, total: 3, remaining: 2 }, reported_telemetry: [],
    recent_event_metadata: [], permitted_response_suggestions: [],
  },
  assessment: { summary: 'Private assessment for user A.', findings: [], suggested_responses: [] },
}
const state = { status: 'running', tick: 7 } as MissionState

function renderAssistant(userId = 'user-a', runId: string | null = null, currentState: MissionState | null = null) {
  return render(<MissionAssistant
    signedIn={true} userId={userId} role="OPERATOR" config={config} runId={runId} state={currentState}
    onManualPlanning={vi.fn()}
  />)
}

describe('MissionAssistant account-scoped workflow', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    apiMock.aiStatus.mockResolvedValue(readyStatus)
    apiMock.aiConversation.mockResolvedValue(collectingConversation)
    apiMock.pendingAIProposals.mockResolvedValue({ proposals: [] })
  })

  afterEach(() => {
    cleanup()
    window.sessionStorage.clear()
    vi.clearAllMocks()
  })

  it('restores a saved conversation, continues it, and hands the proposal to Scenario Lab', async () => {
    const onManualPlanning = vi.fn()
    const savedScenario = { id: 'scenario-1', title: 'Transferred mission' } as ScenarioDetail
    apiMock.continueAIConversation.mockResolvedValue(readyConversation)
    apiMock.saveAIConversationScenario.mockResolvedValue(savedScenario)
    window.sessionStorage.setItem('trinetra-ai-conversation:user-a', collectingConversation.conversation_id)

    render(<MissionAssistant signedIn userId="user-a" role="OPERATOR" config={config} runId={null} state={null} onManualPlanning={onManualPlanning}/>);

    expect(await screen.findByText(/Should this remain a software-only abstract scenario/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('YOUR ANSWER'), { target: { value: 'Yes, keep it abstract and software-only.' } })
    fireEvent.click(screen.getByRole('button', { name: 'SEND ANSWER' }))
    expect(await screen.findByRole('button', { name: 'SAVE AS SCENARIO DRAFT' })).toBeTruthy()
    expect(apiMock.continueAIConversation).toHaveBeenCalledWith('conversation-1', 'Yes, keep it abstract and software-only.')

    fireEvent.click(screen.getByRole('button', { name: 'SAVE AS SCENARIO DRAFT' }))
    await waitFor(() => expect(apiMock.saveAIConversationScenario).toHaveBeenCalledWith('conversation-1'))
    expect(onManualPlanning).toHaveBeenCalledOnce()
    expect(await screen.findByRole('button', { name: 'SAVED TO SCENARIO LAB' })).toBeTruthy()
  })

  it('clears assessment and unsent planning text when the signed-in account changes', async () => {
    apiMock.assessMission.mockResolvedValue(assessment)
    const view = renderAssistant('user-a', 'run-private-to-user-a', state)
    fireEvent.change(await screen.findByLabelText('MISSION REQUEST'), { target: { value: 'Unsent private planning notes' } })
    fireEvent.click(screen.getByRole('button', { name: 'EXPLAIN CURRENT EVIDENCE' }))
    expect(await screen.findByText('Private assessment for user A.')).toBeTruthy()

    view.rerender(<MissionAssistant signedIn userId="user-b" role="OPERATOR" config={config} runId={null} state={null} onManualPlanning={vi.fn()}/>);

    await waitFor(() => expect((screen.getByLabelText('MISSION REQUEST') as HTMLTextAreaElement).value).toBe(''))
    expect(screen.queryByText('Private assessment for user A.')).toBeNull()
    expect(apiMock.aiConversation).not.toHaveBeenCalled()
  })
})
