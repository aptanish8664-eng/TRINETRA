/** The one place the default Cesium basemap is defined, plus the small per-browser preference around it. */
export const DEFAULT_BASEMAP = 'street' as const
export type StoredBasemap = 'offline' | 'street' | 'satellite'
export type StoredMapStyle = 'street' | 'satellite' | 'hybrid' | 'terrain3d'

const LIVE_KEY = 'trinetra.basemap'
const PLANNER_KEY = 'trinetra.planner-map-style'
const LIVE_VALUES: readonly StoredBasemap[] = ['offline', 'street', 'satellite']
const PLANNER_VALUES: readonly StoredMapStyle[] = ['street', 'satellite', 'hybrid', 'terrain3d']

function read<T extends string>(key: string, allowed: readonly T[]): T | null {
  try {
    const value = window.localStorage.getItem(key)
    return allowed.includes(value as T) ? (value as T) : null
  } catch { return null }  // storage can be blocked or unavailable; the default applies
}
function write(key: string, value: string) {
  try { window.localStorage.setItem(key, value) } catch { /* a per-viewer convenience only */ }
}

/** The stored choice if there is a valid one, otherwise the street default. */
export const readLiveBasemap = (): StoredBasemap => read(LIVE_KEY, LIVE_VALUES) ?? DEFAULT_BASEMAP
export const storeLiveBasemap = (value: StoredBasemap) => write(LIVE_KEY, value)
export const readPlannerMapStyle = (): StoredMapStyle => read(PLANNER_KEY, PLANNER_VALUES) ?? DEFAULT_BASEMAP
export const storePlannerMapStyle = (value: StoredMapStyle) => write(PLANNER_KEY, value)
