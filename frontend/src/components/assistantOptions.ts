import { ASSIGNMENT_FIELDS } from './assistantActions'
import { EXPLAINABLE_FIELDS, type HelpContext } from './assistantHelp'
import {
  AIRCRAFT_ROLES, MISSION_DURATION_RANGE_MINUTES, MISSION_OBJECTIVES, TASK_SPECS, UAV_COUNT_RANGE,
} from './missionPlan'

/**
 * Selectable assistant options. An option never talks to the model and never changes the form itself: it only
 * produces the same message a user could type, which the one deterministic handler then interprets.
 */
export type OptionInput =
  | { type: 'select'; label: string; choices: { value: string; label: string }[] }
  | { type: 'number'; label: string; min: number; max: number; step: number }
  | { type: 'field-number'; label: string; fields: { value: string; label: string; min: number; max: number; step: number }[] }

export interface AssistantOption {
  id: string
  label: string
  /** True when the option proposes a change, so it is disabled for accounts that cannot edit. */
  edits?: boolean
  /** Message submitted as if typed (no-input options). */
  message?: string
  /** Inline picker for options that need a value; `compose` turns the value into the equivalent typed message. */
  input?: OptionInput
  compose?: (value: string, extra?: string) => string
}

export function buildAssistantOptions(ctx: HelpContext): AssistantOption[] {
  const options: AssistantOption[] = [
    { id: 'ready', label: 'Is everything ready?', message: 'Is everything ready?' },
    { id: 'missing', label: "What's still missing?", message: "What's still missing?" },
    { id: 'summary', label: 'Summarize my mission', message: 'Summarize my mission' },
    { id: 'capabilities', label: 'What can you do?', message: 'What can you do?' },
  ]
  if (ctx.legacy) return options
  const aircraftNumber = ctx.plan.aircraft.length ? Math.min(ctx.activeIndex, ctx.plan.aircraft.length - 1) + 1 : null
  if (ctx.section === 'A') {
    options.push(
      { id: 'explain-a', label: 'Explain Section A', message: 'Explain Section A' },
      {
        id: 'set-objective', label: 'Set mission objective', edits: true,
        input: { type: 'select', label: 'Mission objective', choices: MISSION_OBJECTIVES.map(item => ({ value: item.title, label: item.title })) },
        compose: value => `Change the mission objective to ${value}`,
      },
      {
        id: 'set-duration', label: 'Set max duration', edits: true,
        input: { type: 'number', label: 'Max duration (minutes)', min: MISSION_DURATION_RANGE_MINUTES.min, max: MISSION_DURATION_RANGE_MINUTES.max, step: 1 },
        compose: value => `Set max duration to ${value}`,
      },
      {
        id: 'set-count', label: 'Set UAV count', edits: true,
        input: {
          type: 'select', label: 'Required UAVs',
          choices: Array.from({ length: UAV_COUNT_RANGE.max - UAV_COUNT_RANGE.min + 1 }, (_, index) => {
            const count = String(UAV_COUNT_RANGE.min + index)
            return { value: count, label: count }
          }),
        },
        compose: value => `I need ${value} UAVs`,
      },
    )
  } else if (ctx.section === 'B') {
    options.push({
      id: 'explain-field', label: 'Explain a field',
      input: { type: 'select', label: 'Field', choices: EXPLAINABLE_FIELDS.map(field => ({ value: field.keywords[0], label: field.label })) },
      compose: value => `What is ${value}?`,
    }, {
      id: 'explain-task', label: 'Explain a task type',
      input: { type: 'select', label: 'Task type', choices: TASK_SPECS.map(task => ({ value: task.value.replace(/_/g, ' '), label: task.label })) },
      compose: value => `What is ${value}?`,
    })
    if (aircraftNumber !== null) {
      const numeric = ['altitude_m', 'speed_mps', 'loiter_duration_s'].map(key => ASSIGNMENT_FIELDS.find(field => field.key === key)!)
      options.push(
        { id: 'missing-uav', label: `What's missing for UAV ${aircraftNumber}?`, message: `What's missing for UAV ${aircraftNumber}?` },
        { id: 'summary-uav', label: `Summarize UAV ${aircraftNumber}`, message: `Summarize UAV ${aircraftNumber}` },
        {
          id: 'set-number', label: 'Set altitude / speed / loiter', edits: true,
          input: {
            type: 'field-number', label: 'Value',
            fields: numeric.map(field => ({ value: field.aliases[0], label: field.label, min: field.min ?? 0, max: field.max ?? 500, step: field.key === 'loiter_duration_s' ? 1 : 0.1 })),
          },
          compose: (value, field) => `Set ${field ?? 'altitude'} of UAV ${aircraftNumber} to ${value}`,
        },
        {
          id: 'set-role', label: 'Set role', edits: true,
          input: { type: 'select', label: 'Role', choices: AIRCRAFT_ROLES.map(role => ({ value: role.value, label: role.label })) },
          compose: value => `Set role of UAV ${aircraftNumber} to ${value}`,
        },
        {
          id: 'set-task-type', label: 'Set task type', edits: true,
          input: { type: 'select', label: 'Task type to set', choices: TASK_SPECS.map(task => ({ value: task.value, label: task.label })) },
          compose: value => `Set task type of UAV ${aircraftNumber} to ${value}`,
        },
      )
    }
  } else {
    options.push(
      { id: 'explain-c', label: 'Explain Section C', message: 'Explain Section C' },
      { id: 'why-not-deploy', label: "Why can't I deploy?", message: "Why can't I deploy?" },
      { id: 'explain-approval', label: 'Explain approval', message: 'Explain approval' },
      { id: 'explain-validation', label: 'Explain validation', message: 'Explain validation' },
      { id: 'explain-attack-lab', label: 'Explain Attack Lab', message: 'Explain Attack Lab' },
      { id: 'explain-presets', label: 'What does each preset do', message: 'What does each preset do' },
    )
  }
  return options
}
