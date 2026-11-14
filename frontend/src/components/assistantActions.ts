import {
  AIRCRAFT_ROLES, MISSION_OBJECTIVES, allowedTaskTypes, PAYLOAD_CATEGORIES, RETURN_MODES, TASK_SPECS, taskSpec,
  MISSION_DURATION_RANGE_MINUTES, UAV_COUNT_RANGE,
  type AircraftPlan, type MissionPlan, type ObjectiveValue, type TaskParameters,
} from './missionPlan'

/**
 * Allowlisted assistant operations over the schema-2 plan. They map one-to-one onto existing form controls and
 * are only ever applied after the operator presses CONFIRM, through the same controlled state as manual edits.
 * The model can never propose a location, a route, a deployment, an approval or any vehicle command.
 */
export type AssistantOp =
  | { op: 'set_objective'; objective: ObjectiveValue }
  | { op: 'set_max_duration'; minutes: number }
  | { op: 'set_mission_text'; field: 'description' | 'notes'; value: string }
  | { op: 'set_required_uav_count'; count: number }
  | { op: 'set_constraint'; field: 'max_distance_from_start_m' | 'max_altitude_m'; value: number }
  | { op: 'set_assignment_field'; uav_index: number; field: string; value: string | number }

export interface FieldDef {
  key: string
  label: string
  type: 'enum' | 'int' | 'number'
  min?: number; max?: number
  choices?: { value: string; label: string }[]
  /** True for per-task parameters, which only exist for the aircraft's current task type. */
  param: boolean
  aliases: string[]
}

const alias = (label: string) => label.toLowerCase().replace(/\(.*?\)/g, '').replace(/\s+/g, ' ').trim()

/** One table for the assistant (and, via TASK_SPECS, the form): aircraft-level fields plus every task parameter. */
export const ASSIGNMENT_FIELDS: FieldDef[] = (() => {
  const fields: FieldDef[] = [
    { key: 'role', label: 'role', type: 'enum', choices: AIRCRAFT_ROLES.map(({ value, label }) => ({ value, label })), param: false, aliases: ['role'] },
    { key: 'priority', label: 'priority', type: 'int', min: 1, max: 3, param: false, aliases: ['priority'] },
    { key: 'task_type', label: 'task type', type: 'enum', choices: TASK_SPECS.map(({ value, label }) => ({ value, label })), param: false, aliases: ['task type', 'task'] },
    { key: 'return_mode', label: 'return mode', type: 'enum', choices: RETURN_MODES, param: false, aliases: ['return mode', 'return'] },
    { key: 'altitude_m', label: 'altitude (m)', type: 'number', min: 0.1, max: 500, param: false, aliases: ['altitude', 'cruise altitude'] },
    { key: 'speed_mps', label: 'speed (m/s)', type: 'number', min: 0.1, max: 50, param: false, aliases: ['speed', 'speed limit'] },
    { key: 'loiter_duration_s', label: 'loiter duration (s)', type: 'number', min: 0, max: 3600, param: false, aliases: ['loiter', 'loiter duration'] },
  ]
  const seen = new Set<string>()
  for (const spec of TASK_SPECS) {
    for (const param of spec.params) {
      if (seen.has(param.key) || (param.kind !== 'number' && param.kind !== 'select')) continue
      seen.add(param.key)
      fields.push(param.kind === 'select'
        ? { key: param.key, label: alias(param.label), type: 'enum', choices: PAYLOAD_CATEGORIES, param: true, aliases: [alias(param.label)] }
        : { key: param.key, label: alias(param.label), type: 'number', min: param.min, max: param.max, param: true, aliases: [alias(param.label)] })
    }
  }
  return fields
})()
export const fieldDef = (key: string): FieldDef | undefined => ASSIGNMENT_FIELDS.find(item => item.key === key)

export interface ValidatedOps { ops: AssistantOp[]; rejected: string[]; summary: string[] }

const CONSTRAINT_LABEL = { max_distance_from_start_m: 'maximum distance from start (m)', max_altitude_m: 'maximum altitude (m)' }
const CONSTRAINT_LIMIT = { max_distance_from_start_m: 100000, max_altitude_m: 500 }
const TEXT_LIMIT = { description: 500, notes: 500 }

/** Validates untrusted operations (typed command or model output) against the live plan; unknown or invalid ones are dropped. */
export function validateAssistantOps(candidates: unknown, plan: MissionPlan): ValidatedOps {
  const ops: AssistantOp[] = []
  const rejected: string[] = []
  const summary: string[] = []
  for (const candidate of (Array.isArray(candidates) ? candidates.slice(0, 8) : [])) {
    const item = (candidate && typeof candidate === 'object' ? candidate : {}) as Record<string, unknown>
    switch (item.op) {
      case 'set_objective': {
        const objective = MISSION_OBJECTIVES.find(option => option.value === item.objective)
        if (!objective) { rejected.push('an unknown mission objective'); break }
        ops.push({ op: 'set_objective', objective: objective.value })
        summary.push(`Set the mission objective to ${objective.title}`)
        break
      }
      case 'set_max_duration': {
        const minutes = Number(item.minutes)
        if (!Number.isInteger(minutes) || minutes < MISSION_DURATION_RANGE_MINUTES.min || minutes > MISSION_DURATION_RANGE_MINUTES.max) {
          rejected.push(`a max duration outside ${MISSION_DURATION_RANGE_MINUTES.min}–${MISSION_DURATION_RANGE_MINUTES.max} minutes`); break
        }
        ops.push({ op: 'set_max_duration', minutes })
        summary.push(`Set the maximum duration to ${minutes} minutes`)
        break
      }
      case 'set_mission_text': {
        const field = item.field === 'description' || item.field === 'notes' ? item.field : null
        const value = typeof item.value === 'string' ? item.value.trim() : ''
        if (!field || !value || value.length > TEXT_LIMIT[field]) { rejected.push('an invalid mission text change'); break }
        ops.push({ op: 'set_mission_text', field, value })
        summary.push(`Set the mission ${field}`)
        break
      }
      case 'set_required_uav_count': {
        const count = Number(item.count)
        if (!Number.isInteger(count) || count < UAV_COUNT_RANGE.min || count > UAV_COUNT_RANGE.max) { rejected.push(`a UAV count outside ${UAV_COUNT_RANGE.min}–${UAV_COUNT_RANGE.max}`); break }
        ops.push({ op: 'set_required_uav_count', count })
        summary.push(`Request ${count} UAV(s) and run the verified fleet activation`)
        break
      }
      case 'set_constraint': {
        const field = item.field === 'max_distance_from_start_m' || item.field === 'max_altitude_m' ? item.field : null
        const value = typeof item.value === 'number' ? item.value : Number.NaN
        if (!field || !Number.isFinite(value) || value <= 0 || value > CONSTRAINT_LIMIT[field]) { rejected.push('an invalid mission constraint'); break }
        ops.push({ op: 'set_constraint', field, value })
        summary.push(`Set the ${CONSTRAINT_LABEL[field]} to ${value}`)
        break
      }
      case 'set_assignment_field': {
        const index = Number(item.uav_index)
        const field = typeof item.field === 'string' ? fieldDef(item.field) : undefined
        const target: AircraftPlan | undefined = Number.isInteger(index) ? plan.aircraft[index - 1] : undefined
        if (!target) { rejected.push(`UAV ${String(item.uav_index)}, which is not in the activated fleet`); break }
        if (index > 1 && !plan.aircraft[index - 2]?.confirmed) {
          rejected.push(`changes to ${target.uav_id}, because ${plan.aircraft[index - 2].uav_id} must be confirmed first`); break
        }
        if (!field) { rejected.push('a field that the assistant may not change (locations and routes are chosen on the map)'); break }
        if (field.param && !taskSpec(target.task_type)?.params.some(param => param.key === field.key)) {
          rejected.push(`${field.label}, which does not apply to ${target.task_type ?? 'an aircraft with no task type'}; choose the task type first`); break
        }
        if (field.type === 'enum') {
          const value = typeof item.value === 'string' ? item.value : ''
          if (field.key === 'task_type' && !allowedTaskTypes(plan.objectives).includes(value as AircraftPlan['task_type'] & string)) {
            rejected.push(`the task type ${value}, which is not allowed for the selected objectives`); break
          }
          if (!field.choices?.some(choice => choice.value === value)) { rejected.push(`an invalid ${field.label}`); break }
          ops.push({ op: 'set_assignment_field', uav_index: index, field: field.key, value })
          summary.push(`Set ${target.uav_id} ${field.label} to ${field.choices.find(choice => choice.value === value)?.label ?? value}`)
        } else {
          const value = typeof item.value === 'number' ? item.value : Number.NaN
          const whole = field.type === 'int'
          if (!Number.isFinite(value) || (whole && !Number.isInteger(value)) || value < (field.min ?? -Infinity) || value > (field.max ?? Infinity)) {
            rejected.push(`a ${field.label} outside ${field.min}–${field.max}`); break
          }
          ops.push({ op: 'set_assignment_field', uav_index: index, field: field.key, value })
          summary.push(`Set ${target.uav_id} ${field.label} to ${value}`)
        }
        break
      }
      default:
        rejected.push('an unsupported action')
    }
  }
  return { ops, rejected, summary }
}

export interface AppliedOps { patch: Partial<MissionPlan>; fleetCount: number | null; touchedAircraft: number | null }

/** Computes the same state a manual edit would produce: changed aircraft lose their route (when inputs change) and confirmation. */
export function applyAssistantOps(plan: MissionPlan, ops: AssistantOp[]): AppliedOps {
  const patch: Partial<MissionPlan> = {}
  const constraints = { ...plan.constraints }
  let constraintsChanged = false
  let fleetCount: number | null = null
  let touchedAircraft: number | null = null
  let aircraft = plan.aircraft.map(item => ({ ...item, parameters: { ...item.parameters } }))
  for (const op of ops) {
    if (op.op === 'set_objective') { patch.objectives = [op.objective]; aircraft = aircraft.map(item => ({ ...item, confirmed: false })) }
    else if (op.op === 'set_max_duration') { patch.max_duration_minutes = op.minutes; aircraft = aircraft.map(item => ({ ...item, confirmed: false })) }
    else if (op.op === 'set_mission_text') patch[op.field] = op.value
    else if (op.op === 'set_constraint') { constraints[op.field] = op.value; constraintsChanged = true; aircraft = aircraft.map(item => ({ ...item, confirmed: false })) }
    else if (op.op === 'set_required_uav_count') fleetCount = op.count
    else {
      const target = aircraft[op.uav_index - 1]
      if (!target) continue
      touchedAircraft = op.uav_index - 1
      const def = fieldDef(op.field)
      if (def?.param) target.parameters = { ...target.parameters, [op.field]: op.value } as TaskParameters
      else if (op.field === 'task_type') { target.task_type = op.value as AircraftPlan['task_type']; target.parameters = {} }
      else (target as unknown as Record<string, unknown>)[op.field] = op.value
      target.confirmed = false
      if (op.field !== 'role' && op.field !== 'priority') target.route = null
    }
  }
  if (constraintsChanged) patch.constraints = constraints
  if (touchedAircraft !== null || ops.some(op => ['set_objective', 'set_max_duration', 'set_constraint'].includes(op.op))) patch.aircraft = aircraft
  return { patch, fleetCount, touchedAircraft }
}

const norm = (text: string) => text.toLowerCase().replace(/[_-]/g, ' ').replace(/\s+/g, ' ').trim()

/** Parses a typed "set <field> [of UAV n] to <value>" command into candidate operations (validated afterwards). */
export function parseSetCommand(text: string, activeIndex: number): { candidates: unknown[]; field: string } | null {
  const planLevel = text.match(/\bset\s+(?:the\s+)?(max(?:imum)?\s+duration|max(?:imum)?\s+altitude|max(?:imum)?\s+distance(?:\s+from\s+start)?)\s+to\s+(-?\d+(?:\.\d+)?)/i)
  if (planLevel) {
    const phrase = planLevel[1].toLowerCase()
    const value = Number(planLevel[2])
    if (phrase.includes('duration')) return { candidates: [{ op: 'set_max_duration', minutes: value }], field: 'max duration' }
    return { candidates: [{ op: 'set_constraint', field: phrase.includes('altitude') ? 'max_altitude_m' : 'max_distance_from_start_m', value }], field: phrase }
  }
  const generic = text.match(/\bset\s+(?:the\s+)?([a-z][a-z _-]*?)\s+(?:of\s+(?:uav|aircraft)[ -]?(\d{1,2})\s+)?to\s+(.+?)[.\s]*$/i)
  if (!generic) return null
  const phrase = norm(generic[1])
  const def = ASSIGNMENT_FIELDS.filter(item => item.aliases.includes(phrase)).sort((a, b) => b.aliases[0].length - a.aliases[0].length)[0]
  if (!def) return null
  const uavIndex = generic[2] ? Number(generic[2]) : activeIndex + 1
  const raw = generic[3].trim()
  let value: string | number | undefined
  if (def.type === 'enum') {
    const wanted = norm(raw)
    value = def.choices?.find(choice => norm(choice.value) === wanted || norm(choice.label) === wanted || norm(choice.label).startsWith(wanted) && wanted.length > 2)?.value
    if (value === undefined) value = raw
  } else {
    const number = raw.match(/^-?\d+(?:\.\d+)?/)
    value = number ? Number(number[0]) : Number.NaN
  }
  return { candidates: [{ op: 'set_assignment_field', uav_index: uavIndex, field: def.key, value }], field: def.label }
}
