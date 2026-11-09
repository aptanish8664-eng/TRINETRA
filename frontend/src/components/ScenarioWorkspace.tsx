import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, FlaskConical, Play, RefreshCw, Save, Send, ShieldCheck } from 'lucide-react'
import type { MissionState } from '../types'
import type { ScenarioApprovalRequest, ScenarioConfig, ScenarioFaultType, ScenarioIntent, ScenarioSummary } from '../services/auth'
import { api } from '../services/api'

type ScenarioWorkspaceProps = {
  config: ScenarioConfig
  onLoadConfig: (config: ScenarioConfig) => void
  onRunCreated: (runId: string, state: MissionState) => void
  canManage: boolean
  sharedScenarioId?: string | null
  onScenarioSelected?: (scenarioId: string | null) => void
  onEditGeographicMission?: () => void
}

export function ScenarioWorkspace({
  config, onLoadConfig, onRunCreated, canManage, sharedScenarioId, onScenarioSelected,
  onEditGeographicMission,
}: ScenarioWorkspaceProps) {
  const emptyIntent: ScenarioIntent = {
    mission_objectives: [], proposed_tasks: [], uav_role_suggestions: [],
    operating_area_notes: '', environment_notes: '', constraints: [], geographic_mission: null,
    uav_fault_assignments: [],
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
  const faultTypes: { value: ScenarioFaultType; label: string }[] = [
    { value: 'none', label: 'No fault' },
    { value: 'sudden', label: 'Sustained GPS offset' },
    { value: 'jump', label: 'Single GPS position jump' },
    { value: 'gradual', label: 'Gradual GPS drift' },
    { value: 'intermittent', label: 'Intermittent GPS offset' },
    { value: 'freeze', label: 'Frozen GPS position' },
    { value: 'noise', label: 'Increased GPS noise' },
    { value: 'degradation', label: 'Degrading GPS quality' },
    { value: 'velocity_inconsistency', label: 'GPS velocity inconsistency' },
  ]
  const faultAssignments = Array.from({ length: config.uav_count }, (_, index) => {
    const uavId = `UAV-${String(index + 1).padStart(2, '0')}`
    return intent.uav_fault_assignments.find(item => item.uav_id === uavId) ?? {
      uav_id: uavId,
      fault_type: config.spoofing_type !== 'none' && config.target_uav === uavId
        ? config.spoofing_type as ScenarioFaultType
        : 'none' as ScenarioFaultType,
      spoof_start: config.spoof_start,
      spoof_duration: config.spoof_duration,
      spoof_magnitude: config.spoof_magnitude,
    }
  })
  const activeFaults = faultAssignments.filter(item => item.fault_type !== 'none')
  const faultExecutionIssue = intent.uav_fault_assignments.length === 0
    ? null
    : activeFaults.length > 1
      ? 'The deterministic simulator supports one active GPS fault at a time; multiple per-UAV profiles are saved for review but cannot be run.'
      : activeFaults.length === 0
        ? config.spoofing_type !== 'none' ? 'Per-UAV fault settings do not match the simulator configuration.' : null
        : activeFaults[0].uav_id !== config.target_uav
          || activeFaults[0].fault_type !== config.spoofing_type
          || activeFaults[0].spoof_start !== config.spoof_start
          || activeFaults[0].spoof_duration !== config.spoof_duration
          || activeFaults[0].spoof_magnitude !== config.spoof_magnitude
          ? 'Per-UAV fault settings do not match the simulator configuration.'
          : null
  const intentForSave: ScenarioIntent = {
    ...intent,
    uav_fault_assignments: faultAssignments,
  }

  const updateFaultAssignment = (
    uavId: string,
    patch: Partial<ScenarioIntent['uav_fault_assignments'][number]>,
  ) => {
    const next = faultAssignments.map(item => item.uav_id === uavId ? { ...item, ...patch } : item)
    setIntent(current => ({ ...current, uav_fault_assignments: next }))
    const active = next.filter(item => item.fault_type !== 'none')
    if (active.length === 1) {
      const fault = active[0]
      onLoadConfig({
        ...config,
        target_uav: fault.uav_id,
        spoofing_type: fault.fault_type,
        spoof_start: fault.spoof_start,
        spoof_duration: fault.spoof_duration,
        spoof_magnitude: fault.spoof_magnitude,
      })
    } else if (active.length === 0) {
      onLoadConfig({ ...config, spoofing_type: 'none' })
    }
  }

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

  const loadScenario = (scenarioId: string, source = scenarios) => {
    const next = source.find(scenario => scenario.id === scenarioId)
    setSelectedId(scenarioId)
    setTitle(next?.title ?? '')
    setSandboxRunId('')
    setIntent(next ? {
      ...emptyIntent,
      ...next.latest_version.intent,
      uav_fault_assignments: next.latest_version.intent.uav_fault_assignments ?? [],
    } : emptyIntent)
    if (next) onLoadConfig(next.latest_version.config)
    onScenarioSelected?.(next?.id ?? null)
  }

  useEffect(() => {
    if (sharedScenarioId && sharedScenarioId !== selectedId && scenarios.some(item => item.id === sharedScenarioId)) {
      loadScenario(sharedScenarioId)
    }
  }, [sharedScenarioId, scenarios, selectedId])

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
      const created = await api.createScenario(title.trim(), config, intentForSave)
      setSelectedId(created.id)
      onScenarioSelected?.(created.id)
      setSandboxRunId('')
      setMessage('Scenario draft saved as version 1.')
    })
  }

  const saveVersion = () => {
    if (!selected) return
    void runAction(async () => {
      const updated = await api.updateScenario(selected.id, title.trim() || selected.title, config, intentForSave)
      onScenarioSelected?.(updated.id)
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
      <label>SAVED SCENARIO<select aria-label="Saved scenario" value={selectedId} onChange={event => loadScenario(event.target.value)}><option value="">NEW DRAFT</option>{scenarios.map(scenario => <option key={scenario.id} value={scenario.id}>{scenario.title} · v{scenario.latest_version.version_number}</option>)}</select></label>
    </div>
    {selected && latest && <div className="scenario-library-current">
      <span>SELECTED VERSION</span><b>{selected.title} · v{latest.version_number}</b>
      <span className={`scenario-approval-state ${approvalStatus?.toLowerCase() ?? 'draft'}`}>{approvalStatus ?? 'DRAFT'}</span>
      <small>SHA-256 {latest.config_hash}</small>
      {approvalRequest?.reason && <small>GENERAL REVIEW: {approvalRequest.reason}</small>}
    </div>}
    {intent.geographic_mission && <div className="assistant-callout">
      <span><b>Geographic mission data is preserved with this version.</b><span>Use the shared Mission Planner map editor for per-aircraft geography. Geographic missions cannot run in the abstract simulator.</span></span>
      <button className="button-secondary" type="button" onClick={onEditGeographicMission}>EDIT GEOGRAPHIC MISSION</button>
    </div>}
    <section className="scenario-intent-editor">
      <div><span className="eyebrow">PER-AIRCRAFT SIMULATOR FAULTS</span><h4>Software-only GPS fault assignments</h4><p>Fault IDs refer only to abstract simulator UAV-XX aircraft; they are not PX4 telemetry identities. The simulator currently runs one active GPS fault profile at a time. GPS outage and IMU faults are not modeled.</p></div>
      {faultAssignments.map(fault => <div className="scenario-role-editor" key={fault.uav_id}>
        <b>{fault.uav_id}</b>
        <label>FAULT PROFILE<select aria-label={`${fault.uav_id} fault profile`} value={fault.fault_type} disabled={!canManage || busy} onChange={event => updateFaultAssignment(fault.uav_id, { fault_type: event.target.value as ScenarioFaultType })}>
          {faultTypes.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          <option value="unavailable-gps-outage" disabled>GPS outage · not modeled</option>
          <option value="unavailable-imu" disabled>IMU faults · not modeled</option>
        </select></label>
        {fault.fault_type !== 'none' && <>
          <label>START TICK<input aria-label={`${fault.uav_id} fault start tick`} type="number" min="0" value={fault.spoof_start} disabled={!canManage || busy} onChange={event => updateFaultAssignment(fault.uav_id, { spoof_start: Number(event.target.value) })}/></label>
          <label>DURATION<input aria-label={`${fault.uav_id} fault duration`} type="number" min="1" value={fault.spoof_duration} disabled={!canManage || busy} onChange={event => updateFaultAssignment(fault.uav_id, { spoof_duration: Number(event.target.value) })}/></label>
          <label>MAGNITUDE<input aria-label={`${fault.uav_id} fault magnitude`} type="number" min="0" step="0.1" value={fault.spoof_magnitude} disabled={!canManage || busy} onChange={event => updateFaultAssignment(fault.uav_id, { spoof_magnitude: Number(event.target.value) })}/></label>
        </>}
      </div>)}
      {faultExecutionIssue && <p className="scenario-library-error" role="status">{faultExecutionIssue}</p>}
    </section>
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
        <button className="button-secondary" type="button" disabled={!canManage || busy || approvalStatus === 'PENDING' || Boolean(latest?.intent.geographic_mission) || Boolean(faultExecutionIssue)} onClick={runSandbox}><FlaskConical size={14}/> RUN SANDBOX</button>
        <button className="button-secondary" type="button" disabled={!canManage || busy || Boolean(approvalStatus)} onClick={submitForReview}><Send size={14}/> SUBMIT VERSION</button>
        {approvalStatus === 'APPROVED' && !latest?.intent.geographic_mission && !faultExecutionIssue && <button className="button-primary" type="button" disabled={!canManage || busy} onClick={executeApproved}><Play size={14}/> EXECUTE APPROVED VERSION</button>}
      </>}
      {!canManage && <small className="permission-note">This role cannot create or run scenarios.</small>}
    </div>
    {!scenarios.length && <div className="scenario-library-empty"><ShieldCheck size={16}/>No saved scenarios yet. Save the current configuration as a draft.</div>}
  </section>
}
