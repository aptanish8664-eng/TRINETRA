import { useEffect, useRef, useState } from 'react'
import { useDocumentVisible, useVisuals } from './useVisuals'
import type { Progress } from './useDescent'

/** The one real clip (an FPV drone flight down a snowy ridge into a valley). Login plays it as a video; the Overview draws it as an image sequence. Files are in public/media. */
export const FOOTAGE = {
  poster: '/media/poster-summit.jpg',
  slopePoster: '/media/poster-slope.jpg',
  valleyPoster: '/media/poster-valley.jpg',
  frames: '/media/overview-frames',
  mp4: { 720: '/media/flight-720.mp4', 1080: '/media/flight-1080.mp4' },
  webm: { 720: '/media/flight-720.webm', 1080: '/media/flight-1080.webm' },
  durationHint: 24.4,
} as const

export const CROSSFADE_S = 1.2
export const NARROW_PX = 800

/** 720p is the default: the laptop also runs PX4 and Gazebo. 1080p is only chosen on a very large, high-density screen. */
export function pickVariant(width: number, ratio: number): 720 | 1080 {
  return width * ratio >= 3000 && width >= 1800 ? 1080 : 720
}

/** True when the footage may move: no reduced motion, no data-saver, and a screen wider than the narrow limit. Otherwise a still poster is shown. */
export function useVideoAllowed(): boolean {
  const { animate } = useVisuals()
  const [wide, setWide] = useState(() => typeof window === 'undefined' || window.innerWidth >= NARROW_PX)
  useEffect(() => {
    const update = () => setWide(window.innerWidth >= NARROW_PX)
    window.addEventListener('resize', update, { passive: true })
    return () => window.removeEventListener('resize', update)
  }, [])
  return animate && wide
}

function useOnScreen(ref: React.RefObject<HTMLElement | null>): boolean {
  const [on, setOn] = useState(true)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => setOn(entries.some(entry => entry.isIntersecting)), { threshold: 0.01 })
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return on
}

function Sources({ variant }: { variant: 720 | 1080 }) {
  return <><source src={FOOTAGE.webm[variant]} type="video/webm"/><source src={FOOTAGE.mp4[variant]} type="video/mp4"/></>
}

const variantNow = () => (typeof window === 'undefined' ? 720 : pickVariant(window.innerWidth, window.devicePixelRatio || 1))

/** Login: a full-screen real video, muted, looping by crossfading two copies of the clip (so the loop point never jumps). It plays by itself and does not
 * depend on scrolling. If the browser refuses to autoplay, or reduced motion / data-saver / a narrow screen applies, the poster is shown instead. */
export function LoopingVideo({ leaving = false }: { leaving?: boolean }) {
  const allowed = useVideoAllowed()
  const visible = useDocumentVisible()
  const root = useRef<HTMLDivElement>(null)
  const onScreen = useOnScreen(root)
  const a = useRef<HTMLVideoElement>(null)
  const b = useRef<HTMLVideoElement>(null)
  const [front, setFront] = useState<'a' | 'b'>('a')
  const [blocked, setBlocked] = useState(false)
  const frontRef = useRef<'a' | 'b'>('a')
  const playing = allowed && visible && onScreen && !blocked

  useEffect(() => {
    const first = a.current, second = b.current
    if (!first || !second) return
    if (!playing) { first.pause(); second.pause(); return }
    const current = () => (frontRef.current === 'a' ? first : second)
    const other = () => (frontRef.current === 'a' ? second : first)
    void current().play().catch(() => setBlocked(true))        // autoplay refused: the poster stays
    let timer = 0
    const tick = () => {
      const video = current()
      if (video.duration && video.duration - video.currentTime < CROSSFADE_S && other().paused) {
        other().currentTime = 0
        void other().play().catch(() => setBlocked(true))
        frontRef.current = frontRef.current === 'a' ? 'b' : 'a'
        setFront(frontRef.current)
        const old = frontRef.current === 'a' ? second : first
        window.setTimeout(() => old.pause(), CROSSFADE_S * 1000 + 200)        // the old copy is faded out by then
      }
    }
    timer = window.setInterval(tick, 150)
    return () => window.clearInterval(timer)
  }, [playing])

  const variant = variantNow()
  return <div ref={root} className={`video-backdrop ${leaving ? 'leaving' : ''}`} data-testid="video-backdrop" data-playing={playing ? 'true' : 'false'} data-mode={allowed ? (blocked ? 'poster-blocked' : 'video') : 'poster'} aria-hidden="true">
    <img className="video-poster" src={FOOTAGE.poster} alt="" decoding="async"/>
    {allowed && !blocked && <>
      <video ref={a} className={`video-layer ${front === 'a' ? 'front' : ''}`} muted playsInline preload="metadata" poster={FOOTAGE.poster} disablePictureInPicture><Sources variant={variant}/></video>
      <video ref={b} className={`video-layer ${front === 'b' ? 'front' : ''}`} muted playsInline preload="metadata" disablePictureInPicture><Sources variant={variant}/></video>
    </>}
    <div className="video-vignette"/>
  </div>
}

/** Overview: the whole clip as an image sequence (about 8 frames per second, 1280 px wide WebP) drawn on a canvas by the smoothed scroll progress, with a
 * crossfade between the two neighbouring frames. Frames load progressively, nearest to the reader first; the poster shows until the first frame is ready.
 * A window of frames just behind and well ahead of the reader is kept decoded as ImageBitmaps, so nothing is decoded while drawing.
 * The canvas is redrawn only when the frame pair or the (quantised) blend changes, from the same requestAnimationFrame that eases the scroll position.
 * If the manifest or the first frame cannot be loaded, the page falls back to one still poster per section. */
export type FrameManifest = { count: number; fps: number; width: number; height: number; pattern: string; first: number }

export const frameUrl = (manifest: FrameManifest, index: number) => `${FOOTAGE.frames}/${manifest.pattern.replace('%04d', String(index + manifest.first).padStart(4, '0'))}`

/** Which still to show when the frames are not used: summit, slope or valley. */
export const posterScene = (progress: number): 'summit' | 'slope' | 'valley' => (progress < 0.34 ? 'summit' : progress < 0.7 ? 'slope' : 'valley')

/** Frames kept decoded: a few behind the reader and about twenty ahead. */
export const DECODE_BEHIND = 6
export const DECODE_AHEAD = 22
/** The blend between two frames is quantised, so the canvas is not redrawn for changes nobody can see. */
export const BLEND_STEPS = 24
/** Above this progress the UAV section covers the whole screen, so the backdrop underneath is hidden and not composited. */
export const COVERED_AT = 0.9995

type Drawable = HTMLImageElement | ImageBitmap
const widthOf = (source: Drawable) => ('naturalWidth' in source ? source.naturalWidth : source.width)
const heightOf = (source: Drawable) => ('naturalHeight' in source ? source.naturalHeight : source.height)

export function FrameScrubber({ progress }: { progress: Progress }) {
  const allowed = useVideoAllowed()
  const visible = useDocumentVisible()
  const root = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  const [scene, setScene] = useState<'summit' | 'slope' | 'valley'>(() => posterScene(progress.value))
  const useFrames = allowed && !failed

  useEffect(() => {          // the still posters follow the scroll in steps (no animation); React state changes only when the scene changes
    const listener = (value: number) => setScene(previous => { const next = posterScene(value); return next === previous ? previous : next })
    progress.listeners.add(listener)
    return () => { progress.listeners.delete(listener) }
  }, [progress])

  useEffect(() => {          // the backdrop is hidden while the UAV section covers the screen
    let covered = false
    const listener = (value: number) => {
      const now = value >= COVERED_AT
      if (now !== covered && root.current) { covered = now; root.current.style.visibility = now ? 'hidden' : 'visible' }
    }
    progress.listeners.add(listener)
    return () => { progress.listeners.delete(listener) }
  }, [progress])

  useEffect(() => {
    const element = canvas.current
    if (!useFrames || !element) return
    let cancelled = false
    let manifest: FrameManifest | null = null
    let images: Array<HTMLImageElement | undefined> = []
    const bitmaps = new Map<number, ImageBitmap>()
    const decoding = new Set<number>()
    const blobs: Array<Blob | undefined> = []
    const requested = new Set<number>()
    const firstReady = { current: false }
    let inFlight = 0
    let loaded = 0
    let version = 0
    let lastKey = ''
    const context = (() => { try { return element.getContext('2d') } catch { return null } })()
    if (!context) { setFailed(true); return }
    const canBitmap = typeof createImageBitmap === 'function'

    // With ImageBitmap support only decoded bitmaps are drawn (drawing an image element decodes it on the main thread, inside the animation frame).
    const sourceAt = (index: number): Drawable | undefined => (canBitmap ? bitmaps.get(index) : images[index])
    const nearest = (index: number): Drawable | undefined => {
      if (!manifest) return undefined
      for (let distance = 0; distance < manifest.count; distance++) {
        const before = sourceAt(index - distance), after = sourceAt(index + distance)
        if (before) return before
        if (after) return after
      }
      return undefined
    }
    const cover = (source: Drawable, alpha: number) => {
      const scale = Math.max(element.width / widthOf(source), element.height / heightOf(source))
      const width = widthOf(source) * scale, height = heightOf(source) * scale
      context.globalAlpha = alpha
      context.drawImage(source, (element.width - width) / 2, (element.height - height) / 2, width, height)
    }
    /** Keep [centre - behind, centre + ahead] decoded as bitmaps and release the rest. */
    const keepWindow = (centre: number) => {
      if (!manifest || !canBitmap) return
      const low = Math.max(0, centre - DECODE_BEHIND), high = Math.min(manifest.count - 1, centre + DECODE_AHEAD)
      for (const key of [...bitmaps.keys()]) if (key < low - 4 || key > high + 4) { bitmaps.get(key)?.close(); bitmaps.delete(key) }
      for (let index = low; index <= high; index++) {
        const file = blobs[index]
        if (!file || bitmaps.has(index) || decoding.has(index)) continue
        decoding.add(index)
        createImageBitmap(file).then(bitmap => {                 // decoded off the main thread
          decoding.delete(index)
          if (cancelled || !manifest) { bitmap.close(); return }
          const now = Math.round(progress.value * (manifest.count - 1))
          if (index < now - DECODE_BEHIND - 4 || index > now + DECODE_AHEAD + 4) { bitmap.close(); return }
          bitmaps.set(index, bitmap)
          if (!firstReady.current) { firstReady.current = true; setReady(true) }
          if (Math.abs(index - now) <= 1 || bitmaps.size === 1) { version++; scheduleDraw() }
        }).catch(() => { decoding.delete(index) })
      }
    }
    let drawQueued = 0
    const scheduleDraw = () => { if (!drawQueued) drawQueued = window.requestAnimationFrame(() => { drawQueued = 0; draw() }) }
    const draw = () => {
      if (!manifest || document.hidden) return
      const position = Math.max(0, Math.min(1, progress.value)) * (manifest.count - 1)
      const lower = Math.floor(position), upper = Math.min(manifest.count - 1, lower + 1)
      const mix = Math.round((position - lower) * BLEND_STEPS) / BLEND_STEPS
      const key = `${lower}|${upper}|${mix}|${element.width}x${element.height}|${version}`
      keepWindow(Math.round(position))
      if (key === lastKey) return                      // nothing visible would change
      const a = nearest(lower)
      if (!a) return
      lastKey = key
      cover(a, 1)
      const b = upper !== lower ? nearest(upper) : undefined
      if (b && b !== a && mix > 0) cover(b, mix)
      context.globalAlpha = 1
    }
    const size = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5)
      element.width = Math.min(1280, Math.round(window.innerWidth * ratio))
      element.height = Math.max(1, Math.round(element.width * window.innerHeight / Math.max(1, window.innerWidth)))
      lastKey = ''
      draw()
    }
    const pump = () => {        // load frames progressively: the one nearest the reader first (so the first frames come first)
      if (cancelled || !manifest) return
      while (inFlight < 6) {
        const centre = Math.round(progress.value * (manifest.count - 1))
        let pick = -1
        for (let distance = 0; distance < manifest.count && pick < 0; distance++) {
          if (centre + distance < manifest.count && !requested.has(centre + distance)) pick = centre + distance
          else if (centre - distance >= 0 && !requested.has(centre - distance)) pick = centre - distance
        }
        if (pick < 0) return
        requested.add(pick)
        inFlight++
        const done = (ok: boolean) => {
          inFlight--
          if (cancelled) return
          if (ok) {
            loaded++
            if (root.current) root.current.dataset.loaded = String(loaded)
            const now = Math.round(progress.value * ((manifest?.count ?? 1) - 1))
            keepWindow(now)
            if (!canBitmap) {
              if (!firstReady.current) { firstReady.current = true; setReady(true); version++; scheduleDraw() }
              else if (Math.abs(pick - now) <= 1) { version++; scheduleDraw() }
            }
          }
          else if (!firstReady.current && requested.size <= 1) { setFailed(true); return }
          pump()
        }
        const address = frameUrl(manifest, pick)
        if (canBitmap) {
          fetch(address).then(response => (response.ok ? response.blob() : Promise.reject(new Error('frame')))).then(file => { blobs[pick] = file; done(true) }).catch(() => done(false))
        } else {
          const image = new Image()
          image.decoding = 'async'
          image.onload = () => { images[pick] = image; done(true) }
          image.onerror = () => done(false)
          image.src = address
        }
      }
    }
    fetch(`${FOOTAGE.frames}/manifest.json`).then(response => (response.ok ? response.json() : Promise.reject(new Error('manifest')))).then((data: FrameManifest) => {
      if (cancelled) return
      if (!data || !(data.count > 1)) throw new Error('manifest')
      manifest = data
      images = new Array(data.count)
      size()
      pump()
    }).catch(() => { if (!cancelled) setFailed(true) })
    progress.listeners.add(draw)                         // called from the eased rAF in useDescent: once per frame, never from the scroll handler
    window.addEventListener('resize', size, { passive: true })
    document.addEventListener('visibilitychange', draw)
    return () => {
      cancelled = true
      if (drawQueued) window.cancelAnimationFrame(drawQueued)
      progress.listeners.delete(draw)
      window.removeEventListener('resize', size); document.removeEventListener('visibilitychange', draw)
      bitmaps.forEach(bitmap => bitmap.close()); bitmaps.clear()
    }
  }, [useFrames, progress])

  const mode = !allowed ? 'poster' : failed ? 'poster-failed' : ready ? 'frames' : 'loading'
  return <div ref={root} className="video-backdrop video-fixed" data-testid="video-backdrop" data-playing={useFrames && visible && ready ? 'true' : 'false'} data-mode={mode} data-scene={scene} aria-hidden="true">
    <img className="video-poster" src={FOOTAGE.poster} alt="" decoding="async"/>
    {!useFrames || !ready ? <>
      <img className={`video-poster scene-still ${scene === 'slope' ? 'on' : ''}`} src={FOOTAGE.slopePoster} alt="" decoding="async"/>
      <img className={`video-poster scene-still ${scene === 'valley' ? 'on' : ''}`} src={FOOTAGE.valleyPoster} alt="" decoding="async"/>
    </> : null}
    {useFrames && <canvas ref={canvas} className={`frame-canvas ${ready ? 'ready' : ''}`}/>}
    <div className="video-shade overview"/>
  </div>
}

/** A few very light snow flakes drifting over the right-hand side of the UAV picture. They are plain elements moved by CSS transform animations (run on the
 * compositor, no JavaScript per frame, no canvas), only while motion is allowed, the tab is visible and the picture is on screen. A CSS mask keeps them away
 * from the logo and the text. */
export const SNOW_FLAKES = 22
export function SnowFall({ target }: { target: React.RefObject<HTMLElement | null> }) {
  const { animate } = useVisuals()
  const visible = useDocumentVisible()
  const onScreen = useOnScreen(target)
  if (!animate) return <div className="snow-fall" aria-hidden="true"/>
  const running = visible && onScreen
  return <div className={`snow-fall ${running ? 'is-running' : ''}`} aria-hidden="true">
    {Array.from({ length: SNOW_FLAKES }, (_, i) => <i key={i} style={{ left: `${(i * 37 + 11) % 100}%`, animationDuration: `${14 + (i % 7) * 2.5}s`, animationDelay: `${-((i * 1.7) % 17)}s`, width: `${2 + (i % 3)}px`, height: `${2 + (i % 3)}px`, ['--drift' as string]: `${(i % 2 ? 1 : -1) * (12 + (i % 5) * 6)}px` }}/>)}
  </div>
}
