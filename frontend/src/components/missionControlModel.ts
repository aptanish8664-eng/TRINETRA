/**
 * Pure derivations for the Mission Control dashboard. Everything here is computed from backend-reported state
 * (telemetry, health, fleet status, the deployed schema-2 plan). Nothing is invented: missing data stays null.
 */
import type { GazeboPoseModel, Px4TelemetryVehicle } from '../types'
import type { MissionDeployment } from '../services/auth'
import { areaPreviewVertices, type AircraftPlan, type AircraftRole, type GeoPoint, type MissionPlan } from './missionPlan'

export const THRESHOLDS = {
  /** Telemetry older than this is shown as STALE. */
  staleAfterSeconds: 5,
  batteryWarnPercent: 40,
  batteryCriticalPercent: 20,
  logLimit: 100,
  trailLimit: 200,
} as const

export type Tone = 'ok' | 'warn' | 'critical' | 'none'

export function batteryTone(percent: number | null | undefined): Tone {
  if (typeof percent !== 'number' || !Number.isFinite(percent)) return 'none'
  if (percent < THRESHOLDS.batteryCriticalPercent) return 'critical'
  if (percent < THRESHOLDS.batteryWarnPercent) return 'warn'
  return 'ok'
}

// PX4 SITL instance 0 uses MAVLink system ID 1; Gazebo names that same instance x500_0.
export function gazeboModelForSystemId(systemId: number): string | null {
  return Number.isInteger(systemId) && systemId >= 1 && systemId <= 255 ? `x500_${systemId - 1}` : null
}

export function systemIdForGazeboModel(modelName: string): number | null {
  const match = /^x500_(\d{1,3})$/.exec(modelName)
  if (!match) return null
  const index = Number(match[1])
  return index <= 254 ? index + 1 : null
}

export function systemIdForAircraftId(uavId: string): number | null {
  const match = /^px4-([1-9]\d{0,2})$/.exec(uavId)
  return match ? Number(match[1]) : null
}

export function isTelemetryStale(vehicle: Px4TelemetryVehicle | null | undefined, threshold: number = THRESHOLDS.staleAfterSeconds): boolean {
  if (!vehicle) return false
  if (vehicle.status !== 'connected') return true
  return typeof vehicle.telemetry_age_seconds === 'number' && vehicle.telemetry_age_seconds > threshold
}

export interface AircraftRow {
  /** TRINETRA aircraft id (px4-N), equal to the PX4 system id N and Gazebo model x500_(N-1). */
  id: string
  systemId: number | null
  gazeboModel: string | null
  vehicle: Px4TelemetryVehicle | null
  plan: AircraftPlan | null
  pose: GazeboPoseModel | null
  stale: boolean
}

/** One row per aircraft that is either planned in the deployment or reporting telemetry, with a consistent identity mapping. */
export function buildAircraftRows(
  vehicles: Px4TelemetryVehicle[], plan: MissionPlan | null, poses: GazeboPoseModel[],
): AircraftRow[] {
  const ids = new Set<string>([...vehicles.map(item => item.id), ...(plan?.aircraft.map(item => item.uav_id) ?? [])])
  const rows = [...ids].map<AircraftRow>(id => {
    const vehicle = vehicles.find(item => item.id === id) ?? null
    const systemId = vehicle?.system_id ?? systemIdForAircraftId(id)
    const gazeboModel = systemId === null ? null : gazeboModelForSystemId(systemId)
    return {
      id, systemId, gazeboModel, vehicle,
      plan: plan?.aircraft.find(item => item.uav_id === id) ?? null,
      pose: gazeboModel ? poses.find(item => item.model_name === gazeboModel) ?? null : null,
      stale: isTelemetryStale(vehicle),
    }
  })
  return rows.sort((a, b) => (a.systemId ?? 999) - (b.systemId ?? 999))
}

export const ROLE_COLORS: Record<AircraftRole | 'none', string> = {
  primary: '#55d9ff', decoy: '#f5b94c', standby: '#d28cff', none: '#91a4b9',
}

export interface RouteOverlay {
  uavId: string
  role: AircraftRole | null
  color: string
  home: GeoPoint | null
  waypoints: { seq: number; latitude: number; longitude: number; altitude_m: number; action: string }[]
  outbound: GeoPoint[]
  returnPath: GeoPoint[]
  dropPoints: { seq: number; latitude: number; longitude: number }[]
  geofence: { centre: GeoPoint; radius_m: number } | null
}
export interface MissionOverlay { areaOutline: GeoPoint[]; routes: RouteOverlay[] }

const NEAR_HOME_DEG = 1e-5

/** Geometry that exists in the deployed schema-2 plan only: area outline, routes, home, drop points and the distance limit. */
export function buildMissionOverlay(plan: MissionPlan | null | undefined): MissionOverlay {
  if (!plan) return { areaOutline: [], routes: [] }
  const areaOutline = areaPreviewVertices(plan.operating_area, 64)
  const limit = plan.constraints.max_distance_from_start_m
  const routes = plan.aircraft.map<RouteOverlay>(aircraft => {
    const waypoints = (aircraft.route?.waypoints ?? []).map(item => ({
      seq: item.seq, latitude: item.latitude, longitude: item.longitude, altitude_m: item.altitude_m, action: item.action,
    }))
    const home = aircraft.start_location ?? (waypoints[0] ? { latitude: waypoints[0].latitude, longitude: waypoints[0].longitude } : null)
    let splitAt = waypoints.length
    if (home && aircraft.return_mode === 'return_to_start') {
      let index = waypoints.length
      while (index > 1 && Math.abs(waypoints[index - 1].latitude - home.latitude) < NEAR_HOME_DEG && Math.abs(waypoints[index - 1].longitude - home.longitude) < NEAR_HOME_DEG) index -= 1
      splitAt = index
    }
    const asPoint = (item: { latitude: number; longitude: number }) => ({ latitude: item.latitude, longitude: item.longitude })
    return {
      uavId: aircraft.uav_id, role: aircraft.role, color: ROLE_COLORS[aircraft.role ?? 'none'], home,
      waypoints,
      outbound: waypoints.slice(0, splitAt).map(asPoint),
      returnPath: splitAt < waypoints.length ? waypoints.slice(Math.max(0, splitAt - 1)).map(asPoint) : [],
      dropPoints: waypoints.filter(item => item.action === 'payload_release_point').map(item => ({ seq: item.seq, latitude: item.latitude, longitude: item.longitude })),
      geofence: home && limit ? { centre: home, radius_m: limit } : null,
    }
  })
  return { areaOutline, routes }
}

export function circleRing(centre: GeoPoint, radiusM: number, segments = 64): GeoPoint[] {
  const metresPerDegree = 111_320
  const cosLat = Math.max(0.01, Math.cos((centre.latitude * Math.PI) / 180))
  return Array.from({ length: segments + 1 }, (_, index) => {
    const angle = (2 * Math.PI * index) / segments
    return {
      latitude: centre.latitude + (radiusM * Math.cos(angle)) / metresPerDegree,
      longitude: centre.longitude + (radiusM * Math.sin(angle)) / (metresPerDegree * cosLat),
    }
  })
}

export function routeSummary(aircraft: AircraftPlan | null | undefined): string {
  if (!aircraft) return 'no mission plan'
  if (!aircraft.route) return 'no generated route'
  return `${aircraft.route.waypoints.length} waypoints · ${(aircraft.route.length_m / 1000).toFixed(2)} km · about ${(aircraft.route.duration_s / 60).toFixed(1)} min`
}

// --- alerts -------------------------------------------------------------------------------------------------

export type HealthPayload = {
  status: string
  components?: Record<string, { status?: string; detail?: string; [key: string]: unknown } | undefined>
}
export interface FleetPayload {
  manager_error: string | null
  bridges?: { telemetry?: { status: string }; pose?: { status: string } } | null
  gazebo_server?: string | null
  verified_count: number
}
export interface Alert { id: string; severity: 'critical' | 'warning' | 'info'; source: string; message: string }

export interface AlertInputs {
  rows: AircraftRow[]
  health: HealthPayload | null
  healthError: boolean
  fleet: FleetPayload | null
  fleetError: boolean
  streamStatus: string
  hasDeployment: boolean
  /** Alerts raised elsewhere from real observed transitions (for example threat tier changes). */
  extra?: Alert[]
}

/** Alerts come only from observed backend state; a missing source produces no alert rather than a guess. */
export function deriveAlerts({ rows, health, healthError, fleet, fleetError, streamStatus, hasDeployment, extra = [] }: AlertInputs): Alert[] {
  const alerts: Alert[] = [...extra]
  if (healthError) alerts.push({ id: 'backend:unreachable', severity: 'critical', source: 'backend', message: 'The backend health endpoint could not be reached.' })
  if (health && health.status !== 'ready') alerts.push({ id: 'backend:degraded', severity: 'warning', source: 'backend', message: `Backend reports ${health.status}.` })
  const components = health?.components ?? {}
  for (const [key, label] of [['px4', 'PX4 receiver'], ['gazebo', 'Gazebo pose receiver']] as const) {
    const status = components[key]?.status
    if (status === 'unavailable' || status === 'stale' || status === 'disconnected') {
      alerts.push({ id: `health:${key}`, severity: 'warning', source: label, message: `${label} is ${status}.` })
    }
  }
  const startup = components.default_sitl_startup
  if (startup?.status === 'FAILED') alerts.push({ id: 'health:startup', severity: 'critical', source: 'fleet startup', message: explainSitlStartup('FAILED', startup.detail) })
  else if (startup?.status === 'DEGRADED') alerts.push({ id: 'health:startup', severity: 'warning', source: 'fleet startup', message: explainSitlStartup('DEGRADED', startup.detail) })
  if (fleetError) alerts.push({ id: 'fleet:unreachable', severity: 'warning', source: 'fleet manager', message: 'Fleet status could not be read.' })
  if (fleet?.manager_error) alerts.push({ id: 'fleet:manager', severity: 'warning', source: 'fleet manager', message: `WSL fleet manager: ${fleet.manager_error}` })
  for (const [key, label] of [['telemetry', 'PX4 telemetry bridge'], ['pose', 'Gazebo pose bridge']] as const) {
    if (fleet?.bridges?.[key]?.status === 'not_running') alerts.push({ id: `bridge:${key}`, severity: 'warning', source: label, message: `${label} is not running.` })
  }
  if (streamStatus === 'offline') alerts.push({ id: 'stream:offline', severity: 'warning', source: 'telemetry stream', message: 'The telemetry stream is offline; the browser is retrying.' })
  for (const row of rows) {
    const vehicle = row.vehicle
    if (!vehicle) {
      if (hasDeployment && row.plan) alerts.push({ id: `missing:${row.id}`, severity: 'warning', source: row.id, message: `${row.id} is in the deployed mission but reports no telemetry.` })
      continue
    }
    if (vehicle.status !== 'connected') {
      alerts.push({ id: `link:${row.id}`, severity: 'critical', source: row.id, message: `${row.id} link ${vehicle.status}${vehicle.telemetry_age_seconds !== null ? ` · last data ${vehicle.telemetry_age_seconds.toFixed(0)} s ago` : ''}.` })
    } else if (row.stale) {
      alerts.push({ id: `stale:${row.id}`, severity: 'warning', source: row.id, message: `${row.id} telemetry is ${vehicle.telemetry_age_seconds?.toFixed(1)} s old (threshold ${THRESHOLDS.staleAfterSeconds} s).` })
    }
    const tone = batteryTone(vehicle.battery?.remaining_percent)
    if (tone === 'critical' || tone === 'warn') {
      alerts.push({ id: `battery:${row.id}`, severity: tone === 'critical' ? 'critical' : 'warning', source: row.id, message: `${row.id} battery ${vehicle.battery?.remaining_percent?.toFixed(0)}%.` })
    }
    if (vehicle.gps_raw && !vehicle.gps_raw.valid) alerts.push({ id: `gps:${row.id}`, severity: 'warning', source: row.id, message: `${row.id} reports no valid GPS fix.` })
    if (vehicle.threat_assessment && vehicle.threat_assessment.status !== 'not_available') {
      alerts.push({ id: `threat:${row.id}`, severity: 'warning', source: row.id, message: `${row.id} threat assessment: ${vehicle.threat_assessment.status}.` })
    }
  }
  return alerts
}

// --- session log ----------------------------------------------------------------------------------------------

export interface Observed {
  deploymentId: string | null
  streamStatus: string
  px4Receiver: string | null
  gazeboReceiver: string | null
  verifiedCount: number | null
  vehicles: Record<string, string>
}
export interface LogEntry { key: string; time: string; message: string; kind: 'deployment' | 'fleet' | 'link' | 'health' }

/** Real observed transitions only; the first observation is a baseline (plus the deployment, if any), never a synthetic event. */
export function diffObservations(previous: Observed | null, next: Observed, time: string): LogEntry[] {
  const entries: LogEntry[] = []
  const add = (kind: LogEntry['kind'], message: string) => entries.push({ key: `${time}:${entries.length}:${kind}`, time, message, kind })
  if (!previous) {
    if (next.deploymentId) add('deployment', `Deployment ${next.deploymentId.slice(0, 12)} loaded into Mission Control.`)
    for (const [id, status] of Object.entries(next.vehicles)) add('link', `${id} first observed: ${status}.`)
    return entries
  }
  if (next.deploymentId !== previous.deploymentId && next.deploymentId) add('deployment', `Deployment ${next.deploymentId.slice(0, 12)} loaded into Mission Control.`)
  if (next.verifiedCount !== previous.verifiedCount && next.verifiedCount !== null) add('fleet', `Fleet manager reports ${next.verifiedCount} verified aircraft (was ${previous.verifiedCount ?? 'unknown'}).`)
  for (const [id, status] of Object.entries(next.vehicles)) {
    const before = previous.vehicles[id]
    if (before === undefined) add('link', `${id} telemetry appeared: ${status}.`)
    else if (before !== status) add('link', `${id} link ${before} → ${status}.`)
  }
  for (const id of Object.keys(previous.vehicles)) if (!(id in next.vehicles)) add('link', `${id} no longer reported.`)
  for (const [label, before, after] of [['PX4 receiver', previous.px4Receiver, next.px4Receiver], ['Gazebo pose receiver', previous.gazeboReceiver, next.gazeboReceiver], ['Telemetry stream', previous.streamStatus, next.streamStatus]] as const) {
    if (before !== after && after) add('health', `${label} ${before ?? 'unknown'} → ${after}.`)
  }
  return entries
}

export function deploymentPlan(deployment: MissionDeployment | null | undefined): MissionPlan | null {
  return deployment?.intent.mission_plan ?? null
}

/** True for a deployment made from a version saved before schema 2: it has no generated routes to draw. */
export function isLegacyDeployment(deployment: MissionDeployment | null | undefined): boolean {
  if (!deployment) return false
  return deployment.legacy ?? !(deployment.intent.schema_version === 2 && deployment.intent.mission_plan)
}

/** "Mission name · version N" for the header; older records without a number fall back to the version id. */
export function deploymentVersionLabel(deployment: MissionDeployment): string {
  return typeof deployment.scenario_version_number === 'number' ? `Version ${deployment.scenario_version_number}` : `Version ${deployment.scenario_version_id.slice(0, 8)}`
}

/**
 * The "fleet startup" status in plain words. It is reported once, by the backend's own start-up attempt to start
 * one PX4/Gazebo aircraft; it is not a live reading, so it can stay "degraded" while the aircraft is fine now.
 */
export function explainSitlStartup(status: string, detail?: string): string {
  const reason = detail ? ` Reported reason: ${detail}` : ''
  if (status === 'FAILED') {
    return `Fleet startup failed: when the backend started it tried to start one PX4/Gazebo aircraft by itself, and that did not work.${reason} This describes backend start-up only. Use Activate and verify in Scenario Lab to start and check the aircraft now.`
  }
  return `Fleet startup degraded: when the backend started, its automatic start of one PX4/Gazebo aircraft did not end with a fully verified aircraft (process, telemetry and Gazebo pose).${reason} This describes backend start-up only, not the current state: if "Verified aircraft" shows the aircraft, nothing is wrong now. Otherwise use Activate and verify in Scenario Lab.`
}
