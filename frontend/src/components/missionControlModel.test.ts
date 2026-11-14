import { describe, expect, it } from 'vitest'
import { THRESHOLDS, batteryTone, buildMissionOverlay, circleRing, diffObservations, gazeboModelForSystemId, isTelemetryStale, systemIdForAircraftId, systemIdForGazeboModel, type Observed } from './missionControlModel'
import type { MissionPlan } from './missionPlan'
import type { Px4TelemetryVehicle } from '../types'

describe('mission control model', () => {
  it('maps TRINETRA id, PX4 system id and Gazebo model consistently both ways', () => {
    expect(gazeboModelForSystemId(1)).toBe('x500_0')
    expect(gazeboModelForSystemId(3)).toBe('x500_2')
    expect(systemIdForGazeboModel('x500_2')).toBe(3)
    expect(systemIdForAircraftId('px4-4')).toBe(4)
    expect(gazeboModelForSystemId(0)).toBeNull()
    expect(systemIdForGazeboModel('iris_0')).toBeNull()
    expect(systemIdForAircraftId('UAV-01')).toBeNull()
  })

  it('classifies battery and staleness with the configured thresholds', () => {
    expect([100, 40, 39.9, 20, 19.9, 0].map(batteryTone)).toEqual(['ok', 'ok', 'warn', 'warn', 'critical', 'critical'])
    expect(batteryTone(null)).toBe('none')
    const base = { status: 'connected', telemetry_age_seconds: THRESHOLDS.staleAfterSeconds } as Px4TelemetryVehicle
    expect(isTelemetryStale(base)).toBe(false)
    expect(isTelemetryStale({ ...base, telemetry_age_seconds: THRESHOLDS.staleAfterSeconds + 0.1 })).toBe(true)
    expect(isTelemetryStale({ ...base, status: 'disconnected' })).toBe(true)
    expect(isTelemetryStale(null)).toBe(false)
  })

  it('draws nothing without a plan and only plan geometry with one', () => {
    expect(buildMissionOverlay(null)).toEqual({ areaOutline: [], routes: [] })
    const plan = {
      operating_area: { kind: 'polygon', centre: null, radius_m: null, vertices: [{ latitude: 1, longitude: 1 }, { latitude: 1, longitude: 2 }, { latitude: 2, longitude: 2 }] },
      constraints: { max_distance_from_start_m: null, max_altitude_m: null },
      aircraft: [{
        uav_id: 'px4-1', role: 'standby', return_mode: 'land_at_destination', start_location: { latitude: 1, longitude: 1 },
        route: { waypoints: [{ seq: 0, latitude: 1, longitude: 1, altitude_m: 10, action: 'takeoff' }, { seq: 1, latitude: 1.1, longitude: 1.1, altitude_m: 10, action: 'waypoint' }, { seq: 2, latitude: 1.1, longitude: 1.1, altitude_m: 0, action: 'land' }] },
      }],
    } as unknown as MissionPlan
    const overlay = buildMissionOverlay(plan)
    expect(overlay.areaOutline).toHaveLength(3)
    expect(overlay.routes[0]).toMatchObject({ role: 'standby', color: '#d28cff', geofence: null, returnPath: [], dropPoints: [] })
    expect(overlay.routes[0].outbound).toHaveLength(3)
  })

  it('builds a closed geofence ring at the requested radius', () => {
    const ring = circleRing({ latitude: 12.97, longitude: 77.59 }, 1000, 32)
    expect(ring).toHaveLength(33)
    expect(ring[0].latitude - 12.97).toBeCloseTo(1000 / 111_320, 6)
    expect(ring[32].latitude).toBeCloseTo(ring[0].latitude, 9)
  })

  it('logs only real transitions after a baseline observation', () => {
    const base: Observed = { deploymentId: null, streamStatus: 'connecting', px4Receiver: 'awaiting_data', gazeboReceiver: null, verifiedCount: null, vehicles: {} }
    expect(diffObservations(null, base, 't0')).toEqual([])
    expect(diffObservations(base, base, 't1')).toEqual([])
    const next: Observed = { ...base, streamStatus: 'connected', px4Receiver: 'receiving', verifiedCount: 1, vehicles: { 'px4-1': 'connected' } }
    const messages = diffObservations(base, next, 't2').map(entry => entry.message)
    expect(messages).toEqual(expect.arrayContaining([
      'px4-1 telemetry appeared: connected.', 'PX4 receiver awaiting_data → receiving.', 'Telemetry stream connecting → connected.',
      'Fleet manager reports 1 verified aircraft (was unknown).',
    ]))
    expect(diffObservations(next, { ...next, vehicles: { 'px4-1': 'stale' } }, 't3').map(entry => entry.message)).toEqual(['px4-1 link connected → stale.'])
  })
})
