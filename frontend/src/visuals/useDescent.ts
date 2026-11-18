import { useEffect, type RefObject } from 'react'
import { useDocumentVisible, useVisuals } from './useVisuals'

export const START_ALTITUDE_M = 120

/** Scroll progress (0 at the top, 1 at the bottom) for the descent: one passive scroll listener, one requestAnimationFrame per frame, and only
 * while motion is allowed and the tab is visible. It writes CSS variables on the root element and the altitude text; it never re-renders React. */
export function useDescent(root: RefObject<HTMLElement | null>, readout: RefObject<HTMLElement | null>, progressRef?: { current: number }) {
  const { animate } = useVisuals()
  const visible = useDocumentVisible()
  useEffect(() => {
    const element = root.current
    if (!element) return
    const apply = (progress: number) => {
      if (progressRef) progressRef.current = progress
      element.style.setProperty('--progress', progress.toFixed(4))
      element.style.setProperty('--descent', String(Math.round(progress * 1000)))
      element.style.setProperty('--land', String(Math.max(0, Math.min(1, (progress - 0.85) / 0.15)).toFixed(3)))
      if (readout.current) readout.current.textContent = `${Math.round(START_ALTITUDE_M * (1 - progress))} m`
    }
    if (!animate || !visible) {
      element.style.setProperty('--progress', '0'); element.style.setProperty('--descent', '0'); element.style.setProperty('--land', '1')
      if (readout.current) readout.current.textContent = `${START_ALTITUDE_M} m`
      return
    }
    let frame = 0
    const measure = () => {
      frame = 0
      const range = Math.max(1, document.documentElement.scrollHeight - window.innerHeight)
      apply(Math.max(0, Math.min(1, window.scrollY / range)))
    }
    const onScroll = () => { if (!frame) frame = window.requestAnimationFrame(measure) }
    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })
    return () => { window.removeEventListener('scroll', onScroll); window.removeEventListener('resize', onScroll); if (frame) window.cancelAnimationFrame(frame) }
  }, [animate, visible, root, readout, progressRef])
}
