import { useEffect, useRef, useState } from 'react'
import { useDocumentVisible, useVisuals } from './useVisuals'

/** The one real clip (an FPV drone flight down a snowy ridge into a valley), in two sizes and two codecs. Files are in public/media. */
export const FOOTAGE = {
  poster: '/media/poster-summit.jpg',
  valleyPoster: '/media/poster-valley.jpg',
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

/** True when the video may play: not Lite, no reduced motion, and a screen wider than the narrow limit. Otherwise the poster image is shown. */
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

/** Login: a full-screen real video, muted, looping by crossfading two copies of the clip (so the loop point never jumps). If the browser refuses
 * to autoplay, or Lite visuals / reduced motion / a narrow screen is on, the poster is shown instead. A dark overlay keeps the card readable. */
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
    <div className="video-shade"/>
  </div>
}

/** Overview: the same clip fixed behind the page; its playback position follows the scroll (a descent from the summit and clouds to the valley).
 * One video, never playing by itself: the position is set from the scroll progress inside one requestAnimationFrame loop. */
export function ScrubVideo({ progressRef }: { progressRef: { current: number } }) {
  const allowed = useVideoAllowed()
  const visible = useDocumentVisible()
  const video = useRef<HTMLVideoElement>(null)
  const [ready, setReady] = useState(false)
  const variant = variantNow()

  useEffect(() => {
    const element = video.current
    if (!element || !allowed || !visible) return
    element.pause()
    let frame = 0
    let shown = 0
    let lastSeek = 0
    const loop = (now: number) => {
      frame = window.requestAnimationFrame(loop)
      if (now - lastSeek < 70) return                       // at most about 14 seeks a second: each seek decodes from a keyframe
      const duration = element.duration
      if (!duration || !Number.isFinite(duration)) return
      const target = Math.max(0, Math.min(duration - 0.05, progressRef.current * duration))
      shown += (target - shown) * 0.12                       // ease, so a wheel notch does not jump the picture
      if (Math.abs(shown - element.currentTime) > 0.06 && !element.seeking) { element.currentTime = shown; lastSeek = now }
    }
    frame = window.requestAnimationFrame(loop)
    return () => window.cancelAnimationFrame(frame)
  }, [allowed, visible, progressRef])

  return <div className="video-backdrop video-fixed" data-testid="video-backdrop" data-playing={allowed && visible ? 'true' : 'false'} data-mode={allowed ? 'scrub' : 'poster'} aria-hidden="true">
    <img className="video-poster" src={FOOTAGE.poster} alt="" decoding="async"/>
    {allowed && <video ref={video} className={`video-layer front ${ready ? 'ready' : ''}`} muted playsInline preload="metadata" disablePictureInPicture onLoadedData={() => setReady(true)}><Sources variant={variant}/></video>}
    <div className="video-shade overview"/>
  </div>
}
