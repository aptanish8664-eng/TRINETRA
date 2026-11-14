import { describe, expect, it } from 'vitest'
import {
  EMPTY_PLAN, MISSION_OBJECTIVES, OBJECTIVE_DEFAULT_TASK, TASK_SPECS, areaPreviewVertices, computePlanDeployBlockers, computePlanMissing, enduranceIssues,
  isPlanComplete, newAircraft, type AircraftPlan, type MissionPlan,
} from './missionPlan'
import { applyAssistantOps, parseSetCommand, validateAssistantOps } from './assistantActions'

const point = { latitude: 12.97, longitude: 77.59 }
const route = { generator: 'waypoint_route', generator_version: 1, waypoints: [], events: [], length_m: 10, duration_s: 5, assumptions: '' }
const aircraft = (id: string, index: number, patch: Partial<AircraftPlan> = {}): AircraftPlan => ({
  ...newAircraft(id, index), task_type: 'waypoint_route', parameters: { points: [point] }, start_location: point,
  altitude_m: 30, speed_mps: 5, route, confirmed: true, ...patch,
})
const full = (patch: Partial<MissionPlan> = {}): MissionPlan => ({
  ...EMPTY_PLAN, objectives: ['custom_mission'], operating_area: { kind: 'circle', centre: point, radius_m: 300, vertices: [] },
  constraints: { max_distance_from_start_m: 1000, max_altitude_m: 100 }, required_uav_count: 1, selected_uav_ids: ['px4-1'],
  activated_uav_ids: ['px4-1'], aircraft: [aircraft('px4-1', 0)], workflow_stage: 'review', ...patch,
})
const ctx = (plan: MissionPlan) => ({ title: 'T', plan, fleetVerified: true, availableVehicleIds: plan.selected_uav_ids })

describe('mission plan model (client mirror of the backend)', () => {
  it('objective defaults only suggest task types, as specified', () => {
    expect(OBJECTIVE_DEFAULT_TASK).toMatchObject({
      search_rescue: 'expanding_square_search', area_survey: 'coverage_survey', disaster_assessment: 'coverage_survey',
      agricultural_survey: 'coverage_survey', mapping_survey: 'coverage_survey', perimeter_monitoring: 'perimeter_patrol',
      inspection: 'point_inspection', environmental_monitoring: 'point_inspection', target_tracking: 'waypoint_route',
      custom_mission: 'waypoint_route', relief_supply_delivery: 'payload_dropoff',
    })
    expect(MISSION_OBJECTIVES).toHaveLength(11)
    expect(TASK_SPECS.map(task => task.value)).toHaveLength(8)
  })

  it('has no weapon, strike or targeting vocabulary in objectives, tasks or labels', () => {
    const text = JSON.stringify([MISSION_OBJECTIVES, TASK_SPECS]).toLowerCase()
    expect(text).not.toMatch(/weapon|bomb|strike|munition|missile|warhead|attack/)
  })

  it('a complete plan has no blockers; each omission is reported', () => {
    expect(isPlanComplete(full())).toBe(true)
    expect(computePlanMissing(ctx(full()))).toEqual([])
    expect(computePlanMissing(ctx(full({ operating_area: null })))).toContain('Select the operating area on the map.')
    expect(computePlanMissing(ctx(full({ constraints: { max_distance_from_start_m: null, max_altitude_m: 100 } })))).toContain('Set the maximum distance from start.')
    expect(computePlanMissing(ctx(full({ aircraft: [aircraft('px4-1', 0, { role: 'decoy' })] })))).toContain('Exactly one aircraft must have the primary role.')
    expect(computePlanMissing(ctx(full({ aircraft: [aircraft('px4-1', 0, { route: null })] })))).toContain('Every selected aircraft needs a generated route and a confirmed mission.')
    expect(computePlanMissing(ctx(full({ workflow_stage: 'aircraft' })))).toContain('Complete the A–C scenario workflow and final review.')
  })

  it('deploy blockers add save, unsaved-change and approval state', () => {
    const base = { ...ctx(full()), hasSavedVersion: true, hasUnsavedChanges: false, isOperator: false, approvalStatus: null, canManage: true }
    expect(computePlanDeployBlockers(base)).toEqual([])
    expect(computePlanDeployBlockers({ ...base, hasSavedVersion: false })[0]).toMatch(/Save the draft first/)
    expect(computePlanDeployBlockers({ ...base, hasUnsavedChanges: true })[0]).toMatch(/unsaved changes/)
    expect(computePlanDeployBlockers({ ...base, isOperator: true })[0]).toMatch(/General approval/)
    expect(computePlanDeployBlockers({ ...base, isOperator: true, approvalStatus: 'APPROVED' })).toEqual([])
  })

  it('previews a circular area as a polygon around the centre', () => {
    const ring = areaPreviewVertices({ kind: 'circle', centre: point, radius_m: 500, vertices: [] }, 8)
    expect(ring).toHaveLength(8)
    expect(Math.max(...ring.map(vertex => vertex.latitude)) - point.latitude).toBeCloseTo(500 / 111_320, 6)
  })
})

describe('assistant operations on the plan', () => {
  it('parses set commands into validated operations', () => {
    const plan = full({ objectives: ['area_survey'], aircraft: [aircraft('px4-1', 0, { confirmed: false })] })
    const parse = (text: string) => validateAssistantOps(parseSetCommand(text, 0)?.candidates, plan)
    const trackingOnly = full({ aircraft: [aircraft('px4-1', 0, { confirmed: false })] })
    expect(validateAssistantOps(parseSetCommand('set task type of UAV 1 to coverage survey', 0)?.candidates, trackingOnly).rejected[0]).toMatch(/not allowed/)
    const noDelivery = validateAssistantOps([{ op: 'set_assignment_field', uav_index: 1, field: 'task_type', value: 'payload_dropoff' }], plan)
    expect(noDelivery.ops).toHaveLength(0)
    expect(parse('set altitude to 45').ops).toEqual([{ op: 'set_assignment_field', uav_index: 1, field: 'altitude_m', value: 45 }])
    expect(parse('set task type of UAV 1 to coverage survey').ops).toEqual([{ op: 'set_assignment_field', uav_index: 1, field: 'task_type', value: 'coverage_survey' }])
    expect(parse('set role of UAV 1 to decoy').ops).toHaveLength(1)
    expect(parse('set max duration to 20 minutes').ops).toEqual([{ op: 'set_max_duration', minutes: 20 }])
    expect(parse('set max altitude to 80').ops).toEqual([{ op: 'set_constraint', field: 'max_altitude_m', value: 80 }])
    expect(parse('set altitude to 900').rejected[0]).toMatch(/outside/)
    expect(parse('set role of UAV 1 to bomber').rejected[0]).toMatch(/invalid role/)
    expect(parse('set priority to 7').rejected[0]).toMatch(/outside/)
    expect(parseSetCommand('hello there', 0)).toBeNull()
  })

  it('task parameters only apply to the aircraft task type and payload category is an allowlist', () => {
    const plan = full({ aircraft: [aircraft('px4-1', 0, { confirmed: false })] })
    expect(validateAssistantOps([{ op: 'set_assignment_field', uav_index: 1, field: 'drop_altitude_m', value: 8 }], plan).rejected[0]).toMatch(/choose the task type first/)
    const drop = full({ aircraft: [aircraft('px4-1', 0, { task_type: 'payload_dropoff', confirmed: false, parameters: {} })] })
    expect(validateAssistantOps([{ op: 'set_assignment_field', uav_index: 1, field: 'drop_altitude_m', value: 8 }], drop).ops).toHaveLength(1)
    expect(validateAssistantOps([{ op: 'set_assignment_field', uav_index: 1, field: 'payload_category', value: 'medical_kit' }], drop).ops).toHaveLength(1)
    expect(validateAssistantOps([{ op: 'set_assignment_field', uav_index: 1, field: 'payload_category', value: 'explosive' }], drop).ops).toHaveLength(0)
    expect(validateAssistantOps([{ op: 'set_assignment_field', uav_index: 1, field: 'drop_point', value: '1,2' }], drop).ops).toHaveLength(0)
  })

  it('sequential configuration is preserved and applying edits clears routes and confirmation like a manual edit', () => {
    const plan = full({ required_uav_count: 2, selected_uav_ids: ['px4-1', 'px4-2'], activated_uav_ids: ['px4-1', 'px4-2'],
      aircraft: [aircraft('px4-1', 0, { confirmed: false }), aircraft('px4-2', 1, { confirmed: false })] })
    expect(validateAssistantOps([{ op: 'set_assignment_field', uav_index: 2, field: 'altitude_m', value: 20 }], plan).rejected[0]).toMatch(/must be confirmed first/)
    const ok = full({ aircraft: [aircraft('px4-1', 0)] })
    const applied = applyAssistantOps(ok, [{ op: 'set_assignment_field', uav_index: 1, field: 'altitude_m', value: 20 }])
    expect(applied.patch.aircraft![0]).toMatchObject({ altitude_m: 20, route: null, confirmed: false })
    const task = applyAssistantOps(ok, [{ op: 'set_assignment_field', uav_index: 1, field: 'task_type', value: 'coverage_survey' }])
    expect(task.patch.aircraft![0].parameters).toEqual({})
    const role = applyAssistantOps(ok, [{ op: 'set_assignment_field', uav_index: 1, field: 'role', value: 'decoy' }])
    expect(role.patch.aircraft![0]).toMatchObject({ role: 'decoy', confirmed: false, route: ok.aircraft[0].route })
    expect(applyAssistantOps(ok, [{ op: 'set_required_uav_count', count: 2 }]).fleetCount).toBe(2)
  })
})

describe('aircraft endurance (mirror of the backend limit)', () => {
  const limit = { duration_s: 1200, length_m: 15000 }
  it('uses the backend wording and numbers for a long route and is silent for a short one', () => {
    expect(enduranceIssues({ duration_s: 224 * 60, length_m: 255_000 }, limit)).toEqual([
      'Route takes about 224 min; the aircraft limit is 20 min. Reduce the area, widen track spacing, or raise speed.',
      'Route is about 255.0 km long; the aircraft limit is 15 km. Reduce the area, widen track spacing, or raise speed.',
    ])
    expect(enduranceIssues({ duration_s: 600, length_m: 3000 }, limit)).toEqual([])
    expect(enduranceIssues({ duration_s: 1200, length_m: 15000 }, limit)).toEqual([])
    expect(enduranceIssues(null, limit)).toEqual([])
    expect(enduranceIssues({ duration_s: 99999, length_m: 1 }, null)).toEqual([])
  })
})
