import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CommandDecisionPanel } from './CommandDecisionPanel'
import type { CommandDecision } from '../services/auth'
import { api } from '../services/api'

vi.mock('../services/api', () => ({
  api: {
    commandDecisions: vi.fn(),
    reviewCommandDecision: vi.fn(),
  },
}))

const requests: CommandDecision[] = [
  {
    id: 'pending-other',
    requester_id: 'operator-requester',
    requester_username: 'operator-requester',
    reviewer_id: null,
    run_id: 'run-1',
    command: 'stop',
    payload: {},
    status: 'pending',
    created_at: '2026-10-26T10:00:00Z',
    decided_at: null,
    decision_reason: null,
  },
  {
    id: 'pending-self',
    requester_id: 'operator-reviewer',
    requester_username: 'operator-reviewer',
    reviewer_id: null,
    run_id: 'run-2',
    command: 'reset',
    payload: {},
    status: 'pending',
    created_at: '2026-10-26T10:01:00Z',
    decided_at: null,
    decision_reason: null,
  },
]

const commandApi = vi.mocked(api)

describe('CommandDecisionPanel', () => {
  beforeEach(() => {
    commandApi.commandDecisions.mockResolvedValue(requests)
    commandApi.reviewCommandDecision.mockResolvedValue({ id: 'pending-other', status: 'approved' })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('shows requests across missions but never offers self-approval', async () => {
    render(<CommandDecisionPanel
      runId=""
      currentUserId="operator-reviewer"
      canRequest
      canApprove
      refreshKey={0}
      onExecuted={vi.fn()}
    />)

    expect(await screen.findByText(/operator-requester.*run-1/)).toBeTruthy()
    expect(screen.getByText(/operator-reviewer.*run-2/)).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'APPROVE & EXECUTE' })).toHaveLength(1)
  })

  it('sends approval to the backend and refreshes request status', async () => {
    const onExecuted = vi.fn()
    render(<CommandDecisionPanel
      runId="run-1"
      currentUserId="operator-reviewer"
      canRequest
      canApprove
      refreshKey={0}
      onExecuted={onExecuted}
    />)

    fireEvent.click(await screen.findByRole('button', { name: 'APPROVE & EXECUTE' }))

    await waitFor(() => expect(commandApi.reviewCommandDecision).toHaveBeenCalledWith('pending-other', 'approve', undefined))
    expect(await screen.findByText('stop was approved and executed.')).toBeTruthy()
    expect(commandApi.commandDecisions).toHaveBeenCalledTimes(2)
    expect(onExecuted).not.toHaveBeenCalled()
  })

  it('does not render decision controls for an operator without review permission', async () => {
    render(<CommandDecisionPanel
      runId="run-1"
      currentUserId="operator-requester"
      canRequest
      canApprove={false}
      refreshKey={0}
      onExecuted={vi.fn()}
    />)

    expect(await screen.findByText(/operator-requester.*run-1/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'APPROVE & EXECUTE' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'REJECT' })).toBeNull()
  })
})
