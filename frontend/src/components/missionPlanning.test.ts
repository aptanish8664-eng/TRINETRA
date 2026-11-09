import { describe, expect, it } from 'vitest'
import type { GeographicMissionIntent, GeographicZone } from '../services/auth'
import { parseGeographicSearchResults, validateGeographicMission, validateSurveyZone } from './missionPlanning'

const triangle: GeographicZone = {
  id: 'zone-1',
  name: 'North area',
  vertices: [
    { latitude: 12, longitude: 77 },
    { latitude: 13, longitude: 77 },
    { latitude: 12.5, longitude: 78 },
  ],
}

const mission: GeographicMissionIntent = {
  objective: 'area_survey',
  start_location: { latitude: 12.9, longitude: 77.5 },
  destination: null,
  survey_zones: [triangle],
  uav_assignments: [{
    uav_id: 'px4-7',
    role: 'area_survey',
    task: 'Survey the north area',
    zone_id: triangle.id,
  }],
  constraints: [],
  ai_recommendations: [],
}

describe('geographic mission validation', () => {
  it('accepts a WGS84 polygon and a current connected aircraft assignment', () => {
    expect(validateSurveyZone(triangle)).toEqual([])
    expect(validateGeographicMission(mission, new Set(['px4-7']))).toEqual([])
  })

  it('rejects invalid and self-intersecting survey geometry', () => {
    expect(validateSurveyZone({ ...triangle, vertices: triangle.vertices.slice(0, 2) })).toContain(
      'North area: add at least three boundary vertices.',
    )
    expect(validateSurveyZone({
      ...triangle,
      vertices: [
        { latitude: 12, longitude: 77 },
        { latitude: 13, longitude: 78 },
        { latitude: 12, longitude: 78 },
        { latitude: 13, longitude: 77 },
      ],
    })).toContain('North area: boundary edges cannot cross.')
    expect(validateSurveyZone({
      ...triangle,
      vertices: [{ latitude: 91, longitude: 77 }, ...triangle.vertices.slice(1)],
    })).toContain('North area: a vertex is outside WGS84 coordinate ranges.')
  })

  it('reports missing locations, unavailable UAVs, and duplicate assignment conflicts', () => {
    const invalid = {
      ...mission,
      start_location: null,
      objective: 'inspection' as const,
      uav_assignments: [
        mission.uav_assignments[0],
        { ...mission.uav_assignments[0], task: 'Duplicate task' },
      ],
    }
    expect(validateGeographicMission(invalid, new Set())).toEqual(expect.arrayContaining([
      'Select a valid starting location on the map.',
      'Select a valid destination for this mission objective.',
      'px4-7 has conflicting assignments.',
      'px4-7 is unavailable or has no valid current position.',
    ]))
  })
})

describe('geographic search results', () => {
  it('maps valid Photon places to WGS84 search results and ignores invalid features', () => {
    expect(parseGeographicSearchResults({
      features: [
        {
          geometry: { coordinates: [77.5946, 12.9716] },
          properties: { name: 'Bengaluru', state: 'Karnataka', country: 'India' },
        },
        { geometry: { coordinates: [181, 12] }, properties: { name: 'Invalid longitude' } },
        { geometry: { coordinates: [77, Number.NaN] }, properties: { name: 'Invalid latitude' } },
      ],
    })).toEqual([{
      id: '12.9716,77.5946:0',
      label: 'Bengaluru',
      detail: 'Karnataka, India',
      latitude: 12.9716,
      longitude: 77.5946,
    }])
  })

  it('rejects a malformed search response', () => {
    expect(() => parseGeographicSearchResults({ results: [] })).toThrow(
      'The geographic search provider returned an invalid response.',
    )
  })
})
