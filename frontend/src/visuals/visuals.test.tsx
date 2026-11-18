import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LoopingVideo, ScrubVideo, pickVariant, CROSSFADE_S } from './VideoBackdrop'
import { VisualsControls } from './VisualsControls'
import { reloadVisualPrefs } from './useVisuals'
import { CommandPalette, filterItems, type PaletteItem } from '../components/CommandPalette'
import { ToastHost, clearToasts, toast } from '../components/Toasts'

const apiMock = vi.hoisted(() => ({ scenarioList: vi.fn() }))
vi.mock('../services/api', () => ({ api: apiMock }))

function matchMediaStub(reduced: boolean, dark = true) {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query.includes('reduced-motion') ? reduced : query.includes('dark') ? dark : false, addEventListener: () => undefined, removeEventListener: () => undefined })))
}
beforeEach(() => { localStorage.clear(); reloadVisualPrefs(); matchMediaStub(false) })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); clearToasts(); localStorage.clear(); reloadVisualPrefs() })


describe('Lite visuals, reduced motion and narrow screens (real video backdrop)', () => {
  const playSpy = () => { const play = vi.fn(() => Promise.resolve()); const pause = vi.fn(); Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value: play }); Object.defineProperty(HTMLMediaElement.prototype, 'pause', { configurable: true, value: pause }); return { play, pause } }
  beforeEach(() => { Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1914 }) })

  it('plays two muted inline videos for the loop crossfade, with the poster underneath, preload metadata and a dark shade', () => {
    const { play } = playSpy()
    render(<LoopingVideo/>)
    const root = screen.getByTestId('video-backdrop')
    expect(root.getAttribute('data-mode')).toBe('video')
    const videos = root.querySelectorAll('video')
    expect(videos).toHaveLength(2)
    videos.forEach(video => { expect(video.muted).toBe(true); expect(video.getAttribute('preload')).toBe('metadata'); expect(video.hasAttribute('playsinline')).toBe(true) })
    expect(root.querySelector('img.video-poster')?.getAttribute('src')).toBe('/media/poster-summit.jpg')
    expect(root.querySelector('.video-shade')).not.toBeNull()
    expect(play).toHaveBeenCalled()
    expect(CROSSFADE_S).toBeGreaterThan(0.5)
  })

  it('Lite visuals shows the poster only, is remembered, and stops the video', () => {
    playSpy()
    render(<><VisualsControls/><LoopingVideo/></>)
    fireEvent.click(screen.getByRole('button', { name: /LITE VISUALS OFF/ }))
    expect(screen.getByTestId('video-backdrop').getAttribute('data-mode')).toBe('poster')
    expect(screen.getByTestId('video-backdrop').querySelectorAll('video')).toHaveLength(0)
    expect(localStorage.getItem('trinetra.lite')).toBe('on')
    act(() => reloadVisualPrefs())
    expect(screen.getByRole('button', { name: /LITE VISUALS ON/ })).toBeTruthy()
  })

  it('reduced motion and a narrow screen both fall back to the poster without changing the saved choice', () => {
    playSpy()
    matchMediaStub(true)
    const { unmount } = render(<LoopingVideo/>)
    expect(screen.getByTestId('video-backdrop').getAttribute('data-mode')).toBe('poster')
    expect(localStorage.getItem('trinetra.lite')).toBeNull()
    unmount()
    matchMediaStub(false)
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

  it('the Overview video is scrubbed: its position follows the scroll progress and it never plays by itself', async () => {
    const { play } = playSpy()
    const frames: Array<() => void> = []
    vi.stubGlobal('requestAnimationFrame', (callback: () => void) => { frames.push(callback); return frames.length })
    vi.stubGlobal('cancelAnimationFrame', () => undefined)
    const progress = { current: 0.5 }
    render(<ScrubVideo progressRef={progress}/>)
    const video = screen.getByTestId('video-backdrop').querySelector('video') as HTMLVideoElement
    Object.defineProperty(video, 'duration', { configurable: true, value: 24 })
    Object.defineProperty(video, 'seeking', { configurable: true, value: false })
    let time = 0
    Object.defineProperty(video, 'currentTime', { configurable: true, get: () => time, set: value => { time = value } })
    for (let i = 0; i < 60; i++) { const next = frames.shift(); next?.() }
    expect(time).toBeGreaterThan(10)
    expect(time).toBeLessThan(12.1)
    expect(play).not.toHaveBeenCalled()
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
