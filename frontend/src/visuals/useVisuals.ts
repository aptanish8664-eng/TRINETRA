import { useEffect, useState } from 'react'

/** Automatic visual fallbacks. There is no on/off button: decorative motion is allowed unless the visitor's system asks for less
 * (prefers-reduced-motion, or the browser's data-saver mode). A narrow screen is handled where the footage is drawn (see useMotionAllowed). */
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

type NetworkInformationLike = { saveData?: boolean }
export function dataSaverOn(): boolean {
  if (typeof navigator === 'undefined') return false
  return Boolean((navigator as Navigator & { connection?: NetworkInformationLike }).connection?.saveData)
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
  const reduced = useMedia('(prefers-reduced-motion: reduce)')
  const saver = dataSaverOn()
  return {
    reduced, saver,
    /** True only when decorative motion may run: the visitor has not asked the system for reduced motion or data saving. */
    animate: !reduced && !saver,
  }
}
