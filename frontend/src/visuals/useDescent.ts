import { useEffect, type RefObject } from 'react'
import { elevationLabel } from './elevation'
import { useDocumentVisible, useVisuals } from './useVisuals'

/** Smoothed scroll progress shared by everything on the Overview (footage frames, elevation readout, flight path). `value` is the smoothed position, 0 to 1. */
export type Progress = { value: number; raw: number; listeners: Set<(value: number) => void> }
export const makeProgress = (): Progress => ({ value: 0, raw: 0, listeners: new Set() })

/** Time constant of the smoothing: the picture eases towards the scroll position instead of following it step for step. */
export const SMOOTHING_SECONDS = 0.22
/** The eased value can never move faster than this (progress per second), so a sudden jump in scroll position cannot make the picture jump. */
export const MAX_RATE_PER_SECOND = 1.6

/** Scroll progress of the Overview itself (0 at its top, 1 when its bottom, the UAV section, fills the screen).
 *  - One passive scroll listener that only reads `scrollY` (no layout reads) and asks for one requestAnimationFrame.
 *  - The page geometry is measured on mount, on resize and when the Overview changes size, never while scrolling.
 *  - Nothing is written to CSS custom properties (they inherit, so each write re-resolves styles for the whole page). The elevation text is written only when
 *    the displayed value changes, and the progress bar and flight path are written directly on their own elements.
 *  With reduced motion or data-saver the value follows the scroll at once (no easing). */
export function useDescent(root: RefObject<HTMLElement | null>, readout: RefObject<HTMLElement | null>, progress: Progress) {
  const { animate } = useVisuals()
  const visible = useDocumentVisible()
  useEffect(() => {
    const element = root.current
    if (!element || !visible) return
    const bar = element.querySelector<HTMLElement>('.elevation-bar i')
    const path = element.querySelector<HTMLElement>('.flight-path')
    let top = 0, range = 1
    const geometry = () => {
      top = element.getBoundingClientRect().top + window.scrollY
      range = Math.max(1, element.offsetHeight - window.innerHeight)
    }
    let lastText = ''
    let lastDrawn = -1
    const apply = (value: number) => {
      progress.value = value
      const text = elevationLabel(value)
      if (readout.current && text !== lastText) { readout.current.textContent = text; lastText = text }
      if (Math.abs(value - lastDrawn) > 0.0005 || value === 0 || value === 1) {
        lastDrawn = value
        if (bar) bar.style.transform = `scaleX(${value.toFixed(4)})`
        if (path) path.style.clipPath = `inset(0 0 ${((1 - value) * 100).toFixed(2)}% 0)`
      }
      progress.listeners.forEach(listener => listener(value))
    }
    const clamp = () => Math.max(0, Math.min(1, (window.scrollY - top) / range))
    let frame = 0
    let last = 0
    const step = (now: number) => {
      frame = 0
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 1 / 60
      last = now
      const target = progress.raw
      let next = target
      if (animate && Math.abs(target - progress.value) >= 0.0002) {
        const blend = 1 - Math.exp(-dt / SMOOTHING_SECONDS)
        let delta = (target - progress.value) * blend
        const cap = MAX_RATE_PER_SECOND * dt
        delta = Math.max(-cap, Math.min(cap, delta))
        next = progress.value + delta
      }
      if (next !== progress.value) apply(next)
      if (next !== target) frame = window.requestAnimationFrame(step)
      else last = 0
    }
    const onScroll = () => { progress.raw = clamp(); if (!frame) frame = window.requestAnimationFrame(step) }
    let measureFrame = 0
    const remeasure = () => { if (measureFrame) return; measureFrame = window.requestAnimationFrame(() => { measureFrame = 0; geometry(); onScroll() }) }
    geometry()
    progress.raw = clamp()
    apply(progress.raw)
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', remeasure, { passive: true })
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(remeasure) : null
    observer?.observe(element)
    return () => {
      window.removeEventListener('scroll', onScroll); window.removeEventListener('resize', remeasure)
      observer?.disconnect()
      if (frame) window.cancelAnimationFrame(frame)
      if (measureFrame) window.cancelAnimationFrame(measureFrame)
    }
  }, [animate, visible, root, readout, progress])
}
