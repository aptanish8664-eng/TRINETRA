import { afterEach, describe, expect, it } from 'vitest'
import { EMPTY_QUICK_INPUTS, applyInputPatch, buildQuickRequest, draftFromQuick, inputsReady, limitMode, summaryLines, type QuickInputs, type QuickResult, type QuickRules } from './quickMission'
import { clearAllDrafts, clearDraft, draftKey, readDraft, writeDraft } from './draftStore'
import { EMPTY_PLAN, NO_AUTO_LIMITS, computePlanMissing, normalizePlan, type MissionPlan } from './missionPlan'

const where = { latitude: 12.97, longitude: 77.59 }
const fleet = [
  { id: 'px4-1', position: { latitude: 12.9, longitude: 77.5 } },
  { id: 'px4-2', position: { latitude: 12.8, longitude: 77.4 } },
]

const rules = { types: [{ id: 'search_spiral', where: 'centre' }, { id: 'waypoints_then_drop', where: 'points' }], max_drones: 5 } as unknown as QuickRules
const inputs = (patch: Partial<QuickInputs>): QuickInputs => ({ ...EMPTY_QUICK_INPUTS, ...patch })

describe('buildQuickRequest (the one request builder the page and the assistant share)', () => {
  it('uses only verified aircraft, never more than asked, and the first one for the aircraft position', () => {
    const one = buildQuickRequest(inputs({ type: 'search_spiral', where, source: 'map', sizeM: 300 }), fleet, rules)
    expect(one).toMatchObject({ mission_type: 'search_spiral', where_source: 'map', size_m: 300, drone_count: 1, aircraft_ids: ['px4-1'], aircraft_position: fleet[0].position })
    const two = buildQuickRequest(inputs({ type: 'search_spiral', where, source: 'aircraft', droneCount: 2, roles: ['decoy'] }), fleet, rules)
    expect(two.aircraft_ids).toEqual(['px4-1', 'px4-2'])
    expect(two.roles).toEqual(['decoy'])
    expect(two.aircraft_positions).toEqual([fleet[0].position, fleet[1].position])
    const many = buildQuickRequest(inputs({ type: 'search_spiral', where, source: 'map', droneCount: 9 }), fleet, rules)
    expect(many.aircraft_ids).toEqual(['px4-1', 'px4-2'])        // the backend refuses the shortfall with a reason; nothing is invented here
    const none = buildQuickRequest(inputs({ type: 'search_spiral', where, source: 'map' }), [], rules)
    expect(none.aircraft_ids).toEqual([])
    expect(none.aircraft_position).toBeNull()
  })

  it('sends the clicked points and the marked drop for a waypoint type, and a typed speed or altitude only when given', () => {
    const points = [where, { latitude: 12.98, longitude: 77.6 }]
    const request = buildQuickRequest(inputs({ type: 'waypoints_then_drop', points, dropIndex: 0, source: 'map' }), fleet, rules)
    expect(request).toMatchObject({ points, drop_index: 0, where: points[0] })
    expect(request.speed_mps).toBeNull()
    expect(buildQuickRequest(inputs({ type: 'search_spiral', where, source: 'map', speedMps: 10, altitudeM: 40, placeName: 'Cubbon Park' }), fleet, rules)).toMatchObject({ speed_mps: 10, altitude_m: 40, place_name: 'Cubbon Park', points: undefined })
  })

  it('knows when there is enough to ask the backend (a type and a place, or at least one point)', () => {
    expect(inputsReady(inputs({ type: 'search_spiral' }), rules)).toBe(false)
    expect(inputsReady(inputs({ type: 'search_spiral', where }), rules)).toBe(true)
    expect(inputsReady(inputs({ type: 'waypoints_then_drop', where }), rules)).toBe(false)
    expect(inputsReady(inputs({ type: 'waypoints_then_drop', points: [where] }), rules)).toBe(true)
  })

  it('converts the place between kinds when the type changes and never keeps the aircraft position for a destination type', () => {
    const typed = { ...rules, types: [...rules.types, { id: 'relief_delivery', where: 'destination' }] } as unknown as QuickRules
    expect(applyInputPatch(inputs({ type: 'search_spiral', where, source: 'map' }), { type: 'waypoints_then_drop' }, typed).points).toEqual([where])
    expect(applyInputPatch(inputs({ type: 'waypoints_then_drop', points: [where] }), { type: 'search_spiral' }, typed).where).toEqual(where)
    expect(applyInputPatch(inputs({ type: 'search_spiral', where, source: 'aircraft' }), { type: 'relief_delivery' }, typed)).toMatchObject({ where: null, source: null })
  })
})

const result = (plan: Partial<MissionPlan>): QuickResult => ({
  plan: { ...EMPTY_PLAN, ...plan } as MissionPlan, suggested_name: 'Search spiral · Cubbon Park', warnings: [], derived: {},
  summary: { mission_type: 'search_spiral', label: 'Search spiral', area: { kind: 'circle', radius_m: 300, centre: where }, start: where, drones: 1, aircraft_ids: ['px4-1'], roles: ['primary'], speed_mps: 8,
    altitude_m: 50, estimated_s: 732, length_m: 5857, waypoints: 30, track_spacing_m: 30, limits: { max_distance_from_start_m: 450, max_altitude_m: 80, max_duration_minutes: 18 }, confirmed: true, explanation: [], points: [], drop_index: null, aircraft: [] },
} as unknown as QuickResult)

describe('draftFromQuick and the summary', () => {
  it('takes the suggested name unless the user already typed one, and carries the auto flags', () => {
    const plan = { aircraft: [], required_uav_count: 1, auto_limits: { max_distance_from_start_m: true, max_altitude_m: true, max_duration_minutes: true } }
    expect(draftFromQuick(result(plan), '', false).title).toBe('Search spiral · Cubbon Park')
    expect(draftFromQuick(result(plan), 'My own name', true).title).toBe('My own name')
    expect(draftFromQuick(result(plan), '   ', true).title).toBe('Search spiral · Cubbon Park')
    const draft = draftFromQuick(result(plan), '', false)
    expect(draft.requiredCount).toBe(1)
    expect(limitMode(draft.plan, 'max_distance_from_start_m')).toBe('auto')
    expect(limitMode(normalizePlan({ ...plan, auto_limits: NO_AUTO_LIMITS }), 'max_altitude_m')).toBe('typed')
  })

  it('gives the one-screen summary with distance, time, speed, altitude and the derived limits', () => {
    const lines = summaryLines(result({}).summary).join('\n')
    for (const text of ['Search spiral', 'radius 300 m', '8 m/s', '50 m altitude', '5.86 km', '12.2 min', '30 waypoints', 'max distance 450 m', 'max altitude 80 m', 'max duration 18 min']) expect(lines).toContain(text)
  })
})

describe('plans from before the auto limits existed', () => {
  it('normalise with no auto flags (every limit stays typed) and keep working in the checklist', () => {
    const legacy = normalizePlan({ ...EMPTY_PLAN, auto_limits: undefined })
    expect(legacy.auto_limits).toEqual(NO_AUTO_LIMITS)
    const items = computePlanMissing({ title: 'x', plan: legacy, fleetVerified: true, availableVehicleIds: [] })
    expect(items).toContain('Set the maximum distance from start.')
    const auto = normalizePlan({ ...EMPTY_PLAN, auto_limits: { max_distance_from_start_m: true, max_altitude_m: true, max_duration_minutes: false } })
    expect(computePlanMissing({ title: 'x', plan: auto, fleetVerified: true, availableVehicleIds: [] }).some(item => item.startsWith('Generate the route'))).toBe(true)
  })
})

describe('unsaved draft storage', () => {
  afterEach(() => clearAllDrafts())
  it('round-trips per user and scenario, ignores damaged data, and clears on request or sign-out', () => {
    const key = draftKey('u1', 's1')
    writeDraft(key, { title: 'T', plan: normalizePlan(EMPTY_PLAN), baseVersionId: 'v1', savedAt: 1 })
    expect(readDraft(key)?.title).toBe('T')
    expect(readDraft(draftKey('u2', 's1'))).toBeNull()                       // another user never sees it
    expect(readDraft(draftKey('u1', null))).toBeNull()
    localStorage.setItem(draftKey('u1', 's2'), '{not json')
    expect(readDraft(draftKey('u1', 's2'))).toBeNull()
    writeDraft(draftKey('u1', null), { title: 'N', plan: normalizePlan(EMPTY_PLAN), baseVersionId: null, savedAt: 2 })
    clearDraft(key)
    expect(readDraft(key)).toBeNull()
    clearAllDrafts('u1')
    expect(readDraft(draftKey('u1', null))).toBeNull()
  })
})

describe('priority of the only primary aircraft', () => {
  it('starts at 1 for the first (primary) aircraft and 2 for the others', async () => {
    const { newAircraft } = await import('./missionPlan')
    expect(newAircraft('px4-1', 0)).toMatchObject({ role: 'primary', priority: 1 })
    expect(newAircraft('px4-2', 1)).toMatchObject({ role: 'standby', priority: 2 })
  })
})

describe('aircraft rows from a parsed sentence', () => {
  it('"12 UAVs, 2 decoys" makes two decoys and leaves the others on the ground; role changes win', async () => {
    const { rowsFromParse, EMPTY_PARSE_STATE } = await import('./quickMission')
    const rows = rowsFromParse({ ...EMPTY_PARSE_STATE, drone_count: 12, decoys: 2, role_changes: { 7: 'primary' } })
    expect(rows).toHaveLength(12)
    expect(rows.map(row => row.role)).toEqual([null, 'decoy', 'decoy', 'standby', 'standby', 'standby', 'primary', 'standby', 'standby', 'standby', 'standby', 'standby'])
    expect(rowsFromParse({ ...EMPTY_PARSE_STATE, drone_count: 4, standbys: 1 }).map(row => row.role)).toEqual([null, 'standby', 'decoy', 'decoy'])
    expect(rowsFromParse({ ...EMPTY_PARSE_STATE, drone_count: 100 })).toEqual([])            // nothing said about roles: every row stays default
  })
})
