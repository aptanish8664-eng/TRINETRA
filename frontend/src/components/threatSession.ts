import { useSyncExternalStore } from 'react'
import type { Assessment, Tier } from './attackLabTypes'

/** Keys that identify ground truth (labels, the attack schedule). Panels refuse any data containing them. */
const FORBIDDEN = ['label', 'ground_truth', 'groundtruth', 'is_attacked', 'attack_schedule', 'attack_index', 'attack_type', 'attacks', 'spoof_schedule', 'truth']

export function findGroundTruthKeys(value: unknown, path = ''): string[] {
  const found: string[] = []
  if (Array.isArray(value)) {
    for (let index = 0; index < Math.min(value.length, 12); index += 1) found.push(...findGroundTruthKeys(value[index], `${path}[${index}]`))
  } else if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      // `label` is ground truth for datasets, but it is also the display text of a ranked action row (assessment.actions[].label).
      const displayText = key === 'label' && /\.actions\[\d+\]$/.test(path)
      if (!displayText && FORBIDDEN.some(fragment => key.toLowerCase().includes(fragment))) found.push(`${path}.${key}`)
      else found.push(...findGroundTruthKeys(item, `${path}.${key}`))
    }
  }
  return found
}

export interface SessionAircraft {
  aircraftId: string
  role: string | null
  assessment: Assessment | null
  history: number[]
  tiers: Tier[]
  battery: number | null
  reassignable: boolean
}
export interface ThreatSession {
  sourceName: string
  experimentId: string
  state: 'playing' | 'paused' | 'finished'
  updatedAt: number
  minBatteryPct: number
  aircraft: Record<string, SessionAircraft>
}

let current: ThreatSession | null = null
const listeners = new Set<() => void>()

/** A tiny shared store so the Research replay can feed the Mission Control monitor without duplicating the engine. */
export const threatSessionStore = {
  get: () => current,
  publish(session: ThreatSession | null) {
    current = session
    listeners.forEach(listener => listener())
  },
  subscribe(listener: () => void) {
    listeners.add(listener)
    return () => { listeners.delete(listener) }
  },
}

export function useThreatSession(): ThreatSession | null {
  return useSyncExternalStore(threatSessionStore.subscribe, threatSessionStore.get, threatSessionStore.get)
}

export const SESSION_STALE_MS = 30_000
export function sessionFreshness(session: ThreatSession | null, now: number): 'no_data' | 'stale' | 'ok' | 'ended' {
  if (!session) return 'no_data'
  if (session.state === 'finished') return 'ended'
  return now - session.updatedAt > SESSION_STALE_MS ? 'stale' : 'ok'
}
