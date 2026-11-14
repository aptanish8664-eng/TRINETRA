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

export function attachRenderGuard(viewer: GuardableViewer, host: HTMLElement, { onNotice }: RenderGuardCallbacks): () => void {
  let disposed = false
  let failed = false
  let retryTimer: number | undefined

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
      onNotice('')
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
    const detail = error instanceof Error ? error.message : String(error ?? '')
    // Cesium stops its render loop after a render error; keep it stopped until the size is valid, then restart it.
    viewer.useDefaultRenderLoop = false
    onNotice(`Map rendering paused (${detail || 'render error'}). Recovering automatically…`)
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
    if (typeof removeListener === 'function') removeListener()
    else viewer.scene?.renderError?.removeEventListener?.(handler)
  }
}
