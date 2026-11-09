import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { GeographicPoint, GeographicUavAssignment, GeographicZone } from '../services/auth'
import { MissionPlannerMap } from './MissionPlannerMap'

const cesiumMock = vi.hoisted(() => {
  const entityStore = new Map<string, { id: string }>()
  const provider = (url = '') => ({ url, errorEvent: { addEventListener: vi.fn() } })
  const viewer = {
    camera: { setView: vi.fn(), flyTo: vi.fn(), pickEllipsoid: vi.fn(() => null) },
    scene: {
      canvas: {},
      globe: { ellipsoid: {} },
      pick: vi.fn(() => undefined),
      requestRender: vi.fn(),
      screenSpaceCameraController: { enableRotate: true },
    },
    imageryLayers: {
      addImageryProvider: vi.fn((imagery: unknown) => ({ imagery })),
      remove: vi.fn(),
    },
    entities: {
      get values() { return [...entityStore.values()] },
      add: vi.fn((entity: { id: string }) => {
        entityStore.set(entity.id, entity)
        return entity
      }),
      getById: vi.fn((id: string) => entityStore.get(id)),
      remove: vi.fn((entity: { id: string }) => entityStore.delete(entity.id)),
    },
    isDestroyed: vi.fn(() => false),
    destroy: vi.fn(),
  }
  const fromUrl = vi.fn(async (url: string) => provider(url))
  return {
    viewer,
    entityStore,
    fromUrl,
    imageryProvider: provider,
    terrainProvider: provider('terrain'),
  }
})

vi.mock('cesium', () => ({
  Viewer: class {
    constructor() {
      return cesiumMock.viewer
    }
  },
  EllipsoidTerrainProvider: class {
    constructor() {
      return cesiumMock.imageryProvider('ellipsoid')
    }
  },
  ArcGisMapServerImageryProvider: { fromUrl: cesiumMock.fromUrl },
  ArcGISTiledElevationTerrainProvider: { fromUrl: vi.fn(async () => cesiumMock.terrainProvider) },
  ScreenSpaceEventHandler: class {
    setInputAction = vi.fn()
    destroy = vi.fn()
  },
  ScreenSpaceEventType: { LEFT_CLICK: 1, LEFT_DOWN: 2, MOUSE_MOVE: 3, LEFT_UP: 4 },
  Cartesian3: { fromDegrees: vi.fn((longitude: number, latitude: number, height: number) => ({ longitude, latitude, height })) },
  Cartesian2: vi.fn(),
  Cartographic: { fromCartesian: vi.fn() },
  Math: { PI_OVER_TWO: Math.PI / 2, toDegrees: vi.fn() },
  Color: { fromCssColorString: vi.fn(() => ({ withAlpha: vi.fn(() => ({})) })) },
  LabelStyle: { FILL_AND_OUTLINE: 1 },
  PolygonHierarchy: vi.fn(),
  ConstantPositionProperty: vi.fn(),
  ConstantProperty: vi.fn(),
  PolylineDashMaterialProperty: vi.fn(),
  JulianDate: { now: vi.fn() },
}))

const point: GeographicPoint = { latitude: 12.9716, longitude: 77.5946 }
const zone: GeographicZone = {
  id: 'zone-a',
  name: 'Survey area',
  vertices: [
    { latitude: 12.9, longitude: 77.5 },
    { latitude: 13, longitude: 77.5 },
    { latitude: 12.95, longitude: 77.6 },
  ],
}
const assignments: GeographicUavAssignment[] = []
const callbacks = {
  onStartChange: vi.fn(),
  onDestinationChange: vi.fn(),
  onAddVertex: vi.fn(),
  onSelectVertex: vi.fn(),
  onMoveVertex: vi.fn(),
  onMoveLocation: vi.fn(),
  onAircraftLocationChange: vi.fn(),
  onAddAircraftWaypoint: vi.fn(),
}

function renderMap() {
  return render(<MissionPlannerMap
    start={point}
    destination={null}
    zones={[zone]}
    draftVertices={[]}
    assignments={assignments}
    activeAssignmentId=""
    mode="navigate"
    {...callbacks}
  />)
}

describe('MissionPlannerMap imagery and search', () => {
  beforeEach(() => {
    cesiumMock.entityStore.clear()
    cesiumMock.fromUrl.mockClear()
    cesiumMock.viewer.entities.add.mockClear()
    cesiumMock.viewer.imageryLayers.addImageryProvider.mockClear()
    cesiumMock.viewer.imageryLayers.remove.mockClear()
    cesiumMock.viewer.camera.flyTo.mockClear()
    cesiumMock.viewer.isDestroyed.mockReturnValue(false)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('switches basemaps without removing mission markers or survey vertices', async () => {
    renderMap()
    await waitFor(() => expect(cesiumMock.fromUrl).toHaveBeenCalledWith(
      'https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer',
    ))
    const entityIds = [...cesiumMock.entityStore.keys()]
    expect(entityIds).toContain('mission-planner:start')
    expect(entityIds).toContain('mission-planner:zone:zone-a:vertex:2')

    fireEvent.change(screen.getByLabelText('Map style'), { target: { value: 'hybrid' } })
    await waitFor(() => expect(cesiumMock.fromUrl).toHaveBeenCalledWith(
      'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer',
    ))
    await waitFor(() => expect(cesiumMock.fromUrl).toHaveBeenCalledWith(
      'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer',
    ))
    expect([...cesiumMock.entityStore.keys()]).toEqual(entityIds)
    expect(cesiumMock.viewer.imageryLayers.remove).toHaveBeenCalled()
  }, 15_000)

  it('searches places and flies to a valid result without changing mission entities', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      features: [{
        geometry: { coordinates: [77.5946, 12.9716] },
        properties: { name: 'Bengaluru', state: 'Karnataka', country: 'India' },
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    renderMap()
    await waitFor(() => expect(cesiumMock.viewer.entities.add).toHaveBeenCalled())
    const entityIds = [...cesiumMock.entityStore.keys()]

    fireEvent.change(screen.getByLabelText('Search cities, addresses, and landmarks'), {
      target: { value: 'Bengaluru, India' },
    })
    fireEvent.submit(screen.getByRole('search'))

    const result = await screen.findByRole('button', { name: /Bengaluru/ })
    expect(fetchMock).toHaveBeenCalledWith(
      expect.objectContaining({ href: expect.stringContaining('q=Bengaluru%2C+India') }),
      expect.objectContaining({ headers: { Accept: 'application/geo+json, application/json' } }),
    )
    fireEvent.click(result)
    await waitFor(() => expect(cesiumMock.viewer.camera.flyTo).toHaveBeenCalled())
    expect([...cesiumMock.entityStore.keys()]).toEqual(entityIds)
  }, 15_000)
})
