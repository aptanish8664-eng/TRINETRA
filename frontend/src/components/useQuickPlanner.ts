import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../services/api'
import type { Px4TelemetryVehicle } from '../types'
import { EMPTY_QUICK_INPUTS, applyInputPatch, buildQuickRequest, inputsReady, type FleetAircraft, type QuickInputs, type QuickResult, type QuickRequest, type QuickRules } from './quickMission'

const LIVE_FLEET_POLL_MS = 20_000
const PREVIEW_DELAY_MS = 300

/**
 * The state behind the Quick Mission page and the assistant: the served rules, the verified aircraft (read from the backend, never invented), the
 * user's inputs, and the backend's plan for them. Both pages and the assistant go through `buildRequest` and `api.quickMission`, so the same inputs
 * always give the same plan. Nothing here saves, deploys or starts anything.
 */
export function useQuickPlanner({ active = true, onResult }: { active?: boolean; onResult?: (result: QuickResult, inputs: QuickInputs) => void } = {}) {
  const [rules, setRules] = useState<QuickRules | null>(null)
  const [inputs, setInputsState] = useState<QuickInputs>(EMPTY_QUICK_INPUTS)
  const [result, setResult] = useState<QuickResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [liveVerified, setLiveVerified] = useState<string[]>([])
  const [vehicles, setVehicles] = useState<Px4TelemetryVehicle[]>([])
  const sequence = useRef(0)
  const onResultRef = useRef(onResult)
  onResultRef.current = onResult
  const rulesRef = useRef<QuickRules | null>(null)
  rulesRef.current = rules

  useEffect(() => {
    let alive = true
    Promise.resolve().then(() => api.planningLimits()).then(value => { if (alive && value.quick) setRules(value.quick) }).catch(() => undefined)
    return () => { alive = false }
  }, [])

  // The verified aircraft and their positions, while this page is shown.
  useEffect(() => {
    if (!active) return
    let alive = true
    const read = async () => {
      if (typeof document !== 'undefined' && document.hidden) return
      try {
        const [status, telemetry] = await Promise.all([api.fleetStatus(), api.px4Telemetry({ trails: false })])
        if (!alive) return
        setLiveVerified(status.instances.filter(item => item.verified).map(item => item.aircraft_id).sort())
        setVehicles(telemetry.vehicles)
      } catch { /* the explicit fleet checks report problems */ }
    }
    void read()
    const timer = window.setInterval(() => void read(), LIVE_FLEET_POLL_MS)
    return () => { alive = false; window.clearInterval(timer) }
  }, [active])

  const fleet: FleetAircraft[] = useMemo(() => liveVerified.map(id => {
    const vehicle = vehicles.find(item => item.id === id)
    const position = vehicle?.position?.valid && Number.isFinite(vehicle.position.latitude) && Number.isFinite(vehicle.position.longitude)
      ? { latitude: vehicle.position.latitude, longitude: vehicle.position.longitude } : null
    return { id, position }
  }), [liveVerified, vehicles])
  const fleetKey = liveVerified.join(',')

  const buildRequest = useCallback((value: QuickInputs): QuickRequest => buildQuickRequest(value, fleet, rulesRef.current), [fleet])

  const run = useCallback(async (value: QuickInputs): Promise<QuickResult | null> => {
    if (!inputsReady(value, rulesRef.current)) return null
    const current = ++sequence.current
    setBusy(true); setError('')
    try {
      const planned = await api.quickMission(buildRequest(value))
      if (current !== sequence.current) return null
      setResult(planned)
      onResultRef.current?.(planned, value)
      return planned
    } catch (cause) {
      if (current !== sequence.current) return null
      setResult(null)
      const issues = (cause as { detail?: { issues?: { message?: string }[] } } | null)?.detail?.issues
      setError(issues?.length ? issues.map(item => item.message).filter(Boolean).join(' ') : cause instanceof Error ? cause.message : 'The quick mission could not be planned.')
      return null
    } finally {
      if (current === sequence.current) setBusy(false)
    }
  }, [buildRequest])

  // A change of inputs plans again after a short pause; incomplete inputs clear the plan.
  useEffect(() => {
    if (!rules) return      // planning never waits for a running fleet
    if (!inputsReady(inputs, rules)) { sequence.current += 1; setResult(null); setError(''); setBusy(false); return }
    const timer = window.setTimeout(() => void run(inputs), PREVIEW_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [inputs, rules, fleetKey])  // eslint-disable-line react-hooks/exhaustive-deps

  const setInputs = useCallback((patch: Partial<QuickInputs>) => setInputsState(current => applyInputPatch(current, patch, rulesRef.current)), [])
  const replaceInputs = useCallback((value: QuickInputs) => setInputsState(value), [])
  const reset = useCallback(() => { sequence.current += 1; setInputsState(EMPTY_QUICK_INPUTS); setResult(null); setError(''); setBusy(false) }, [])
  return { rules, inputs, setInputs, replaceInputs, reset, result, busy, error, fleet, liveVerified, vehicles, setVehicles, buildRequest, refresh: () => run(inputs), run }
}
