import { describe, expect, it } from 'vitest'
import { EMPTY_PLAN, newAircraft } from './missionPlan'
import { buildAssistantOptions } from './assistantOptions'
import type { HelpContext, SectionId } from './assistantHelp'

const context = (section: SectionId, withAircraft = false): HelpContext => ({
  title: 'T', plan: withAircraft ? { ...EMPTY_PLAN, aircraft: [newAircraft('px4-1', 0)], selected_uav_ids: ['px4-1'] } : EMPTY_PLAN,
  activeIndex: 0, section, scenarioTitle: null, versionNumber: null, approvalStatus: null, isOperator: false,
  fleetVerified: false, requiredCountInput: '', activeFaultCount: 0, validationItems: [], deployBlockers: [],
})
const labels = (section: SectionId, withAircraft = false) => buildAssistantOptions(context(section, withAircraft)).map(option => option.label)
const ALWAYS = ['Is everything ready?', "What's still missing?", 'Summarize my mission', 'What can you do?']

describe('assistant option sets', () => {
  it('Section A offers mission-level options only', () => {
    expect(labels('A')).toEqual([...ALWAYS, 'Explain Section A', 'Set mission objective', 'Set max duration', 'Set UAV count'])
  })

  it('Section B adds aircraft options for the selected aircraft', () => {
    expect(labels('B')).toEqual([...ALWAYS, 'Explain a field', 'Explain a task type'])
    expect(labels('B', true)).toEqual([...ALWAYS, 'Explain a field', 'Explain a task type', "What's missing for UAV 1?", 'Summarize UAV 1',
      'Set altitude / speed / loiter', 'Set role', 'Set task type'])
  })

  it('Section C (review and validation) offers the explanation and the deploy help', () => {
    expect(labels('C')).toEqual([...ALWAYS, 'Explain Section C', "Why can't I deploy?", 'Explain approval', 'Explain validation', 'Explain Attack Lab', 'What does each preset do'])
  })

  it('pickers take their choices from the shared specs, including the new payload task type', () => {
    const taskPicker = buildAssistantOptions(context('B', true)).find(option => option.id === 'set-task-type')!
    expect(taskPicker.input?.type === 'select' && taskPicker.input.choices.map(choice => choice.value)).toContain('payload_dropoff')
    const rolePicker = buildAssistantOptions(context('B', true)).find(option => option.id === 'set-role')!
    expect(rolePicker.input?.type === 'select' && rolePicker.input.choices.map(choice => choice.value)).toEqual(['primary', 'decoy', 'standby'])
    const objectivePicker = buildAssistantOptions(context('A')).find(option => option.id === 'set-objective')!
    expect(objectivePicker.input?.type === 'select' && objectivePicker.input.choices.map(choice => choice.value)).toContain('Relief Supply Delivery')
  })

  it('no option both sends a message and opens a picker', () => {
    for (const section of ['A', 'B', 'C'] as SectionId[]) {
      for (const option of buildAssistantOptions(context(section, true))) {
        expect(Boolean(option.message) !== Boolean(option.input)).toBe(true)
        if (option.input) expect(option.compose).toBeTypeOf('function')
      }
    }
  })

  it('legacy versions only offer read-only options', () => {
    expect(buildAssistantOptions({ ...context('B', true), legacy: true }).map(option => option.label)).toEqual(ALWAYS)
  })
})
