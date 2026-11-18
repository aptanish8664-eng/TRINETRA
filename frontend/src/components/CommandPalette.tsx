import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../services/api'

export type PaletteItem = { id: string; kind: 'page' | 'mission'; label: string; hint: string; run: () => void }

/** Pure filter used by the palette and its tests: every word must appear in the label (case-insensitive); pages come before missions. */
export function filterItems(items: PaletteItem[], query: string): PaletteItem[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  return items.filter(item => words.every(word => item.label.toLowerCase().includes(word))).sort((a, b) => (a.kind === b.kind ? 0 : a.kind === 'page' ? -1 : 1)).slice(0, 12)
}

/** Ctrl+K quick navigation: jump to a page or open a saved mission by name. Navigation only: nothing here starts, deploys, saves or runs anything. */
export function CommandPalette({ pages, onNavigate, onOpenMission }: { pages: string[]; onNavigate: (page: string) => void; onOpenMission: (scenarioId: string) => void }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [missions, setMissions] = useState<Array<{ id: string; title: string }> | null>(null)
  const [cursor, setCursor] = useState(0)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setOpen(value => !value) }
      else if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  useEffect(() => {
    if (!open) return
    setQuery(''); setCursor(0)
    window.setTimeout(() => input.current?.focus(), 0)
    let stopped = false
    void Promise.resolve().then(() => api.scenarioList()).then(list => { if (!stopped) setMissions(list.map(item => ({ id: item.id, title: item.title }))) }).catch(() => { if (!stopped) setMissions([]) })
    return () => { stopped = true }
  }, [open])

  const items = useMemo<PaletteItem[]>(() => [
    ...pages.map(page => ({ id: `page:${page}`, kind: 'page' as const, label: page, hint: 'Go to page', run: () => onNavigate(page) })),
    ...(missions ?? []).map(item => ({ id: `mission:${item.id}`, kind: 'mission' as const, label: item.title, hint: 'Open saved mission', run: () => onOpenMission(item.id) })),
  ], [pages, missions, onNavigate, onOpenMission])
  const shown = useMemo(() => filterItems(items, query), [items, query])
  useEffect(() => setCursor(0), [query])

  if (!open) return null
  const choose = (item: PaletteItem | undefined) => { if (!item) return; setOpen(false); item.run() }
  return <div className="palette-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setOpen(false) }}>
    <div className="palette" role="dialog" aria-modal="true" aria-label="Quick navigation">
      <input ref={input} role="combobox" aria-expanded="true" aria-controls="palette-list" aria-label="Search pages and saved missions" placeholder="Go to a page or open a saved mission…" value={query}
        onChange={event => setQuery(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'ArrowDown') { event.preventDefault(); setCursor(value => Math.min(shown.length - 1, value + 1)) }
          else if (event.key === 'ArrowUp') { event.preventDefault(); setCursor(value => Math.max(0, value - 1)) }
          else if (event.key === 'Enter') { event.preventDefault(); choose(shown[cursor]) }
        }}/>
      <ul id="palette-list" role="listbox" aria-label="Results">
        {missions === null && <li className="palette-note" role="status"><span className="skeleton"/></li>}
        {shown.map((item, index) => <li key={item.id} role="option" aria-selected={index === cursor} className={index === cursor ? 'active' : ''} onMouseEnter={() => setCursor(index)} onClick={() => choose(item)}>
          <span>{item.label}</span><small>{item.hint}</small></li>)}
        {shown.length === 0 && missions !== null && <li className="palette-note">No page or saved mission matches. Create one in Quick Mission.</li>}
      </ul>
      <p className="palette-foot">Navigation only. Nothing here starts, deploys or runs anything. Esc closes.</p>
    </div>
  </div>
}
