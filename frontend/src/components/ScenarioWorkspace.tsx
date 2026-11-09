import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, FlaskConical, Play, RefreshCw, Save, Send, ShieldCheck } from 'lucide-react'
import type { MissionState } from '../types'
import type { ScenarioApprovalRequest, ScenarioConfig, ScenarioIntent, ScenarioSummary } from '../services/auth'
import { api } from '../services/api'

type ScenarioWorkspaceProps = {
  config: ScenarioConfig
  onLoadConfig: (config: ScenarioConfig) => void
  onRunCreated: (runId: string, state: MissionState) => void
  canManage: boolean
}

export function ScenarioWorkspace({ config, onLoadConfig, onRunCreated, canManage }: ScenarioWorkspaceProps) {
  const emptyIntent: ScenarioIntent = {
    mission_objectives: [], proposed_tasks: [], uav_role_suggestions: [],
    operating_area_notes: '', environment_notes: '', constraints: [],
  }
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([])
  const [requests, setRequests] = useState<ScenarioApprovalRequest[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [title, setTitle] = useState('')
  const [intent, setIntent] = useState<ScenarioIntent>(emptyIntent)
  const [sandboxRunId, setSandboxRunId] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const selected = scenarios.find(scenario => scenario.id === selectedId)
  const latest = selected?.latest_version
  const approvalRequest = latest
    ? requests.find(request => request.scenario_version_id === latest.id)
    : undefined
  const approvalStatus = latest?.approval?.status ?? approvalRequest?.status ?? null

  const refresh = async () => {
    setError('')
    try {
      const [nextScenarios, nextRequests] = await Promise.all([
        api.scenarioList(),
        api.myScenarioRequests(),
      ])
      setScenarios(nextScenarios)
      setRequests(nextRequests)
      if (selectedId && !nextScenarios.some(scenario => scenario.id === selectedId)) {
        setSelectedId('')
        setTitle('')
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load saved scenarios.')
    }
  }

  useEffect(() => { void refresh() }, [])

  const runAction = async (action: () => Promise<void>) => {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await action()
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Scenario action failed.')
    } finally {
      setBusy(false)
    }
  }

  const createDraft = () => {
    if (!title.trim()) {
      setError('Enter a title for the scenario draft.')
      return
    }
    void runAction(async () => {
      const created = await api.createScenario(title.trim(), config, intent)
      setSelectedId(created.id)
      setSandboxRunId('')
      setMessage('Scenario draft saved as version 1.')
    })
  }

  const saveVersion = () => {
    if (!selected) return
    void runAction(async () => {
      const updated = await api.updateScenario(selected.id, title.trim() || selected.title, config, intent)
      setMessage(`Saved immutable version ${updated.versions[0].version_number}.`)
    })
  }

  const submitForReview = () => {
    if (!selected || !latest) return
    void runAction(async () => {
      await api.submitScenario(selected.id, latest.id)
      setMessage(`Version ${latest.version_number} submitted for General review.`)
    })
  }

  const runSandbox = () => {
    if (!selected || !latest) return
    void runAction(async () => {
      const result = await api.sandboxScenario(selected.id, latest.id)
      setSandboxRunId(result.run_id)
      setMessage('Sandbox run created. It does not approve this scenario version.')
    })
  }

  const executeApproved = () => {
    if (!selected || !latest) return
    void runAction(async () => {
      const result = await api.executeScenario(selected.id, latest.id)
      onRunCreated(result.run_id, result.state)
    })
  }

  return <section className="scenario-library panel">
    <div className="panel-heading">
      <div><span className="eyebrow">PERSISTENT SCENARIO WORKFLOW</span><h3>Save, test, and request approval</h3></div>
      <button className="small-icon" type="button" onClick={() => void refresh()} title="Refresh saved scenarios" disabled={busy}><RefreshCw size={14}/></button>
    </div>
    <p className="scenario-library-note">Saved versions are immutable. Any configuration change creates a new version and requires its own General review.</p>
    {error && <div className="scenario-library-error" role="alert"><AlertTriangle size={14}/>{error}</div>}
    {message && <div className="scenario-library-message" role="status"><CheckCircle2 size={14}/>{message}</div>}
    <div className="scenario-library-controls">
      <label>SCENARIO TITLE<input value={title} onChange={event => setTitle(event.target.value)} maxLength={120} placeholder="Name this scenario"/></label>
      <label>SAVED SCENARIO<select aria-label="Saved scenario" value={selectedId} onChange={event => {
        const nextId = event.target.value
        const next = scenarios.find(scenario => scenario.id === nextId)
        setSelectedId(nextId)
        setTitle(next?.title ?? '')
        setSandboxRunId('')
        setIntent(next?.latest_version.intent ?? emptyIntent)
        if (next) onLoadConfig(next.latest_version.config)
      }}><option value="">NEW DRAFT</option>{scenarios.map(scenario => <option key={scenario.id} value={scenario.id}>{scenario.title} · v{scenario.latest_version.version_number}</option>)}</select></label>
    </div>
    {selected && latest && <div className="scenario-library-current">
      <span>SELECTED VERSION</span><b>{selected.title} · v{latest.version_number}</b>
      <span className={`scenario-approval-state ${approvalStatus?.toLowerCase() ?? 'draft'}`}>{approvalStatus ?? 'DRAFT'}</span>
      <small>SHA-256 {latest.config_hash}</small>
      {approvalRequest?.reason && <small>GENERAL REVIEW: {approvalRequest.reason}</small>}
    </div>}
    <section className="scenario-intent-editor">
      <div><span className="eyebrow">VERSIONED MISSION INTENT</span><h4>Planning notes · advisory</h4><p>These notes are saved with each immutable version. They do not change simulator task placement or UAV behavior. Area notes are descriptive only; geographic coordinates and routes are unsupported.</p></div>
      <label>MISSION OBJECTIVES<textarea rows={3} value={intent.mission_objectives.join('\n')} disabled={!canManage || busy} placeholder="One objective per line" onChange={event => setIntent(current => ({ ...current, mission_objectives: event.target.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean) }))}/></label>
      <label>PROPOSED TASK NOTES<textarea rows={4} value={intent.proposed_tasks.map(task => task.description).join('\n')} disabled={!canManage || busy} placeholder="One task note per line" onChange={event => setIntent(current => ({ ...current, proposed_tasks: event.target.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean).map((description, index) => ({ description, task_reference: current.proposed_tasks[index]?.task_reference ?? null, assigned_uav_id: current.proposed_tasks[index]?.assigned_uav_id ?? null, priority: current.proposed_tasks[index]?.priority ?? 1 })) }))}/></label>
      <div className="scenario-role-editor"><b>UAV ROLE SUGGESTIONS · ADVISORY</b>{(['general', 'search', 'monitor', 'relay', 'reserve'] as const).map(role => <label key={role}>{role.toUpperCase()}<input type="number" min="0" max={config.uav_count} disabled={!canManage || busy} value={intent.uav_role_suggestions.find(item => item.role === role)?.count ?? 0} onChange={event => { const count = Number(event.target.value); setIntent(current => { const remaining = current.uav_role_suggestions.filter(item => item.role !== role); return { ...current, uav_role_suggestions: count > 0 ? [...remaining, { role, count, rationale: current.uav_role_suggestions.find(item => item.role === role)?.rationale ?? 'Operator-edited advisory role.' }] : remaining } }) }}/></label>)}</div>
      <label>ABSTRACT AREA NOTES<textarea rows={2} value={intent.operating_area_notes} disabled={!canManage || busy} placeholder="Descriptive text only; no coordinates or geographic routes" onChange={event => setIntent(current => ({ ...current, operating_area_notes: event.target.value }))}/></label>
      <label>ENVIRONMENT NOTES<textarea rows={2} value={intent.environment_notes} disabled={!canManage || busy} placeholder="Advisory context; not modeled by the simulator" onChange={event => setIntent(current => ({ ...current, environment_notes: event.target.value }))}/></label>
      <label>CONSTRAINTS<textarea rows={2} value={intent.constraints.join('\n')} disabled={!canManage || busy} placeholder="One advisory constraint per line" onChange={event => setIntent(current => ({ ...current, constraints: event.target.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean) }))}/></label>
    </section>
    {sandboxRunId && <div className="scenario-library-message"><FlaskConical size={14}/> Sandbox run: {sandboxRunId}</div>}
    <div className="scenario-library-actions">
      {!selected ? <button className="button-secondary" type="button" disabled={!canManage || busy} onClick={createDraft}><Save size={14}/> SAVE NEW DRAFT</button> : <>
        <button className="button-secondary" type="button" disabled={!canManage || busy} onClick={saveVersion}><Save size={14}/> SAVE NEW VERSION</button>
        <button className="button-secondary" type="button" disabled={!canManage || busy || approvalStatus === 'PENDING'} onClick={runSandbox}><FlaskConical size={14}/> RUN SANDBOX</button>
        <button className="button-secondary" type="button" disabled={!canManage || busy || Boolean(approvalStatus)} onClick={submitForReview}><Send size={14}/> SUBMIT VERSION</button>
        {approvalStatus === 'APPROVED' && <button className="button-primary" type="button" disabled={!canManage || busy} onClick={executeApproved}><Play size={14}/> EXECUTE APPROVED VERSION</button>}
      </>}
      {!canManage && <small className="permission-note">This role cannot create or run scenarios.</small>}
    </div>
    {!scenarios.length && <div className="scenario-library-empty"><ShieldCheck size={16}/>No saved scenarios yet. Save the current configuration as a draft.</div>}
  </section>
}
