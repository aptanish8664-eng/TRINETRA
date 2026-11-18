import { useEffect, useState } from 'react'

export type ToastKind = 'success' | 'warning' | 'error'
type Toast = { id: number; kind: ToastKind; text: string }

const MAX_VISIBLE = 3
const LIFETIME_MS: Record<ToastKind, number> = { success: 4500, warning: 7000, error: 9000 }
let counter = 0
let toasts: Toast[] = []
const listeners = new Set<(items: Toast[]) => void>()
const publish = () => listeners.forEach(listener => listener(toasts))

/** Shows one consistent toast (top centre, under the header, never over the page's buttons). Errors stay longest; at most three are shown. */
export function toast(kind: ToastKind, text: string) {
  if (!text || toasts.some(item => item.text === text && item.kind === kind)) return       // the same message is not stacked twice
  const item = { id: ++counter, kind, text }
  toasts = [...toasts, item].slice(-MAX_VISIBLE)
  publish()
  window.setTimeout(() => dismiss(item.id), LIFETIME_MS[kind])
}
export function dismiss(id: number) { toasts = toasts.filter(item => item.id !== id); publish() }
export function clearToasts() { toasts = []; publish() }

export function ToastHost() {
  const [items, setItems] = useState<Toast[]>(toasts)
  useEffect(() => { listeners.add(setItems); setItems(toasts); return () => { listeners.delete(setItems) } }, [])
  if (items.length === 0) return null
  return <div className="toast-host" aria-label="Notifications">{items.map(item => <div key={item.id} className={`toast toast-${item.kind}`} role={item.kind === 'error' ? 'alert' : 'status'}>
    <span>{item.text}</span><button type="button" aria-label="Dismiss notification" onClick={() => dismiss(item.id)}>×</button></div>)}</div>
}
