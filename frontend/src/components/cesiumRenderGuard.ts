/**
 * Shared Cesium safety net.
 *
 * Cesium's default render loop resizes the canvas from the container every frame. When the container is hidden
 * (`display: none` while a tab/section is hidden but still mounted, collapsed layouts, initial layout before CSS
 * applies) the canvas becomes 0 x 0 and Cesium throws "Expected width to be greater than 0, actual value was 0",
 * which stops rendering and shows a modal error dialog. This guard only lets the render loop run while the
 * container has a real size, resizes the viewer when it becomes visible again, and turns render errors into an
 * inline notice with automatic recovery instead of a dead map.
 */

type Listener = (scene: unknown, error: unknown) => void
export interface GuardableViewer {
  isDestroyed(): boolean
  resize?: () => void
  useDefaultRenderLoop: boolean
  scene?: {
    renderError?: { addEventListener(listener: Listener): (() => void) | void; removeEventListener?: (listener: Listener) => unknown }
    requestRender?: () => void
  }
}

/** Options every viewer must be created with: no modal render-error dialog. */
export const SAFE_VIEWER_OPTIONS = { showRenderLoopErrors: false } as const
/** Render on demand: a frame is drawn when something changed (camera, entities, imagery) or at most every half second of clock time. */
export const ON_DEMAND_RENDER_OPTIONS = { requestRenderMode: true, maximumRenderTimeChange: 0.5 } as const

/**
 * WebGL can lose its context (GPU reset, too many contexts, sleep). Calls `onLost` once per loss; the caller recreates the viewer.
 * Returns the cleanup function.
 */
export function watchContextLoss(canvas: HTMLCanvasElement | null | undefined, onLost: () => void): () => void {
  if (!canvas || typeof canvas.addEventListener !== 'function') return () => {}
  const lost = (event: Event) => { event.preventDefault(); onLost() }
  canvas.addEventListener('webglcontextlost', lost)
  return () => canvas.removeEventListener('webglcontextlost', lost)
}

export function hasRenderableSize(host: HTMLElement | null | undefined): boolean {
  return Boolean(host) && host!.clientWidth > 0 && host!.clientHeight > 0
}

/**
 * Resolves once the container has a non-zero size. A viewer must never be constructed against a zero-width or
 * zero-height container (Cesium throws "Expected width to be greater than 0"), so creation waits for this.
 */
export function whenRenderable(host: HTMLElement): { promise: Promise<void>; cancel: () => void } {
  let cancel = () => {}
  const promise = new Promise<void>(resolve => {
    if (hasRenderableSize(host)) { resolve(); return }
    let observer: ResizeObserver | null = null
    let poll: number | undefined
    const check = () => {
      if (hasRenderableSize(host)) { cancel(); resolve() }
    }
    cancel = () => {
      observer?.disconnect()
      if (poll !== undefined) window.clearInterval(poll)
    }
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(check)
      observer.observe(host)
    } else poll = window.setInterval(check, 250)
  })
  return { promise, cancel: () => cancel() }
}

export interface RenderGuardCallbacks {
  /** A non-modal notice for the map (render paused or failed); an empty string clears it. */
  onNotice: (message: string) => void
}

const RETRY_MS = 1500
/**
 * A render error is usually one transient zero-size frame (a tab or section hidden, a layout change, a resized window). The loop is stopped at once and
 * restarted by the recovery below; the notice appears only when the error comes back after that recovery (a real, repeating failure), and it clears after
 * the map has rendered cleanly for HEALTHY_MS. A map that fixes itself therefore never shows "Map rendering paused".
 */
const HEALTHY_MS = 3000

export function attachRenderGuard(viewer: GuardableViewer, host: HTMLElement, { onNotice }: RenderGuardCallbacks): () => void {
  let disposed = false
  let failed = false
  let consecutive = 0
  let noticeShown = false
  let retryTimer: number | undefined
  let healthyTimer: number | undefined
  const clearHealthyTimer = () => {
    if (healthyTimer !== undefined) window.clearTimeout(healthyTimer)
    healthyTimer = undefined
  }

  const alive = () => !disposed && !viewer.isDestroyed()

  const apply = () => {
    if (!alive()) return
    if (!hasRenderableSize(host)) {
      // Pause: nothing may render (or resize the canvas to 0) while the container is hidden or collapsed.
      viewer.useDefaultRenderLoop = false
      return
    }
    try { viewer.resize?.() } catch { /* a transient resize failure is retried on the next size change */ }
    viewer.useDefaultRenderLoop = true
    viewer.scene?.requestRender?.()
    if (failed) {
      failed = false
      clearHealthyTimer()
      healthyTimer = window.setTimeout(() => {
        healthyTimer = undefined
        consecutive = 0
        if (noticeShown) { noticeShown = false; onNotice('') }
      }, HEALTHY_MS)
    }
  }

  const scheduleRetry = () => {
    if (retryTimer !== undefined || !alive()) return
    retryTimer = window.setTimeout(() => {
      retryTimer = undefined
      if (!alive() || !failed) return
      if (hasRenderableSize(host)) apply()
      else scheduleRetry()
    }, RETRY_MS)
  }

  const handler: Listener = (_scene, error) => {
    if (!alive()) return
    failed = true
    consecutive += 1
    clearHealthyTimer()
    const detail = error instanceof Error ? error.message : String(error ?? '')
    // Cesium stops its render loop after a render error; keep it stopped until the size is valid, then restart it.
    viewer.useDefaultRenderLoop = false
    if (consecutive >= 2 && !noticeShown) {
      noticeShown = true
      onNotice(`Map rendering paused (${detail || 'render error'}). Recovering automatically…`)
    }
    scheduleRetry()
  }
  const removeListener = viewer.scene?.renderError?.addEventListener(handler)

  let observer: ResizeObserver | null = null
  let poll: number | undefined
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => apply())
    observer.observe(host)
  } else {
    poll = window.setInterval(apply, 500)
  }
  apply()

  return () => {
    disposed = true
    observer?.disconnect()
    if (poll !== undefined) window.clearInterval(poll)
    if (retryTimer !== undefined) window.clearTimeout(retryTimer)
    clearHealthyTimer()
    if (typeof removeListener === 'function') removeListener()
    else viewer.scene?.renderError?.removeEventListener?.(handler)
  }
}
