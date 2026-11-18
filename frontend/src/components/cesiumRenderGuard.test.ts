import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SAFE_VIEWER_OPTIONS, attachRenderGuard, hasRenderableSize, type GuardableViewer } from './cesiumRenderGuard'

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = []
  disconnected = false
  constructor(public callback: () => void) { FakeResizeObserver.instances.push(this) }
  observe() {}
  disconnect() { this.disconnected = true }
  fire() { this.callback() }
}

function setup(width: number, height: number) {
  const host = { clientWidth: width, clientHeight: height } as HTMLElement
  const listeners: Array<(scene: unknown, error: unknown) => void> = []
  const viewer = {
    destroyed: false, useDefaultRenderLoop: true, resize: vi.fn(),
    isDestroyed() { return this.destroyed },
    scene: { requestRender: vi.fn(), renderError: { addEventListener: (listener: (scene: unknown, error: unknown) => void) => { listeners.push(listener); return () => listeners.splice(listeners.indexOf(listener), 1) } } },
  }
  const notices: string[] = []
  const dispose = attachRenderGuard(viewer as unknown as GuardableViewer, host, { onNotice: message => notices.push(message) })
  return { host, viewer, listeners, notices, dispose, observer: FakeResizeObserver.instances[FakeResizeObserver.instances.length - 1] }
}

describe('Cesium render guard', () => {
  beforeEach(() => { FakeResizeObserver.instances = []; vi.stubGlobal('ResizeObserver', FakeResizeObserver) })
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

  it('never renders while the container is zero-sized and never resizes the canvas to 0', () => {
    const { viewer } = setup(0, 0)
    expect(viewer.useDefaultRenderLoop).toBe(false)
    expect(viewer.resize).not.toHaveBeenCalled()
    expect(hasRenderableSize({ clientWidth: 0, clientHeight: 300 } as HTMLElement)).toBe(false)
    expect(hasRenderableSize({ clientWidth: 300, clientHeight: 0 } as HTMLElement)).toBe(false)
    expect(hasRenderableSize(null)).toBe(false)
    expect(SAFE_VIEWER_OPTIONS.showRenderLoopErrors).toBe(false)
  })

  it('resizes and resumes when the container becomes visible, and pauses again when it is hidden', () => {
    const { viewer, host, observer } = setup(0, 0)
    Object.assign(host, { clientWidth: 640, clientHeight: 480 })
    observer.fire()
    expect(viewer.resize).toHaveBeenCalledTimes(1)
    expect(viewer.useDefaultRenderLoop).toBe(true)
    expect(viewer.scene.requestRender).toHaveBeenCalled()
    Object.assign(host, { clientWidth: 0, clientHeight: 0 })
    observer.fire()
    expect(viewer.useDefaultRenderLoop).toBe(false)
    expect(viewer.resize).toHaveBeenCalledTimes(1)
  })

  it('a repeating render error shows the notice, keeps the loop stopped, and clears it after the map has rendered cleanly again', () => {
    vi.useFakeTimers()
    const { viewer, host, listeners, notices, observer } = setup(640, 480)
    expect(viewer.useDefaultRenderLoop).toBe(true)
    const boom = () => listeners[0](viewer.scene, new Error('Expected width to be greater than 0, actual value was 0'))
    boom()
    expect(viewer.useDefaultRenderLoop).toBe(false)
    expect(notices).toEqual([])                                   // one transient frame: no warning
    vi.advanceTimersByTime(1600)                                  // automatic recovery restarts the loop ...
    expect(viewer.useDefaultRenderLoop).toBe(true)
    boom()                                                        // ... and the error comes back: a real failure
    expect(viewer.useDefaultRenderLoop).toBe(false)
    expect(notices[notices.length - 1]).toMatch(/Map rendering paused \(Expected width to be greater than 0/)
    Object.assign(host, { clientWidth: 700, clientHeight: 500 })
    observer.fire()
    expect(viewer.useDefaultRenderLoop).toBe(true)
    vi.advanceTimersByTime(3100)
    expect(notices[notices.length - 1]).toBe('')
  })

  it('a transient zero-size error that recovers by itself never shows the "Map rendering paused" notice', () => {
    vi.useFakeTimers()
    const { viewer, host, listeners, notices, observer } = setup(640, 480)
    Object.assign(host, { clientWidth: 0, clientHeight: 0 })
    listeners[0](viewer.scene, new Error('Expected width to be greater than 0, actual value was 0'))
    Object.assign(host, { clientWidth: 640, clientHeight: 480 })
    observer.fire()
    vi.advanceTimersByTime(10_000)
    expect(viewer.useDefaultRenderLoop).toBe(true)
    expect(notices.filter(Boolean)).toEqual([])
  })

  it('retries on a timer so the map is never left permanently dead', () => {
    vi.useFakeTimers()
    const { viewer, host, listeners, notices } = setup(0, 0)
    listeners[0](viewer.scene, new Error('boom'))
    vi.advanceTimersByTime(1600)
    expect(viewer.useDefaultRenderLoop).toBe(false) // still zero-sized: stays paused and keeps retrying
    Object.assign(host, { clientWidth: 320, clientHeight: 200 })
    vi.advanceTimersByTime(1600)
    expect(viewer.useDefaultRenderLoop).toBe(true)
    expect(notices.filter(Boolean)).toEqual([])
  })

  it('does nothing after dispose or after the viewer is destroyed', () => {
    const { viewer, host, listeners, observer, dispose } = setup(640, 480)
    dispose()
    expect(observer.disconnected).toBe(true)
    expect(listeners).toHaveLength(0)
    const before = viewer.resize.mock.calls.length
    Object.assign(host, { clientWidth: 100, clientHeight: 100 })
    observer.fire()
    expect(viewer.resize.mock.calls.length).toBe(before)
    const second = setup(640, 480)
    second.viewer.destroyed = true
    expect(() => second.observer.fire()).not.toThrow()
  })

  it('falls back to polling when ResizeObserver is unavailable', () => {
    vi.unstubAllGlobals()
    vi.useFakeTimers()
    const { viewer, host } = setup(0, 0)
    Object.assign(host, { clientWidth: 200, clientHeight: 200 })
    vi.advanceTimersByTime(600)
    expect(viewer.useDefaultRenderLoop).toBe(true)
  })
})

describe('context loss and on-demand rendering', () => {
  it('calls back once per lost WebGL context, prevents the default handling, and stops after cleanup', async () => {
    const { watchContextLoss, ON_DEMAND_RENDER_OPTIONS } = await import('./cesiumRenderGuard')
    const canvas = document.createElement('canvas')
    const lost = vi.fn()
    const stop = watchContextLoss(canvas, lost)
    const event = new Event('webglcontextlost', { cancelable: true })
    canvas.dispatchEvent(event)
    expect(lost).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(true)
    stop()
    canvas.dispatchEvent(new Event('webglcontextlost'))
    expect(lost).toHaveBeenCalledTimes(1)
    expect(ON_DEMAND_RENDER_OPTIONS.requestRenderMode).toBe(true)
    expect(() => watchContextLoss(null, lost)()).not.toThrow()
  })
})
