import { describe, expect, it } from 'vitest'
import { mergeGazeboPoseStream, mergePx4TelemetryStream } from './telemetryStreams'
import type {
  GazeboPoseSnapshot,
  GazeboPoseStreamSnapshot,
  Px4TelemetrySnapshot,
  Px4TelemetryStreamSnapshot,
} from '../types'

const px4Base = { source: 'px4_mavlink', generated_at: '2026-11-08T10:00:00Z', transport: {}, capabilities: {} }
const gazeboBase = { source: 'gazebo_pose_info', world: 'default', generated_at: '2026-11-08T10:00:00Z', transport: {} }

describe('compact telemetry stream trail merging', () => {
  it('appends new PX4 points once and keeps the bounded trail', () => {
    const previous = {
      ...px4Base,
      vehicles: [{ id: 'px4-1', flight_trail: [
        { latitude: 1, longitude: 2, altitude_m: 3, received_at: 'a' },
        { latitude: 2, longitude: 3, altitude_m: 4, received_at: 'b' },
      ] }],
    } as unknown as Px4TelemetrySnapshot
    const update = {
      ...px4Base,
      stream_mode: 'trail_delta_v1',
      vehicles: [{ id: 'px4-1', flight_trail_delta: [
        { latitude: 2, longitude: 3, altitude_m: 4, received_at: 'b' },
        { latitude: 3, longitude: 4, altitude_m: 5, received_at: 'c' },
      ] }],
    } as unknown as Px4TelemetryStreamSnapshot

    expect(mergePx4TelemetryStream(previous, update).vehicles[0].flight_trail.map(point => point.received_at)).toEqual(['a', 'b', 'c'])
  })

  it('replaces PX4 trail on a full reconnect snapshot and caps it at 3000 points', () => {
    const previous = { ...px4Base, vehicles: [{ id: 'px4-1', flight_trail: [] }] } as unknown as Px4TelemetrySnapshot
    const trail = Array.from({ length: 3001 }, (_, index) => ({
      latitude: index, longitude: 0, altitude_m: 0, received_at: String(index),
    }))
    const update = { ...px4Base, vehicles: [{ id: 'px4-1', flight_trail: trail }] } as unknown as Px4TelemetryStreamSnapshot

    const merged = mergePx4TelemetryStream(previous, update).vehicles[0].flight_trail
    expect(merged).toHaveLength(3000)
    expect(merged[0].received_at).toBe('1')
    expect(merged[merged.length - 1]?.received_at).toBe('3000')
  })

  it('appends authoritative Gazebo pose trail deltas', () => {
    const previous = {
      ...gazeboBase,
      models: [{ model_name: 'x500_0', flight_trail: [{ x: 0, y: 0, z: 0, received_at: 'a' }] }],
    } as unknown as GazeboPoseSnapshot
    const update = {
      ...gazeboBase,
      stream_mode: 'trail_delta_v1',
      models: [{ model_name: 'x500_0', flight_trail_delta: [{ x: 1, y: 2, z: 3, received_at: 'b' }] }],
    } as unknown as GazeboPoseStreamSnapshot

    expect(mergeGazeboPoseStream(previous, update).models[0].flight_trail.map(point => point.received_at)).toEqual(['a', 'b'])
  })
})
