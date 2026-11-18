import { useCallback, useEffect, useState } from 'react'
import { api, type ExecutionView } from '../services/api'

type Row = ExecutionView & { role: string | null }
type Fleet = { executions: Row[]; on_the_ground: Array<{ aircraft_id: string; role: string }> }

const STATE_TEXT: Record<string, string> = { executing: 'Flying its route', paused: 'Paused (holding)', connecting: 'Starting', returning: 'Returning to launch', landing: 'Landing', completed: 'Completed', aborted: 'Ended', failed: 'Failed', link_lost: 'Link lost' }

/** Every aircraft started from this deployment, each with its own run and its own controls. Shown only when more than one aircraft flies or waits. */
export function FleetRuns({ deploymentId }: { deploymentId: string }) {
  const [fleet, setFleet] = useState<Fleet | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const load = useCallback(() => Promise.resolve().then(() => api.fleetExecutions(deploymentId)).then(setFleet).catch(() => undefined), [deploymentId])
  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    const tick = async () => {
      await load()
      if (!stopped) timer = window.setTimeout(tick, 2500)
    }
    void tick()
    return () => { stopped = true; if (timer !== undefined) window.clearTimeout(timer) }
  }, [load])

  if (!fleet || (fleet.executions.length < 2 && fleet.on_the_ground.length === 0)) return null
  const act = async (row: Row, action: 'pause' | 'resume' | 'abort' | 'land') => {
    setBusy(`${row.aircraft_id}:${action}`); setError('')
    try {
      if (action === 'pause') await api.pauseExecution(row.id)
      else if (action === 'resume') await api.resumeExecution(row.id)
      else if (action === 'abort') await api.abortExecution(row.id)
      else await api.landHere(row.aircraft_id)
      await load()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'The command was not sent.') } finally { setBusy('') }
  }
  const button = (row: Row, action: 'pause' | 'resume' | 'abort' | 'land', label: string) => {
    const control = row.controls[action]
    const allowed = Boolean(control?.allowed)
    return <button type="button" className="button-quiet" disabled={!allowed || Boolean(busy)} title={allowed ? undefined : control?.reason ?? ''}
      aria-label={`${label} ${row.aircraft_id}`} onClick={() => void act(row, action)}>{label}</button>
  }
  return <section className="mc-fleet-runs" aria-label="Aircraft runs">
    <small className="mc-note">Each aircraft has its own run and controls. The main panel above follows the primary.</small>
    <ul className="mc-fleet-list">
      {fleet.executions.map(row => <li key={row.id} aria-label={`Run of ${row.aircraft_id}`}>
        <div className="mc-fleet-head"><b>{row.aircraft_id}</b> <small>{row.role ?? '—'}</small> <span>{STATE_TEXT[row.state] ?? row.state}</span></div>
        <small>{row.percent === null || row.percent === undefined ? '—' : `${Math.round(row.percent)}%`}{row.route_total ? ` · waypoint ${row.route_reached ?? 0} of ${row.route_total}` : ''}</small>
        <div className="mc-fleet-buttons">{button(row, 'pause', 'PAUSE')}{button(row, 'resume', 'RESUME')}{button(row, 'abort', 'RETURN')}{button(row, 'land', 'LAND HERE')}</div></li>)}
      {fleet.on_the_ground.map(item => <li key={item.aircraft_id} aria-label={`Run of ${item.aircraft_id}`}>
        <div className="mc-fleet-head"><b>{item.aircraft_id}</b> <small>{item.role}</small> <span>On the ground</span></div>
        <small>Not started: stays on the ground until it is needed.</small></li>)}
    </ul>
    {error && <p role="alert" className="threat-error">{error}</p>}
  </section>
}
