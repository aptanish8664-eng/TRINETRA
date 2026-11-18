import { useEffect, useState, useSyncExternalStore } from 'react'

/** Visual preferences shared by every decorative scene: "Lite visuals" (static, no animation) and day/night. Both are remembered in the browser. */
const LITE_KEY = 'trinetra.lite'
const NIGHT_KEY = 'trinetra.night'

const read = (key: string): string | null => { try { return localStorage.getItem(key) } catch { return null } }
const write = (key: string, value: string) => { try { localStorage.setItem(key, value) } catch { /* private window */ } }

type Prefs = { lite: boolean; night: boolean | null }      // night null = follow the system theme
let prefs: Prefs = { lite: read(LITE_KEY) === 'on', night: read(NIGHT_KEY) === 'night' ? true : read(NIGHT_KEY) === 'day' ? false : null }
const listeners = new Set<() => void>()
const emit = () => listeners.forEach(listener => listener())
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }

export function setLite(value: boolean) { prefs = { ...prefs, lite: value }; write(LITE_KEY, value ? 'on' : 'off'); emit() }
export function setNight(value: boolean) { prefs = { ...prefs, night: value }; write(NIGHT_KEY, value ? 'night' : 'day'); emit() }
/** Test helper: forget the in-memory choice and read storage again. */
export function reloadVisualPrefs() { prefs = { lite: read(LITE_KEY) === 'on', night: read(NIGHT_KEY) === 'night' ? true : read(NIGHT_KEY) === 'day' ? false : null }; emit() }

const media = (query: string) => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query) : null)

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => Boolean(media(query)?.matches))
  useEffect(() => {
    const list = media(query)
    if (!list) return
    const update = () => setMatches(list.matches)
    update()
    list.addEventListener?.('change', update)
    return () => list.removeEventListener?.('change', update)
  }, [query])
  return matches
}

export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || !document.hidden)
  useEffect(() => {
    const update = () => setVisible(!document.hidden)
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])
  return visible
}

export function useVisuals() {
  const current = useSyncExternalStore(subscribe, () => prefs)
  const reduced = useMedia('(prefers-reduced-motion: reduce)')
  const systemDark = useMedia('(prefers-color-scheme: dark)')
  const night = current.night ?? systemDark
  return {
    lite: current.lite, reduced, night,
    /** True only when decorative motion may run: not Lite, and the user has not asked the system for reduced motion. */
    animate: !current.lite && !reduced,
    setLite, setNight,
  }
}
