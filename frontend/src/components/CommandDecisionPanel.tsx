import { useCallback, useEffect, useState } from 'react'
import type { MissionState } from '../types'
import type { CommandDecision } from '../services/auth'
import { api } from '../services/api'

export function CommandDecisionPanel({ runId, currentUserId, canRequest, canApprove, refreshKey, onExecuted }: {
  runId: string
  currentUserId: string
  canRequest: boolean
  canApprove: boolean
  refreshKey: number
  onExecuted: (state: MissionState) => void
}) {
  const [requests, setRequests] = useState<CommandDecision[]>([])
  const [busyId, setBusyId] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const refresh = useCallback(async () => {
    try {
      setRequests(await api.commandDecisions())
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load command decisions.')
    }
  }, [])
  useEffect(() => { void refresh() }, [refresh, refreshKey])

  const decide = async (request: CommandDecision, decision: 'approve' | 'reject') => {
    const reason = decision === 'reject' ? window.prompt('Reason for rejecting this command:') : undefined
    if (decision === 'reject' && !reason?.trim()) return
    setBusyId(request.id)
    setMessage('')
    setError('')
    try {
      const result = await api.reviewCommandDecision(request.id, decision, reason?.trim())
      if (result.state && request.run_id === runId) onExecuted(result.state)
      setMessage(decision === 'approve' ? `${request.command.replace(/_/g, ' ')} was approved and executed.` : 'Command request rejected.')
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Command decision failed.')
    } finally {
      setBusyId('')
    }
  }

  if (!canRequest && !canApprove) return null
  return <section className="panel command-decision-panel">
    <div className="panel-heading"><div><span className="eyebrow">RESTRICTED COMMAND DECISIONS</span><h3>General review</h3></div><button className="small-icon" onClick={() => void refresh()} title="Refresh command requests">REFRESH</button></div>
    <p className="permission-note">Manual stop, reset, UAV isolation and task reassignment require a separate reviewer. Mission start, pause and step remain routine controls.</p>
    {error && <div className="admin-inline-error" role="alert">{error}</div>}
    {message && <div className="assistant-message" role="status">{message}</div>}
    {!requests.length ? <div className="empty-inline">No command requests are available.</div> : requests.map(request => <article className="command-decision-row" key={request.id}>
      <div><b>{request.command.replace(/_/g, ' ').toUpperCase()}</b><span>{request.requester_username} · RUN {request.run_id} · {new Date(request.created_at).toLocaleString()}</span><small>{JSON.stringify(request.payload)} · {request.status.toUpperCase()}</small>{request.decision_reason && <small>{request.decision_reason}</small>}</div>
      {canApprove && request.status === 'pending' && request.requester_id !== currentUserId && <div className="admin-action-buttons">
        <button className="button-secondary" disabled={Boolean(busyId)} onClick={() => void decide(request, 'approve')}>{busyId === request.id ? 'PROCESSING…' : 'APPROVE & EXECUTE'}</button>
        <button className="button-secondary" disabled={Boolean(busyId)} onClick={() => void decide(request, 'reject')}>REJECT</button>
      </div>}
    </article>)}
  </section>
}
