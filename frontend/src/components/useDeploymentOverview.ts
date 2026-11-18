import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../services/api'
import type { DeploymentOverview } from '../services/auth'

/**
 * The latest deployment and the deployment the newest execution belongs to, read from one cheap endpoint.
 * Polls every `intervalMs` while `enabled` and the tab is visible (never in a hidden tab, never overlapping requests); `refresh` reads now.
 */
export function useDeploymentOverview(enabled: boolean, intervalMs = 15_000) {
  const [overview, setOverview] = useState<DeploymentOverview | null>(null)
  const [loaded, setLoaded] = useState(false)
  const inflight = useRef(false)
  const refresh = useCallback(async () => {
    if (inflight.current) return
    inflight.current = true
    try { setOverview(await api.missionDeployments()) } catch { /* keep the last answer */ } finally { inflight.current = false; setLoaded(true) }
  }, [])
  useEffect(() => {
    if (!enabled) return
    void refresh()
    const timer = window.setInterval(() => { if (typeof document === 'undefined' || !document.hidden) void refresh() }, intervalMs)
    const onVisible = () => { if (!document.hidden) void refresh() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [enabled, intervalMs, refresh])
  return { overview, loaded, refresh }
}
