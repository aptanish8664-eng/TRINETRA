import type {
  GeographicMissionIntent,
  GeographicPoint,
  GeographicZone,
} from '../services/auth'

export type GeographicSearchResult = GeographicPoint & {
  id: string
  label: string
  detail: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function parseGeographicSearchResults(payload: unknown): GeographicSearchResult[] {
  if (!isRecord(payload) || !Array.isArray(payload.features)) {
    throw new Error('The geographic search provider returned an invalid response.')
  }

  return payload.features.flatMap((feature, index) => {
    if (!isRecord(feature) || !isRecord(feature.geometry) || !Array.isArray(feature.geometry.coordinates)) return []
    const [longitude, latitude] = feature.geometry.coordinates
    if (typeof longitude !== 'number' || typeof latitude !== 'number'
      || !Number.isFinite(longitude) || !Number.isFinite(latitude)
      || longitude < -180 || longitude > 180 || latitude < -90 || latitude > 90) return []

    const properties = isRecord(feature.properties) ? feature.properties : {}
    const name = [properties.housenumber, properties.street]
      .filter((part): part is string => typeof part === 'string' && part.length > 0)
      .join(' ')
      || [properties.name, properties.city, properties.state, properties.country]
        .find((part): part is string => typeof part === 'string' && part.length > 0)
      || 'Unnamed place'
    const detail = [...new Set(
      [properties.city, properties.district, properties.state, properties.country]
        .filter((part): part is string => typeof part === 'string' && part.length > 0 && part !== name),
    )].join(', ')

    return [{
      id: `${latitude},${longitude}:${index}`,
      label: name,
      detail,
      latitude,
      longitude,
    }]
  })
}

function validPoint(point: GeographicPoint | null): boolean {
  return point !== null
    && Number.isFinite(point.latitude)
    && point.latitude >= -90 && point.latitude <= 90
    && Number.isFinite(point.longitude)
    && point.longitude >= -180 && point.longitude <= 180
}

function orientation(a: GeographicPoint, b: GeographicPoint, c: GeographicPoint): number {
  return (b.longitude - a.longitude) * (c.latitude - a.latitude)
    - (b.latitude - a.latitude) * (c.longitude - a.longitude)
}

function onSegment(a: GeographicPoint, b: GeographicPoint, c: GeographicPoint): boolean {
  return Math.min(a.longitude, c.longitude) <= b.longitude
    && b.longitude <= Math.max(a.longitude, c.longitude)
    && Math.min(a.latitude, c.latitude) <= b.latitude
    && b.latitude <= Math.max(a.latitude, c.latitude)
}

function segmentsIntersect(a: GeographicPoint, b: GeographicPoint, c: GeographicPoint, d: GeographicPoint): boolean {
  const first = orientation(a, b, c)
  const second = orientation(a, b, d)
  const third = orientation(c, d, a)
  const fourth = orientation(c, d, b)
  if (first * second < 0 && third * fourth < 0) return true
  return (first === 0 && onSegment(a, c, b))
    || (second === 0 && onSegment(a, d, b))
    || (third === 0 && onSegment(c, a, d))
    || (fourth === 0 && onSegment(c, b, d))
}

export function validateSurveyZone(zone: GeographicZone): string[] {
  const errors: string[] = []
  if (zone.vertices.length < 3) return [`${zone.name}: add at least three boundary vertices.`]
  if (zone.vertices.some(point => !validPoint(point))) return [`${zone.name}: a vertex is outside WGS84 coordinate ranges.`]
  if (zone.vertices.some((point, index) => {
    const next = zone.vertices[(index + 1) % zone.vertices.length]
    return Math.abs(next.longitude - point.longitude) > 180
  })) return [`${zone.name}: polygons crossing the antimeridian are not supported.`]
  const coordinates = zone.vertices.map(point => `${point.latitude},${point.longitude}`)
  if (new Set(coordinates).size !== coordinates.length) return [`${zone.name}: polygon vertices must be distinct.`]
  for (let first = 0; first < zone.vertices.length; first += 1) {
    const a = zone.vertices[first]
    const b = zone.vertices[(first + 1) % zone.vertices.length]
    for (let second = first + 1; second < zone.vertices.length; second += 1) {
      if (second === first || second === (first + 1) % zone.vertices.length || first === (second + 1) % zone.vertices.length) continue
      if (segmentsIntersect(a, b, zone.vertices[second], zone.vertices[(second + 1) % zone.vertices.length])) {
        return [`${zone.name}: boundary edges cannot cross.`]
      }
    }
  }
  const area = zone.vertices.reduce((sum, point, index) => {
    const next = zone.vertices[(index + 1) % zone.vertices.length]
    return sum + point.longitude * next.latitude - next.longitude * point.latitude
  }, 0)
  if (Math.abs(area) < 1e-10) return [`${zone.name}: boundary must enclose a non-zero area.`]
  return errors
}

export function validateGeographicMission(
  mission: GeographicMissionIntent,
  availableAircraftIds: ReadonlySet<string>,
): string[] {
  const issues: string[] = []
  if (!validPoint(mission.start_location)) issues.push('Select a valid starting location on the map.')
  if (mission.objective !== 'area_survey' && !validPoint(mission.destination)) {
    issues.push('Select a valid destination for this mission objective.')
  }
  if ((mission.objective === 'area_survey' || mission.objective === 'perimeter_monitoring') && mission.survey_zones.length === 0) {
    issues.push('Draw at least one survey or monitoring zone.')
  }
  mission.survey_zones.forEach(zone => issues.push(...validateSurveyZone(zone)))
  if (mission.uav_assignments.length === 0) issues.push('Assign at least one connected PX4 aircraft.')
  const assignedIds = new Set<string>()
  mission.uav_assignments.forEach(assignment => {
    if (assignedIds.has(assignment.uav_id)) issues.push(`${assignment.uav_id} has conflicting assignments.`)
    assignedIds.add(assignment.uav_id)
    if (!availableAircraftIds.has(assignment.uav_id)) issues.push(`${assignment.uav_id} is unavailable or has no valid current position.`)
    if (!assignment.task.trim()) issues.push(`${assignment.uav_id} needs a task description.`)
    if (assignment.start_location && !validPoint(assignment.start_location)) {
      issues.push(`${assignment.uav_id} has a start point outside WGS84 coordinate ranges.`)
    }
    if (assignment.destination && !validPoint(assignment.destination)) {
      issues.push(`${assignment.uav_id} has a destination outside WGS84 coordinate ranges.`)
    }
    if (assignment.zone_id && !mission.survey_zones.some(zone => zone.id === assignment.zone_id)) {
      issues.push(`${assignment.uav_id} references a survey zone that no longer exists.`)
    }
    if (new Set(assignment.zone_ids ?? []).size !== (assignment.zone_ids ?? []).length) {
      issues.push(`${assignment.uav_id} has duplicate survey zone assignments.`)
    }
    if ((assignment.zone_ids ?? []).some(zoneId => !mission.survey_zones.some(zone => zone.id === zoneId))) {
      issues.push(`${assignment.uav_id} references a survey zone that no longer exists.`)
    }
    if ((assignment.waypoints ?? []).some(point => !validPoint(point))) {
      issues.push(`${assignment.uav_id} has a waypoint outside WGS84 coordinate ranges.`)
    }
  })
  return [...new Set(issues)]
}

export function formatCoordinate(point: GeographicPoint | null): string {
  return point
    ? `${point.latitude.toFixed(6)}, ${point.longitude.toFixed(6)}`
    : 'Not selected'
}
