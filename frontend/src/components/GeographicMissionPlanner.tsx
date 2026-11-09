import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { AlertTriangle, LoaderCircle, MapPin, Plus, Radio, Save, Send, Sparkles, Trash2 } from 'lucide-react'
import type { GeographicMissionIntent, GeographicMissionObjective, GeographicPoint, GeographicUavAssignment, GeographicZone, ScenarioConfig, ScenarioIntent, ScenarioSummary } from '../services/auth'
import type { GeographicMissionProposal } from '../services/ai'
import type { Px4TelemetryVehicle } from '../types'
import { api } from '../services/api'
import { MissionPlannerMap, type PlannerMapMode } from './MissionPlannerMap'
import { formatCoordinate, validateGeographicMission } from './missionPlanning'
import './mission-planner.css'

type Props = {
  signedIn: boolean
  canManage: boolean
  userId?: string
  children?: ReactNode
  sharedScenarioId?: string | null
  onScenarioSelected?: (scenarioId: string | null) => void
  onLoadConfig?: (config: ScenarioConfig) => void
}

type GeographicConversationMessage = { role: 'user' | 'assistant'; content: string }
type GeographicConversationState = {
  messages: GeographicConversationMessage[]
  proposal: GeographicMissionProposal | null
}

function isGeographicProposal(value: unknown): value is GeographicMissionProposal {
  if (!value || typeof value !== 'object') return false
  const proposal = value as Record<string, unknown>
  return typeof proposal.summary === 'string'
    && Array.isArray(proposal.assignments)
    && proposal.assignments.every(item => item && typeof item === 'object'
      && typeof (item as Record<string, unknown>).uav_id === 'string'
      && typeof (item as Record<string, unknown>).role === 'string'
      && typeof (item as Record<string, unknown>).task === 'string')
    && Array.isArray(proposal.missing_information)
    && proposal.missing_information.every(item => typeof item === 'string')
    && Array.isArray(proposal.constraints)
    && proposal.constraints.every(item => typeof item === 'string')
    && Array.isArray(proposal.warnings)
    && proposal.warnings.every(item => typeof item === 'string')
}

function readGeographicConversation(userId: string): { state: GeographicConversationState; error: string } {
  if (!userId) return { state: { messages: [], proposal: null }, error: '' }
  try {
    const stored = window.sessionStorage.getItem(`trinetra-geographic-conversation:${userId}`)
    if (!stored) return { state: { messages: [], proposal: null }, error: '' }
    const parsed: unknown = JSON.parse(stored)
    if (!parsed || typeof parsed !== 'object') throw new Error('Stored planner conversation is invalid.')
    const record = parsed as Record<string, unknown>
    const messages = record.messages
    const validMessages = Array.isArray(messages)
      && messages.length <= 16
      && messages.every(item => item && typeof item === 'object'
        && ((item as Record<string, unknown>).role === 'user' || (item as Record<string, unknown>).role === 'assistant')
        && typeof (item as Record<string, unknown>).content === 'string'
        && ((item as Record<string, unknown>).content as string).length <= 2000)
    if (!validMessages || (record.proposal !== null && record.proposal !== undefined && !isGeographicProposal(record.proposal))) {
      throw new Error('Stored planner conversation has an invalid shape.')
    }
    return {
      state: {
        messages: messages as GeographicConversationMessage[],
        proposal: isGeographicProposal(record.proposal) ? record.proposal : null,
      },
      error: '',
    }
  } catch (cause) {
    return {
      state: { messages: [], proposal: null },
      error: cause instanceof Error ? cause.message : 'The saved geographic conversation could not be restored.',
    }
  }
}

const OBJECTIVES: { value: GeographicMissionObjective; label: string }[] = [
  { value: 'area_survey', label: 'Area surveying' },
  { value: 'perimeter_monitoring', label: 'Perimeter monitoring' },
  { value: 'inspection', label: 'Inspection' },
  { value: 'search_observation', label: 'Search and observation' },
  { value: 'communication_relay', label: 'Communication relay' },
]

const EMPTY_MISSION: GeographicMissionIntent = {
  objective: 'area_survey',
  start_location: null,
  destination: null,
  survey_zones: [],
  uav_assignments: [],
  constraints: [],
  ai_recommendations: [],
}

const EMPTY_INTENT: ScenarioIntent = {
  mission_objectives: [],
  proposed_tasks: [],
  uav_role_suggestions: [],
  operating_area_notes: '',
  environment_notes: '',
  constraints: [],
  geographic_mission: null,
  uav_fault_assignments: [],
}

const COMPATIBILITY_CONFIG: ScenarioConfig = {
  uav_count: 2,
  task_count: 1,
  seed: 1,
  duration: 1,
  spoofing_type: 'none',
  target_uav: 'UAV-01',
  spoof_start: 0,
  spoof_duration: 1,
  spoof_magnitude: 0,
  response_policy: 'baseline',
  detector_threshold: null,
}

function isAvailable(vehicle: Px4TelemetryVehicle): boolean {
  return vehicle.status === 'connected'
    && vehicle.position?.valid === true
    && Number.isFinite(vehicle.position.latitude)
    && Number.isFinite(vehicle.position.longitude)
}

function intentWithMission(mission: GeographicMissionIntent, base: ScenarioIntent): ScenarioIntent {
  const objective = OBJECTIVES.find(item => item.value === mission.objective)?.label ?? mission.objective
  const geographicObjectives = new Set(OBJECTIVES.map(item => item.label))
  return {
    ...base,
    mission_objectives: [objective, ...base.mission_objectives.filter(item => !geographicObjectives.has(item))],
    geographic_mission: mission,
  }
}

export function GeographicMissionPlanner({
  signedIn, canManage, userId = '', children, sharedScenarioId, onScenarioSelected, onLoadConfig,
}: Props) {
  const [initialConversation] = useState(() => readGeographicConversation(userId))
  const [conversation, setConversation] = useState<GeographicConversationState>(initialConversation.state)
  const [vehicles, setVehicles] = useState<Px4TelemetryVehicle[]>([])
  const [fleetError, setFleetError] = useState('')
  const [aiStatus, setAiStatus] = useState<{ available: boolean; reason: string | null } | null>(null)
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([])
  const [scenarioId, setScenarioId] = useState('')
  const [title, setTitle] = useState('')
  const [mission, setMission] = useState<GeographicMissionIntent>(EMPTY_MISSION)
  const [baseIntent, setBaseIntent] = useState<ScenarioIntent>(EMPTY_INTENT)
  const [activeAssignmentId, setActiveAssignmentId] = useState('')
  const [mapMode, setMapMode] = useState<PlannerMapMode>('navigate')
  const [draftVertices, setDraftVertices] = useState<GeographicMissionIntent['survey_zones'][number]['vertices']>([])
  const [zoneName, setZoneName] = useState('')
  const [selectedVertex, setSelectedVertex] = useState<{ zoneId: string; index: number } | null>(null)
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(initialConversation.error)
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(true)

  const availableVehicles = useMemo(() => vehicles.filter(isAvailable), [vehicles])
  const availableIds = useMemo(() => new Set(availableVehicles.map(vehicle => vehicle.id)), [availableVehicles])
  const selectedScenario = scenarios.find(item => item.id === scenarioId)
  const latest = selectedScenario?.latest_version
  const approvalStatus = latest?.approval?.status ?? null
  const validationIssues = useMemo(() => validateGeographicMission(mission, availableIds), [mission, availableIds])
  const aiProposal = conversation.proposal
  const conversationAtTurnLimit = conversation.messages.filter(item => item.role === 'user').length >= 8

  useEffect(() => {
    if (!userId) return
    try {
      window.sessionStorage.setItem(
        `trinetra-geographic-conversation:${userId}`,
        JSON.stringify(conversation),
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The geographic conversation could not be saved in this browser session.')
    }
  }, [conversation, userId])

  const refreshScenarios = async () => {
    const records = await api.scenarioList()
    setScenarios(records)
  }

  useEffect(() => {
    let active = true
    const loadFleet = async () => {
      try {
        const snapshot = await api.px4Telemetry()
        if (!active) return
        setVehicles(snapshot.vehicles)
        setFleetError('')
      } catch (cause) {
        if (active) setFleetError(cause instanceof Error ? cause.message : 'PX4 aircraft could not be loaded.')
      }
    }
    const loadInitial = async () => {
      setLoading(true)
      try {
        const result = await Promise.allSettled([refreshScenarios(), loadFleet(), api.aiStatus()])
        if (!active) return
        if (result[0].status === 'rejected') setError(result[0].reason instanceof Error ? result[0].reason.message : 'Saved missions could not be loaded.')
        if (result[2].status === 'fulfilled') setAiStatus(result[2].value)
        else setAiStatus({ available: false, reason: result[2].reason instanceof Error ? result[2].reason.message : 'AI availability could not be checked.' })
      } finally {
        if (active) setLoading(false)
      }
    }
    void loadInitial()
    const interval = window.setInterval(() => { void loadFleet() }, 5000)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [])

  const selectScenario = (id: string) => {
    const selected = scenarios.find(item => item.id === id)
    setScenarioId(id)
    setTitle(selected?.title ?? '')
    const intent = selected ? {
      ...EMPTY_INTENT,
      ...selected.latest_version.intent,
      uav_fault_assignments: selected.latest_version.intent.uav_fault_assignments ?? [],
    } : EMPTY_INTENT
    setBaseIntent(intent)
    setMission(intent.geographic_mission ?? EMPTY_MISSION)
    setActiveAssignmentId(intent.geographic_mission?.uav_assignments[0]?.uav_id ?? '')
    if (selected) onLoadConfig?.(selected.latest_version.config)
    onScenarioSelected?.(selected?.id ?? null)
    setConversation({ messages: [], proposal: null })
    setDescription('')
    setDraftVertices([])
    setSelectedVertex(null)
    setMapMode('navigate')
    setError('')
    setMessage(selected ? `Loaded ${selected.title} version ${selected.latest_version.version_number}.` : 'New geographic mission draft.')
  }

  useEffect(() => {
    if (sharedScenarioId && sharedScenarioId !== scenarioId && scenarios.some(item => item.id === sharedScenarioId)) {
      selectScenario(sharedScenarioId)
    }
  }, [sharedScenarioId, scenarios, scenarioId])

  const setObjective = (objective: GeographicMissionObjective) => {
    setMission(current => ({ ...current, objective }))
  }

  const beginZone = () => {
    setZoneName(`Zone ${mission.survey_zones.length + 1}`)
    setDraftVertices([])
    setSelectedVertex(null)
    setMapMode('draw_zone')
  }

  const finishZone = () => {
    if (draftVertices.length < 3) {
      setError('Add at least three map points before completing a survey zone.')
      return
    }
    const zone: GeographicZone = {
      id: `zone-${Date.now()}-${mission.survey_zones.length + 1}`,
      name: zoneName.trim() || `Zone ${mission.survey_zones.length + 1}`,
      vertices: draftVertices,
    }
    setMission(current => ({ ...current, survey_zones: [...current.survey_zones, zone] }))
    setDraftVertices([])
    setMapMode('navigate')
    setError('')
  }

  const addAssignment = () => {
    const next = availableVehicles.find(vehicle => !mission.uav_assignments.some(item => item.uav_id === vehicle.id))
    if (!next) {
      setError(availableVehicles.length ? 'Every currently available aircraft already has an assignment.' : 'No connected PX4 aircraft with valid position telemetry are available.')
      return
    }
    setMission(current => ({
      ...current,
      uav_assignments: [...current.uav_assignments, {
        uav_id: next.id,
        role: current.objective,
        task: '',
        zone_id: null,
        zone_ids: [],
        start_location: null,
        destination: null,
        waypoints: [],
      }],
    }))
    setActiveAssignmentId(next.id)
  }

  const changeAssignment = (index: number, patch: Partial<GeographicUavAssignment>) => {
    setMission(current => ({
      ...current,
      uav_assignments: current.uav_assignments.map((item, position) => position === index ? { ...item, ...patch } : item),
    }))
    if (patch.uav_id && mission.uav_assignments[index]?.uav_id === activeAssignmentId) {
      setActiveAssignmentId(patch.uav_id)
    }
  }

  const setAircraftLocation = (uavId: string, location: 'start_location' | 'destination', point: GeographicPoint) => {
    setMission(current => ({
      ...current,
      uav_assignments: current.uav_assignments.map(item => item.uav_id === uavId ? { ...item, [location]: point } : item),
    }))
  }

  const addAircraftWaypoint = (uavId: string, point: GeographicPoint) => {
    setMission(current => ({
      ...current,
      uav_assignments: current.uav_assignments.map(item => item.uav_id === uavId
        ? { ...item, waypoints: [...(item.waypoints ?? []), point] }
        : item),
    }))
  }

  const askAI = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const content = description.trim()
      if (!content) throw new Error('Enter a mission-planning response before continuing.')
      if (conversationAtTurnLimit) {
        throw new Error('This conversation has reached the eight-turn limit. Start a new conversation to continue.')
      }
      const userTurn = { role: 'user' as const, content }
      const transcript = [...conversation.messages, userTurn]
      const prompt = transcript.map(item => `${item.role === 'user' ? 'Operator' : 'Assistant'}: ${item.content}`).join('\n')
      const requestDescription = conversation.messages.length ? prompt : content
      if (requestDescription.length > 2000) {
        throw new Error('This geographic planning conversation has reached the 2,000-character provider limit. Start a new conversation to continue.')
      }
      const proposal = await api.planGeographicMission({
        objective: mission.objective,
        description: requestDescription,
        zones: mission.survey_zones.map(({ id, name }) => ({ id, name })),
      })
      const assistantReply = [
        proposal.summary,
        ...(proposal.missing_information.length ? ['Follow-up questions:', ...proposal.missing_information.map(item => `- ${item}`)] : []),
        ...proposal.warnings.map(item => `Warning: ${item}`),
      ].join('\n')
      if (assistantReply.length > 2000) {
        throw new Error('The geographic planning response exceeded the conversation limit. Review the manual planner instead.')
      }
      setConversation({
        messages: [...transcript, { role: 'assistant', content: assistantReply }],
        proposal,
      })
      setDescription('')
      setMessage(proposal.missing_information.length
        ? 'The assistant needs more information. Answer its follow-up question to continue planning.'
        : 'AI returned an advisory draft. Review each suggested assignment before applying it.')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'AI planning failed. Manual planning remains available.')
    } finally {
      setBusy(false)
    }
  }

  const applyRecommendations = () => {
    if (!aiProposal || aiProposal.missing_information.length) return
    const allowed = new Set(availableVehicles.map(vehicle => vehicle.id))
    const existing = new Map(mission.uav_assignments.map(item => [item.uav_id, item]))
    for (const suggestion of aiProposal.assignments) {
      if (!allowed.has(suggestion.uav_id)) continue
      const previous = existing.get(suggestion.uav_id)
      const existingZones = previous?.zone_ids ?? (previous?.zone_id ? [previous.zone_id] : [])
      const zoneIds = [...new Set([...existingZones, ...(suggestion.zone_id ? [suggestion.zone_id] : [])])]
      existing.set(suggestion.uav_id, {
        ...previous,
        ...suggestion,
        zone_id: previous?.zone_id ?? suggestion.zone_id ?? null,
        zone_ids: zoneIds,
        start_location: previous?.start_location ?? null,
        destination: previous?.destination ?? null,
        waypoints: previous?.waypoints ?? [],
      })
    }
    setMission(current => ({
      ...current,
      uav_assignments: [...existing.values()],
      constraints: [...new Set([...current.constraints, ...aiProposal.constraints])],
      ai_recommendations: [...new Set([...current.ai_recommendations, aiProposal.summary, ...aiProposal.warnings])].slice(0, 12),
    }))
    setMessage('Suggestions were copied into the editable mission. Review all aircraft, tasks, zones, and constraints before saving.')
  }

  const startNewConversation = () => {
    setConversation({ messages: [], proposal: null })
    setDescription('')
    setError('')
    setMessage('Started a new geographic planning conversation.')
  }

  const saveMission = async () => {
    if (!canManage) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const missionIntent = intentWithMission(mission, baseIntent)
      const saved = scenarioId && selectedScenario
        ? await api.updateScenario(scenarioId, title.trim(), latest!.config, missionIntent)
        : await api.createScenario(title.trim(), COMPATIBILITY_CONFIG, missionIntent)
      const latestVersion = saved.versions[0]
      setScenarioId(saved.id)
      setBaseIntent(missionIntent)
      onScenarioSelected?.(saved.id)
      setScenarios(current => {
        const summary: ScenarioSummary = {
          id: saved.id,
          owner_user_id: saved.owner_user_id,
          title: saved.title,
          created_at: saved.created_at,
          updated_at: saved.updated_at,
          latest_version: latestVersion,
        }
        return [summary, ...current.filter(item => item.id !== saved.id)]
      })
      setMessage(`Saved ${saved.title} version ${latestVersion.version_number} as a draft. Saving does not execute a flight mission.`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Mission could not be saved.')
    } finally {
      setBusy(false)
    }
  }

  const submitForApproval = async () => {
    if (!selectedScenario || !latest) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await api.submitScenario(selectedScenario.id, latest.id)
      await refreshScenarios()
      setMessage(`Version ${latest.version_number} was submitted for General review. No flight was started.`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Mission could not be submitted for review.')
    } finally {
      setBusy(false)
    }
  }

  const removeSelectedVertex = () => {
    if (!selectedVertex) return
    setMission(current => ({
      ...current,
      survey_zones: current.survey_zones.map(zone => zone.id === selectedVertex.zoneId
        ? { ...zone, vertices: zone.vertices.filter((_, index) => index !== selectedVertex.index) }
        : zone),
    }))
    setSelectedVertex(null)
  }

  return <div className="geographic-planner">
    <section className="panel geographic-planner-card">
      <div className="panel-heading">
        <div><span className="eyebrow">GEOGRAPHIC MISSION PLANNING</span><h3>Build and review a mission draft</h3></div>
        <span className={`assistant-status ${vehicles.some(isAvailable) ? 'available' : ''}`}><i/>{vehicles.some(isAvailable) ? `${availableVehicles.length} PX4 AIRCRAFT AVAILABLE` : 'PX4 TELEMETRY UNAVAILABLE'}</span>
      </div>
      <p className="geographic-planner-note">WGS84 map coordinates are stored separately from abstract simulator coordinates. Proposed lines are illustrative connections only, not validated flight paths.</p>
      {error && <div className="assistant-callout unavailable" role="alert"><AlertTriangle size={16}/><div><b>Mission planner error</b><span>{error}</span></div></div>}
      {message && <div className="assistant-message" role="status">{message}</div>}
      <div className="mission-map-toolbar" aria-label="Map controls">
        <button className={`button-secondary ${mapMode === 'set_start' ? 'selected' : ''}`} type="button" onClick={() => setMapMode(mapMode === 'set_start' ? 'navigate' : 'set_start')}><MapPin size={14}/> SELECT START</button>
        <button className={`button-secondary ${mapMode === 'set_destination' ? 'selected' : ''}`} type="button" onClick={() => setMapMode(mapMode === 'set_destination' ? 'navigate' : 'set_destination')}><MapPin size={14}/> SELECT DESTINATION</button>
        <button className={`button-secondary ${mapMode === 'draw_zone' ? 'selected' : ''}`} type="button" onClick={beginZone}><Plus size={14}/> DRAW SURVEY ZONE</button>
        {mission.uav_assignments.length > 0 && <>
          <label>MAP AIRCRAFT<select aria-label="Map aircraft" value={activeAssignmentId} onChange={event => setActiveAssignmentId(event.target.value)}>{mission.uav_assignments.map(item => <option key={item.uav_id} value={item.uav_id}>{item.uav_id}</option>)}</select></label>
          <button className={`button-secondary ${mapMode === 'set_aircraft_start' ? 'selected' : ''}`} type="button" onClick={() => setMapMode(mapMode === 'set_aircraft_start' ? 'navigate' : 'set_aircraft_start')}><MapPin size={14}/> AIRCRAFT START</button>
          <button className={`button-secondary ${mapMode === 'set_aircraft_destination' ? 'selected' : ''}`} type="button" onClick={() => setMapMode(mapMode === 'set_aircraft_destination' ? 'navigate' : 'set_aircraft_destination')}><MapPin size={14}/> AIRCRAFT DESTINATION</button>
          <button className={`button-secondary ${mapMode === 'add_aircraft_waypoint' ? 'selected' : ''}`} type="button" onClick={() => setMapMode(mapMode === 'add_aircraft_waypoint' ? 'navigate' : 'add_aircraft_waypoint')}><Plus size={14}/> AIRCRAFT WAYPOINT</button>
        </>}
        {mapMode === 'draw_zone' && <><input aria-label="Survey zone name" value={zoneName} onChange={event => setZoneName(event.target.value)} maxLength={60}/><button className="button-primary" type="button" onClick={finishZone}>FINISH ZONE ({draftVertices.length} POINTS)</button><button className="button-quiet" type="button" onClick={() => { setDraftVertices([]); setMapMode('navigate') }}>CANCEL</button></>}
        {mapMode === 'set_start' && <span>Click the map to set the starting location.</span>}
        {mapMode === 'set_destination' && <span>Click the map to set the destination.</span>}
        {mapMode === 'draw_zone' && <span>Click at least three map points; drag a vertex to adjust it.</span>}
        {mapMode === 'set_aircraft_start' && <span>Click to set {activeAssignmentId}&apos;s individual start point.</span>}
        {mapMode === 'set_aircraft_destination' && <span>Click to set {activeAssignmentId}&apos;s individual destination.</span>}
        {mapMode === 'add_aircraft_waypoint' && <span>Click to add a waypoint to {activeAssignmentId}&apos;s illustrative route.</span>}
      </div>
      <MissionPlannerMap
        start={mission.start_location}
        destination={mission.destination}
        zones={mission.survey_zones}
        draftVertices={draftVertices}
        assignments={mission.uav_assignments}
        activeAssignmentId={activeAssignmentId}
        mode={mapMode}
        onStartChange={point => setMission(current => ({ ...current, start_location: point }))}
        onDestinationChange={point => setMission(current => ({ ...current, destination: point }))}
        onAddVertex={point => setDraftVertices(current => [...current, point])}
        onSelectVertex={(zoneId, index) => { setSelectedVertex({ zoneId, index }); setMapMode('navigate') }}
        onMoveVertex={(zoneId, index, point) => setMission(current => ({
          ...current,
          survey_zones: current.survey_zones.map(zone => zone.id === zoneId
            ? { ...zone, vertices: zone.vertices.map((vertex, vertexIndex) => vertexIndex === index ? point : vertex) }
            : zone),
        }))}
        onMoveLocation={(location, point) => setMission(current => ({
          ...current,
          [location === 'start' ? 'start_location' : 'destination']: point,
        }))}
        onAircraftLocationChange={setAircraftLocation}
        onAddAircraftWaypoint={addAircraftWaypoint}
      />
      <div className="mission-location-summary">
        <span>START <b>{formatCoordinate(mission.start_location)}</b>{mission.start_location && <button className="button-quiet" type="button" onClick={() => setMission(current => ({ ...current, start_location: null }))}>CLEAR START</button>}</span>
        <span>DESTINATION <b>{formatCoordinate(mission.destination)}</b>{mission.destination && <button className="button-quiet" type="button" onClick={() => setMission(current => ({ ...current, destination: null }))}>CLEAR DESTINATION</button>}</span>
        <span>COORDINATE REFERENCE <b>WGS84 · degrees</b></span>
      </div>
    </section>

    <div className="geographic-planner-columns">
      <section className="panel geographic-planner-card">
        <div className="panel-heading"><div><span className="eyebrow">MISSION CONFIGURATION</span><h3>Mission details</h3></div></div>
        <label className="planner-field">MISSION OBJECTIVE<select value={mission.objective} onChange={event => setObjective(event.target.value as GeographicMissionObjective)}>{OBJECTIVES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        <div className="planner-section-heading"><b>SURVEY ZONES</b><small>Drawn boundaries are geographic planning data.</small></div>
        {!mission.survey_zones.length ? <p className="planner-empty">No survey zones have been drawn.</p> : mission.survey_zones.map(zone => <article className="planner-zone-card" key={zone.id}>
          <div><b>{zone.name}</b><small>{zone.vertices.length} vertices</small></div>
          <button type="button" className="button-quiet" onClick={() => setMission(current => ({
            ...current,
            survey_zones: current.survey_zones.filter(item => item.id !== zone.id),
            uav_assignments: current.uav_assignments.map(item => ({
              ...item,
              zone_id: item.zone_id === zone.id ? null : item.zone_id,
              zone_ids: (item.zone_ids ?? []).filter(zoneId => zoneId !== zone.id),
            })),
          }))}><Trash2 size={14}/> DELETE ZONE</button>
          {selectedVertex?.zoneId === zone.id && <button type="button" className="button-quiet" onClick={removeSelectedVertex}><Trash2 size={14}/> REMOVE SELECTED VERTEX</button>}
        </article>)}
        <div className="planner-section-heading"><b>INDIVIDUAL AIRCRAFT ASSIGNMENTS</b><small>Only current PX4 IDs from the authenticated backend are listed.</small></div>
        {fleetError && <p className="planner-fleet-error" role="alert">{fleetError}</p>}
        {vehicles.filter(vehicle => !isAvailable(vehicle)).map(vehicle => <div className="planner-unavailable-aircraft" key={vehicle.id}><Radio size={13}/><span><b>{vehicle.id}</b> · {vehicle.status}{vehicle.position?.valid !== true ? ' · no valid position' : ''}</span></div>)}
        {mission.uav_assignments.map((assignment, index) => {
          const vehicle = vehicles.find(item => item.id === assignment.uav_id)
          const assignedElsewhere = new Set(
            mission.uav_assignments.filter((_, itemIndex) => itemIndex !== index).map(item => item.uav_id),
          )
          return <article className={`planner-assignment ${vehicle && isAvailable(vehicle) ? '' : 'unavailable'}`} key={`${assignment.uav_id}-${index}`}>
                <div className="planner-assignment-heading"><b>{assignment.uav_id}</b><span>{vehicle?.status ?? 'not reporting'}</span><button aria-label={`Remove ${assignment.uav_id} assignment`} type="button" className="button-quiet" onClick={() => {
                  setMission(current => ({ ...current, uav_assignments: current.uav_assignments.filter((_, itemIndex) => itemIndex !== index) }))
                  if (activeAssignmentId === assignment.uav_id) setActiveAssignmentId(mission.uav_assignments.find(item => item.uav_id !== assignment.uav_id)?.uav_id ?? '')
                  setMapMode('navigate')
                }}><Trash2 size={14}/></button></div>
                <label>AIRCRAFT<select value={assignment.uav_id} onChange={event => changeAssignment(index, { uav_id: event.target.value })}>
                  {(!vehicle || !isAvailable(vehicle)) && <option value={assignment.uav_id} disabled>{assignment.uav_id} · unavailable</option>}
                  {availableVehicles.filter(item => !assignedElsewhere.has(item.id)).map(item => <option key={item.id} value={item.id}>{item.id} · connected</option>)}
                </select></label>
                <label>ROLE<select value={assignment.role} onChange={event => changeAssignment(index, { role: event.target.value as GeographicMissionObjective })}>{OBJECTIVES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            <label>TASK<input value={assignment.task} maxLength={300} onChange={event => changeAssignment(index, { task: event.target.value })} placeholder="Describe this aircraft's task"/></label>
            <label>ASSIGNED SURVEY ZONES<select multiple aria-label={`${assignment.uav_id} assigned survey zones`} value={assignment.zone_ids?.length ? assignment.zone_ids : (assignment.zone_id ? [assignment.zone_id] : [])} onChange={event => changeAssignment(index, { zone_id: null, zone_ids: Array.from(event.target.selectedOptions, option => option.value) })}>{mission.survey_zones.map(zone => <option key={zone.id} value={zone.id}>{zone.name}</option>)}</select><small>Select one or more zones; assignments remain advisory.</small></label>
            <div className="planner-section-heading"><b>INDIVIDUAL GEOGRAPHY</b><small>WGS84 points override the shared mission endpoints.</small></div>
            <div className="mission-location-summary">
              <span>START <b>{assignment.start_location ? formatCoordinate(assignment.start_location) : `Inherit: ${formatCoordinate(mission.start_location)}`}</b></span>
              <span>DESTINATION <b>{assignment.destination ? formatCoordinate(assignment.destination) : `Inherit: ${formatCoordinate(mission.destination)}`}</b></span>
              <span>WAYPOINTS <b>{assignment.waypoints?.length ?? 0}</b></span>
            </div>
            <div className="planner-assignment-route-actions">
              <button type="button" className="button-secondary" onClick={() => { setActiveAssignmentId(assignment.uav_id); setMapMode('set_aircraft_start') }}>MAP START FOR {assignment.uav_id}</button>
              <button type="button" className="button-secondary" onClick={() => { setActiveAssignmentId(assignment.uav_id); setMapMode('set_aircraft_destination') }}>MAP DESTINATION FOR {assignment.uav_id}</button>
              <button type="button" className="button-secondary" onClick={() => { setActiveAssignmentId(assignment.uav_id); setMapMode('add_aircraft_waypoint') }}>ADD WAYPOINT FOR {assignment.uav_id}</button>
              {assignment.start_location && <button type="button" className="button-quiet" onClick={() => changeAssignment(index, { start_location: null })}>CLEAR AIRCRAFT START</button>}
              {assignment.destination && <button type="button" className="button-quiet" onClick={() => changeAssignment(index, { destination: null })}>CLEAR AIRCRAFT DESTINATION</button>}
            </div>
            {(assignment.waypoints ?? []).map((point, waypointIndex) => <div className="mission-location-summary" key={`${assignment.uav_id}-waypoint-${waypointIndex}`}>
              <span>WAYPOINT {waypointIndex + 1} <b>{formatCoordinate(point)}</b></span>
              <button type="button" className="button-quiet" onClick={() => changeAssignment(index, { waypoints: (assignment.waypoints ?? []).filter((_, itemIndex) => itemIndex !== waypointIndex) })}>REMOVE</button>
            </div>)}
            {vehicle?.battery?.remaining_percent != null && <small className="planner-aircraft-info">Backend-reported battery: {vehicle.battery.remaining_percent}% · telemetry age {vehicle.telemetry_age_seconds.toFixed(1)} s</small>}
          </article>
        })}
        <button className="button-secondary" type="button" onClick={addAssignment} disabled={!availableVehicles.some(vehicle => !mission.uav_assignments.some(item => item.uav_id === vehicle.id))}><Plus size={14}/> ADD AVAILABLE AIRCRAFT</button>
        {fleetError && <div className="assistant-callout unavailable"><AlertTriangle size={15}/><div><b>Fleet data is unavailable.</b><span>Mission assignments cannot be validated until authenticated PX4 telemetry is available.</span></div></div>}
        <label className="planner-field planner-constraints">MISSION CONSTRAINTS<textarea rows={3} value={mission.constraints.join('\n')} onChange={event => setMission(current => ({ ...current, constraints: event.target.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean) }))} placeholder="One constraint per line"/></label>
      </section>

      <section className="panel geographic-planner-card">
        <div className="panel-heading"><div><span className="eyebrow">AI-ASSISTED · OPERATOR REVIEW REQUIRED</span><h3>Mission recommendations</h3></div><span className={`assistant-status ${aiStatus?.available ? 'available' : ''}`}><i/>{aiStatus?.available ? 'AI CONFIGURED' : 'AI UNAVAILABLE'}</span></div>
        <p className="planner-ai-note">The configured backend provider receives your description, zone names, mission objective, and available aircraft IDs only. Map coordinates are not sent. AI suggestions are advisory and cannot create or execute a mission.</p>
        {!signedIn ? <div className="assistant-callout"><AlertTriangle size={15}/><div><b>Sign in to use AI planning.</b></div></div> : <>
         {conversation.messages.length > 0 && <div className="planner-ai-proposal" role="log" aria-label="Geographic planning conversation" aria-live="polite">
           {conversation.messages.map((item, index) => <p key={`${index}-${item.role}`}><b>{item.role === 'user' ? 'YOU' : 'ASSISTANT'}:</b> {item.content}</p>)}
         </div>}
         <form className="planner-ai-form" onSubmit={event => void askAI(event)}>
           <label>{aiProposal?.missing_information.length ? 'ANSWER THE FOLLOW-UP QUESTION' : 'MISSION DESCRIPTION'}<textarea aria-label={aiProposal?.missing_information.length ? 'Answer the follow-up question' : 'MISSION DESCRIPTION'} value={description} onChange={event => setDescription(event.target.value)} rows={4} maxLength={2000} placeholder="Describe the requested survey, inspection, or observation tasks without entering coordinates." disabled={!aiStatus?.available || busy}/></label>
           <div className="planner-ai-actions">
             <button type="submit" className="button-primary" disabled={!aiStatus?.available || busy || conversationAtTurnLimit || !description.trim()}>{busy ? <LoaderCircle className="spin" size={14}/> : <Sparkles size={14}/>} {conversation.messages.length ? 'CONTINUE CONVERSATION' : 'REQUEST RECOMMENDATIONS'}</button>
             {conversation.messages.length > 0 && <button type="button" className="button-secondary" onClick={startNewConversation} disabled={busy}>NEW CONVERSATION</button>}
           </div>
           {conversationAtTurnLimit && <small role="status">This conversation reached its eight-turn limit. Start a new conversation to continue.</small>}
         </form>
          {!aiStatus?.available && <div className="assistant-callout unavailable"><AlertTriangle size={15}/><div><b>{aiStatus?.reason ?? 'AI availability has not been confirmed.'}</b><span>Manual map planning remains available. No hardcoded AI response is used.</span></div></div>}
        </>}
        {aiProposal && <div className="planner-ai-proposal">
          <h4>ADVISORY PROPOSAL</h4>
          <p>{aiProposal.summary}</p>
          {aiProposal.assignments.map((assignment, index) => <p key={`${assignment.uav_id}-${index}`}><b>{assignment.uav_id} · {assignment.role}</b> — {assignment.task}{assignment.zone_id ? ` · ${mission.survey_zones.find(zone => zone.id === assignment.zone_id)?.name ?? assignment.zone_id}` : ''}</p>)}
          {[...aiProposal.missing_information, ...aiProposal.constraints, ...aiProposal.warnings].map((item, index) => <p className="planner-ai-warning" key={`${index}-${item}`}>{item}</p>)}
          <button className="button-secondary" type="button" onClick={applyRecommendations} disabled={Boolean(aiProposal.missing_information.length)}>APPLY TO EDITABLE DRAFT FOR REVIEW</button>
        </div>}
      </section>
    </div>

    <section className="panel geographic-planner-card planner-preview">
      <div className="panel-heading"><div><span className="eyebrow">MISSION PREVIEW</span><h3>{title.trim() || 'Untitled geographic mission'}</h3></div><span className="planner-draft-label">PROPOSED · NOT FLIGHT-VALIDATED</span></div>
      <p><b>Objective:</b> {OBJECTIVES.find(item => item.value === mission.objective)?.label}</p>
      <p><b>Aircraft:</b> {mission.uav_assignments.length ? mission.uav_assignments.map(item => `${item.uav_id} (${item.role})`).join(' · ') : 'None assigned'}</p>
      <p><b>Survey zones:</b> {mission.survey_zones.length ? mission.survey_zones.map(item => item.name).join(' · ') : 'None drawn'}</p>
      <p><b>Route limitation:</b> Dashed map lines are illustrative direct connections, not validated routes or flight-feasibility results.</p>
      {aiProposal && <div className="planner-ai-review">
        <b>AI ADVISORY · REVIEW ONLY</b><span>{aiProposal.summary}</span>
        {[...aiProposal.missing_information, ...aiProposal.warnings].map((item, index) => <small key={`${index}-${item}`}>{item}</small>)}
      </div>}
      <div className="planner-validation" aria-live="polite">
        <b>{validationIssues.length ? `${validationIssues.length} VALIDATION ISSUE${validationIssues.length === 1 ? '' : 'S'}` : 'GEOGRAPHIC INPUTS VALIDATED'}</b>
        {validationIssues.length > 0 && <ul>{validationIssues.map(item => <li key={item}>{item}</li>)}</ul>}
        {!validationIssues.length && <span>Coordinate ranges, boundary geometry, assignment availability, zone references, and required fields passed local checks.</span>}
      </div>
      {selectedScenario && latest && <div className="scenario-library-current">
        <span>SAVED MISSION</span><b>{selectedScenario.title} · v{latest.version_number}</b>
        <span className={`scenario-approval-state ${approvalStatus?.toLowerCase() ?? 'draft'}`}>{approvalStatus ?? 'DRAFT'}</span>
        <small>SHA-256 {latest.config_hash}</small>
        {approvalStatus === 'APPROVED' && <small>Approved intent does not authorize or start a geographic flight.</small>}
      </div>}
      {loading && <p className="planner-empty">Loading saved missions and live aircraft records…</p>}
      <div className="planner-persistence-controls">
        <label>SAVED SCENARIO<select aria-label="Saved geographic mission" value={scenarioId} onChange={event => selectScenario(event.target.value)}><option value="">NEW SCENARIO DRAFT</option>{scenarios.map(item => <option key={item.id} value={item.id}>{item.title} · v{item.latest_version.version_number}</option>)}</select></label>
        <label>MISSION NAME<input value={title} onChange={event => setTitle(event.target.value)} maxLength={120} placeholder="Name this geographic mission"/></label>
        <button className="button-primary" type="button" onClick={() => void saveMission()} disabled={!canManage || busy || !title.trim() || validationIssues.length > 0}><Save size={14}/> {scenarioId ? 'SAVE NEW VERSION' : 'SAVE MISSION DRAFT'}</button>
        {selectedScenario && latest && <button className="button-secondary" type="button" onClick={() => void submitForApproval()} disabled={!canManage || busy || !latest.id || approvalStatus === 'PENDING' || approvalStatus === 'APPROVED'}><Send size={14}/> SUBMIT FOR GENERAL APPROVAL</button>}
        {approvalStatus === 'PENDING' && <span className="planner-pending">Pending General review</span>}
        {!canManage && <span className="permission-note">Your account cannot create or edit mission drafts.</span>}
      </div>
    </section>
    {children}
  </div>
}
