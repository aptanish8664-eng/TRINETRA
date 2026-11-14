import { AlertTriangle, Battery, BatteryWarning, Box, CheckCircle2, Circle, Layers, LocateFixed, Maximize2, Satellite } from 'lucide-react'
import type { MissionDeployment } from '../services/auth'
import type { GazeboPoseSnapshot } from '../types'
import type { MapLayers, Basemap } from './CesiumLiveMap'
import type { Tier } from './attackLabTypes'
import { ROLE_COLORS, THRESHOLDS, batteryTone, deploymentVersionLabel, isLegacyDeployment, routeSummary, type Alert, type AircraftRow, type LogEntry } from './missionControlModel'

const NO_DATA = 'no data'
const num = (value: number | null | undefined, digits = 1, suffix = '') =>
  typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : NO_DATA

export type ChipState = 'ok' | 'warn' | 'bad' | 'unknown'
export interface StatusChip { key: string; label: string; state: ChipState; detail: string }

export function ConnectionChips({ chips }: { chips: StatusChip[] }) {
  return <ul className="mc-chips" aria-label="Connection status">{chips.map(chip =>
    <li key={chip.key} className={`mc-chip ${chip.state}`} title={chip.detail}><i aria-hidden="true"/><span>{chip.label}</span><b>{chip.detail}</b></li>)}</ul>
}

export function HeaderStrip({ deployment, chips, onOpenScenarioLab }: { deployment: MissionDeployment | null; chips: StatusChip[]; onOpenScenarioLab?: () => void }) {
  const legacy = isLegacyDeployment(deployment)
  const routes = deployment?.intent.mission_plan?.aircraft.filter(item => item.route).length ?? 0
  const scenarioLab = onOpenScenarioLab ? <button type="button" className="inline-link" onClick={onOpenScenarioLab}>Open Scenario Lab</button> : null
  return <header className="mc-header panel" aria-label="Mission header">
    <div className="mc-header-main">
      <span className="eyebrow">LIVE OPERATIONS · PX4 SITL</span>
      <h3>{deployment ? deployment.scenario_title : 'No mission deployed'}</h3>
      <small>{deployment
        ? `${deploymentVersionLabel(deployment)} · ${legacy ? 'legacy (schema 1), no generated routes' : `schema 2 · ${routes} generated route${routes === 1 ? '' : 's'} on the map`} · hash ${deployment.scenario_version_hash.slice(0, 12)}`
        : 'Use Deploy to Mission Control in Scenario Lab to load a validated mission here.'}</small>
      {!deployment && scenarioLab}
    </div>
    <div className="mc-header-status">
      <span className="mc-status-tag">{deployment ? 'DEPLOYED TO MISSION CONTROL' : 'NOT DEPLOYED'}</span>
      {deployment && <small>{deployment.execution_status.replace(/_/g, ' ').toLowerCase()} · not executing on PX4</small>}
    </div>
    <ConnectionChips chips={chips}/>
    {deployment && legacy && <p className="mc-header-note legacy" role="status">
      The latest deployment is a legacy (schema 1) mission saved before route planning existed. It has no generated routes, so none are drawn and it has no mission start.
      Plan and deploy a schema-2 mission to replace it. {scenarioLab}</p>}
    {deployment && !legacy && deployment.superseded && <p className="mc-header-note" role="status">
      A newer version{typeof deployment.latest_version_number === 'number' ? ` (v${deployment.latest_version_number})` : ''} of this scenario has been saved but not deployed.
      This page shows the deployed {deploymentVersionLabel(deployment).toLowerCase()}. {scenarioLab}</p>}
  </header>
}

export function AircraftCards({ rows, selectedId, onSelect, threatTiers = {} }: { rows: AircraftRow[]; selectedId: string | null; onSelect: (id: string) => void; threatTiers?: Record<string, Tier> }) {
  return <section className="panel mc-panel" aria-label="Active aircraft">
    <div className="panel-heading"><div><span className="eyebrow">ACTIVE AIRCRAFT</span><h3>Aircraft <span className="count-tag">{rows.length}</span></h3></div></div>
    {rows.length === 0 ? <div className="mc-empty"><Satellite size={18}/><span>No aircraft reported or deployed.</span><small>Only systems decoded from the configured MAVLink source, or listed in a deployed mission, appear here.</small></div>
      : <div className="mc-card-list">{rows.map(row => {
        const vehicle = row.vehicle
        const position = vehicle?.position
        const tone = batteryTone(vehicle?.battery?.remaining_percent)
        return <button key={row.id} type="button" aria-pressed={selectedId === row.id} className={`mc-aircraft-card ${selectedId === row.id ? 'selected' : ''} ${row.stale ? 'stale' : ''} ${threatTiers[row.id] && threatTiers[row.id] !== 'Low' ? `threat-${threatTiers[row.id].toLowerCase()}` : ''}`}
          onClick={() => onSelect(row.id)} aria-label={`Select aircraft ${row.id}`}>
          <span className="mc-card-head">
            <i className="mc-role-dot" style={{ background: ROLE_COLORS[row.plan?.role ?? 'none'] }} aria-hidden="true"/>
            <b>{row.id}</b>{threatTiers[row.id] && threatTiers[row.id] !== 'Low' && <small className="mc-threat-tag">THREAT {threatTiers[row.id].toUpperCase()}</small>}
            <span className={`mc-link ${vehicle ? (row.stale ? 'stale' : 'ok') : 'none'}`}>{!vehicle ? 'NO TELEMETRY' : row.stale ? (vehicle.status === 'connected' ? 'STALE' : vehicle.status.toUpperCase()) : 'LINKED'}</span>
          </span>
          <span className="mc-card-grid">
            <span>PX4 SYS<b>{row.systemId ?? NO_DATA}</b></span>
            <span>GAZEBO<b>{row.gazeboModel ?? NO_DATA}</b></span>
            <span>BATTERY<b className={`tone-${tone}`}>{num(vehicle?.battery?.remaining_percent, 0, '%')}</b></span>
            <span>ALT (rel)<b>{num(position?.relative_altitude_m, 1, ' m')}</b></span>
            <span>SPEED<b>{num(vehicle?.velocity?.ground_speed_m_s, 1, ' m/s')}</b></span>
            <span>HEADING<b>{num(position?.heading_deg, 0, '°')}</b></span>
            <span className="wide">LAT / LON<b>{position ? `${position.latitude.toFixed(5)}, ${position.longitude.toFixed(5)}` : NO_DATA}</b></span>
            <span>GPS<b>{vehicle?.gps_raw ? (vehicle.gps_raw.valid ? `fix ${vehicle.gps_raw.fix_type} · ${vehicle.gps_raw.satellites_visible ?? '—'} sats` : 'no valid fix') : NO_DATA}</b></span>
            <span>ROLE<b>{row.plan?.role ?? 'not planned'}</b></span>
            <span className="wide">TASK<b>{row.plan?.task_type?.replace(/_/g, ' ') ?? 'not planned'} · {routeSummary(row.plan)}</b></span>
          </span>
        </button>
      })}</div>}
  </section>
}

export function SystemStatus({ chips, verified, withMission, openAlerts }: { chips: StatusChip[]; verified: number | null; withMission: number; openAlerts: number }) {
  return <section className="panel mc-panel" aria-label="System status">
    <div className="panel-heading"><div><span className="eyebrow">SYSTEM STATUS</span><h3>Services</h3></div></div>
    <ul className="mc-status-list">{chips.map(chip => <li key={chip.key}><span className={`mc-dot ${chip.state}`} aria-hidden="true"/><span>{chip.label}</span><b>{chip.detail}</b></li>)}</ul>
    <dl className="mc-counts">
      <div><dt>Verified aircraft</dt><dd>{verified ?? NO_DATA}</dd></div>
      <div><dt>Aircraft with a mission</dt><dd>{withMission}</dd></div>
      <div><dt>Open alerts</dt><dd>{openAlerts}</dd></div>
    </dl>
  </section>
}

export function AlertsPanel({ alerts, acknowledged, onAcknowledge }: { alerts: Alert[]; acknowledged: Set<string>; onAcknowledge: (id: string) => void }) {
  return <section className="panel mc-panel" aria-label="Alerts">
    <div className="panel-heading"><div><span className="eyebrow">ALERTS</span><h3>Active alerts <span className="count-tag">{alerts.filter(item => !acknowledged.has(item.id)).length}</span></h3></div></div>
    {alerts.length === 0 ? <div className="mc-empty"><CheckCircle2 size={18}/><span>No alerts from backend-reported state.</span></div>
      : <ul className="mc-alert-list">{alerts.map(alert => <li key={alert.id} className={`${alert.severity} ${acknowledged.has(alert.id) ? 'acknowledged' : ''}`}>
        <AlertTriangle size={14} aria-hidden="true"/><span><b>{alert.source}</b>{alert.message}</span>
        {!acknowledged.has(alert.id) && <button type="button" onClick={() => onAcknowledge(alert.id)} title="Client-side only: hides the alert in this browser session and does not notify the backend">ACKNOWLEDGE (this browser)</button>}
      </li>)}</ul>}
  </section>
}

export function MissionStatus({ deployment, rows }: { deployment: MissionDeployment | null; rows: AircraftRow[] }) {
  return <section className="panel mc-panel" aria-label="Mission status">
    <div className="panel-heading"><div><span className="eyebrow">MISSION STATUS</span><h3>Deployed mission</h3></div></div>
    {!deployment ? <div className="mc-empty"><Circle size={18}/><span>No deployed mission.</span></div> : <>
      <ul className="mc-lifecycle" aria-label="Mission lifecycle">
        <li className="done"><CheckCircle2 size={13}/>Deployed to Mission Control</li>
        <li className="na"><Circle size={13}/>Uploaded to PX4 <small>not available yet</small></li>
        <li className="na"><Circle size={13}/>Executing <small>not available yet</small></li>
      </ul>
      <small className="mc-note">Deployment hands the validated plan to this page only. Nothing is uploaded to PX4 and no aircraft is commanded.</small>
      {isLegacyDeployment(deployment) && <small className="mc-note">This is a legacy (schema 1) deployment: no generated routes are available to draw.</small>}
      <ul className="mc-mission-rows">{rows.filter(row => row.plan).map(row => <li key={row.id}>
        <b>{row.id}</b><span>{row.plan?.role ?? 'no role'} · {row.plan?.task_type?.replace(/_/g, ' ') ?? 'no task'}</span>
        <small>{routeSummary(row.plan)}</small>
        <small>Progress: {row.vehicle?.mission && row.vehicle.mission.mission_state !== 1 ? `PX4 reports waypoint ${row.vehicle.mission.current_sequence + 1} (not linked to this plan)` : 'not started'}</small>
      </li>)}</ul>
    </>}
  </section>
}

export function FlightLog({ entries }: { entries: LogEntry[] }) {
  return <section className="panel mc-panel" aria-label="Flight log">
    <div className="panel-heading"><div><span className="eyebrow">FLIGHT LOG · SESSION LOG</span><h3>Observed events <span className="count-tag">{entries.length}</span></h3></div></div>
    <small className="mc-note">Transitions observed by this browser since the page opened (no backend event feed exists yet). It is not a flight record.</small>
    {entries.length === 0 ? <div className="mc-empty"><span>No transitions observed yet.</span></div>
      : <ol className="mc-log" reversed>{[...entries].reverse().map(entry => <li key={entry.key} className={entry.kind}><time>{new Date(entry.time).toLocaleTimeString()}</time><span>{entry.message}</span></li>)}</ol>}
  </section>
}

export function BatteryPanel({ rows }: { rows: AircraftRow[] }) {
  return <section className="panel mc-panel" aria-label="Battery status">
    <div className="panel-heading"><div><span className="eyebrow">BATTERY STATUS</span><h3>Per aircraft</h3></div></div>
    {rows.length === 0 ? <div className="mc-empty"><BatteryWarning size={18}/><span>No aircraft.</span></div> : <ul className="mc-battery-list">{rows.map(row => {
      const percent = row.vehicle?.battery?.remaining_percent
      const tone = batteryTone(percent)
      return <li key={row.id} className={`tone-${tone}`}>
        {tone === 'none' ? <BatteryWarning size={14} aria-hidden="true"/> : <Battery size={14} aria-hidden="true"/>}
        <b>{row.id}</b>
        <span className="mc-bar" role="img" aria-label={`${row.id} battery ${num(percent, 0, '%')}`}><i style={{ width: `${typeof percent === 'number' ? Math.max(0, Math.min(100, percent)) : 0}%` }}/></span>
        <span>{num(percent, 0, '%')}</span><small>{num(row.vehicle?.battery?.voltage_v, 2, ' V')}</small>
      </li>
    })}</ul>}
    <small className="mc-note">Warning below {THRESHOLDS.batteryWarnPercent}%, critical below {THRESHOLDS.batteryCriticalPercent}%.</small>
  </section>
}

export function HandoffsPanel() {
  return <section className="panel mc-panel" aria-label="Mission handoffs">
    <div className="panel-heading"><div><span className="eyebrow">MISSION HANDOFFS</span><h3>Reassignments</h3></div></div>
    <div className="mc-empty"><span>No reassignments yet.</span><small>No backend source for live PX4 reassignment records exists yet; adaptive reassignment is validated only in the offline research simulation.</small></div>
  </section>
}

export function GazeboPanel({ rows, snapshot, error, children }: { rows: AircraftRow[]; snapshot: GazeboPoseSnapshot | null; error: string; children?: React.ReactNode }) {
  return <section className="panel mc-panel" aria-label="Gazebo panel">
    <div className="panel-heading"><div><span className="eyebrow">GAZEBO SIMULATION</span><h3><Box size={15}/> Pose verification</h3></div></div>
    <p className="mc-note">The native Gazebo window runs separately on the WSL desktop; this page only receives read-only model poses.</p>
    {error && <div className="mc-empty" role="status">{error}</div>}
    <table className="mc-table" aria-label="Gazebo pose per aircraft"><thead><tr><th>Aircraft</th><th>Gazebo model</th><th>Pose verified</th><th>Pose (x, y, z m)</th><th>Last update</th></tr></thead>
      <tbody>{rows.length === 0 ? <tr><td colSpan={5}>No aircraft.</td></tr> : rows.map(row => {
        const pose = row.pose
        const verified = Boolean(pose && pose.status === 'connected')
        return <tr key={row.id}><td>{row.id}</td><td>{row.gazeboModel ?? NO_DATA}</td><td className={verified ? 'ok' : 'warn'}>{verified ? 'yes' : 'no'}</td>
          <td>{pose ? `${pose.position_m.x.toFixed(2)}, ${pose.position_m.y.toFixed(2)}, ${pose.position_m.z.toFixed(2)}` : NO_DATA}</td>
          <td>{pose ? `${pose.telemetry_age_seconds.toFixed(1)} s ago (${pose.status})` : NO_DATA}</td></tr>
      })}</tbody></table>
    <small className="mc-note">World: {snapshot?.world ?? NO_DATA} · transport {snapshot?.transport.status ?? NO_DATA}</small>
    <div className="mc-video-slot" role="note"><Layers size={16}/><span>Embedded Gazebo view</span><small>Reserved for a future embedded view. No video or screen capture is streamed or faked here.</small></div>
    {children}
  </section>
}

export function DetailDrawer({ row }: { row: AircraftRow | null }) {
  if (!row) return null
  const vehicle = row.vehicle
  const position = vehicle?.position
  const item = (label: string, value: string) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
  return <section className="mc-drawer" aria-label={`Details for ${row.id}`}>
    <div className="mc-drawer-head"><b>{row.id}</b><span>PX4 system {row.systemId ?? NO_DATA} · Gazebo {row.gazeboModel ?? NO_DATA}</span></div>
    <dl className="mc-drawer-grid">
      {item('Link', vehicle ? (row.stale ? `${vehicle.status} · STALE` : 'connected') : 'no telemetry')}
      {item('Flight mode', vehicle?.heartbeat?.flight_mode ?? NO_DATA)}
      {item('Armed', vehicle?.armed === null || vehicle?.armed === undefined ? NO_DATA : vehicle.armed ? 'armed' : 'disarmed')}
      {item('Altitude MSL / rel', `${num(position?.altitude_m, 1, ' m')} / ${num(position?.relative_altitude_m, 1, ' m')}`)}
      {item('Ground speed', num(vehicle?.velocity?.ground_speed_m_s, 2, ' m/s'))}
      {item('Battery', `${num(vehicle?.battery?.remaining_percent, 0, '%')} · ${num(vehicle?.battery?.voltage_v, 2, ' V')}`)}
      {item('Last heartbeat', vehicle?.heartbeat_age_seconds == null ? NO_DATA : `${vehicle.heartbeat_age_seconds.toFixed(1)} s ago`)}
      {item('Role / priority', row.plan ? `${row.plan.role ?? 'none'} / ${row.plan.priority}${row.plan.reassignable ? ' · reassignable' : ''}` : 'not planned')}
      {item('Task', row.plan?.task_type?.replace(/_/g, ' ') ?? 'not planned')}
      {item('Route', routeSummary(row.plan))}
      {item('Gazebo pose', row.pose ? `${row.pose.position_m.x.toFixed(2)}, ${row.pose.position_m.y.toFixed(2)}, ${row.pose.position_m.z.toFixed(2)} m` : NO_DATA)}
    </dl>
    {row.plan?.route?.events.map(event => <small key={event.waypoint_seq} className="mc-note">Simulated {event.type} planned at +{event.planned_time_s} s (logged event only; no release command).</small>)}
  </section>
}

export function MapToolbar({ layers, onLayers, basemap, onBasemap, follow, onFollow, onFit, selectedId }: {
  layers: MapLayers; onLayers: (next: MapLayers) => void; basemap: Basemap; onBasemap: (next: Basemap) => void
  follow: boolean; onFollow: (next: boolean) => void; onFit: () => void; selectedId: string | null
}) {
  const toggle = (key: keyof MapLayers, label: string) => <label key={key} className="mc-toggle"><input type="checkbox" checked={layers[key]} onChange={event => onLayers({ ...layers, [key]: event.target.checked })}/>{label}</label>
  return <div className="mc-map-toolbar" role="toolbar" aria-label="Map tools">
    <button type="button" onClick={onFit}><Maximize2 size={13}/> FIT TO MISSION</button>
    <button type="button" aria-pressed={follow} disabled={!selectedId} onClick={() => onFollow(!follow)}><LocateFixed size={13}/> FOLLOW {selectedId ?? 'SELECTED'}</button>
    <label className="mc-toggle">BASEMAP<select aria-label="Basemap" value={basemap} onChange={event => onBasemap(event.target.value as Basemap)}>
      <option value="offline">Offline</option><option value="street">Street</option><option value="satellite">Satellite</option></select></label>
    {toggle('routes', 'Routes')}{toggle('geofence', 'Geofence')}{toggle('trails', 'Trails')}{toggle('labels', 'Labels')}
    <ul className="mc-legend" aria-label="Legend">
      {(['primary', 'decoy', 'standby'] as const).map(role => <li key={role}><i style={{ background: ROLE_COLORS[role] }}/>{role} route (dashed)</li>)}
      <li><i className="geofence"/>max distance from start</li><li><i className="area"/>operating area</li><li><i className="drop"/>simulated drop point</li>
    </ul>
  </div>
}
