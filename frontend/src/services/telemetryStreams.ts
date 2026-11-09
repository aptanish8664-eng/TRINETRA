import type {
  GazeboPoseSnapshot,
  GazeboPoseStreamSnapshot,
  Px4TelemetrySnapshot,
  Px4TelemetryStreamSnapshot,
} from '../types'

const MAX_TRAIL_POINTS = 3000

type TrailPoint = { received_at: string }

function mergeTrail<T extends TrailPoint>(previous: T[], full?: T[], delta?: T[]): T[] {
  if (Array.isArray(full)) return full.slice(-MAX_TRAIL_POINTS)
  if (!delta?.length) return previous.slice(-MAX_TRAIL_POINTS)

  const seen = new Set(previous.map(point => point.received_at))
  return [...previous, ...delta.filter(point => !seen.has(point.received_at))].slice(-MAX_TRAIL_POINTS)
}

export function mergePx4TelemetryStream(
  previous: Px4TelemetrySnapshot | null,
  incoming: Px4TelemetryStreamSnapshot,
): Px4TelemetrySnapshot {
  const previousById = new Map(previous?.vehicles.map(vehicle => [vehicle.id, vehicle]) ?? [])
  const vehicles = incoming.vehicles.map(vehicle => {
    const prior = previousById.get(vehicle.id)
    return {
      ...vehicle,
      flight_trail: mergeTrail(prior?.flight_trail ?? [], vehicle.flight_trail, vehicle.flight_trail_delta),
    }
  })
  const { stream_mode: _streamMode, ...snapshot } = incoming
  return { ...snapshot, vehicles } as Px4TelemetrySnapshot
}

export function mergeGazeboPoseStream(
  previous: GazeboPoseSnapshot | null,
  incoming: GazeboPoseStreamSnapshot,
): GazeboPoseSnapshot {
  const previousByName = new Map(previous?.models.map(model => [model.model_name, model]) ?? [])
  const models = incoming.models.map(model => {
    const prior = previousByName.get(model.model_name)
    return {
      ...model,
      flight_trail: mergeTrail(prior?.flight_trail ?? [], model.flight_trail, model.flight_trail_delta),
    }
  })
  const { stream_mode: _streamMode, ...snapshot } = incoming
  return { ...snapshot, models } as GazeboPoseSnapshot
}
