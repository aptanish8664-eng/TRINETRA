export type MissionProposalContent = {
  mission_objectives: string[]
  required_information: string[]
  proposed_tasks: { description: string; task_reference: string | null; assigned_uav_id: string | null; waypoint_reference: string | null; priority: number }[]
  uav_role_suggestions: { role: string; count: number; rationale: string }[]
  waypoint_references: string[]
  assumptions: string[]
  constraints: string[]
  assessment_explanations: string[]
  clarification_questions: string[]
}

export type OperatorSimulationConfig = {
  uav_count: number; task_count: number; seed: number; duration: number
  spoofing_type: string; target_uav: string; spoof_start: number; spoof_duration: number
  spoof_magnitude: number; response_policy: string; detector_threshold: number | null
}

export type MissionProposal = {
  proposal_id: string
  status: 'needs_clarification' | 'pending_review' | 'approved' | 'approving' | 'approval_failed' | 'rejected' | 'expired'
  ready_for_review: boolean
  validation_issues: string[]
  proposal: MissionProposalContent
  operator_config: OperatorSimulationConfig
  run_id: string | null
  decided_by?: string | null
  decision_reason?: string | null
  expires_at?: string | null
}

export type MissionConversation = {
  conversation_id: string
  status: 'collecting' | 'ready_for_review'
  messages: { role: 'user' | 'assistant'; content: string }[]
  proposal: MissionProposalContent | null
  proposal_id: string | null
  scenario_id: string | null
  operator_config: OperatorSimulationConfig
  expires_at: string
}

export type PendingMissionProposal = {
  id: string
  operator_id: string
  status: 'pending_review'
  proposal: MissionProposalContent
  operator_config: OperatorSimulationConfig
  validation_issues: string[]
  run_id: string | null
  created_at: string
  expires_at: string | null
}

export type AIStatus = {
  available: boolean; provider: 'openai'; model: string; reason: string | null
  remaining_requests: number | null; remaining_output_tokens: number | null
}

export type MissionAssessment = {
  run_id: string; tick: number; simulation_status: string
  validated_evidence: {
    coordinate_system: string; run_id: string; tick: number; simulation_status: string
    validated_progress: { completed: number; total: number; remaining: number }
    reported_telemetry: { uav_id: string; reported_gps: { x: number; y: number }; assigned_task_id: string | null; status: string; threat_level: string; anomaly_score: number; trajectory_residual: number }[]
    recent_event_metadata: { id: string; tick: number; type: string; uav_id?: string; task_id?: string; severity?: string }[]
    permitted_response_suggestions: string[]
  }
  assessment: {
    summary: string
    findings: { explanation: string; evidence_references: string[] }[]
    suggested_responses: ('continue_monitoring' | 'pause_simulation' | 'stop_simulation')[]
  }
}
