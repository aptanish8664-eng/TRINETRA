import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { CesiumLiveMap } from './CesiumLiveMap'

const cesium = vi.hoisted(() => {
  const listeners: Array<(scene: unknown, error: unknown) => void> = []
  const viewer = {
    useDefaultRenderLoop: true, trackedEntity: undefined as unknown, resize: vi.fn(),
    camera: { setView: vi.fn(), flyTo: vi.fn() },
    scene: { canvas: {}, requestRender: vi.fn(), renderError: { addEventListener: (listener: (scene: unknown, error: unknown) => void) => { listeners.push(listener); return () => listeners.splice(listeners.indexOf(listener), 1) } } },
    imageryLayers: { removeAll: vi.fn(), addImageryProvider: vi.fn() },
    entities: { values: [] as unknown[], getById: vi.fn(() => undefined), add: vi.fn(), remove: vi.fn() },
    isDestroyed: vi.fn(() => false), destroy: vi.fn(), flyTo: vi.fn(),
  }
  const arcgis = vi.fn(async (_url: string) => ({}))
  const offline = vi.fn(async (_url: string) => ({}))
  return { viewer, listeners, created: [] as Array<Record<string, unknown>>, arcgis, offline }
})

vi.mock('cesium', () => ({
  Viewer: class { constructor(_host: unknown, options: Record<string, unknown>) { cesium.created.push(options); return cesium.viewer } },
  EllipsoidTerrainProvider: class {},
  ScreenSpaceEventHandler: class { setInputAction = vi.fn(); destroy = vi.fn() },
  ScreenSpaceEventType: { LEFT_CLICK: 0 },
  Cartesian3: { fromDegrees: vi.fn(() => ({})) },
  TileMapServiceImageryProvider: { fromUrl: (url: string) => cesium.offline(url) },
  ArcGisMapServerImageryProvider: { fromUrl: (url: string) => cesium.arcgis(url) },
  buildModuleUrl: vi.fn((path: string) => path),
}))

vi.setConfig({ testTimeout: 30000 })
let size = { width: 0, height: 0 }
let observerCallback: (() => void) | null = null
class FakeResizeObserver { constructor(callback: () => void) { observerCallback = callback } observe() {} disconnect() {} }
const fireResize = () => act(() => observerCallback?.())

describe('CesiumLiveMap render safety', () => {
  beforeEach(() => {
    size = { width: 0, height: 0 }
    observerCallback = null
    cesium.created.length = 0
    cesium.listeners.length = 0
    cesium.viewer.useDefaultRenderLoop = true
    cesium.viewer.resize.mockClear()
    cesium.viewer.destroy.mockClear()
    cesium.arcgis.mockReset().mockImplementation(async () => ({}))
    cesium.offline.mockReset().mockImplementation(async () => ({}))
    cesium.viewer.imageryLayers.addImageryProvider.mockClear()
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => size.width)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(() => size.height)
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('does not construct a viewer in a zero-size container, then creates it once with rendering on when it becomes visible', async () => {
    render(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined}/>)
    await new Promise(resolve => setTimeout(resolve, 100))
    expect(cesium.created).toHaveLength(0)
    expect(cesium.viewer.resize).not.toHaveBeenCalled()
    size = { width: 800, height: 600 }
    fireResize()
    await waitFor(() => expect(cesium.created).toHaveLength(1))
    expect(cesium.created[0]).toMatchObject({ useDefaultRenderLoop: true, showRenderLoopErrors: false })
  })

  it('pauses the render loop when the container is hidden later and resumes with a resize when visible again', async () => {
    size = { width: 800, height: 600 }
    render(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined}/>)
    await waitFor(() => expect(cesium.created).toHaveLength(1))
    await waitFor(() => expect(cesium.viewer.useDefaultRenderLoop).toBe(true))
    size = { width: 0, height: 0 }
    fireResize()
    expect(cesium.viewer.useDefaultRenderLoop).toBe(false)
    const resizes = cesium.viewer.resize.mock.calls.length
    size = { width: 800, height: 600 }
    fireResize()
    expect(cesium.viewer.resize.mock.calls.length).toBe(resizes + 1)
    expect(cesium.viewer.useDefaultRenderLoop).toBe(true)
  })

  it('shows an inline notice for a render error and recovers when the size is valid again', async () => {
    size = { width: 800, height: 600 }
    render(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined}/>)
    await waitFor(() => expect(cesium.listeners).toHaveLength(1))
    act(() => cesium.listeners[0](cesium.viewer.scene, new Error('Expected width to be greater than 0, actual value was 0')))
    const notice = await screen.findByText(/Map rendering paused \(Expected width to be greater than 0/)
    expect(notice.getAttribute('role')).toBe('status')
    expect(cesium.viewer.useDefaultRenderLoop).toBe(false)
    expect(screen.queryByRole('alert')).toBeNull()
    fireResize()
    await waitFor(() => expect(screen.queryByText(/Map rendering paused/)).toBeNull())
    expect(cesium.viewer.useDefaultRenderLoop).toBe(true)
  })

  it('creates the viewer once: telemetry and prop changes never recreate it', async () => {
    size = { width: 800, height: 600 }
    const view = render(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined}/>)
    await waitFor(() => expect(cesium.created).toHaveLength(1))
    view.rerender(<CesiumLiveMap vehicles={[]} selected="px4-1" onSelect={() => undefined} basemap="street" follow fitSignal={1}/>)
    view.rerender(<CesiumLiveMap vehicles={[]} selected="px4-2" onSelect={() => undefined} layers={{ routes: false, geofence: false, trails: false, labels: false }}/>)
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(cesium.created).toHaveLength(1)
    expect(cesium.viewer.destroy).not.toHaveBeenCalled()
    view.unmount()
    expect(cesium.viewer.destroy).toHaveBeenCalledTimes(1)
  })

  describe('basemap', () => {
    const STREET = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer'
    const SATELLITE = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer'

    it('uses the street basemap by default', async () => {
      size = { width: 800, height: 600 }
      render(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined}/>)
      await waitFor(() => expect(cesium.viewer.imageryLayers.addImageryProvider).toHaveBeenCalledTimes(1))
      expect(cesium.arcgis).toHaveBeenCalledWith(STREET)
      expect(cesium.arcgis).not.toHaveBeenCalledWith(SATELLITE)
    })

    it('still offers satellite imagery when it is selected', async () => {
      size = { width: 800, height: 600 }
      const view = render(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined}/>)
      await waitFor(() => expect(cesium.viewer.imageryLayers.addImageryProvider).toHaveBeenCalledTimes(1))
      view.rerender(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined} basemap="satellite"/>)
      await waitFor(() => expect(cesium.arcgis).toHaveBeenCalledWith(SATELLITE))
      await waitFor(() => expect(cesium.viewer.imageryLayers.addImageryProvider).toHaveBeenCalledTimes(2))
    })

    it('falls back to offline imagery with a notice when the street tiles fail, never leaving an empty globe', async () => {
      size = { width: 800, height: 600 }
      cesium.arcgis.mockRejectedValue(new Error('tiles unreachable'))
      render(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined}/>)
      expect(await screen.findByText('The online basemap is unavailable; showing offline imagery.')).toBeTruthy()
      await waitFor(() => expect(cesium.offline).toHaveBeenCalled())
      await waitFor(() => expect(cesium.viewer.imageryLayers.addImageryProvider).toHaveBeenCalledTimes(1))
    })

    it('creates the viewer once even when the basemap changes', async () => {
      size = { width: 800, height: 600 }
      const view = render(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined}/>)
      await waitFor(() => expect(cesium.created).toHaveLength(1))
      view.rerender(<CesiumLiveMap vehicles={[]} selected={null} onSelect={() => undefined} basemap="satellite"/>)
      await waitFor(() => expect(cesium.arcgis).toHaveBeenCalledWith(SATELLITE))
      expect(cesium.created).toHaveLength(1)
      expect(cesium.viewer.destroy).not.toHaveBeenCalled()
    })
  })
})

