import { useEffect, useState, type FormEvent } from 'react'
import { AlertTriangle, ArrowRight, CheckCircle2, CircleHelp, ClipboardCheck, LoaderCircle, MapPinOff, MessageSquareText, Radio, ShieldAlert, Sparkles } from 'lucide-react'
import type { MissionState } from '../types'
import type { OperatorSimulationConfig } from '../services/ai'
import type { ApplicationRole } from '../services/auth'
import { api } from '../services/api'
import type { AIStatus, MissionAssessment, MissionConversation, MissionProposal, PendingMissionProposal } from '../services/ai'
import './mission-assistant.css'

type Props = {
  signedIn: boolean
  userId: string
  role: ApplicationRole
  config: OperatorSimulationConfig
  runId: string | null
  state: MissionState | null
  onManualPlanning: () => void
}

function ListBlock({ title, values, empty }: { title: string; values: string[]; empty?: string }) {
  return <section className="assistant-list-block"><h4>{title}</h4>{values.length ? <ul>{values.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul> : <p>{empty ?? 'None returned.'}</p>}</section>
}

export function MissionAssistant({ signedIn, userId, role, config, runId, state, onManualPlanning }: Props) {
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [description, setDescription] = useState('')
  const [proposal, setProposal] = useState<MissionProposal | null>(null)
  const [conversation, setConversation] = useState<MissionConversation | null>(null)
  const [assessment, setAssessment] = useState<MissionAssessment | null>(null)
  const [pendingProposals, setPendingProposals] = useState<PendingMissionProposal[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    let active = true
    setConversation(null)
    setProposal(null)
    setAssessment(null)
    setPendingProposals([])
    setDescription('')
    setMessage('')
    setBusy(false)
    setStatus(null)
    if (!signedIn) return () => { active = false }
    const savedConversationId = window.sessionStorage.getItem(`trinetra-ai-conversation:${userId}`)
    if (savedConversationId) {
      void api.aiConversation(savedConversationId).then(value => {
        if (!active) return
        setConversation(value)
        setProposal(value.proposal ? {
          proposal_id: value.proposal_id ?? '', status: value.status === 'ready_for_review' ? 'pending_review' : 'needs_clarification',
          ready_for_review: value.status === 'ready_for_review', validation_issues: [], proposal: value.proposal,
          operator_config: value.operator_config, run_id: null,
        } : null)
      }).catch(() => window.sessionStorage.removeItem(`trinetra-ai-conversation:${userId}`))
    }
    void api.aiStatus().then(value => { if (active) setStatus(value) }).catch(error => {
      if (active) setMessage(error instanceof Error ? error.message : 'Could not check AI availability.')
    })
    if (role === 'GENERAL') {
      void api.pendingAIProposals().then(value => {
        if (active) setPendingProposals(value.proposals)
      }).catch(error => {
        if (active) setMessage(error instanceof Error ? error.message : 'Could not load the approval queue.')
      })
    }
    return () => { active = false }
  }, [signedIn, role, userId])

  const applyConversation = (value: MissionConversation) => {
    setConversation(value)
    window.sessionStorage.setItem(`trinetra-ai-conversation:${userId}`, value.conversation_id)
    setProposal(value.proposal ? {
      proposal_id: value.proposal_id ?? '', status: value.status === 'ready_for_review' ? 'pending_review' : 'needs_clarification',
      ready_for_review: value.status === 'ready_for_review', validation_issues: [], proposal: value.proposal,
      operator_config: value.operator_config, run_id: null,
    } : null)
  }

  const submitDescription = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true); setMessage(''); setProposal(null)
    try {
      const next = conversation?.status === 'collecting'
        ? await api.continueAIConversation(conversation.conversation_id, description)
        : await api.createAIConversation(description, config)
      applyConversation(next)
      setDescription('')
      if (role === 'GENERAL') setPendingProposals((await api.pendingAIProposals()).proposals)
      if (next.status === 'collecting') setMessage('The conversation is saved. Answer the follow-up question to continue; no scenario or run has been created.')
      else setMessage('The structured proposal is ready. Review it below, then save it as a Scenario Lab draft when ready.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not create the proposal. Manual planning remains available.')
    } finally { setBusy(false) }
  }

  const saveConversationScenario = async () => {
    if (!conversation || conversation.status !== 'ready_for_review') return
    setBusy(true); setMessage('')
    try {
      const saved = await api.saveAIConversationScenario(conversation.conversation_id)
      applyConversation({ ...conversation, scenario_id: saved.id })
      setMessage(`Saved Scenario Lab draft “${saved.title}”. Review and edit its configuration in Scenario Lab.`)
      onManualPlanning()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save this scenario draft.')
    } finally { setBusy(false) }
  }

  const startNewConversation = () => {
    if (userId) window.sessionStorage.removeItem(`trinetra-ai-conversation:${userId}`)
    setConversation(null)
    setProposal(null)
    setDescription('')
    setMessage('Started a new planning conversation. Previous conversations remain available only through their owner-scoped API IDs until expiry.')
  }

  const approvePending = async (item: PendingMissionProposal) => {
    if (item.operator_id === userId) return
    setBusy(true); setMessage('')
    try {
      const result = await api.approveAIProposal(item.id)
      setPendingProposals(previous => previous.filter(proposalItem => proposalItem.id !== item.id))
      setMessage(`Independently approved. Ready run ${result.run_id} was created; simulation has not started.`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Approval failed. No start command was sent.')
    } finally { setBusy(false) }
  }

  const rejectPending = async (item: PendingMissionProposal) => {
    if (item.operator_id === userId) return
    const reason = window.prompt('Reason for rejecting this mission proposal:')
    if (!reason?.trim()) return
    setBusy(true); setMessage('')
    try {
      await api.rejectAIProposal(item.id, reason.trim())
      setPendingProposals(previous => previous.filter(proposalItem => proposalItem.id !== item.id))
      setMessage('Proposal rejected. The requester can review the recorded decision.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Rejection failed.')
    } finally { setBusy(false) }
  }

  const assess = async () => {
    if (!runId) return
    setBusy(true); setMessage('')
    try { setAssessment(await api.assessMission(runId)) }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Assessment unavailable; deterministic telemetry remains visible in Mission Control.') }
    finally { setBusy(false) }
  }

  const ready = signedIn && status?.available === true
  const configSummary = [
    `${config.uav_count} UAVs`, `${config.task_count} abstract tasks`, `${config.duration} ticks`,
    `${config.spoofing_type} spoof scenario`, `seed ${config.seed}`, `${config.response_policy} response`,
  ].join(' · ')

  return <div className="mission-assistant-layout">
    <section className="panel assistant-planner">
      <div className="panel-heading"><div><span className="eyebrow">NATURAL LANGUAGE · GENERAL REVIEW REQUIRED</span><h3>Mission Planner</h3></div><span className={`assistant-status ${status?.available ? 'available' : ''}`}><i/>{status?.available ? 'AI CONFIGURED' : signedIn ? 'AI UNAVAILABLE' : 'SIGN IN REQUIRED'}</span></div>
      {!signedIn ? <div className="assistant-callout"><ShieldAlert size={17}/><div><b>Sign in to use the protected AI planner.</b><span>Manual scenario planning remains in Scenario Lab.</span></div></div> : <>
        <form className="assistant-request-form" onSubmit={event => void submitDescription(event)}>
          <label htmlFor="mission-description">{conversation?.status === 'collecting' ? 'YOUR ANSWER' : 'MISSION REQUEST'}</label>
          <textarea id="mission-description" value={description} onChange={event => setDescription(event.target.value)} maxLength={2000} rows={4} placeholder={conversation?.status === 'collecting' ? 'Answer the assistant’s follow-up question.' : 'Describe the abstract simulator scenario you want to plan. Do not include personal information or geographic coordinates.'} disabled={!ready || busy || conversation?.status === 'ready_for_review'}/>
          <div className="assistant-form-foot"><small>{description.length}/2,000 characters · geographic coordinates are rejected</small><button className="button-primary" type="submit" disabled={!ready || busy || !description.trim() || conversation?.status === 'ready_for_review'}>{busy ? <LoaderCircle className="spin" size={14}/> : <Sparkles size={14}/>} {conversation?.status === 'collecting' ? 'SEND ANSWER' : 'START PLANNING'}</button></div>
        </form>
        {conversation && <section className="assistant-list-block" aria-label="Mission planning conversation"><h4>Conversation · {conversation.status === 'collecting' ? 'collecting details' : 'ready for review'}</h4><ol>{conversation.messages.map((item, index) => <li key={`${conversation.conversation_id}-${index}`}><b>{item.role === 'user' ? 'You' : 'TRINETRA assistant'}:</b> {item.content}</li>)}</ol>{conversation.status === 'ready_for_review' && <div className="assistant-form-foot"><small>Objectives, task notes, roles, constraints, and non-geographic area notes transfer as advisory fields. They do not alter simulator behavior; geographic coordinates remain unsupported.</small><button className="button-primary" type="button" disabled={busy || Boolean(conversation.scenario_id)} onClick={() => void saveConversationScenario()}>{conversation.scenario_id ? 'SAVED TO SCENARIO LAB' : 'SAVE AS SCENARIO DRAFT'}</button></div>}<button className="inline-link" type="button" disabled={busy} onClick={startNewConversation}>START NEW CONVERSATION</button></section>}
        {!status?.available && <div className="assistant-callout unavailable"><AlertTriangle size={17}/><div><b>{status?.reason ?? 'AI availability has not been confirmed.'}</b><span>Set the key in the backend environment. Continue with manual planning in Scenario Lab.</span></div></div>}
      </>}

      {message && <div className="assistant-message" role="status">{message}</div>}
      {proposal && <div className="assistant-proposal">
        <div className="assistant-review-banner"><ClipboardCheck size={16}/><span><b>{proposal.status === 'pending_review' ? 'READY FOR OPERATOR REVIEW' : proposal.status === 'approved' ? 'OPERATOR APPROVAL RECORDED' : proposal.status === 'expired' ? 'PROPOSAL EXPIRED' : proposal.status === 'rejected' ? 'PROPOSAL REJECTED' : 'CLARIFICATION REQUIRED'}</b><small>{proposal.status === 'pending_review' ? 'Review the proposal and exact operator-selected simulator settings below.' : proposal.status === 'approved' ? `Ready run ${proposal.run_id} was created without starting it.` : proposal.status === 'expired' ? 'The seven-day review window elapsed. Submit a new proposal for renewed review.' : proposal.status === 'rejected' ? 'An independent reviewer rejected this proposal.' : 'Resolve the questions before approval. No run exists for this proposal.'}</small></span></div>
        <div className="assistant-config-summary"><b>OPERATOR-SELECTED SIMULATOR CONFIGURATION</b><span>{configSummary}</span><small>These are the current manual settings submitted with the request. AI does not choose or modify them.</small></div>
        <ListBlock title="Mission objectives" values={proposal.proposal.mission_objectives}/>
        {proposal.proposal.required_information.length > 0 && <ListBlock title="Required information" values={proposal.proposal.required_information}/>}
        {proposal.proposal.clarification_questions.length > 0 && <ListBlock title="Clarifying questions" values={proposal.proposal.clarification_questions}/>}
        <section className="assistant-list-block"><h4>Proposed tasks · advisory</h4>{proposal.proposal.proposed_tasks.length ? <ul>{proposal.proposal.proposed_tasks.map((task, index) => <li key={`task-${index}`}><b>{task.task_reference ?? 'Unassigned task'}{task.assigned_uav_id ? ` · ${task.assigned_uav_id}` : ''}</b> — {task.description} <small>(priority {task.priority})</small></li>)}</ul> : <p>No task suggestions returned.</p>}</section>
        <section className="assistant-list-block"><h4>UAV role suggestions · advisory</h4>{proposal.proposal.uav_role_suggestions.length ? <ul>{proposal.proposal.uav_role_suggestions.map((role, index) => <li key={`role-${index}`}><b>{role.count} × {role.role}</b> — {role.rationale}</li>)}</ul> : <p>No role suggestions returned.</p>}</section>
        <ListBlock title="Waypoint references" values={proposal.proposal.waypoint_references} empty="No waypoint registry exists. The proposal contains no geographic route."/>
        <ListBlock title="Assumptions" values={proposal.proposal.assumptions}/>
        <ListBlock title="Constraints" values={proposal.proposal.constraints}/>
        <ListBlock title="Assessment considerations" values={proposal.proposal.assessment_explanations}/>
        {proposal.validation_issues.length > 0 && <div className="assistant-callout"><MapPinOff size={16}/><div><b>Deterministic validation</b><span>{proposal.validation_issues.join(', ')}</span></div></div>}
        {proposal.ready_for_review && <div className="assistant-callout"><ShieldAlert size={16}/><div><b>A General must review this proposal.</b><span>Self-approval is blocked. Approval creates a ready run; starting remains a separate mission-control action.</span></div></div>}
        {proposal.status === 'rejected' && <div className="assistant-callout"><AlertTriangle size={16}/><div><b>Proposal rejected by an independent reviewer.</b><span>{proposal.decision_reason}</span></div></div>}
      </div>}
      {role === 'GENERAL' && <section className="assistant-approval-queue">
        <div className="panel-heading"><div><span className="eyebrow">INDEPENDENT REVIEW</span><h3>Approval queue</h3></div><span className="count-tag">{pendingProposals.length}</span></div>
        {!pendingProposals.length ? <p>No proposals are waiting for review.</p> : pendingProposals.map(item => <article className="assistant-queue-item" key={item.id}>
          <span className="eyebrow">{item.id} · REQUESTER {item.operator_id}</span>
          <h4>{item.proposal.mission_objectives[0] ?? 'Mission proposal'}</h4>
          <p>{item.proposal.mission_objectives.join(' · ')}</p>
          <small>{item.operator_config.uav_count} UAVs · {item.operator_config.task_count} abstract tasks · {item.operator_config.duration} ticks · {item.operator_config.response_policy} response</small>
          {item.validation_issues.length > 0 && <small>Validation notes: {item.validation_issues.join(', ')}</small>}
          {item.operator_id === userId ? <div className="assistant-callout"><ShieldAlert size={15}/><div><b>You requested this proposal. You cannot approve or reject it.</b></div></div> : <div className="assistant-form-foot">
            <button className="button-primary" type="button" disabled={busy} onClick={() => void approvePending(item)}><CheckCircle2 size={14}/> APPROVE READY RUN</button>
            <button className="button-secondary" type="button" disabled={busy} onClick={() => void rejectPending(item)}>REJECT WITH REASON</button>
          </div>}
        </article>)}
      </section>}
      <div className="assistant-manual-note"><Radio size={15}/><span>Manual scenario planning stays available in Scenario Lab even when AI is unavailable.</span><button className="inline-link" type="button" onClick={onManualPlanning}>OPEN SCENARIO LAB <ArrowRight size={13}/></button></div>
    </section>

    <aside className="panel assistant-assessment">
      <div className="panel-heading"><div><span className="eyebrow">TELEMETRY-BOUNDED EXPLANATION</span><h3>Mission Assessment</h3></div><MessageSquareText size={17}/></div>
      {!runId || !state ? <div className="assistant-assessment-empty"><CircleHelp size={22}/><b>Choose a live simulation to assess.</b><span>Assessment uses reported GPS, deterministic scores, progress and event metadata. Hidden truth and scenario attack labels are excluded from provider input.</span></div> : <>
        <div className="assessment-source"><span>RUN <b>{runId}</b></span><span>TICK <b>{state.tick}</b></span><span>STATE <b>{state.status.toUpperCase()}</b></span></div>
        <button className="button-secondary assistant-assess-button" type="button" disabled={!ready || busy} onClick={() => void assess()}>{busy ? <LoaderCircle size={14} className="spin"/> : <Sparkles size={14}/>} EXPLAIN CURRENT EVIDENCE</button>
        {!ready && <small className="assessment-unavailable">AI unavailable; inspect deterministic telemetry and event records in Mission Control.</small>}
      </>}
      {assessment && <div className="assessment-result"><div className="assessment-ai-label"><Sparkles size={13}/> AI EXPLANATION · NOT A SIMULATION METRIC</div><p>{assessment.assessment.summary}</p>{assessment.assessment.findings.map((finding, index) => <article key={`finding-${index}`}><p>{finding.explanation}</p><small>Evidence: {finding.evidence_references.join(', ') || 'not referenced'}</small></article>)}<div className="assessment-grounded"><b>BACKEND-VALIDATED STATE</b><span>{assessment.validated_evidence.validated_progress.completed}/{assessment.validated_evidence.validated_progress.total} tasks complete · {assessment.validated_evidence.reported_telemetry.length} UAV records</span><small>Threat levels, anomaly scores and simulation metrics come from validated simulator state. Suggestions below never execute automatically.</small></div>{assessment.assessment.suggested_responses.length > 0 && <div className="assessment-suggestions"><b>Permitted response options</b>{assessment.assessment.suggested_responses.map(item => <span key={item}>{item.replace(/_/g, ' ')}</span>)}</div>}</div>}
    </aside>
  </div>
}
