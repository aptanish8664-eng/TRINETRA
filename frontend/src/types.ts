export type Point = { x: number; y: number }
export type MLDetection = {
  attack_probability: number; confidence: number; confidence_is_calibrated: boolean
  detected: boolean; classification: string; threat_severity: number; threat_level: string; model_id: string
}
export type DetectorInfo = { mode: 'heuristic' | 'trained_ml'; model_id: string | null }
export type UAV = {
  id: string; position: Point; gps: Point; heading: number; speed: number; task_id: string | null
  status: string; capacity: number; anomaly_score: number; threat_level: string
  readiness?: string; workload?: number; workload_capacity?: number; communications_available?: boolean
  capabilities?: string[]; payload_capacity_kg?: number; battery_pct?: number
  battery_reserve_pct?: number; endurance_remaining_ticks?: number; endurance_reserve_ticks?: number
  alert_acknowledged: boolean
  velocity: Point; gps_velocity?: Point; residual: number
  ml_detection?: MLDetection | null
}
export type Task = {
  id: string; position: Point; priority: number; status: string; uav_id: string | null; completed_tick: number | null
  required_capability?: string; payload_required_kg?: number
}
export type MissionEvent = { id: string; tick: number; time: number; type: string; uav_id?: string; task_id?: string; severity?: string; reason?: string }
export type MissionState = {
  status: string; tick: number; time: number; seed: number; coordinate_system: string
  detector?: DetectorInfo
  config: Record<string, number | string>; uavs: Record<string, UAV>; tasks: Record<string, Task>
  progress: { completed: number; total: number; remaining: number }; events: MissionEvent[]
  calibration: { threshold: number; samples?: number; mean?: number; std?: number; quantile?: number }
}
export type Px4Position = {
  latitude: number; longitude: number; altitude_m: number; relative_altitude_m: number
  heading_deg: number | null; valid: boolean | null; coordinate_frame: 'WGS84'
  source_message: 'GLOBAL_POSITION_INT'; source_time_boot_ms: number; received_at: string
}
export type Px4TelemetryVehicle = {
  id: string; system_id: number; component_id: number; source: 'px4_mavlink'
  status: 'connected' | 'stale' | 'disconnected'; armed: boolean | null
  heartbeat: { vehicle_type: number; autopilot: number; base_mode: number; custom_mode: number; flight_mode: string | null; system_status: number; received_at: string } | null
  position: Px4Position | null
  velocity: { north_m_s: number; east_m_s: number; down_m_s: number; ground_speed_m_s: number; source_message: string; received_at: string } | null
  attitude: { roll_rad: number; pitch_rad: number; yaw_rad: number; source_message: string; source_time_boot_ms: number; received_at: string } | null
  gps_raw: { latitude: number; longitude: number; altitude_m: number; fix_type: number; valid: boolean; source_time_usec: number; eph_m: number | null; epv_m: number | null; satellites_visible: number | null; received_at: string } | null
  imu_raw: { acceleration_m_s2: Array<number | null>; angular_rate_rad_s: Array<number | null>; magnetic_field_gauss: Array<number | null>; temperature_c: number | null; fields_updated: number; source_time_usec: number; received_at: string } | null
  battery: { voltage_v: number | null; current_a: number | null; remaining_percent: number | null; source_message: string; received_at: string } | null
  mission: { current_sequence: number; total_sequence: number | null; mission_state: number; received_at: string } | null
  threat_assessment: { status: 'not_available'; reason: string }
  last_heartbeat_at: string | null; last_message_at: string
  telemetry_age_seconds: number; heartbeat_age_seconds: number | null
  flight_trail: Array<{ latitude: number; longitude: number; altitude_m: number; received_at: string }>
}
export type Px4TelemetrySnapshot = {
  source: 'px4_mavlink'; generated_at: string
  transport: { status: 'awaiting_data' | 'receiving' | 'stale' | 'disconnected'; datagrams_received: number; frames_decoded: number; invalid_datagrams: number; last_datagram_at: string | null; last_datagram_age_seconds: number | null }
  capabilities: { gps_spoof_detection: string; independent_navigation_estimate: string; telemetry_persistence: string }
  vehicles: Px4TelemetryVehicle[]
}
export type Px4TelemetryStreamSnapshot = Omit<Px4TelemetrySnapshot, 'vehicles'> & {
  stream_mode?: 'trail_delta_v1'
  vehicles: Array<Omit<Px4TelemetryVehicle, 'flight_trail'> & {
    flight_trail?: Px4TelemetryVehicle['flight_trail']
    flight_trail_delta?: Px4TelemetryVehicle['flight_trail']
  }>
}
export type GazeboPoseModel = {
  model_name: string
  position_m: { x: number; y: number; z: number }
  orientation_quaternion: { x: number; y: number; z: number; w: number }
  received_at: string
  status: 'connected' | 'stale' | 'disconnected'
  telemetry_age_seconds: number
  flight_trail: Array<{ x: number; y: number; z: number; received_at: string }>
}
export type GazeboPoseSnapshot = {
  source: 'gazebo_pose_info'
  world: string | null
  generated_at: string
  transport: { status: 'awaiting_data' | 'receiving' | 'stale' | 'disconnected'; datagrams_received: number; invalid_datagrams: number; last_datagram_at: string | null; last_datagram_age_seconds: number | null }
  models: GazeboPoseModel[]
}
export type GazeboPoseStreamSnapshot = Omit<GazeboPoseSnapshot, 'models'> & {
  stream_mode?: 'trail_delta_v1'
  models: Array<Omit<GazeboPoseModel, 'flight_trail'> & {
    flight_trail?: GazeboPoseModel['flight_trail']
    flight_trail_delta?: GazeboPoseModel['flight_trail']
  }>
}
export type RunRecord = { id: string; created_at: string; status: string; config: Record<string, unknown> }
