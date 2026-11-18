import { useRef } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DECODE_AHEAD, DECODE_BEHIND, FrameScrubber, LoopingVideo, SNOW_FLAKES, SnowFall, frameUrl, pickVariant, posterScene, CROSSFADE_S } from './VideoBackdrop'
import { ELEVATION_BOTTOM_M, ELEVATION_CAPTION, ELEVATION_TOP_M, elevationAt, elevationLabel, formatElevation } from './elevation'
import { MAX_RATE_PER_SECOND, makeProgress, useDescent } from './useDescent'
import { CommandPalette, filterItems, type PaletteItem } from '../components/CommandPalette'
import { ToastHost, clearToasts, toast } from '../components/Toasts'
import { formatDate, formatDateTime, formatTime } from '../services/format'

const apiMock = vi.hoisted(() => ({ scenarioList: vi.fn() }))
vi.mock('../services/api', () => ({ api: apiMock }))

function matchMediaStub(reduced: boolean) {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query.includes('reduced-motion') ? reduced : false, addEventListener: () => undefined, removeEventListener: () => undefined })))
}
beforeEach(() => { localStorage.clear(); matchMediaStub(false) })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); clearToasts(); localStorage.clear() })

const playSpy = () => { const play = vi.fn(() => Promise.resolve()); const pause = vi.fn(); Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value: play }); Object.defineProperty(HTMLMediaElement.prototype, 'pause', { configurable: true, value: pause }); return { play, pause } }

describe('Login footage (self-playing loop, automatic fallbacks, no toggle)', () => {
  beforeEach(() => { Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1914 }) })

  it('plays two muted inline videos for the loop crossfade, with the poster underneath, preload metadata and no on/off button', () => {
    const { play } = playSpy()
    render(<LoopingVideo/>)
    const root = screen.getByTestId('video-backdrop')
    expect(root.getAttribute('data-mode')).toBe('video')
    const videos = root.querySelectorAll('video')
    expect(videos).toHaveLength(2)
    videos.forEach(video => { expect(video.muted).toBe(true); expect(video.getAttribute('preload')).toBe('metadata'); expect(video.hasAttribute('playsinline')).toBe(true) })
    expect(root.querySelector('img.video-poster')?.getAttribute('src')).toBe('/media/poster-summit.jpg')
    expect(root.querySelector('.video-vignette')).not.toBeNull()
    expect(root.querySelector('.video-shade')).toBeNull()           // no full-screen overlay on the sign-in page
    expect(screen.queryByRole('button')).toBeNull()                 // the Lite visuals toggle is gone
    expect(play).toHaveBeenCalled()
    expect(CROSSFADE_S).toBeGreaterThan(0.5)
  })

  it('reduced motion, data-saver and a narrow screen each fall back to the poster', () => {
    playSpy()
    matchMediaStub(true)
    const { unmount } = render(<LoopingVideo/>)
    expect(screen.getByTestId('video-backdrop').getAttribute('data-mode')).toBe('poster')
    expect(screen.getByTestId('video-backdrop').querySelectorAll('video')).toHaveLength(0)
    unmount()
    matchMediaStub(false)
    Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } })
    const second = render(<LoopingVideo/>)
    expect(screen.getByTestId('video-backdrop').getAttribute('data-mode')).toBe('poster')
    second.unmount()
    Object.defineProperty(navigator, 'connection', { configurable: true, value: undefined })
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 600 })
    render(<LoopingVideo/>)
    expect(screen.getByTestId('video-backdrop').getAttribute('data-mode')).toBe('poster')
  })

  it('shows the poster when the browser refuses autoplay, and pauses while the tab is hidden', async () => {
    const { play } = playSpy()
    play.mockImplementationOnce(() => Promise.reject(new Error('NotAllowedError')))
    render(<LoopingVideo/>)
    await act(async () => { await Promise.resolve() })
    expect(screen.getByTestId('video-backdrop').getAttribute('data-mode')).toBe('poster-blocked')
    cleanup()
    playSpy()
    render(<LoopingVideo/>)
    expect(screen.getByTestId('video-backdrop').getAttribute('data-playing')).toBe('true')
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(screen.getByTestId('video-backdrop').getAttribute('data-playing')).toBe('false')
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
  })

  it('720p is the default; 1080p only on a very large high-density screen', () => {
    expect(pickVariant(1914, 1)).toBe(720)
    expect(pickVariant(1440, 2)).toBe(720)
    expect(pickVariant(2560, 1.5)).toBe(1080)
  })
})

describe('Overview elevation readout', () => {
  it('runs from the named top value to the named bottom value, never reads 0 m, rounds to 10 m and uses thousands separators', () => {
    expect(ELEVATION_TOP_M).toBe(5400)
    expect(ELEVATION_BOTTOM_M).toBe(2300)
    expect(elevationAt(0)).toBe(ELEVATION_TOP_M)
    expect(elevationAt(1)).toBe(ELEVATION_BOTTOM_M)
    expect(elevationLabel(0)).toBe('5,400 m')
    expect(elevationLabel(1)).toBe('2,300 m')
    expect(formatElevation(4875)).toBe('4,875')
    let previous = Infinity
    for (let i = 0; i <= 1000; i++) {
      const metres = elevationAt(i / 1000)
      expect(metres).toBeGreaterThan(0)
      expect(metres % 10).toBe(0)
      expect(metres).toBeLessThanOrEqual(previous)
      previous = metres
    }
    // out-of-range or broken progress values are clamped, never 0
    expect(elevationAt(-4)).toBe(ELEVATION_TOP_M)
    expect(elevationAt(9)).toBe(ELEVATION_BOTTOM_M)
    expect(elevationAt(Number.NaN)).toBe(ELEVATION_TOP_M)
    expect(ELEVATION_CAPTION).toBe('scenic elevation, not live telemetry')
  })
})

describe('Overview frame sequence (canvas scrubbing with poster fallback)', () => {
  const manifest = { count: 10, fps: 8, width: 1280, height: 720, pattern: 'f%04d.webp', first: 1 }
  const requested: string[] = []
  let draws: Array<[number]> = []
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1914 })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 826 })
    requested.length = 0
    draws = []
    class FakeImage {
      onload: null | (() => void) = null; onerror: null | (() => void) = null; decoding = ''; naturalWidth = 1280; naturalHeight = 720
      set src(value: string) { requested.push(value); queueMicrotask(() => this.onload?.()) }
    }
    vi.stubGlobal('Image', FakeImage)
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(manifest) })))
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => ({ drawImage: vi.fn(), set globalAlpha(value: number) { draws.push([value]) }, get globalAlpha() { return 1 } })) as never)
  })

  it('shows the poster first, then draws frames starting with the first one, and crossfades between neighbours', async () => {
    const progress = makeProgress()
    render(<FrameScrubber progress={progress}/>)
    const root = screen.getByTestId('video-backdrop')
    expect(root.querySelector('img.video-poster')?.getAttribute('src')).toBe('/media/poster-summit.jpg')
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(requested[0]).toBe('/media/overview-frames/f0001.webp')           // the first frames come first
    expect(requested).toHaveLength(manifest.count)                           // every frame is eventually requested
    expect(root.getAttribute('data-mode')).toBe('frames')
    draws.length = 0
    act(() => { progress.value = 0.5; progress.listeners.forEach(listener => listener(0.5)) })
    const alphas = draws.map(entry => entry[0])
    expect(alphas).toContain(1)                                              // the lower frame at full opacity…
    expect(alphas.some(value => value > 0 && value < 1)).toBe(true)          // …and the next one blended in
  })

  it('falls back to a poster per section when the frames cannot be loaded, or with reduced motion', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))))
    const progress = makeProgress()
    const { unmount } = render(<FrameScrubber progress={progress}/>)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(screen.getByTestId('video-backdrop').getAttribute('data-mode')).toBe('poster-failed')
    act(() => { progress.listeners.forEach(listener => listener(0.9)) })
    expect(screen.getByTestId('video-backdrop').getAttribute('data-scene')).toBe('valley')
    expect(screen.getByTestId('video-backdrop').querySelector('canvas')).toBeNull()
    unmount()
    matchMediaStub(true)
    render(<FrameScrubber progress={makeProgress()}/>)
    expect(screen.getByTestId('video-backdrop').getAttribute('data-mode')).toBe('poster')
    expect(screen.getByTestId('video-backdrop').querySelector('canvas')).toBeNull()
  })

  it('maps scroll progress to a still scene and builds numbered frame addresses', () => {
    expect(posterScene(0)).toBe('summit')
    expect(posterScene(0.5)).toBe('slope')
    expect(posterScene(1)).toBe('valley')
    expect(frameUrl(manifest, 0)).toBe('/media/overview-frames/f0001.webp')
    expect(frameUrl(manifest, 9)).toBe('/media/overview-frames/f0010.webp')
  })

  it('snow flakes are plain elements moved by CSS (no canvas, no script per frame), only while motion is allowed', () => {
    const target = { current: document.createElement('section') }
    const { container } = render(<SnowFall target={target}/>)
    expect(container.querySelectorAll('.snow-fall i')).toHaveLength(SNOW_FLAKES)
    expect(container.querySelector('canvas')).toBeNull()
    cleanup()
    matchMediaStub(true)
    const reduced = render(<SnowFall target={target}/>)
    expect(reduced.container.querySelectorAll('.snow-fall i')).toHaveLength(0)
  })

  it('redraws the canvas only when the frame pair or the blend changes, keeps a window of decoded bitmaps, and hides the backdrop once the UAV section covers it', async () => {
    const closed: number[] = []
    let bitmapCount = 0
    vi.stubGlobal('createImageBitmap', vi.fn(() => { const id = ++bitmapCount; return Promise.resolve({ width: 1280, height: 720, close: () => closed.push(id) }) }))
    vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(String(url).endsWith('manifest.json') ? { ok: true, json: () => Promise.resolve(manifest) } : { ok: true, blob: () => Promise.resolve(new Blob(['x'])) })))
    const progress = makeProgress()
    render(<FrameScrubber progress={progress}/>)
    await act(async () => { for (let i = 0; i < 8; i++) await Promise.resolve() })
    const root = screen.getByTestId('video-backdrop')
    expect(root.getAttribute('data-mode')).toBe('frames')
    draws.length = 0
    act(() => { progress.value = 0.5; progress.listeners.forEach(listener => listener(0.5)) })
    const first = draws.length
    expect(first).toBeGreaterThan(0)
    act(() => { progress.listeners.forEach(listener => listener(0.5)) })            // same position again: nothing visible changes
    act(() => { progress.value = 0.50001; progress.listeners.forEach(listener => listener(0.50001)) })   // far below one blend step
    expect(draws.length).toBe(first)
    expect((createImageBitmap as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBeLessThanOrEqual(DECODE_BEHIND + DECODE_AHEAD + 1 + 4)   // only the window is decoded, never all frames
    expect(root.style.visibility).not.toBe('hidden')
    act(() => { progress.value = 1; progress.listeners.forEach(listener => listener(1)) })
    expect(root.style.visibility).toBe('hidden')
    act(() => { progress.value = 0.4; progress.listeners.forEach(listener => listener(0.4)) })
    expect(root.style.visibility).toBe('visible')
  })
})

describe('Overview scroll progress (no layout reads or custom properties while scrolling)', () => {
  function Harness({ progress }: { progress: ReturnType<typeof makeProgress> }) {
    const root = useRef<HTMLDivElement>(null)
    const readout = useRef<HTMLSpanElement>(null)
    useDescent(root, readout, progress)
    return <div ref={root} className="overview-page"><div className="altitude-readout"><span ref={readout}/><span className="elevation-bar"><i/></span></div><div className="flight-path"/></div>
  }
  it('eases towards the scroll position with a capped step, writes the text only when it changes, and never sets a CSS custom property', () => {
    const frames: Array<(now: number) => void> = []
    vi.stubGlobal('requestAnimationFrame', (callback: (now: number) => void) => { frames.push(callback); return frames.length })
    vi.stubGlobal('cancelAnimationFrame', () => undefined)
    const setProperty = vi.spyOn(CSSStyleDeclaration.prototype, 'setProperty')
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 })
    Object.defineProperty(window, 'scrollY', { configurable: true, writable: true, value: 0 })
    const progress = makeProgress()
    const { container } = render(<Harness progress={progress}/>)
    const root = container.querySelector('.overview-page') as HTMLElement
    Object.defineProperty(root, 'offsetHeight', { configurable: true, value: 8800 })     // 8000 px of scroll range
    root.getBoundingClientRect = () => ({ top: 0, left: 0, right: 0, bottom: 8800, width: 0, height: 8800, x: 0, y: 0, toJSON: () => ({}) })
    act(() => { window.dispatchEvent(new Event('resize')); frames.splice(0).forEach(callback => callback(16)) })
    const rects = vi.spyOn(root, 'getBoundingClientRect')
    ;(window as unknown as { scrollY: number }).scrollY = 8000          // a sudden jump to the very end
    act(() => { window.dispatchEvent(new Event('scroll')) })
    expect(rects).not.toHaveBeenCalled()                               // the scroll handler reads no layout
    const readout = container.querySelector('.altitude-readout span') as HTMLElement
    const texts = new Set<string>()
    let previous = progress.value
    let now = 16
    for (let i = 0; i < 300 && frames.length; i++) {
      now += 16.7
      act(() => { frames.splice(0).forEach(callback => callback(now)) })
      expect(progress.value - previous).toBeLessThanOrEqual(MAX_RATE_PER_SECOND * 0.0167 + 1e-9)   // never jumps
      previous = progress.value
      texts.add(readout.textContent ?? '')
    }
    expect(progress.value).toBeCloseTo(1, 3)
    expect(readout.textContent).toBe('2,300 m')
    expect(texts.size).toBeGreaterThan(5)
    expect(setProperty.mock.calls.filter(([name]) => String(name).startsWith('--progress') || String(name).startsWith('--descent'))).toHaveLength(0)
    expect((container.querySelector('.elevation-bar i') as HTMLElement).style.transform).toBe('scaleX(1.0000)')
  })
})

describe('British date and time text', () => {
  it('formats day first with a three-letter month and a 24-hour clock', () => {
    const date = new Date(2026, 10, 18, 14, 5, 9)
    expect(formatDate(date)).toBe('18 NOV 2026')
    expect(formatTime(date)).toBe('14:05')
    expect(formatTime(date, true)).toBe('14:05:09')
    expect(formatDateTime(date)).toBe('18 NOV 2026 14:05')
    expect(formatDateTime('not a date')).toBe('—')
  })
})

describe('Ctrl+K palette', () => {
  beforeEach(() => apiMock.scenarioList.mockResolvedValue([{ id: 's1', title: 'Coastal survey' }, { id: 's2', title: 'Hill patrol' }]))
  const pages = ['Overview', 'Mission Control', 'Quick Mission']

  it('opens on Ctrl+K, filters pages and saved missions by name, and only navigates', async () => {
    const onNavigate = vi.fn(), onOpenMission = vi.fn()
    render(<CommandPalette pages={pages} onNavigate={onNavigate} onOpenMission={onOpenMission}/>)
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    const box = await screen.findByRole('combobox')
    expect(await screen.findByText('Coastal survey')).toBeTruthy()
    fireEvent.change(box, { target: { value: 'coast' } })
    expect(screen.queryByText('Hill patrol')).toBeNull()
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(onOpenMission).toHaveBeenCalledWith('s1')
    expect(onNavigate).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    fireEvent.change(await screen.findByRole('combobox'), { target: { value: 'quick' } })
    fireEvent.click(await screen.findByRole('option', { name: /Quick Mission/ }))
    expect(onNavigate).toHaveBeenCalledWith('Quick Mission')
  })

  it('Escape closes it and an empty result says what to do next', async () => {
    render(<CommandPalette pages={pages} onNavigate={vi.fn()} onOpenMission={vi.fn()}/>)
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    fireEvent.change(await screen.findByRole('combobox'), { target: { value: 'zzzz' } })
    expect(await screen.findByText(/No page or saved mission matches. Create one in Quick Mission/)).toBeTruthy()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('the filter puts pages before missions and needs every word', () => {
    const items: PaletteItem[] = [{ id: 'm', kind: 'mission', label: 'Mission Control drill', hint: '', run: () => undefined }, { id: 'p', kind: 'page', label: 'Mission Control', hint: '', run: () => undefined }]
    expect(filterItems(items, 'mission control').map(item => item.id)).toEqual(['p', 'm'])
    expect(filterItems(items, 'mission drill').map(item => item.id)).toEqual(['m'])
  })
})

describe('toasts', () => {
  it('shows consistent success, warning and error toasts, does not stack duplicates and dismisses', () => {
    render(<ToastHost/>)
    act(() => { toast('success', 'Saved.'); toast('success', 'Saved.'); toast('warning', 'Careful.'); toast('error', 'Failed.') })
    expect(screen.getAllByRole('status')).toHaveLength(2)
    expect(screen.getByRole('alert').textContent).toContain('Failed.')
    fireEvent.click(screen.getAllByRole('button', { name: 'Dismiss notification' })[0])
    expect(screen.queryByText('Saved.')).toBeNull()
  })
})
