import { useEffect, useState } from 'react'
import { api } from '../services/api'

type Tile = { id: string; status: string; battery: number | null; tier: string | null }

/** One small card per aircraft on the Overview: link status, battery and threat tier. Existing endpoints only (telemetry without trails and the live threat list). */
export function FleetStrip() {
  const [tiles, setTiles] = useState<Tile[] | null>(null)
  useEffect(() => {
    let stopped = false
    const read = async () => {
      if (typeof document !== 'undefined' && document.hidden) return
      try {
        const [telemetry, live] = await Promise.all([api.px4Telemetry({ trails: false }), api.researchLive().catch(() => null)])
        if (stopped) return
        const tiers = new Map((live?.aircraft ?? []).map(item => [item.aircraft_id, item.assessment?.tier ?? null]))
        setTiles(telemetry.vehicles.map(vehicle => ({ id: vehicle.id, status: vehicle.status, battery: vehicle.battery?.remaining_percent ?? null, tier: tiers.get(vehicle.id) ?? null })))
      } catch { if (!stopped) setTiles(null) }
    }
    void Promise.resolve().then(read)
    const timer = window.setInterval(() => void read(), 6000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [])
  if (tiles === null) return <section className="fleet-strip" aria-label="Fleet strip"><div className="skeleton-row" role="status" aria-label="Loading fleet">{[0, 1, 2].map(i => <span key={i} className="skeleton"/>)}</div></section>
  if (tiles.length === 0) return <section className="fleet-strip" aria-label="Fleet strip"><p className="empty-state">No aircraft are running. Start the simulator from Mission Control (Activate and verify) and they appear here.</p></section>
  return <section className="fleet-strip" aria-label="Fleet strip"><ul>{tiles.map(tile => <li key={tile.id} className={`fleet-tile status-${tile.status}`}>
    <b>{tile.id}</b><span className="fleet-status">{tile.status}</span>
    <span className="fleet-battery">{tile.battery === null ? 'battery —' : `battery ${Math.round(tile.battery)}%`}</span>
    <span className={`fleet-tier tier-${(tile.tier ?? 'none').toLowerCase()}`}>{tile.tier ? `threat ${tile.tier}` : 'no assessment'}</span></li>)}</ul></section>
}
