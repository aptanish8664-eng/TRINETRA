import { ATTACK_FIELD_HELP, ATTACK_LAB_NAME, ATTACK_TYPE_SPECS, GROUND_TRUTH_NOTE, HANDOVER_BATTERY_NOTE, HIDDEN_SCHEDULE_NOTE, PRESET_SPECS, RESPONSE_NOTE, SOFTWARE_ONLY_NOTE } from './attackLabTypes'
import {
  AIRCRAFT_FIELD_SPECS, MISSION_FIELD_SPECS, MISSION_OBJECTIVES, TASK_SPECS, missingAircraftInputs, taskSpec,
  type AircraftPlan, type MissionPlan,
} from './missionPlan'

/**
 * Deterministic, read-only Scenario Lab help. Nothing here changes form state, calls an API or invents data:
 * every answer is derived from the controlled plan state and the shared field specs in missionPlan.ts.
 */

/** A mission, B individual UAV configuration, C review and validation. */
export type SectionId = 'A' | 'B' | 'C'

export function sectionOf(stage: MissionPlan['workflow_stage']): SectionId {
  // 'faults' is a stage stored by older schema-2 plans; it maps to the review section.
  return stage === 'aircraft' ? 'B' : stage === 'faults' || stage === 'review' ? 'C' : 'A'
}

export interface HelpContext {
  title: string
  plan: MissionPlan
  activeIndex: number
  section: SectionId
  scenarioTitle: string | null
  versionNumber: number | null
  approvalStatus: string | null
  isOperator: boolean
  fleetVerified: boolean
  requiredCountInput: string
  activeFaultCount: number
  validationItems: string[]
  deployBlockers: string[]
  /** True while a legacy (schema 1) version is open: it is read-only. */
  legacy?: boolean
}

interface HelpField { key: string; label: string; help: string; example: string; command?: string; keywords: string[] }

const labelKeyword = (label: string) => label.toLowerCase().replace(/\(.*?\)/g, '').replace(/\s+/g, ' ').trim()
const keywordsFor = (key: string, label: string): string[] =>
  [...new Set([labelKeyword(label), key.replace(/_/g, ' ')])].filter(Boolean)

export const AIRCRAFT_HELP_FIELDS: HelpField[] = AIRCRAFT_FIELD_SPECS.map(spec => ({ ...spec, keywords: keywordsFor(spec.key, spec.label) }))
export const MISSION_HELP_FIELDS: HelpField[] = MISSION_FIELD_SPECS.map(spec => ({ ...spec, keywords: keywordsFor(spec.key, spec.label) }))
const PARAM_HELP_FIELDS: HelpField[] = TASK_SPECS.flatMap(task => task.params.map(param => ({
  key: param.key, label: param.label, help: `${param.help} (used by ${task.label}).`, example: param.example, keywords: keywordsFor(param.key, param.label),
})))
/** Fields the "Explain a field" option offers: one entry per distinct field, mission level first. */
export const EXPLAINABLE_FIELDS: HelpField[] = [...MISSION_HELP_FIELDS, ...AIRCRAFT_HELP_FIELDS,
  ...PARAM_HELP_FIELDS.filter((field, index, all) => all.findIndex(item => item.key === field.key) === index)]

const SECTION_HELP: Record<SectionId, string> = {
  A: 'Section A — the mission: name, objectives, the operating area picked on the map, the enforced limits (maximum distance from start and maximum altitude), an optional maximum duration, and how many UAVs are needed. Requesting a count activates and verifies disarmed PX4/Gazebo aircraft; nothing is flown.',
  B: 'Section B — one aircraft at a time, in order: role, priority, task type and its parameters, start on the map, altitude, speed, loiter and return mode. Press GENERATE / PREVIEW ROUTE: the backend builds and validates the waypoints, shows them on the map with length and time, and only then can you confirm the aircraft. Routes are software planning data and are never uploaded to PX4.',
  C: `Section C — review and validation. It shows the exact stored waypoints for each aircraft and lists everything still blocking the scenario, then lets you save an immutable version. Beneath Save, the next action is always shown: a General deploys to Mission Control; an Operator submits for review and deploys once that exact version is approved. If the action is unavailable, the reasons are listed beside it. Deploying hands the mission and its routes to Mission Control; it does not arm, take off or upload a mission. GPS attack experiments are designed in the ${ATTACK_LAB_NAME} after saving.`,
}

interface Topic { id: string; keywords: string[]; text: string }
const TASK_TOPICS: Topic[] = TASK_SPECS.map(task => ({
  id: task.value, keywords: [task.label.toLowerCase().replace(/\s*\(.*\)/, ''), task.value.replace(/_/g, ' ')],
  text: `${task.label}: ${task.summary} Parameters: ${task.params.map(param => `${param.label}${param.required ? ' (required)' : ''}`).join(', ')}.`,
}))
const TOPICS: Topic[] = [
  { id: 'section a', keywords: ['section a', 'general details'], text: SECTION_HELP.A },
  { id: 'section b', keywords: ['section b', 'aircraft configuration', 'individual mission'], text: SECTION_HELP.B },
  { id: 'section c', keywords: ['section c', 'review section', 'final review'], text: SECTION_HELP.C },
  { id: 'attack lab', keywords: ['attack lab', 'attack experiment', 'fault configuration', 'fault section', 'faults'], text: `GPS attack experiments are designed in the ${ATTACK_LAB_NAME} tab after saving, pinned to one saved version of this plan. ${SOFTWARE_ONLY_NOTE} ${RESPONSE_NOTE}` },
  { id: 'attack types', keywords: ['attack type', 'attack types', 'explain attack'], text: `${ATTACK_LAB_NAME} attack types (simulated, software-only): ${ATTACK_TYPE_SPECS.map(item => `${item.label} — ${item.summary}`).join(' ')}` },
  { id: 'attack presets', keywords: ['preset', 'presets', 'what does preset'], text: `${ATTACK_LAB_NAME} presets define the attack only: ${PRESET_SPECS.map(item => `${item.label} — ${item.does}${item.minAircraft > 1 ? ` (${item.needs})` : ''}`).join(' ')} ${RESPONSE_NOTE}` },
  { id: 'attack fields', keywords: ['seed', 'start time', 'attack start', 'attack duration', 'magnitude', 'direction'], text: `${ATTACK_LAB_NAME} fields: start — ${ATTACK_FIELD_HELP.start} Duration — ${ATTACK_FIELD_HELP.duration} Magnitude — ${ATTACK_FIELD_HELP.magnitude} Direction — ${ATTACK_FIELD_HELP.direction} Seed — ${ATTACK_FIELD_HELP.seed}` },
  { id: 'attack response', keywords: ['handover battery', 'minimum battery', 'response policy', 'scripted', 'who decides'], text: `${RESPONSE_NOTE} ${HANDOVER_BATTERY_NOTE}` },
  { id: 'ground truth', keywords: ['ground truth', 'schedule', 'labels', 'is the attack hidden'], text: `${HIDDEN_SCHEDULE_NOTE} ${GROUND_TRUTH_NOTE}` },
  { id: 'task types', keywords: ['task types', 'task type options', 'which tasks'], text: `Task types: ${TASK_SPECS.map(task => task.label).join('; ')}. Each objective only suggests a default; you always pick the task type, and only that type's parameters appear.` },
  ...TASK_TOPICS,
  { id: 'payload', keywords: ['payload', 'drop off', 'dropoff', 'relief supply', 'relief supplies', 'supply delivery'], text: 'Relief supply drop-off is a simulation task: the aircraft flies to the drop point, descends to a low hover, logs a simulated payload_released event (with a planned time and aircraft ID) and then returns or lands. No release or actuator command is sent to PX4 or Gazebo and no physical payload is modelled. The payload category comes from a fixed list: relief supplies, medical kit or flotation device.' },
  { id: 'roles', keywords: ['roles', 'primary', 'decoy', 'standby'], text: 'Roles: primary carries the mission task (exactly one unless you enable several), decoy is a simulated decoy aircraft for later threat research, standby is a reserve that may be reassigned. Priority (1-3) and the reassignable flag are carried to Mission Control and later reassignment logic.' },
  { id: 'route', keywords: ['generate route', 'preview route', 'route preview', 'generated route', 'waypoints', 'waypoint'], text: 'The route is generated by the backend from your inputs in a local ENU frame around the start position, then validated: every waypoint must be within the maximum distance and altitude, the speed and altitude must be within vehicle limits, the waypoint count within the cap, and the estimated duration within the maximum if set. The exact waypoints are stored in the version. The local flat-earth approximation is only suitable for areas of a few kilometres.' },
  { id: 'operating area', keywords: ['operating area', 'area'], text: 'The operating area is a circle (centre and radius) or polygon picked on the map. Search datums, inspection points and the drop point must lie inside it, and a coverage survey fills it. Changing the area clears generated routes.' },
  { id: 'limits', keywords: ['constraints', 'limits', 'max distance', 'max altitude'], text: 'The two numeric constraints are enforced: every generated waypoint must be within the maximum distance from the aircraft start and below the maximum altitude. Vehicle limits (speed, altitude) and a start-to-area distance limit apply too.' },
  { id: 'gps drift', keywords: ['gps drift', 'drift'], text: `GPS drift means the reported position slowly walks away from the true one. It is configured as an attack type in the ${ATTACK_LAB_NAME} tab and is never sent to PX4.` },
  { id: 'spoofing', keywords: ['spoof', 'gps fault', 'fault profile'], text: `GPS spoofing experiments are software-only and are configured in the ${ATTACK_LAB_NAME} tab. They are not applied to PX4 or Gazebo.` },
  { id: 'confirm', keywords: ['confirm aircraft', 'confirmed', 'confirm uav', 'confirm mission'], text: 'An aircraft can be confirmed once its inputs are complete and its route has been generated and validated. Confirming UAV 1 unlocks UAV 2. Editing a confirmed aircraft clears its confirmation, and editing route inputs clears the generated route.' },
  { id: 'approval', keywords: ['approval', 'approve', 'general review'], text: 'Operators submit an immutable version for General review; only that exact approved version can be deployed. General accounts do not need approval.' },
  { id: 'validation', keywords: ['validation', 'validate', 'validation list'], text: 'Validation is the list in Section C of everything still blocking the scenario. The backend re-validates the whole plan, regenerates every route and compares it with the stored one on save, submit and deploy; the assistant reads the same list and never decides an item is satisfied itself.' },
  { id: 'deploy', keywords: ['deploy', 'create & deploy', 'create and deploy', 'deploy to mission control'], text: 'Deploy to Mission Control hands the saved, validated mission version, with its generated waypoints and roles, to Mission Control and opens it. It does not arm, take off, or upload a mission to PX4. It needs: no unsaved changes, the latest version, a complete checklist and a freshly verified fleet (use Activate and verify next to the button). Operators need General approval of that exact version first.' },
  { id: 'simulation home', keywords: ['mission start', 'move simulation', 'simulation home', 'zurich', 'wrong location', 'far from'], text: 'The mission start is the take-off point of the primary aircraft. PX4 SITL and Gazebo start at their default location unless told otherwise, so the aircraft can be far from the mission. When it is more than 1 km away an amber notice offers "Move simulation to mission start": after you confirm, the PX4 and Gazebo simulation is restarted at the mission start. It commands no flight and never happens automatically.' },
  { id: 'activation', keywords: ['activate', 'activation', 'verify fleet'], text: 'Activation starts or adopts disarmed PX4/Gazebo instances and counts a UAV only after both PX4 telemetry and a Gazebo pose are verified.' },
  { id: 'legacy', keywords: ['legacy', 'old version', 'schema'], text: 'Versions saved before schema 2 are legacy: they stay readable and unmodified but are read-only. Start a new draft to plan with the current model.' },
]

function normalize(text: string): string {
  return text.toLowerCase().replace(/[“”"']/g, '').replace(/[_-]/g, ' ').replace(/\s+/g, ' ')
}

function bestMatch<T>(text: string, items: T[], keywords: (item: T) => string[]): T | null {
  const lowered = normalize(text)
  let best: { item: T; length: number } | null = null
  for (const item of items) {
    for (const keyword of keywords(item)) {
      if (lowered.includes(keyword) && (!best || keyword.length > best.length)) best = { item, length: keyword.length }
    }
  }
  return best?.item ?? null
}

export function explainTopic(text: string): string | null {
  const lowered = normalize(text)
  const section = lowered.match(/\bsection ([abc])\b/)
  if (section) return SECTION_HELP[section[1].toUpperCase() as SectionId]
  if (/\bsection d\b/.test(lowered)) return `There is no Section D any more: review and validation is Section C. ${SECTION_HELP.C}`
  const topic = bestMatch(text, TOPICS, item => item.keywords)
  const field = bestMatch(text, EXPLAINABLE_FIELDS, item => item.keywords)
  const topicLength = topic ? Math.max(...topic.keywords.filter(keyword => lowered.includes(keyword)).map(keyword => keyword.length)) : 0
  const fieldLength = field ? Math.max(...field.keywords.filter(keyword => lowered.includes(keyword)).map(keyword => keyword.length)) : 0
  if (field && (!topic || fieldLength > topicLength)) {
    return `${field.label}: ${field.help} Example: ${field.example}${field.command ? ` ${field.command}` : ''}`
  }
  return topic ? topic.text : null
}

const describePoint = (point: { latitude: number; longitude: number } | null | undefined) =>
  point ? `${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}` : 'not selected'

export function summarizeAircraft(aircraft: AircraftPlan, index: number): string {
  const missing = missingAircraftInputs(aircraft)
  const route = aircraft.route
  return [
    `UAV ${index + 1} (${aircraft.uav_id}) — ${aircraft.confirmed ? 'CONFIRMED' : 'not confirmed'}`,
    `Role: ${aircraft.role ?? 'not set'}; priority ${aircraft.priority}; ${aircraft.reassignable ? 'reassignable' : 'fixed'}`,
    `Task: ${taskSpec(aircraft.task_type)?.label ?? 'not chosen'}`,
    `Start: ${describePoint(aircraft.start_location)}; altitude ${aircraft.altitude_m ?? 'not set'} m; speed ${aircraft.speed_mps ?? 'not set'} m/s; loiter ${aircraft.loiter_duration_s} s`,
    `Return: ${aircraft.return_mode === 'return_to_start' ? 'return to start and land' : 'land at the last point'}`,
    route ? `Route: ${route.waypoints.length} waypoints, ${(route.length_m / 1000).toFixed(2)} km, about ${(route.duration_s / 60).toFixed(1)} min${route.events.length ? `, ${route.events.length} simulated payload_released event(s)` : ''}` : 'Route: not generated yet',
    missing.length ? `Still missing: ${missing.map(field => field.label).join(', ')}.` : 'All required inputs are filled.',
  ].join('\n')
}

export function summarizeMission(ctx: HelpContext): string {
  const { plan } = ctx
  const objectives = plan.objectives.map(value => MISSION_OBJECTIVES.find(item => item.value === value)?.title ?? value)
  const area = plan.operating_area
  return [
    `Draft: ${ctx.title.trim() || 'unnamed'}${ctx.scenarioTitle ? ` (saved${ctx.versionNumber ? `, latest v${ctx.versionNumber}` : ''}${ctx.approvalStatus ? `, ${ctx.approvalStatus}` : ''})` : ' (not saved yet)'}`,
    `Currently in Section ${ctx.section}.`,
    `Objectives: ${objectives.join(', ') || 'none selected'}`,
    `Operating area: ${!area ? 'not selected' : area.kind === 'circle' ? `circle, radius ${area.radius_m ?? 0} m` : `polygon, ${area.vertices.length} vertices`}`,
    `Limits: max distance ${plan.constraints.max_distance_from_start_m ?? 'not set'} m; max altitude ${plan.constraints.max_altitude_m ?? 'not set'} m; max duration ${plan.max_duration_minutes ?? 'not set'} min`,
    `UAVs: ${plan.required_uav_count ?? 'none'} required, ${plan.activated_uav_ids.length} activated, ${plan.selected_uav_ids.length} assigned, ${plan.aircraft.filter(item => item.confirmed).length} confirmed; fleet verified: ${ctx.fleetVerified ? 'yes' : 'no'}`,
    `Attack experiments: configured in the ${ATTACK_LAB_NAME} tab, not in this plan.`,
  ].join('\n')
}

export function readinessAnswer(ctx: HelpContext): string {
  if (ctx.legacy) return 'This is a legacy (schema 1) version. It is read-only and cannot be edited or deployed from here; start a new draft to plan with the current model.'
  if (!ctx.deployBlockers.length) {
    return 'The same validation the Review section uses reports no blockers, the latest version is saved, and no unsaved changes were found. You can deploy from Section C. Deployment hands the mission and its routes to Mission Control; it does not arm, take off or upload anything.'
  }
  return `Not ready yet. ${ctx.deployBlockers.length} thing(s) block deployment:\n${ctx.deployBlockers.map(item => `• ${item}`).join('\n')}`
}

export function missingSectionAFields(ctx: HelpContext): string[] {
  const { plan } = ctx
  const spec = (key: string) => MISSION_FIELD_SPECS.find(item => item.key === key)!
  const line = (key: string) => { const item = spec(key); return `${item.label} — ${item.help} Example: ${item.example}${item.command ? ` ${item.command}` : ''}` }
  const items: string[] = []
  if (!ctx.title.trim()) items.push('SCENARIO NAME — name this scenario. Type it in the name box at the top.')
  if (!plan.objectives.length) items.push(line('objectives'))
  if (!plan.operating_area) items.push(line('operating_area'))
  if (plan.constraints.max_distance_from_start_m === null) items.push(line('max_distance_from_start_m'))
  if (plan.constraints.max_altitude_m === null) items.push(line('max_altitude_m'))
  if (!plan.required_uav_count || plan.activated_uav_ids.length !== plan.required_uav_count || !ctx.fleetVerified || !plan.selected_uav_ids.length) items.push(line('required_uav_count'))
  return items
}

export function missingAnswer(ctx: HelpContext, requestedSection: SectionId | null, requestedAircraft: number | null): string {
  if (ctx.legacy) return readinessAnswer(ctx)
  const section = requestedSection ?? ctx.section
  if (section === 'A') {
    const items = missingSectionAFields(ctx)
    return items.length ? `Section A still needs:\n${items.map(item => `• ${item}`).join('\n')}` : 'Section A has everything it needs. Continue with Section B.'
  }
  if (section === 'B') {
    const aircraft = ctx.plan.aircraft
    if (!aircraft.length) return 'Section B has no aircraft yet. Request a UAV count in Section A and activate and verify the fleet first.'
    const index = requestedAircraft !== null ? requestedAircraft - 1 : ctx.activeIndex
    const item = aircraft[index]
    if (!item) return `UAV ${requestedAircraft} is not in the activated fleet.`
    const missing = missingAircraftInputs(item)
    const previous = aircraft[index - 1]
    const order = index > 0 && !previous.confirmed ? `\nNote: confirm ${previous.uav_id} before ${item.uav_id} can be confirmed.` : ''
    if (!missing.length) {
      return item.confirmed ? `${item.uav_id} is fully filled in and confirmed.${order}` : `${item.uav_id} has every required input and a generated route. Press its CONFIRM button to confirm it and continue.${order}`
    }
    return `${item.uav_id} (UAV ${index + 1}) still needs ${missing.length} item(s):\n${missing.map(field => `• ${field.label} — ${field.help} Example: ${field.example}${field.command ? ` ${field.command}` : ''}`).join('\n')}${order}`
  }
  return ctx.validationItems.length
    ? `Section C validation currently reports:\n${ctx.validationItems.map(item => `• ${item}`).join('\n')}`
    : 'Section C validation reports nothing missing. Save a version, then use the action beneath Save: deploy to Mission Control (Operators submit for review first).'
}

export const ASSISTANT_EXAMPLES = [
  'Ask "is everything ready?" or "what is still missing?"',
  'Ask "what is a coverage survey?" or "explain relief supply drop-off"',
  'Say "summarize UAV 1" or "what is my mission?"',
  'Say "set altitude to 30" or "set task type of UAV 1 to coverage survey"',
  'Say "I need 2 UAVs" (you will be asked to confirm)',
]

export function capabilitiesAnswer(): string {
  return `I can answer questions about this form and propose changes that only apply after you confirm them:\n${ASSISTANT_EXAMPLES.map(item => `• ${item}`).join('\n')}\nYou can also tap one of the options below. Locations, areas and routes are always chosen on the map and generated by the backend.`
}

/** User-facing text for each AI failure kind. Quota/billing and transient rate limits are deliberately different. */
export function aiFailureMessage(code: string | undefined, fallback: string): string {
  switch (code) {
    case 'provider_insufficient_quota':
      return 'The AI provider reports no remaining credits or billing is not enabled. Add credits or billing for the API project; waiting will not help.'
    case 'provider_rate_limited':
      return 'The AI provider is rate limiting requests right now. This is temporary: try again in a minute.'
    case 'provider_authentication_failed':
      return 'The AI provider rejected the backend API key or project permissions. Check the backend configuration.'
    case 'openai_key_not_configured':
      return 'No AI API key is configured on the backend.'
    case 'provider_timeout':
      return 'The AI provider did not answer in time. Try again.'
    default:
      return fallback
  }
}

export function unknownHelp(prefix = ''): string {
  return `${prefix}I could not understand that as a question about this form or a command I can run. Things I can do:\n${ASSISTANT_EXAMPLES.map(item => `• ${item}`).join('\n')}`
}

export interface HelpAnswer { text: string }

/** Returns an answer for a deterministic read-only intent, or null when the message is not one. */
export function answerReadOnlyQuestion(raw: string, ctx: HelpContext): HelpAnswer | null {
  const text = normalize(raw)
  const aircraft = text.match(/\b(?:uav|aircraft) ?(\d{1,2})\b/)
  const aircraftNumber = aircraft ? Number(aircraft[1]) : null
  const sectionMention = text.match(/\bsection ([abcd])\b/)
  // "Section D" was the review section before the rename; it is Section C now.
  const requestedSection = sectionMention ? ((sectionMention[1] === 'd' ? 'C' : sectionMention[1].toUpperCase()) as SectionId) : null
  if (/\bwhat (can|could) you do\b|\bwhat are your (options|capabilities)\b|^help\??$/.test(text)) return { text: capabilitiesAnswer() }
  const explainVerb = /\b(what is|whats|what does|what do|explain|meaning|mean|define|tell me about|describe)\b/.test(text)
  const fillWords = /\b(fill|filled|left|remaining|missing|to be done|todo|still need|what do i need|what else|next step|what next|incomplete|help me)\b/.test(text)

  if (/\b(summari[sz]e|summary|overview|recap|whats my mission|what is my mission|my mission|current status)\b/.test(text)) {
    if (aircraftNumber !== null) {
      const item = ctx.plan.aircraft[aircraftNumber - 1]
      return { text: item ? summarizeAircraft(item, aircraftNumber - 1) : `UAV ${aircraftNumber} is not in the activated fleet.` }
    }
    return { text: summarizeMission(ctx) }
  }
  const topic = explainTopic(raw)
  if (explainVerb && topic && !fillWords) return { text: topic }
  if (fillWords || /\bwhat (to be|is left)\b/.test(text) || /\bwhats to be\b/.test(text)) {
    return { text: missingAnswer(ctx, requestedSection ?? (aircraftNumber !== null ? 'B' : null), aircraftNumber) }
  }
  if (/\b(ready|deploy|deployable|good to go|can i (submit|go|proceed|launch)|everything (ok|okay|done|set|complete)|all set|blockers?)\b/.test(text)) {
    return { text: readinessAnswer(ctx) }
  }
  if (/^(how|why|where)\b/.test(text) && topic) return { text: topic }
  return null
}

/** Compact, coordinate-free snapshot for the model. It is data for the backend to treat as untrusted, never instructions. */
export function buildAssistantSnapshot(ctx: HelpContext) {
  const { plan } = ctx
  const cut = (value: string | null | undefined, limit: number) => (value ?? '').slice(0, limit)
  return {
    section: ctx.section,
    title: cut(ctx.title, 120),
    objectives: plan.objectives.slice(0, 11).map(value => cut(MISSION_OBJECTIVES.find(item => item.value === value)?.title ?? value, 60)),
    description: cut(plan.description, 500),
    area_kind: plan.operating_area?.kind ?? null,
    max_duration_minutes: plan.max_duration_minutes,
    max_distance_from_start_m: plan.constraints.max_distance_from_start_m,
    max_altitude_m: plan.constraints.max_altitude_m,
    required_uav_count: plan.required_uav_count,
    activated_uav_count: Math.min(20, plan.activated_uav_ids.length),
    fleet_verified: ctx.fleetVerified,
    active_uav_index: Math.min(5, Math.max(1, ctx.activeIndex + 1)),
    validation_items: ctx.validationItems.slice(0, 14).map(item => cut(item, 300)),
    deploy_blockers: ctx.deployBlockers.slice(0, 16).map(item => cut(item, 300)),
    aircraft: plan.aircraft.slice(0, 5).map((item, index) => ({
      index: index + 1, uav_id: cut(item.uav_id, 16), confirmed: item.confirmed === true, role: item.role,
      priority: item.priority, reassignable: item.reassignable, task_type: item.task_type,
      altitude_m: item.altitude_m, speed_mps: item.speed_mps, loiter_duration_s: item.loiter_duration_s, return_mode: item.return_mode,
      start_selected: Boolean(item.start_location), route_waypoints: item.route?.waypoints.length ?? 0,
      parameters: Object.fromEntries(Object.entries(item.parameters).filter(([, value]) => typeof value === 'number' || typeof value === 'string').slice(0, 12)),
      missing: missingAircraftInputs(item).map(field => field.label).slice(0, 16),
    })),
  }
}
