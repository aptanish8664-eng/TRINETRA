import { useEffect, useState } from 'react'
import { ArrowRightLeft, Power, RotateCcw } from 'lucide-react'
import { api } from '../services/api'
import type { MissionDeployment } from '../services/auth'
import { landState, type AircraftRow } from './missionControlModel'
import { ConfirmDialog } from './ConfirmDialog'

type Props = {
  /** The newest deployment (what a switch would show). */
  latest: MissionDeployment
  /** The newest execution's deployment, which Mission Control keeps showing until the user switches. */
  pinned: MissionDeployment
  execution: { active: boolean; state: string; aircraft_id: string }
  row: AircraftRow | null
  onSwitch: () => void
  /** Opens the one-click "Switch and restart at the new start" dialog (owned by the page: the flight ends, the simulation restarts at the NEW start). */
  onSwitchAndRestart?: () => void
}

const label = (deployment: MissionDeployment) => `${deployment.scenario_title} v${deployment.scenario_version_number ?? '?'}`

/**
 * Mission switching: one aircraft runs one mission at a time and a newer deployment never changes a flight on its own.
 * "Switch and restart at the new start" works in every aircraft state (it ends the flight and restarts the simulation at the NEW mission's start);
 * it follows the role rules (an Operator needs an approved version). The plain switch waits for the flight to end.
 */
export function MissionSwitchBanner({ latest, pinned, execution, row, onSwitch, onSwitchAndRestart }: Props) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [rule, setRule] = useState<{ allowed: boolean; message: string } | null>(null)
  const land = landState(row, { ...execution, active: false })
  const next = label(latest)

  // Who may start the NEW version (the backend enforces it again): the one-click switch is disabled with that reason otherwise.
  useEffect(() => {
    let stopped = false
    setRule(null)
    Promise.resolve().then(() => api.deploymentRoleRule(latest.id)).then(value => { if (!stopped) setRule(value) }).catch(() => { if (!stopped) setRule(null) })
    return () => { stopped = true }
  }, [latest.id])

  const landAndSwitch = async () => {
    setBusy(true); setError('')
    try {
      await api.landHere(execution.aircraft_id)           // armed on the ground: a normal disarm (never forced)
      setConfirming(false)
      onSwitch()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The disarm request failed.')
    } finally { setBusy(false) }
  }
  const blocked = rule !== null && !rule.allowed
  const restartButton = onSwitchAndRestart && <button type="button" className="button-primary" disabled={blocked} title={blocked ? rule?.message : 'Ends the current flight, restarts PX4 and Gazebo at the new mission start and shows the new deployment'}
    onClick={onSwitchAndRestart}><RotateCcw size={13}/> SWITCH AND RESTART AT THE NEW START</button>
  const reason = blocked ? <span className="mc-switch-note" role="status">{rule?.message}</span> : null

  if (execution.active) {
    return <div className="mission-switch-banner" role="status" aria-label="A newer deployment is ready">
      <ArrowRightLeft size={14}/><span>A newer deployment ({next}) is ready. Finish or abort the current flight to switch. Showing {label(pinned)}, the mission this flight belongs to.</span>
      {restartButton}{reason}</div>
  }
  return <div className="mission-switch-banner" role="status" aria-label="A newer deployment is ready">
    <ArrowRightLeft size={14}/>
    <span>The flight of {label(pinned)} is over ({execution.state}). A newer deployment ({next}) is ready.</span>
    {land.armed && !land.armedOnGround && <span>{execution.aircraft_id} is still armed in the air: Land here or Abort (return to launch) comes first, or use the restart switch.</span>}
    {land.armedOnGround && !confirming && <button type="button" className="button-secondary" onClick={() => { setError(''); setConfirming(true) }}><Power size={13}/> DISARM AND SWITCH…</button>}
    {!land.armed && <button type="button" className="button-secondary" onClick={onSwitch}>SWITCH TO NEW MISSION</button>}
    {restartButton}{reason}
    {land.armedOnGround && confirming && <ConfirmDialog label="Confirm disarm and switch" title={`Disarm ${execution.aircraft_id} and switch to ${next}?`} onCancel={() => { if (!busy) setConfirming(false) }}>
      <p>This sends a normal disarm (not forced): the motors stop. PX4 refuses it if the aircraft is not safely on the ground. Then Mission Control shows the new mission. Simulation only; nothing is started.</p>
      <div className="mc-exec-confirm-actions">
        <button type="button" className="button-primary" disabled={busy} onClick={() => void landAndSwitch()}>{busy ? 'DISARMING…' : 'CONFIRM DISARM AND SWITCH'}</button>
        <button type="button" className="button-quiet" disabled={busy} onClick={() => setConfirming(false)}>CANCEL</button>
      </div>
      {error && <small role="alert">{error}</small>}
    </ConfirmDialog>}
    {error && !confirming && <small role="alert">{error}</small>}
  </div>
}
