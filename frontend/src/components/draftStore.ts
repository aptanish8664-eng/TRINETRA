/**
 * Unsaved Scenario Lab drafts survive a page refresh. They live only in this browser's localStorage, one entry per signed-in user and
 * scenario (or "new"), and are removed when the draft is saved, discarded, or the user signs out. Nothing here is sent to the server.
 */
import type { MissionPlan } from './missionPlan'

export interface StoredDraft { title: string; plan: MissionPlan; baseVersionId: string | null; savedAt: number }

const PREFIX = 'trinetra-unsaved-draft:'
export const draftKey = (userId: string, scenarioId: string | null | undefined) => `${PREFIX}${userId}:${scenarioId || 'new'}`

export function readDraft(key: string): StoredDraft | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const value = JSON.parse(raw) as StoredDraft
    return value && typeof value.title === 'string' && value.plan && typeof value.plan === 'object' ? value : null
  } catch { return null }
}

export function writeDraft(key: string, draft: StoredDraft): void {
  try { localStorage.setItem(key, JSON.stringify(draft)) } catch { /* storage may be full or blocked: the draft is simply not kept */ }
}

export function clearDraft(key: string): void {
  try { localStorage.removeItem(key) } catch { /* ignore */ }
}

/** Removes every stored draft of one user (sign-out) or of everyone on this browser when `userId` is omitted. */
export function clearAllDrafts(userId?: string): void {
  try {
    const prefix = userId ? `${PREFIX}${userId}:` : PREFIX
    for (let index = localStorage.length - 1; index >= 0; index -= 1) {
      const key = localStorage.key(index)
      if (key?.startsWith(prefix)) localStorage.removeItem(key)
    }
  } catch { /* ignore */ }
}
