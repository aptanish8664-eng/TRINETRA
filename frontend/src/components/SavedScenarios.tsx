import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, ExternalLink, Pencil, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '../services/api'
import type { GeneralScenarioRow, ScenarioDeletePreview } from '../services/auth'

type Props = {
  /** Open a scenario (and optionally one version) in Scenario Lab. */
  onOpen: (scenarioId: string, versionNumber?: number) => void
  /** Open a quick-made scenario in Quick Mission with its stored values. */
  onOpenQuick: (scenarioId: string) => void
}

const STATUSES: GeneralScenarioRow['status'][] = ['draft', 'submitted', 'approved', 'deployed']
const when = (value: string) => new Date(value).toLocaleString()
const COUNT_LABELS: [keyof ScenarioDeletePreview['counts'], string][] = [
  ['versions', 'versions'], ['approvals', 'approvals'], ['deployments', 'deployments'], ['executions', 'executions'], ['execution_events', 'execution events'],
  ['run_records', 'run records'], ['experiments', 'experiments'], ['experiment_results', 'experiment results'],
]

/** GENERAL's list of every saved scenario and draft: filters, open/edit, export and a counted, name-confirmed delete. */
export function SavedScenarios({ onOpen, onOpenQuick }: Props) {
  const [rows, setRows] = useState<GeneralScenarioRow[]>([])
  const [owner, setOwner] = useState('')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [versionChoice, setVersionChoice] = useState<Record<string, number>>({})
  const [deleting, setDeleting] = useState<{ row: GeneralScenarioRow; preview: ScenarioDeletePreview | null; typed: string; withResults: boolean; busy: boolean } | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { setRows(await api.generalScenarios()) } catch (cause) { setError(cause instanceof Error ? cause.message : 'The saved scenarios could not be loaded.') } finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const owners = useMemo(() => [...new Map(rows.map(row => [row.owner_user_id, `${row.owner_name ?? row.owner_username ?? 'unknown'} (${row.owner_role ?? '?'})`])).entries()], [rows])
  const shown = rows.filter(row => (!owner || row.owner_user_id === owner) && (!status || row.status === status) && (!search.trim() || row.title.toLowerCase().includes(search.trim().toLowerCase())))

  const exportRecord = async (row: GeneralScenarioRow) => {
    setError(''); setNotice('')
    try {
      const record = await api.generalScenarioExport(row.id)
      const link = document.createElement('a')
      link.href = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type: 'application/json' }))
      link.download = `${row.title.replace(/[^\w.-]+/g, '_') || 'scenario'}-record.json`
      link.click()
      URL.revokeObjectURL(link.href)
      setNotice(`Exported ${row.title} (${row.versions.length} version${row.versions.length === 1 ? '' : 's'}).`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'The export failed.') }
  }

  const askDelete = async (row: GeneralScenarioRow) => {
    setError(''); setNotice('')
    setDeleting({ row, preview: null, typed: '', withResults: false, busy: false })
    try { const preview = await api.generalDeletePreview(row.id); setDeleting(current => current && current.row.id === row.id ? { ...current, preview } : current) }
    catch (cause) { setDeleting(null); setError(cause instanceof Error ? cause.message : 'The delete preview failed.') }
  }
  const confirmDelete = async () => {
    if (!deleting?.preview) return
    setDeleting({ ...deleting, busy: true }); setError('')
    try {
      const done = await api.generalDeleteScenario(deleting.row.id, deleting.typed, deleting.withResults)
      setNotice(`Deleted ${done.title}: ${Object.entries(done.counts).filter(([, count]) => count).map(([key, count]) => `${count} ${key.replace(/_/g, ' ')}`).join(', ')}. Audit record written.`)
      setDeleting(null)
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The delete failed.')
      setDeleting(current => current ? { ...current, busy: false } : current)
      await load()
    }
  }

  return <section className="admin-queue panel saved-scenarios" aria-label="Saved scenarios">
    <div className="admin-queue-head"><div><span className="eyebrow">SAVED SCENARIOS</span><h2>Every user’s scenarios and drafts</h2></div>
      <button className="button-secondary" disabled={loading} onClick={() => void load()}><RefreshCw size={13}/> {loading ? 'LOADING…' : 'REFRESH'}</button></div>
    <div className="saved-filters">
      <label>OWNER<select aria-label="Filter by owner" value={owner} onChange={event => setOwner(event.target.value)}><option value="">All owners</option>{owners.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      <label>STATUS<select aria-label="Filter by status" value={status} onChange={event => setStatus(event.target.value)}><option value="">All statuses</option>{STATUSES.map(item => <option key={item} value={item}>{item}</option>)}</select></label>
      <label>NAME<input aria-label="Search scenario names" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search by name"/></label>
    </div>
    {error && <div className="admin-inline-error" role="alert">{error}</div>}
    {notice && <div className="assistant-message" role="status">{notice}</div>}
    {!shown.length ? <div className="admin-empty"><b>{rows.length ? 'No scenario matches these filters' : 'No saved scenarios yet'}</b></div> : <ul className="saved-list">{shown.map(row => {
      const chosen = versionChoice[row.id] ?? row.latest_version_number
      return <li className="saved-row" key={row.id}>
        <div className="saved-main"><b>{row.title}</b>
          <span>{row.owner_name ?? row.owner_username ?? 'unknown'} · {row.owner_role ?? '?'}</span>
          <small>v{row.latest_version_number} · updated {when(row.updated_at)}</small></div>
        <div className="saved-facts"><span className={`saved-status ${row.status}`}>{row.status}{row.approval_status ? ` · ${row.approval_status.toLowerCase().replace(/_/g, ' ')}` : ''}</span>
          <span>{row.source === 'quick' ? 'Quick Mission' : row.source === 'manual' ? 'Manual' : 'Legacy'}</span>
          <small>{row.deployments} deployment{row.deployments === 1 ? '' : 's'} · {row.executions} execution{row.executions === 1 ? '' : 's'}{row.active_execution ? ' · flying' : ''}</small></div>
        <div className="saved-actions">
          <label className="saved-version">VERSION<select aria-label={`Version of ${row.title}`} value={chosen} onChange={event => setVersionChoice(current => ({ ...current, [row.id]: Number(event.target.value) }))}>
            {row.versions.map(item => <option key={item.id} value={item.number}>v{item.number}{item.number === row.latest_version_number ? ' (latest)' : ''} · {item.source}</option>)}</select></label>
          <button className="button-secondary" onClick={() => onOpen(row.id, chosen)}><ExternalLink size={13}/> OPEN / EDIT</button>
          {row.source === 'quick' && <button className="button-secondary" onClick={() => onOpenQuick(row.id)}><Pencil size={13}/> EDIT IN QUICK MISSION</button>}
          <button className="button-secondary" onClick={() => void exportRecord(row)}><Download size={13}/> EXPORT</button>
          <button className="button-secondary danger" onClick={() => void askDelete(row)}><Trash2 size={13}/> DELETE</button></div>
        {deleting?.row.id === row.id && <div className="scenario-review-form saved-delete" role="group" aria-label="Confirm delete">
          <b>Delete “{row.title}”?</b>
          {!deleting.preview ? <small>Counting what would be deleted…</small> : <>
            <p>This permanently deletes: {COUNT_LABELS.filter(([key]) => deleting.preview!.counts[key] > 0 || key === 'versions').map(([key, label]) => `${deleting.preview!.counts[key]} ${label}`).join(', ')}. The audit log is never deleted.</p>
            {deleting.preview.blockers.length > 0 && <div className="admin-inline-error" role="alert">{deleting.preview.blockers.map(item => <p key={item.code}>{item.message}</p>)}</div>}
            {deleting.preview.counts.experiment_results > 0 && <label className="saved-check"><input type="checkbox" checked={deleting.withResults} onChange={event => setDeleting({ ...deleting, withResults: event.target.checked })}/>
              Also delete the {deleting.preview.counts.experiment_results} experiment result(s). Otherwise they are kept and marked “from a deleted scenario”.</label>}
            <label>TYPE THE SCENARIO NAME TO CONFIRM<input aria-label="Type the scenario name" value={deleting.typed} onChange={event => setDeleting({ ...deleting, typed: event.target.value })}/></label>
            <div className="admin-action-buttons"><button className="button-primary" disabled={deleting.busy || deleting.typed.trim() !== row.title || deleting.preview.blockers.length > 0} onClick={() => void confirmDelete()}>{deleting.busy ? 'DELETING…' : 'DELETE FOR GOOD'}</button>
              <button className="button-quiet" disabled={deleting.busy} onClick={() => setDeleting(null)}>CANCEL</button></div></>}
        </div>}
      </li>
    })}</ul>}
  </section>
}
