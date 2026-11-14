import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Circle, ExternalLink, FlaskConical, Plus, Save, Trash2 } from 'lucide-react'
import { api } from '../services/api'
import {
  ATTACK_FIELD_HELP, ATTACK_LAB_NAME, ATTACK_TYPE_SPECS, DEFAULT_CONFIG, GROUND_TRUTH_NOTE, HANDOVER_BATTERY_LABEL, HANDOVER_BATTERY_NOTE,
  HIDDEN_SCHEDULE_NOTE, MEASUREMENT_SPECS, NOT_EXECUTED_NOTE, PRESET_SPECS, RESPONSE_NOTE, RUN_AFTER_DEPLOY_NOTE, SOFTWARE_ONLY_NOTE, presetsForPlan,
  type AttackLimits, type Experiment, type ExperimentAttack, type ExperimentConfig, type ExperimentInput, type ExperimentIssue,
  type PickerMission, type PickerVersion, type PresetId,
} from './attackLabTypes'
import './attack-lab.css'

type Props = {
  canEdit: boolean
  /** Scenario last opened in Scenario Lab: preselected (with its latest version) for a new experiment. */
  initialScenarioId?: string | null
  onOpenScenarioLab?: (scenarioId: string | null) => void
}
export interface ReadinessItem { key: 'mission' | 'attack' | 'duration' | 'battery'; label: string; ok: boolean; detail: string }

const blank = (): ExperimentInput => ({
  name: '', scenario_id: '', scenario_version_id: '', preset_id: null, seed: 1, status: 'draft',
  config: { ...DEFAULT_CONFIG, attacks: [], handover: { ...DEFAULT_CONFIG.handover }, measurements: { ...DEFAULT_CONFIG.measurements } },
})
const minutes = (seconds: number | null | undefined) => (typeof seconds === 'number' ? `${(seconds / 60).toFixed(1)} min (${seconds.toFixed(0)} s)` : 'no generated route')
const presetLabel = (id: string | null) => PRESET_SPECS.find(item => item.id === id)?.label ?? null
const versionMarks = (version: PickerVersion) => [
  version.latest ? 'latest' : 'superseded', version.deployed ? 'deployed' : 'not deployed', version.in_mission_control ? 'in Mission Control' : '',
].filter(Boolean).join(' · ')

/** The four readiness checks shown beside the builder. Client-side only; VALIDATE asks the backend for the authoritative answer. */
export function computeReadiness(draft: ExperimentInput, version: PickerVersion | null, limits: AttackLimits | null): ReadinessItem[] {
  const attacks = draft.config.attacks
  const aircraft = new Map((version?.aircraft ?? []).map(item => [item.uav_id, item]))
  const invalid = attacks.flatMap((attack, index) => {
    const spec = limits?.types.find(item => item.type === attack.type)
    const problems: string[] = []
    if (!aircraft.has(attack.target_aircraft)) problems.push(`attack ${index + 1}: ${attack.target_aircraft} is not in this version`)
    if (!(attack.duration_s > 0) || !(attack.start_s >= 0)) problems.push(`attack ${index + 1}: start and duration must be positive`)
    if (spec && attack.type !== 'freeze' && (attack.magnitude < spec.magnitude.min || attack.magnitude > spec.magnitude.max)) {
      problems.push(`attack ${index + 1}: ${spec.magnitude.label.toLowerCase()} must be ${spec.magnitude.min}–${spec.magnitude.max} ${spec.magnitude.unit}`)
    }
    return problems
  })
  const late = attacks.flatMap((attack, index) => {
    const total = aircraft.get(attack.target_aircraft)?.duration_s
    return typeof total === 'number' && attack.start_s + attack.duration_s > total
      ? [`attack ${index + 1} ends at ${(attack.start_s + attack.duration_s).toFixed(0)} s but the route lasts ${total.toFixed(0)} s`] : []
  })
  const noRoute = attacks.some(attack => aircraft.has(attack.target_aircraft) && typeof aircraft.get(attack.target_aircraft)?.duration_s !== 'number')
  const battery = draft.config.handover.min_battery_pct
  return [
    { key: 'mission', label: 'Mission pinned', ok: Boolean(version),
      detail: version ? `v${version.version_number} (${versionMarks(version)})` : 'Choose a saved mission and an exact version.' },
    { key: 'attack', label: 'Attack valid', ok: Boolean(version) && invalid.length === 0,
      detail: !version ? 'Needs a pinned mission version.' : invalid.length ? invalid.join('; ') : attacks.length ? `${attacks.length} attack(s) within the allowed ranges.` : 'No attack: a baseline run.' },
    { key: 'duration', label: 'Within duration', ok: Boolean(version) && late.length === 0 && !noRoute,
      detail: !version ? 'Needs a pinned mission version.' : noRoute ? 'A targeted aircraft has no generated route.' : late.length ? late.join('; ') : attacks.length ? 'Every attack ends before its aircraft finishes the route.' : 'Nothing to check: no attack.' },
    { key: 'battery', label: 'Battery threshold', ok: Number.isFinite(battery) && battery >= 0 && battery <= 100,
      detail: Number.isFinite(battery) && battery >= 0 && battery <= 100 ? `Handover eligibility: at least ${battery}% battery.` : 'Enter a battery level from 0 to 100%.' },
  ]
}

/** Attack Lab: defines simulated, software-only GPS-attack experiments pinned to an exact schema-2 version. */
export function AttackLab({ canEdit, initialScenarioId = null, onOpenScenarioLab }: Props) {
  const [limits, setLimits] = useState<AttackLimits | null>(null)
  const [missions, setMissions] = useState<PickerMission[]>([])
  const [legacyOnly, setLegacyOnly] = useState(0)
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [draft, setDraft] = useState<ExperimentInput>(blank)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [readOnly, setReadOnly] = useState(false)
  const [issues, setIssues] = useState<ExperimentIssue[]>([])
  const [validated, setValidated] = useState<boolean | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    const [list, picker] = await Promise.all([api.attackExperiments(), api.attackLabMissions()])
    setExperiments(list)
    setMissions(picker.missions)
    setLegacyOnly(picker.legacy_only_count)
    return picker.missions
  }, [])

  useEffect(() => {
    let active = true
    void Promise.all([api.attackLabLimits(), refresh()]).then(([value, picker]) => {
      if (!active) return
      setLimits(value)
      // Coming from Scenario Lab: start on that scenario's latest saved version.
      const preferred = picker.find(item => item.scenario_id === initialScenarioId)
      if (preferred) setDraft(current => (current.scenario_id ? current : { ...current, scenario_id: preferred.scenario_id, scenario_version_id: preferred.versions[0]?.id ?? '' }))
      setLoaded(true)
    }).catch(cause => { if (active) { setError(cause instanceof Error ? cause.message : `${ATTACK_LAB_NAME} could not load.`); setLoaded(true) } })
    return () => { active = false }
  }, [refresh, initialScenarioId])

  const mission = useMemo(() => missions.find(item => item.scenario_id === draft.scenario_id) ?? null, [missions, draft.scenario_id])
  const version = useMemo(() => mission?.versions.find(item => item.id === draft.scenario_version_id) ?? null, [mission, draft.scenario_version_id])
  const latestVersion = mission?.versions[0] ?? null
  const aircraft = version?.aircraft ?? []
  const locked = readOnly || !canEdit
  const spec = (type: string) => limits?.types.find(item => item.type === type)
  const fieldHelp = { ...ATTACK_FIELD_HELP, ...(limits?.field_help ?? {}) }
  const { offered, hidden } = presetsForPlan(version ? aircraft.length : 1)
  const readiness = computeReadiness(draft, version, limits)
  const missionOf = (experiment: Experiment) => {
    const found = missions.find(item => item.scenario_id === experiment.scenario_id)
    const pinned = found?.versions.find(item => item.id === experiment.scenario_version_id)
    return found ? `${found.title}${pinned ? ` · v${pinned.version_number}` : ''}` : 'mission not available'
  }

  const update = (patch: Partial<ExperimentInput>) => { setDraft(current => ({ ...current, ...patch })); setIssues([]); setMessage(''); setValidated(null) }
  const updateConfig = (patch: Partial<ExperimentConfig>) => update({ config: { ...draft.config, ...patch } })
  const updateAttack = (index: number, patch: Partial<ExperimentAttack>) =>
    updateConfig({ attacks: draft.config.attacks.map((item, position) => (position === index ? { ...item, ...patch } : item)) })

  const open = (experiment: Experiment) => {
    setEditingId(experiment.id)
    setReadOnly(experiment.status !== 'draft')
    setDraft({ name: experiment.name, scenario_id: experiment.scenario_id, scenario_version_id: experiment.scenario_version_id, preset_id: experiment.preset_id, seed: experiment.seed, config: experiment.config, status: experiment.status })
    setIssues([]); setError(''); setMessage(''); setValidated(null)
    window.scrollTo?.({ top: 0, behavior: 'smooth' })
  }
  const startNew = () => { setEditingId(null); setReadOnly(false); setDraft(blank()); setIssues([]); setError(''); setMessage(''); setValidated(null) }

  const applyPreset = async (presetId: PresetId) => {
    if (!draft.scenario_id || !draft.scenario_version_id) return
    setBusy(true); setError('')
    try {
      const applied = await api.applyAttackPreset(presetId, draft.scenario_id, draft.scenario_version_id, draft.seed)
      setDraft(current => ({ ...current, preset_id: presetId, config: applied.config }))
      setMessage(`${presetLabel(presetId)} loaded. It defines the attack only; every value can still be edited.`)
      setIssues([]); setValidated(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The preset could not be applied.')
    } finally { setBusy(false) }
  }

  const addAttack = () => {
    if (!limits || !aircraft.length) return
    const first = limits.types[0]
    updateConfig({ attacks: [...draft.config.attacks, {
      target_aircraft: aircraft.find(item => item.role === 'primary')?.uav_id ?? aircraft[0].uav_id, type: first.type, start_s: 30, duration_s: 30,
      magnitude: first.magnitude.default, direction_deg: null,
    }] })
  }

  const payload = (status: 'draft' | 'ready'): ExperimentInput => ({ ...draft, status, name: draft.name.trim() })

  const validate = async () => {
    setBusy(true); setError('')
    try {
      const result = await api.validateExperiment(payload('draft'))
      setIssues(result.issues)
      setValidated(result.valid)
      setMessage(result.valid ? 'Valid: this experiment passes every backend check.' : '')
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Validation failed.') } finally { setBusy(false) }
  }

  const save = async (status: 'draft' | 'ready') => {
    setBusy(true); setError(''); setIssues([])
    try {
      const saved = editingId ? await api.updateExperiment(editingId, payload(status)) : await api.createExperiment(payload(status))
      setEditingId(saved.id); setReadOnly(saved.status !== 'draft'); setValidated(true)
      setMessage(`Saved “${saved.name}” as ${saved.status}. Nothing was executed.`)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The experiment could not be saved.')
      const found = (cause as { detail?: { issues?: ExperimentIssue[] } } | null)?.detail?.issues
      if (Array.isArray(found)) { setIssues(found); setValidated(false) }
    } finally { setBusy(false) }
  }

  const remove = async () => {
    if (!editingId) return
    setBusy(true)
    try { await api.deleteExperiment(editingId); startNew(); await refresh(); setMessage('Draft deleted.') }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'The draft could not be deleted.') } finally { setBusy(false) }
  }

  return <section className="attack-lab" aria-label={ATTACK_LAB_NAME}>
    <header className="panel attack-lab-head">
      <div><span className="eyebrow">SIMULATED · SOFTWARE-ONLY</span><h2>{ATTACK_LAB_NAME}</h2>
        <p>{SOFTWARE_ONLY_NOTE}</p>
        <p className="attack-lab-note">{NOT_EXECUTED_NOTE}</p></div>
      <div className="attack-lab-head-notes">
        <p><b>ATTACK ONLY</b>{RESPONSE_NOTE}</p>
        <details><summary>How the attack schedule is kept hidden</summary><p>{HIDDEN_SCHEDULE_NOTE}</p><p>{GROUND_TRUTH_NOTE}</p></details>
      </div>
    </header>
    {error && <div className="scenario-library-error" role="alert"><AlertTriangle size={14}/>{error}</div>}
    {message && <div className="scenario-library-message" role="status"><CheckCircle2 size={14}/>{message}</div>}
    {!canEdit && <p className="permission-note" role="note">Operator accounts can view experiments but not create or edit them.</p>}

    <div className="attack-lab-grid">
      <div className="attack-lab-left">
        <section className="panel attack-card" aria-label="Mission and version">
          <div className="panel-heading"><div><span className="eyebrow">{editingId ? (readOnly ? 'VIEWING · LOCKED' : 'EDITING DRAFT') : 'NEW EXPERIMENT'} · STEP 1</span><h3><FlaskConical size={15}/> Mission and version</h3></div>
            {canEdit && <button type="button" className="button-secondary" onClick={startNew}><Plus size={13}/> NEW EXPERIMENT</button>}</div>
          <fieldset disabled={locked || busy} className="attack-lab-fields">
            <label>EXPERIMENT NAME<input value={draft.name} maxLength={120} onChange={event => update({ name: event.target.value })} placeholder="Name this experiment"/></label>
            <label>MISSION (saved schema-2 scenarios)<select aria-label="Mission" value={draft.scenario_id} onChange={event => {
              const next = missions.find(item => item.scenario_id === event.target.value)
              update({ scenario_id: event.target.value, scenario_version_id: next?.versions[0]?.id ?? '', preset_id: null, config: { ...draft.config, attacks: [] } })
            }}>
              <option value="">Choose a saved mission</option>{missions.map(item => <option key={item.scenario_id} value={item.scenario_id}>{item.title} · {item.versions.length} version{item.versions.length === 1 ? '' : 's'}</option>)}</select></label>
            <label>VERSION<select aria-label="Version" value={draft.scenario_version_id} disabled={!mission} onChange={event => update({ scenario_version_id: event.target.value })}>
              <option value="">Choose an exact version</option>{(mission?.versions ?? []).map(item => <option key={item.id} value={item.id}>v{item.version_number} · {versionMarks(item)}</option>)}</select></label>
          </fieldset>
          {loaded && missions.length === 0 && <p className="planner-empty">No saved schema-2 scenario yet. Plan and save one in Scenario Lab; it will be listed here.{legacyOnly > 0 ? ` ${legacyOnly} legacy scenario(s) are not listed because they have no route plan.` : ''}</p>}
          {mission && !version && <p className="planner-empty">Choose the exact version to pin this experiment to.</p>}
          {mission && version && <div className="attack-mission-summary" aria-label="Mission summary">
            <dl>
              <div><dt>Scenario</dt><dd>{mission.title}</dd></div>
              <div><dt>Version</dt><dd>v{version.version_number} · {versionMarks(version)}</dd></div>
              <div><dt>Aircraft and roles</dt><dd>{aircraft.map(item => `${item.uav_id} ${item.role ?? 'no role'}`).join(', ') || 'none'}</dd></div>
              <div><dt>Estimated duration</dt><dd>{minutes(version.estimated_duration_s)}</dd></div>
              <div><dt>Area</dt><dd>{version.area_summary}</dd></div>
            </dl>
            {version.superseded && <p className="attack-flag warn" role="status">A newer version (v{latestVersion?.version_number}) of this mission exists. An experiment saved here stays pinned to v{version.version_number}, a superseded version.</p>}
            {!version.deployed && <p className="attack-flag" role="note">{RUN_AFTER_DEPLOY_NOTE}</p>}
            {version.deployed && <p className="attack-flag ok" role="note">Deployed{version.deployed_at ? ` on ${new Date(version.deployed_at).toLocaleString()}` : ''}{version.in_mission_control ? ' and currently shown in Mission Control' : ''}.</p>}
          </div>}
          {onOpenScenarioLab && <button type="button" className="button-quiet attack-open-link" onClick={() => onOpenScenarioLab(draft.scenario_id || null)}><ExternalLink size={13}/> Open in Scenario Lab</button>}
        </section>

        <section className="panel attack-card" aria-label="Presets">
          <div className="panel-heading"><div><span className="eyebrow">STEP 2 · OPTIONAL</span><h3>Start from a preset</h3></div>
            {draft.preset_id && <span className="count-tag">{presetLabel(draft.preset_id)}</span>}</div>
          <p className="attack-hint">A preset fills in the attack list below. It defines the attack only and can be edited afterwards.{!version ? ' Choose a mission version first.' : ''}</p>
          <div className="attack-preset-grid">{offered.map(item => <button key={item.id} type="button" className={`attack-preset ${draft.preset_id === item.id ? 'selected' : ''}`}
            disabled={locked || busy || !version} aria-pressed={draft.preset_id === item.id} onClick={() => void applyPreset(item.id)}>
            <b>{item.label}</b><span>{item.does}</span><small>Why: {item.useful}</small></button>)}</div>
          {hidden.length > 0 && <p className="attack-hint" role="note">Not listed for this plan: {hidden.map(item => `${item.label} — ${item.needs}`).join(' ')}</p>}
        </section>

        <section className="panel attack-card" aria-label="Attacks">
          <div className="panel-heading"><div><span className="eyebrow">STEP 3</span><h3>Simulated GPS attacks <span className="count-tag">{draft.config.attacks.length}</span></h3></div>
            <button type="button" className="button-secondary" disabled={locked || busy || !limits || !aircraft.length || draft.config.attacks.length >= (limits?.max_attacks ?? 0)} onClick={addAttack}><Plus size={13}/> ADD ATTACK</button></div>
          <details className="attack-help"><summary>What each attack type does</summary>
            <ul>{ATTACK_TYPE_SPECS.map(item => <li key={item.value}><b>{item.label}</b> {limits?.types.find(entry => entry.type === item.value)?.summary ?? item.summary}</li>)}</ul></details>
          <details className="attack-help"><summary>What the fields mean</summary>
            <ul>{(['start', 'duration', 'magnitude', 'direction', 'seed'] as const).map(key => <li key={key}><b>{key[0].toUpperCase() + key.slice(1)}</b> {fieldHelp[key]}</li>)}</ul></details>
          {draft.config.attacks.length === 0 && <p className="planner-empty">No attacks: this is a baseline run.</p>}
          {draft.config.attacks.map((attack, index) => {
            const attackSpec = spec(attack.type)
            return <fieldset key={index} className="attack-row" disabled={locked || busy} aria-label={`Attack ${index + 1}`}>
              <label>AIRCRAFT<select aria-label={`Attack ${index + 1} aircraft`} value={attack.target_aircraft} onChange={event => updateAttack(index, { target_aircraft: event.target.value })}>
                {!aircraft.some(item => item.uav_id === attack.target_aircraft) && <option value={attack.target_aircraft}>{attack.target_aircraft} · not in this version</option>}
                {aircraft.map(item => <option key={item.uav_id} value={item.uav_id}>{item.uav_id}{item.role ? ` · ${item.role}` : ''}</option>)}</select></label>
              <label>TYPE<select aria-label={`Attack ${index + 1} type`} value={attack.type} onChange={event => {
                const next = spec(event.target.value)
                updateAttack(index, { type: event.target.value as ExperimentAttack['type'], magnitude: next?.magnitude.default ?? 0 })
              }}>{limits?.types.map(item => <option key={item.type} value={item.type}>{item.label}</option>)}</select></label>
              <label title={fieldHelp.start}>START (s){limits ? ` ${limits.start_s.min}–${limits.start_s.max}` : ''}<input type="number" aria-label={`Attack ${index + 1} start`} min={limits?.start_s.min} max={limits?.start_s.max} value={attack.start_s} onChange={event => updateAttack(index, { start_s: Number(event.target.value) })}/></label>
              <label title={fieldHelp.duration}>DURATION (s){limits ? ` ${limits.duration_s.min}–${limits.duration_s.max}` : ''}<input type="number" aria-label={`Attack ${index + 1} duration`} min={limits?.duration_s.min} max={limits?.duration_s.max} value={attack.duration_s} onChange={event => updateAttack(index, { duration_s: Number(event.target.value) })}/></label>
              {attack.type !== 'freeze' && <label title={fieldHelp.magnitude}>{(attackSpec?.magnitude.label ?? 'MAGNITUDE').toUpperCase()} ({attackSpec?.magnitude.unit}) {attackSpec ? `${attackSpec.magnitude.min}–${attackSpec.magnitude.max}` : ''}
                <input type="number" step="any" aria-label={`Attack ${index + 1} magnitude`} min={attackSpec?.magnitude.min} max={attackSpec?.magnitude.max} value={attack.magnitude} onChange={event => updateAttack(index, { magnitude: Number(event.target.value) })}/></label>}
              {attackSpec?.uses_direction && <label title={fieldHelp.direction}>DIRECTION (deg, blank = random)<input type="number" aria-label={`Attack ${index + 1} direction`} min={0} max={359.9} value={attack.direction_deg ?? ''} onChange={event => updateAttack(index, { direction_deg: event.target.value === '' ? null : Number(event.target.value) })}/></label>}
              <button type="button" className="button-quiet" aria-label={`Remove attack ${index + 1}`} onClick={() => updateConfig({ attacks: draft.config.attacks.filter((_, position) => position !== index) })}><Trash2 size={13}/> REMOVE</button>
              <small className="attack-row-help">{attackSpec?.summary ?? ATTACK_TYPE_SPECS.find(item => item.value === attack.type)?.summary}</small>
            </fieldset>
          })}
          <fieldset disabled={locked || busy} className="attack-lab-fields">
            <label title={fieldHelp.seed}>SEED{limits ? ` (${limits.seed.min}–${limits.seed.max})` : ''}<input aria-label="Seed" type="number" value={draft.seed} min={limits?.seed.min} max={limits?.seed.max} onChange={event => update({ seed: Number(event.target.value) })}/>
              <small>{fieldHelp.seed}</small></label>
            <label title={HANDOVER_BATTERY_NOTE}>{(limits?.handover.label ?? HANDOVER_BATTERY_LABEL).toUpperCase()}<input type="number" aria-label="Handover eligibility minimum battery" min={0} max={100} value={draft.config.handover.min_battery_pct} onChange={event => updateConfig({ handover: { ...draft.config.handover, min_battery_pct: Number(event.target.value) } })}/>
              <small>{limits?.handover.note ?? HANDOVER_BATTERY_NOTE}</small></label>
            <label className="attack-inline"><input type="checkbox" checked={draft.config.allow_overlap} onChange={event => updateConfig({ allow_overlap: event.target.checked })}/> Allow overlapping attacks on the same aircraft</label>
          </fieldset>
          {issues.length > 0 && <div className="scenario-library-error" role="alert"><AlertTriangle size={14}/>This experiment needs changes:
            <ul aria-label="Experiment issues">{issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul></div>}
          {!locked && <div className="scenario-library-actions">
            <button type="button" className="button-secondary" disabled={busy || !draft.scenario_version_id} onClick={() => void validate()}>VALIDATE</button>
            <button type="button" className="button-primary" disabled={busy || !draft.name.trim() || !draft.scenario_version_id} onClick={() => void save('draft')}><Save size={14}/> SAVE DRAFT</button>
            <button type="button" className="button-secondary" disabled={busy || !draft.name.trim() || !draft.scenario_version_id} onClick={() => void save('ready')} title="Marks the experiment ready and locks it from further edits">SAVE AS READY (LOCKS)</button>
            {editingId && <button type="button" className="button-quiet" disabled={busy} onClick={() => void remove()}><Trash2 size={13}/> DELETE DRAFT</button>}
          </div>}
          {readOnly && editingId && <p className="permission-note">Ready experiments are locked. Create a new experiment to change them.</p>}
        </section>
      </div>

      <aside className="attack-lab-right" aria-label="Experiment overview">
        <section className="panel attack-card attack-timeline" aria-label="Timeline preview">
          <span className="eyebrow">TIMELINE PREVIEW · PER AIRCRAFT</span>
          {aircraft.length === 0 ? <p className="planner-empty">Choose a mission version to preview attacks against each aircraft's route.</p> : aircraft.map(item => {
            const total = item.duration_s
            return <div key={item.uav_id} className="attack-timeline-row">
              <b>{item.uav_id}<small>{item.role ?? 'no role'}</small></b>
              <div className="attack-timeline-bar" role="img" aria-label={`${item.uav_id} route ${total ? `${total.toFixed(0)} s` : 'duration unknown'}`}>
                {draft.config.attacks.map((attack, index) => attack.target_aircraft === item.uav_id && total
                  ? <i key={index} title={`${attack.type} ${attack.start_s}–${attack.start_s + attack.duration_s} s`} className={attack.start_s + attack.duration_s > total ? 'over' : ''}
                    style={{ left: `${Math.min(100, (attack.start_s / total) * 100)}%`, width: `${Math.max(1, Math.min(100 - (attack.start_s / total) * 100, (attack.duration_s / total) * 100))}%` }}/> : null)}
              </div>
              <small>{total ? `${total.toFixed(0)} s route` : 'no generated route'}</small>
            </div>
          })}
          <small className="attack-hint">Amber: attack window. Red: it runs past the end of the route.</small>
        </section>
        <section className="panel attack-card" aria-label="Readiness checklist">
          <span className="eyebrow">READINESS</span>
          <ul className="attack-readiness">{readiness.map(item => <li key={item.key} className={item.ok ? 'ok' : 'todo'}>
            {item.ok ? <CheckCircle2 size={14} aria-hidden="true"/> : <Circle size={14} aria-hidden="true"/>}
            <span><b>{item.label}</b><small>{item.detail}</small></span></li>)}</ul>
          <small className="attack-hint">{validated === true ? 'The backend validated this experiment.' : validated === false ? 'The backend found problems: see the list under the attacks.' : 'These checks run in the browser. Press VALIDATE for the backend check.'}</small>
        </section>
        <section className="panel attack-card" aria-label="What will be measured">
          <span className="eyebrow">WHAT WILL BE MEASURED</span>
          <ul className="attack-measures">{MEASUREMENT_SPECS.map(item => <li key={item.key} title={item.help}><b>{item.label}</b><small>{item.help}</small></li>)}</ul>
          <small className="attack-hint">{HIDDEN_SCHEDULE_NOTE}</small>
        </section>
      </aside>
    </div>

    <section className="panel attack-card attack-lab-list" aria-label="Experiments">
      <div className="panel-heading"><div><span className="eyebrow">SAVED EXPERIMENTS</span><h3>Experiments <span className="count-tag">{experiments.length}</span></h3></div></div>
      {experiments.length === 0 ? <p className="planner-empty">No experiments yet. Build one above and save it.</p> : <ul className="attack-experiment-grid">{experiments.map(item => <li key={item.id}>
        <button type="button" className={editingId === item.id ? 'selected' : ''} onClick={() => open(item)} aria-label={`Open experiment ${item.name}`}>
          <b>{item.name}</b>
          <small>{item.status.toUpperCase()}{presetLabel(item.preset_id) ? ` · ${presetLabel(item.preset_id)}` : ' · custom'} · {item.config.attacks.length} attack(s)</small>
          <small>{missionOf(item)}</small>
          {item.superseded && <small className="warn">Mission version superseded; the experiment stays pinned to it</small>}
        </button></li>)}</ul>}
    </section>
  </section>
}
