import { describe, expect, it } from 'vitest'
import { EMPTY_PLAN, newAircraft, missingAircraftInputs, type AircraftPlan, type MissionPlan } from './missionPlan'
import { answerReadOnlyQuestion, buildAssistantSnapshot, explainTopic, missingAnswer, readinessAnswer, sectionOf, type HelpContext } from './assistantHelp'
import { ATTACK_FIELD_HELP, HANDOVER_BATTERY_NOTE, HIDDEN_SCHEDULE_NOTE, PRESET_SPECS, RESPONSE_NOTE } from './attackLabTypes'

const point = { latitude: 12.97, longitude: 77.59 }
const route = { generator: 'waypoint_route', generator_version: 1, waypoints: [], events: [], length_m: 10, duration_s: 5, assumptions: '' }
const complete: AircraftPlan = {
  ...newAircraft('px4-1', 0), task_type: 'waypoint_route', parameters: { points: [point] }, start_location: point,
  altitude_m: 30, speed_mps: 5, route, confirmed: true,
}
const planWith = (aircraft: AircraftPlan[]): MissionPlan => ({ ...EMPTY_PLAN, aircraft, selected_uav_ids: aircraft.map(item => item.uav_id) })
const context = (overrides: Partial<HelpContext> = {}): HelpContext => ({
  title: 'T', plan: planWith([complete]), activeIndex: 0, section: 'B', scenarioTitle: null, versionNumber: null,
  approvalStatus: null, isOperator: false, fleetVerified: true, requiredCountInput: '1', activeFaultCount: 0,
  validationItems: [], deployBlockers: [], ...overrides,
})

describe('assistant help on the schema-2 model', () => {
  it('reports nothing missing exactly for a complete aircraft and names each missing input otherwise', () => {
    expect(missingAircraftInputs(complete)).toEqual([])
    const cases: [string, Partial<AircraftPlan>][] = [
      ['role', { role: null }], ['task_type', { task_type: null }], ['start_location', { start_location: null }],
      ['altitude_m', { altitude_m: null }], ['speed_mps', { speed_mps: null }], ['points', { parameters: {} }], ['route', { route: null }],
    ]
    for (const [key, patch] of cases) expect(missingAircraftInputs({ ...complete, ...patch }).map(item => item.key)).toEqual([key])
  })

  it('requires the payload drop-off parameters and a positive loiter for a loiter hold', () => {
    const drop = { ...complete, task_type: 'payload_dropoff' as const, parameters: {}, route: null }
    expect(missingAircraftInputs(drop).map(item => item.key)).toEqual(['drop_point', 'drop_altitude_m', 'hover_duration_s', 'payload_category'])
    const hold = { ...complete, task_type: 'loiter_hold' as const, parameters: {}, loiter_duration_s: 0 }
    expect(missingAircraftInputs(hold).map(item => item.key)).toEqual(['loiter_duration_s'])
  })

  it('only says deploy is possible when there are no blockers', () => {
    expect(readinessAnswer(context({ deployBlockers: ['Save the draft first.'] }))).toMatch(/Not ready yet[\s\S]*Save the draft first/)
    expect(readinessAnswer(context())).toMatch(/no blockers/)
    expect(readinessAnswer(context({ legacy: true, deployBlockers: [] }))).toMatch(/legacy \(schema 1\) version/)
  })

  it('explains every task type, the payload drop-off and the new fields from shared specs', () => {
    expect(explainTopic('what is a coverage survey?')).toMatch(/Parallel tracks/)
    expect(explainTopic('explain payload_dropoff')).toMatch(/payload_released event/)
    expect(explainTopic('what is expanding square search')).toMatch(/square spiral/)
    expect(explainTopic('what is track spacing?')).toMatch(/Distance between spiral tracks/)
    expect(explainTopic('explain section C')).toMatch(/Attack Lab/)
    expect(explainTopic('what is reassignable?')).toMatch(/taken over/)
  })

  it('has no weapon, strike or targeting vocabulary in any explanation', () => {
    const everything = ['payload_dropoff', 'section a', 'section b', 'section c', 'section d', 'roles', 'route', 'task types']
      .map(topic => explainTopic(`explain ${topic}`) ?? '').join(' ').toLowerCase()
    expect(everything).not.toMatch(/weapon|bomb|strike|munition|missile/)
    // "attack" is allowed only as the product name "Attack Lab" / the simulated "attack type" vocabulary
    expect(everything.replace(/attack lab|attack types?|attack presets?|attack experiments?|attack start/g, '')).not.toMatch(/attack/)
    expect(everything).not.toMatch(/section d —|scripted/)
  })

  it('returns null for text that is not a read-only question', () => {
    expect(answerReadOnlyQuestion('zzqv blorp', context())).toBeNull()
  })

  it('builds a coordinate-free snapshot', () => {
    const snapshot = JSON.stringify(buildAssistantSnapshot(context()))
    expect(snapshot).not.toContain('12.97')
    expect(JSON.parse(snapshot).aircraft[0]).toMatchObject({ index: 1, start_selected: true, task_type: 'waypoint_route', parameters: {} })
  })
})

describe('three sections: A mission, B individual UAV configuration, C review and validation', () => {
  it('maps every workflow stage to A, B or C and never to D', () => {
    expect([sectionOf('general'), sectionOf('aircraft'), sectionOf('review'), sectionOf('faults')]).toEqual(['A', 'B', 'C', 'C'])
  })

  it('explains Section C as review and validation with the single Attack Lab line, and redirects Section D', () => {
    const text = explainTopic('explain section C') ?? ''
    expect(text).toMatch(/^Section C — review and validation/)
    expect(text).toMatch(/GPS attack experiments are designed in the Attack Lab after saving\./)
    expect(text).toMatch(/reasons are listed beside it/)
    expect(text).not.toMatch(/drift|offset|magnitude|preset/i)  // no attack details in the review section
    expect(explainTopic('what is section D?')).toMatch(/There is no Section D any more: review and validation is Section C/)
    expect(missingAnswer(context({ section: 'C', validationItems: ['x'] }), null, null)).toMatch(/^Section C validation currently reports/)
    expect(answerReadOnlyQuestion('what is missing in section D', context({ validationItems: ['x'] }))?.text).toMatch(/^Section C validation/)
    expect(readinessAnswer(context())).toMatch(/You can deploy from Section C/)
  })

  it('describes presets as attack-only from the shared specs, with the operator deciding the response', () => {
    const presets = explainTopic('what does each preset do') ?? ''
    for (const item of PRESET_SPECS) expect(presets).toContain(item.label)
    expect(presets).toContain(RESPONSE_NOTE)
    expect(presets).toMatch(/Slow drift.*Rapid jump.*Strong offset.*Noise burst/s)
    expect(presets).not.toMatch(/Isolation \/ fallback|Role handover|scripted|benchmark/i)
    expect(explainTopic('what does the seed do')).toContain(ATTACK_FIELD_HELP.seed)
    expect(explainTopic('what is the handover battery')).toContain(HANDOVER_BATTERY_NOTE)
    expect(explainTopic('is the attack schedule hidden')).toContain(HIDDEN_SCHEDULE_NOTE)
    expect(explainTopic('how do I deploy')).toMatch(/Deploy to Mission Control hands the saved/)
    expect(explainTopic('why is the aircraft in zurich')).toMatch(/Move simulation to mission start/)
  })
})
