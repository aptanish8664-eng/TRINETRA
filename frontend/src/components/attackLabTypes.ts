/**
 * Attack Lab and threat-assessment types plus the small amount of text the UI and the assistant need offline.
 * Numeric ranges (magnitude, duration, start, seed, handover battery) are NOT defined here: they are fetched from
 * GET /api/attack-lab/limits so the backend stays the single authority. `backend/tests/test_attack_lab.py` checks
 * that the ids, labels and text below match what the backend serves.
 */
export const ATTACK_LAB_NAME = 'Attack Lab'
export const NOT_EXECUTED_NOTE = 'This defines a simulated, software-only attack. It is not executed until the experiment runner exists, and nothing is sent to PX4 or Gazebo.'
export const GROUND_TRUTH_NOTE = 'Ground truth (which samples are attacked, the attack schedule and any labels) is never an input to the detector or the threat engine and is never shown in Mission Control or Scenario Lab. It is revealed only in an evaluation summary after an offline replay ends.'
export const HIDDEN_SCHEDULE_NOTE = 'Operators will not see this schedule while the experiment runs. It is shown only in the evaluation after the run ends.'
export const SOFTWARE_ONLY_NOTE = 'This is a software simulation. It changes only the simulated GPS data the monitor reads; it does not affect the aircraft.'
export const RESPONSE_NOTE = "Presets and experiments define the attack only. An attack never changes an aircraft's role: isolation and handover are the operator's decisions in the Threat Monitor."
export const HANDOVER_BATTERY_NOTE = 'Used later, when the operator considers a handover: an aircraft below this battery level is not offered. It never triggers a handover.'
export const HANDOVER_BATTERY_LABEL = 'Handover eligibility: minimum battery (%)'
export const RUN_AFTER_DEPLOY_NOTE = 'This version is not deployed. You can define the experiment now and run it after the version is deployed.'

export type AttackType = 'offset' | 'jump' | 'drift' | 'freeze' | 'noise' | 'velocity_inconsistency'
export const ATTACK_TYPE_SPECS: { value: AttackType; label: string; summary: string; unit: string; usesDirection: boolean }[] = [
  { value: 'offset', label: 'Position offset', summary: 'The reported position is moved a fixed distance to one side and held there (it slides over in about 3 seconds).', unit: 'm', usesDirection: true },
  { value: 'jump', label: 'Position jump', summary: 'The reported position leaps a fixed distance in one step, usually for a short time.', unit: 'm', usesDirection: true },
  { value: 'drift', label: 'Gradual drift', summary: 'The reported position creeps away at a steady rate, so the error grows slowly.', unit: 'm/s', usesDirection: true },
  { value: 'freeze', label: 'Position freeze', summary: 'The reported position stops updating while the aircraft keeps moving.', unit: '', usesDirection: false },
  { value: 'noise', label: 'Noise burst', summary: 'Random scatter is added to the reported position; it does not pull in one direction.', unit: 'm', usesDirection: false },
  { value: 'velocity_inconsistency', label: 'Velocity inconsistency', summary: 'The reported velocity disagrees with how the aircraft is really moving, by a fixed bias.', unit: 'm/s', usesDirection: true },
]

/** What each experiment field means, in plain words (mirrors `FIELD_HELP` in backend/app/research/attacks.py). */
export const ATTACK_FIELD_HELP = {
  start: 'Seconds after the aircraft starts its route when the attack begins.',
  duration: 'How long the attack lasts, in seconds. It must end before the route does.',
  magnitude: 'How strong the attack is: a distance in metres, a rate in m/s, or a noise level, depending on the type.',
  direction: 'Compass direction the reported position is pushed (0 = north, 90 = east). Leave it blank for a random direction fixed by the seed.',
  seed: 'A number that fixes every random choice, so the same seed repeats the experiment exactly.',
} as const

/** Presets describe the ATTACK only. `minAircraft` is how many aircraft the attack itself needs. */
export type PresetId = 'baseline' | 'slow_drift' | 'rapid_jump' | 'strong_offset' | 'noise_burst' | 'paired_drift'
export interface PresetSpec { id: PresetId; label: string; does: string; useful: string; summary: string; minAircraft: number; needs: string }
const preset = (id: PresetId, label: string, does: string, useful: string, minAircraft = 1, needs = ''): PresetSpec =>
  ({ id, label, does, useful, summary: `${does} ${useful}`, minAircraft, needs })
export const PRESET_SPECS: PresetSpec[] = [
  preset('baseline', 'Baseline (no attack)', 'No attack: the GPS data is left untouched.', 'Shows how quiet the monitor stays and how many false alarms it raises.'),
  preset('slow_drift', 'Slow drift', "The primary aircraft's reported position creeps away at 0.5 m/s for 40% of the route.", 'The hardest attack to notice: measures how long a gradual error stays hidden.'),
  preset('rapid_jump', 'Rapid jump', "The primary aircraft's reported position leaps 150 m for 10 seconds.", 'The easiest attack to notice: measures the fastest detection time.'),
  preset('strong_offset', 'Strong offset', "The primary aircraft's reported position is held 150 m away for 60% of the route.", 'A sustained, obvious error: measures detection and how the options offered to the operator hold up.'),
  preset('noise_burst', 'Noise burst', "15 m of random scatter is added to the primary aircraft's reported position for 30% of the route.", 'Checks that jitter is told apart from a real displacement.'),
  preset('paired_drift', 'Drift on two aircraft', 'The same 0.5 m/s drift is applied to the primary and to a second aircraft at the same time.', 'The monitor compares aircraft with each other: measures detection when the comparison aircraft is attacked too.', 2, 'It attacks two aircraft at once, so the plan needs a second aircraft.'),
]
/** Presets offered for a plan with `aircraftCount` aircraft, and the ones kept out of the list with the reason. */
export function presetsForPlan(aircraftCount: number): { offered: PresetSpec[]; hidden: PresetSpec[] } {
  return { offered: PRESET_SPECS.filter(item => aircraftCount >= item.minAircraft), hidden: PRESET_SPECS.filter(item => aircraftCount < item.minAircraft) }
}

/** What an experiment measures (shown as a short summary; the stored `measurements` flags all default to on). */
export const MEASUREMENT_SPECS: { key: keyof ExperimentConfig['measurements']; label: string; help: string }[] = [
  { key: 'detection_latency', label: 'Detection latency', help: 'Seconds from the start of the attack to the first sustained alert.' },
  { key: 'false_alarms', label: 'False alarms', help: 'Alerts raised while no attack is active.' },
  { key: 'position_error', label: 'Position error', help: 'How far the reported position is from the true one.' },
  { key: 'mission_completion', label: 'Mission completion', help: 'Not measured yet: it needs the experiment runner.' },
  { key: 'handover_time', label: 'Handover time', help: 'Not measured yet: no handover is executed in an offline replay.' },
]

export interface AttackLimits {
  name: string
  types: { type: AttackType; label: string; summary: string; uses_direction: boolean; magnitude: { label: string; unit: string; min: number; max: number; default: number } }[]
  duration_s: { min: number; max: number }
  start_s: { min: number; max: number }
  seed: { min: number; max: number }
  max_attacks: number
  handover: { min_battery_pct: number; roles: string[]; label?: string; note?: string }
  response?: { policy: 'operator_decides'; note: string }
  field_help?: Record<string, string>
  notes?: { hidden_schedule: string; software_only: string }
  presets: { id: PresetId; label: string; summary: string; does?: string; useful?: string; min_aircraft?: number }[]
  executes: boolean
}

/** Mission picker rows from GET /api/attack-lab/missions: saved schema-2 scenarios and their versions. */
export interface PickerAircraft { uav_id: string; role: string | null; reassignable: boolean; duration_s: number | null }
export interface PickerVersion {
  id: string; version_number: number; config_hash: string; created_at: string
  latest: boolean; superseded: boolean; deployed: boolean; deployed_at: string | null; in_mission_control: boolean
  aircraft: PickerAircraft[]; estimated_duration_s: number | null; area_summary: string; objectives: string[]
  presets: { presets: { id: PresetId }[]; hidden: { id: PresetId; label: string; reason: string }[] }
}
export interface PickerMission { scenario_id: string; title: string; versions: PickerVersion[] }

export interface ExperimentAttack {
  target_aircraft: string
  type: AttackType
  start_s: number
  duration_s: number
  magnitude: number
  direction_deg: number | null
}
export interface ExperimentConfig {
  attacks: ExperimentAttack[]
  /** Always `operator_decides`: kept only because stored experiments carry the field. Nothing scripts a response. */
  response_policy: 'operator_decides'
  /** Eligibility settings for the operator's later handover choice; they never trigger a handover. */
  handover: { min_battery_pct: number; candidate_roles: ('standby' | 'decoy')[] }
  measurements: { detection_latency: boolean; false_alarms: boolean; position_error: boolean; mission_completion: boolean; handover_time: boolean }
  allow_overlap: boolean
}
export interface ExperimentInput {
  name: string
  scenario_id: string
  scenario_version_id: string
  preset_id: PresetId | null
  seed: number
  config: ExperimentConfig
  status: 'draft' | 'ready'
}
export interface Experiment extends ExperimentInput {
  id: string
  owner_user_id: string
  config_hash: string
  superseded: boolean
  executed: false
  note: string
  created_at: string
  updated_at: string
}
export interface ExperimentIssue { code: string; message: string; field?: string | null }

export const DEFAULT_CONFIG: ExperimentConfig = {
  attacks: [], response_policy: 'operator_decides',
  handover: { min_battery_pct: 40, candidate_roles: ['standby', 'decoy'] },
  measurements: { detection_latency: true, false_alarms: true, position_error: true, mission_completion: true, handover_time: true },
  allow_overlap: false,
}

// --- threat assessment ---------------------------------------------------------------------------------------

export type Tier = 'Low' | 'Medium' | 'High' | 'Critical'
export const TIERS: Tier[] = ['Low', 'Medium', 'High', 'Critical']
export type ActionId = 'continue_monitor' | 'increase_monitoring' | 'dead_reckoning_fallback' | 'isolate_aircraft' | 'handover_to_standby' | 'return_to_launch'
export interface ActionRow { action: ActionId; label: string; reason: string; tradeoff: string; eligible: boolean; rank: number; rank_score: number }
export interface HandoverCandidate {
  aircraft_id: string; role: string; battery_pct: number; distance_to_task_m: number; workload: number
  eligible: boolean; ineligible_reason: string | null; score: number
}
export interface EvidenceItem { feature: string; value: number; reference: number; unit: string; times_normal: number; text: string }
export interface Assessment {
  aircraft_id: string
  anomaly_confidence: number
  severity: number
  mission_impact: number
  threat_score: number
  tier: Tier
  threat_score_is_probability: false
  evidence: EvidenceItem[]
  actions: ActionRow[]
  handover_candidates: HandoverCandidate[]
  t: number
  model_id: string
  held?: boolean
  hold_s?: number
}
export interface DecisionRecord {
  id: string; username: string; aircraft_id: string; action: ActionId; handover_candidate: string | null
  source: 'live_telemetry' | 'offline_replay'; source_name: string | null; tier: Tier | null; threat_score: number | null; created_at: string; executed: false
}
export interface ReplayAircraft {
  aircraft_id: string; role: 'primary' | 'standby' | 'decoy' | null; reassignable: boolean
  t: number[]; gps_position: number[][]; gps_velocity: number[][]; imu_velocity: number[][]; battery_pct: number[]
}
export interface Replay {
  replay_id: string; experiment_name: string; experiment_id: string; hz: number; duration_s: number
  aircraft: ReplayAircraft[]; min_battery_pct: number; candidate_roles: string[]; source: 'offline_replay'
}
export interface ReplayEvaluation {
  experiment_name: string
  attacks: ExperimentAttack[]
  aircraft: {
    aircraft_id: string; role: string; attacked: boolean; attack_start_s: number | null; detection_latency_s: number | null; detected: boolean | null
    false_alarms: number; false_alarms_per_hour: number; max_gps_position_error_m: number; mean_gps_position_error_m: number; max_tier: Tier
    mission_completion_pct: number | null; handover_time_s: number | null
  }[]
  not_measured: Record<string, string>
}
export interface ModelInfo {
  available: boolean
  reason?: string
  model_id?: string
  tiers?: { medium: number; high: number; critical: number }
  formula: string
  test_metrics?: Record<string, number | null>
  limits?: string[]
  live_residual_inputs_available?: boolean
  min_battery_pct_default?: number
  sample_hz?: number
}

/** Tier colours are paired with the tier name everywhere; colour is never the only signal. */
export const TIER_COLORS: Record<Tier, string> = { Low: '#56e3b1', Medium: '#f5b94c', High: '#ff9d5c', Critical: '#ff4d5a' }
