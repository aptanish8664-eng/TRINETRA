import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { Entity, Viewer } from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import type { GeographicPoint, GeographicUavAssignment, GeographicZone } from '../services/auth'
import { parseGeographicSearchResults, type GeographicSearchResult } from './missionPlanning'
import './mission-planner.css'
import { readPlannerMapStyle, storePlannerMapStyle } from './basemapPreference'
import { SAFE_VIEWER_OPTIONS, attachRenderGuard, hasRenderableSize, whenRenderable } from './cesiumRenderGuard'

export type PlannerMapMode =
  | 'navigate'
  | 'set_start'
  | 'set_destination'
  | 'draw_zone'
  | 'set_aircraft_start'
  | 'set_aircraft_destination'
  | 'add_aircraft_waypoint'
type MapStyle = 'street' | 'satellite' | 'hybrid' | 'terrain3d'
type Props = {
  start: GeographicPoint | null
  destination: GeographicPoint | null
  zones: GeographicZone[]
  draftVertices: GeographicPoint[]
  assignments: GeographicUavAssignment[]
  activeAssignmentId: string
  mode: PlannerMapMode
  onStartChange: (point: GeographicPoint) => void
  onDestinationChange: (point: GeographicPoint) => void
  onAddVertex: (point: GeographicPoint) => void
  onSelectVertex: (zoneId: string, index: number) => void
  onMoveVertex: (zoneId: string, index: number, point: GeographicPoint) => void
  onMoveLocation: (location: 'start' | 'destination', point: GeographicPoint) => void
  onAircraftLocationChange: (uavId: string, location: 'start_location' | 'destination', point: GeographicPoint) => void
  onAddAircraftWaypoint: (uavId: string, point: GeographicPoint) => void
}

const ENTITY_PREFIX = 'mission-planner:'
const ROUTE_COLORS = ['#55d9ff', '#f5b94c', '#56e3b1', '#d28cff', '#ff7890', '#a7e8ff']
const STREET_MAP_URL = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer'
const SATELLITE_MAP_URL = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer'
const PLACE_LABELS_URL = 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer'
const TERRAIN_URL = 'https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer'
const GEOCODER_URL = 'https://photon.komoot.io/api/'
const MAP_STYLES: { value: MapStyle; label: string }[] = [
  { value: 'street', label: 'Street' },
  { value: 'satellite', label: 'Satellite' },
  { value: 'hybrid', label: 'Hybrid' },
  { value: 'terrain3d', label: '3D terrain' },
]

function toDegrees(Cesium: typeof import('cesium'), point: GeographicPoint, height = 0) {
  return Cesium.Cartesian3.fromDegrees(point.longitude, point.latitude, height)
}

function errorDetail(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function providerFailure(name: string, error: unknown): string {
  const detail = errorDetail(error)
  const accessHint = /\b401\b|\b403\b|token|credential|api.?key/i.test(detail)
    ? ' Provider access was denied; verify the configured access credentials and service permissions.'
    : ''
  return `${name} could not be loaded. Check the network and provider availability.${accessHint} ${detail}`
}

function fromScreenPosition(Cesium: typeof import('cesium'), viewer: Viewer, position: import('cesium').Cartesian2): GeographicPoint | null {
  const cartesian = viewer.camera.pickEllipsoid(position, viewer.scene.globe.ellipsoid)
  if (!cartesian) return null
  const coordinates = Cesium.Cartographic.fromCartesian(cartesian)
  const latitude = Cesium.Math.toDegrees(coordinates.latitude)
  const longitude = Cesium.Math.toDegrees(coordinates.longitude)
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null
  return { latitude, longitude }
}

export function MissionPlannerMap({
  start, destination, zones, draftVertices, assignments, activeAssignmentId, mode,
  onStartChange, onDestinationChange, onAddVertex, onSelectVertex, onMoveVertex, onMoveLocation,
  onAircraftLocationChange, onAddAircraftWaypoint,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<Viewer | null>(null)
  // The Cesium module the viewer was created with. Effects that run after `viewerReady` reuse it instead of awaiting another dynamic
  // import(), whose resolution time is unbounded under load (it made the entity sync race the viewer creation).
  const cesiumRef = useRef<typeof import('cesium') | null>(null)
  const modeRef = useRef(mode)
  const callbacksRef = useRef({
    onStartChange, onDestinationChange, onAddVertex, onSelectVertex, onMoveVertex, onMoveLocation,
    onAircraftLocationChange, onAddAircraftWaypoint,
  })
  const activeAssignmentIdRef = useRef(activeAssignmentId)
  const zonesRef = useRef(zones)
  const imageryLayersRef = useRef<import('cesium').ImageryLayer[]>([])
  const searchControllerRef = useRef<AbortController | null>(null)
  const lastSearchAtRef = useRef(0)
  const dragRef = useRef<{ entityId: string; kind: 'start' | 'destination' | 'vertex'; zoneId?: string; index?: number; moved: boolean } | null>(null)
  const [viewerReady, setViewerReady] = useState(false)
  const [mapError, setMapError] = useState('')
  const [renderNotice, setRenderNotice] = useState('')
  const [mapStyle, setMapStyleState] = useState<MapStyle>(readPlannerMapStyle)  // stored choice, otherwise the street default
  const setMapStyle = (next: MapStyle) => { setMapStyleState(next); storePlannerMapStyle(next) }
  const [imageryLoading, setImageryLoading] = useState(false)
  const [searchText, setSearchText] = useState('')
  const [searchResults, setSearchResults] = useState<GeographicSearchResult[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchError, setSearchError] = useState('')

  modeRef.current = mode
  callbacksRef.current = {
    onStartChange, onDestinationChange, onAddVertex, onSelectVertex, onMoveVertex, onMoveLocation,
    onAircraftLocationChange, onAddAircraftWaypoint,
  }
  activeAssignmentIdRef.current = activeAssignmentId
  zonesRef.current = zones

  useEffect(() => {
    let cancelled = false
    let viewer: Viewer | null = null
    let input: import('cesium').ScreenSpaceEventHandler | null = null
    let disposeGuard: (() => void) | null = null
    const sized = hostRef.current ? whenRenderable(hostRef.current) : null

    void (async () => {
      const Cesium = await import('cesium')
      cesiumRef.current = Cesium
      await sized?.promise  // never construct a viewer against a zero-size container
      if (cancelled || !hostRef.current) return
      viewer = new Cesium.Viewer(hostRef.current, {
        baseLayer: false,
        terrainProvider: new Cesium.EllipsoidTerrainProvider(),
        animation: false,
        baseLayerPicker: false,
        fullscreenButton: false,
        geocoder: false,
        homeButton: false,
        infoBox: false,
        navigationHelpButton: false,
        sceneModePicker: false,
        selectionIndicator: false,
        timeline: false,
        scene3DOnly: true,
        ...SAFE_VIEWER_OPTIONS,
        useDefaultRenderLoop: hasRenderableSize(hostRef.current),
      })
      viewerRef.current = viewer
      disposeGuard = attachRenderGuard(viewer, hostRef.current, { onNotice: setRenderNotice })
      viewer.scene.globe.show = true
      viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#233a49')
      viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(0, 20, 20_000_000),
        orientation: { heading: 0, pitch: -Cesium.Math.PI_OVER_TWO, roll: 0 },
      })
      input = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
      input.setInputAction((event: import('cesium').ScreenSpaceEventHandler.PositionedEvent) => {
        const picked = viewer?.scene.pick(event.position)
        const entity = picked?.id as Entity | undefined
        const entityId = typeof entity?.id === 'string' ? entity.id : ''
        const vertexMatch = entityId.match(/^mission-planner:zone:([^:]+):vertex:(\d+)$/)
        if (vertexMatch) {
          callbacksRef.current.onSelectVertex(vertexMatch[1], Number(vertexMatch[2]))
          return
        }
        if (entityId.startsWith(ENTITY_PREFIX)) return
        const point = viewer ? fromScreenPosition(Cesium, viewer, event.position) : null
        if (!point) return
        if (modeRef.current === 'set_start') callbacksRef.current.onStartChange(point)
        else if (modeRef.current === 'set_destination') callbacksRef.current.onDestinationChange(point)
        else if (modeRef.current === 'draw_zone') callbacksRef.current.onAddVertex(point)
        else if (modeRef.current === 'set_aircraft_start' && activeAssignmentIdRef.current) {
          callbacksRef.current.onAircraftLocationChange(activeAssignmentIdRef.current, 'start_location', point)
        } else if (modeRef.current === 'set_aircraft_destination' && activeAssignmentIdRef.current) {
          callbacksRef.current.onAircraftLocationChange(activeAssignmentIdRef.current, 'destination', point)
        } else if (modeRef.current === 'add_aircraft_waypoint' && activeAssignmentIdRef.current) {
          callbacksRef.current.onAddAircraftWaypoint(activeAssignmentIdRef.current, point)
        }
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK)
      input.setInputAction((event: import('cesium').ScreenSpaceEventHandler.PositionedEvent) => {
        const picked = viewer?.scene.pick(event.position)
        const entity = picked?.id as Entity | undefined
        const entityId = typeof entity?.id === 'string' ? entity.id : ''
        if (entityId === `${ENTITY_PREFIX}start`) {
          dragRef.current = { entityId, kind: 'start', moved: false }
        } else if (entityId === `${ENTITY_PREFIX}destination`) {
          dragRef.current = { entityId, kind: 'destination', moved: false }
        } else {
          const match = entityId.match(/^mission-planner:zone:([^:]+):vertex:(\d+)$/)
          if (match) dragRef.current = { entityId, kind: 'vertex', zoneId: match[1], index: Number(match[2]), moved: false }
        }
        if (dragRef.current) viewer!.scene.screenSpaceCameraController.enableRotate = false
      }, Cesium.ScreenSpaceEventType.LEFT_DOWN)
      input.setInputAction((event: import('cesium').ScreenSpaceEventHandler.MotionEvent) => {
        const drag = dragRef.current
        if (!drag || !viewer) return
        const point = fromScreenPosition(Cesium, viewer, event.endPosition)
        if (!point) return
        drag.moved = true
        const marker = viewer.entities.getById(drag.entityId)
        if (marker) marker.position = new Cesium.ConstantPositionProperty(toDegrees(Cesium, point, 10))
        if (drag.kind === 'vertex' && drag.zoneId !== undefined && drag.index !== undefined) {
          const polygonEntity = viewer.entities.getById(`${ENTITY_PREFIX}zone:${drag.zoneId}:polygon`)
          const currentZone = zonesRef.current.find(zone => zone.id === drag.zoneId)
          if (polygonEntity && currentZone) {
            const vertices = currentZone.vertices.map((vertex, index) => index === drag.index ? point : vertex)
            polygonEntity.polygon!.hierarchy = new Cesium.ConstantProperty(
              new Cesium.PolygonHierarchy(vertices.map(vertex => toDegrees(Cesium, vertex))),
            )
          }
        }
      }, Cesium.ScreenSpaceEventType.MOUSE_MOVE)
      input.setInputAction(() => {
        const drag = dragRef.current
        if (viewer) viewer.scene.screenSpaceCameraController.enableRotate = true
        if (drag?.moved && viewer) {
          const marker = viewer.entities.getById(drag.entityId)
          const position = marker?.position?.getValue(Cesium.JulianDate.now())
          if (position) {
            const coordinates = Cesium.Cartographic.fromCartesian(position)
            const point = {
              latitude: Cesium.Math.toDegrees(coordinates.latitude),
              longitude: Cesium.Math.toDegrees(coordinates.longitude),
            }
            if (drag.kind === 'start' || drag.kind === 'destination') {
              callbacksRef.current.onMoveLocation(drag.kind, point)
            } else if (drag.zoneId !== undefined && drag.index !== undefined) {
              callbacksRef.current.onMoveVertex(drag.zoneId, drag.index, point)
            }
          }
        }
        dragRef.current = null
      }, Cesium.ScreenSpaceEventType.LEFT_UP)
      setViewerReady(true)
    })().catch(error => {
      if (!cancelled) setMapError(`Cesium could not initialize. Check WebGL support and the local Cesium assets. ${errorDetail(error)}`)
    })

    return () => {
      cancelled = true
      sized?.cancel()
      disposeGuard?.()
      input?.destroy()
      searchControllerRef.current?.abort()
      if (viewer && !viewer.isDestroyed()) viewer.destroy()
      viewerRef.current = null
      imageryLayersRef.current = []
      setViewerReady(false)
    }
  }, [])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewerReady || !viewer || viewer.isDestroyed()) return
    let cancelled = false
    let newLayers: import('cesium').ImageryLayer[] = []
    setImageryLoading(true)
    setMapError('')

    void (async () => {
      const Cesium = cesiumRef.current ?? await import('cesium')
      const isSatellite = mapStyle === 'satellite' || mapStyle === 'hybrid'
      const baseUrl = isSatellite ? SATELLITE_MAP_URL : STREET_MAP_URL
      let styleWarning = ''
      let baseProvider: import('cesium').ArcGisMapServerImageryProvider
      try {
        baseProvider = await Cesium.ArcGisMapServerImageryProvider.fromUrl(baseUrl)
      } catch (error) {
        if (mapStyle !== 'street') throw error
        // Never leave an empty globe: fall back to the next available basemap and say so.
        baseProvider = await Cesium.ArcGisMapServerImageryProvider.fromUrl(SATELLITE_MAP_URL)
        styleWarning = `${providerFailure('Street map imagery', error)} Showing satellite imagery instead.`
      }
      if (cancelled || viewer.isDestroyed()) return

      let labelsProvider: import('cesium').ArcGisMapServerImageryProvider | null = null
      if (mapStyle === 'hybrid') {
        try {
          labelsProvider = await Cesium.ArcGisMapServerImageryProvider.fromUrl(PLACE_LABELS_URL)
        } catch (error) {
          styleWarning = providerFailure('Hybrid place labels', error)
        }
      }

      let terrainProvider: import('cesium').TerrainProvider = new Cesium.EllipsoidTerrainProvider()
      if (mapStyle === 'terrain3d') {
        try {
          terrainProvider = await Cesium.ArcGISTiledElevationTerrainProvider.fromUrl(TERRAIN_URL)
        } catch (error) {
          styleWarning = providerFailure('3D terrain', error)
        }
      }
      if (cancelled || viewer.isDestroyed()) return

      try {
        newLayers = [viewer.imageryLayers.addImageryProvider(baseProvider)]
        if (labelsProvider) newLayers.push(viewer.imageryLayers.addImageryProvider(labelsProvider))
      } catch (error) {
        newLayers.forEach(layer => viewer.imageryLayers.remove(layer, true))
        newLayers = []
        throw error
      }

      const previousLayers = imageryLayersRef.current
      imageryLayersRef.current = newLayers
      previousLayers.forEach(layer => viewer.imageryLayers.remove(layer, true))
      viewer.terrainProvider = terrainProvider
      viewer.scene.requestRender()
      const reportTileError = (providerName: string) => (error: import('cesium').TileProviderError) => {
        if (!cancelled) setMapError(providerFailure(providerName, new Error(error.message)))
      }
      baseProvider.errorEvent.addEventListener(reportTileError(isSatellite ? 'Satellite imagery' : 'Street map imagery'))
      labelsProvider?.errorEvent.addEventListener(reportTileError('Hybrid place labels'))
      terrainProvider.errorEvent.addEventListener(reportTileError('3D terrain'))
      setMapError(styleWarning)
    })().catch(error => {
      if (!cancelled) setMapError(providerFailure(mapStyle === 'satellite' || mapStyle === 'hybrid' ? 'Satellite imagery' : 'Street map imagery', error))
    }).finally(() => {
      if (!cancelled) setImageryLoading(false)
    })

    return () => {
      cancelled = true
    }
  }, [mapStyle, viewerReady])

  const searchPlaces = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const query = searchText.trim()
    if (query.length < 2) {
      setSearchResults([])
      setSearchError('Enter at least two characters to search for a place.')
      return
    }

    searchControllerRef.current?.abort()
    const controller = new AbortController()
    searchControllerRef.current = controller
    setSearchLoading(true)
    setSearchError('')
    setSearchResults([])
    try {
      const waitMs = Math.max(0, 1000 - (Date.now() - lastSearchAtRef.current))
      if (waitMs > 0) await new Promise(resolve => window.setTimeout(resolve, waitMs))
      if (controller.signal.aborted) return
      lastSearchAtRef.current = Date.now()
      const url = new URL(GEOCODER_URL)
      url.searchParams.set('q', query)
      url.searchParams.set('limit', '5')
      const response = await fetch(url, { signal: controller.signal, headers: { Accept: 'application/geo+json, application/json' } })
      if (!response.ok) throw new Error(`Search provider returned HTTP ${response.status}.`)
      const results = parseGeographicSearchResults(await response.json())
      setSearchResults(results)
      if (!results.length) setSearchError('No places matched that search.')
    } catch (error) {
      if (!controller.signal.aborted) setSearchError(`Geographic search is temporarily unavailable. ${errorDetail(error)}`)
    } finally {
      if (searchControllerRef.current === controller) {
        searchControllerRef.current = null
        setSearchLoading(false)
      }
    }
  }

  const goToSearchResult = (result: GeographicSearchResult) => {
    const viewer = viewerRef.current
    if (!viewer || viewer.isDestroyed()) return
    void Promise.resolve(cesiumRef.current ?? import('cesium')).then(Cesium => {
      if (viewer.isDestroyed()) return
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(result.longitude, result.latitude, 16_000),
        duration: 1.2,
      })
    }).catch(error => setSearchError(`The map could not move to that result. ${errorDetail(error)}`))
    setSearchResults([])
  }

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewerReady || !viewer || viewer.isDestroyed()) return
    const loaded = cesiumRef.current
    if (!loaded) return  // set before viewerReady, so this only guards against a torn-down viewer
    void Promise.resolve(loaded).then(Cesium => {
      if (viewer.isDestroyed()) return
      for (const entity of viewer.entities.values.slice()) {
        if (entity.id.startsWith(ENTITY_PREFIX)) viewer.entities.remove(entity)
      }
      const addLocation = (name: 'start' | 'destination', point: GeographicPoint | null) => {
        if (!point) return
        const isStart = name === 'start'
        viewer.entities.add({
          id: `${ENTITY_PREFIX}${name}`,
          position: toDegrees(Cesium, point, 10),
          point: {
            pixelSize: 13,
            color: isStart ? Cesium.Color.fromCssColorString('#56e3b1') : Cesium.Color.fromCssColorString('#f5b94c'),
            outlineColor: Cesium.Color.fromCssColorString('#07111d'),
            outlineWidth: 3,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
          label: {
            text: isStart ? 'START' : 'DESTINATION',
            font: '12px sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.fromCssColorString('#07111d'),
            outlineWidth: 3,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            pixelOffset: new Cesium.Cartesian2(0, -20),
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        })
      }
      addLocation('start', start)
      addLocation('destination', destination)

      for (const zone of zones) {
        const positions = zone.vertices.map(point => toDegrees(Cesium, point))
        if (positions.length >= 3) {
          viewer.entities.add({
            id: `${ENTITY_PREFIX}zone:${zone.id}:polygon`,
            polygon: {
              hierarchy: new Cesium.PolygonHierarchy(positions),
              material: Cesium.Color.fromCssColorString('#55d9ff').withAlpha(0.16),
              outline: true,
              outlineColor: Cesium.Color.fromCssColorString('#55d9ff'),
              outlineWidth: 2,
            },
          })
        }
        zone.vertices.forEach((point, index) => viewer.entities.add({
          id: `${ENTITY_PREFIX}zone:${zone.id}:vertex:${index}`,
          position: toDegrees(Cesium, point, 10),
          point: {
            pixelSize: 9,
            color: Cesium.Color.fromCssColorString('#a7e8ff'),
            outlineColor: Cesium.Color.fromCssColorString('#07111d'),
            outlineWidth: 2,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        }))
        if (positions.length > 1) viewer.entities.add({
          id: `${ENTITY_PREFIX}zone:${zone.id}:outline`,
          polyline: {
            positions: [...positions, ...(positions.length >= 3 ? [positions[0]] : [])],
            width: 2,
            material: Cesium.Color.fromCssColorString('#55d9ff'),
          },
        })
      }
      if (draftVertices.length > 0) {
        const positions = draftVertices.map(point => toDegrees(Cesium, point, 3))
        draftVertices.forEach((point, index) => viewer.entities.add({
          id: `${ENTITY_PREFIX}draft:${index}`,
          position: toDegrees(Cesium, point, 10),
          point: { pixelSize: 8, color: Cesium.Color.YELLOW, disableDepthTestDistance: Number.POSITIVE_INFINITY },
        }))
        if (positions.length > 1) viewer.entities.add({
          id: `${ENTITY_PREFIX}draft:outline`,
          polyline: { positions, width: 2, material: Cesium.Color.YELLOW },
        })
      }
      assignments.forEach((assignment, index) => {
        const zoneIds = [...new Set([...(assignment.zone_ids ?? []), ...(assignment.zone_id ? [assignment.zone_id] : [])])]
        const zoneCenters = zoneIds.flatMap(zoneId => {
          const zone = zones.find(item => item.id === zoneId)
          if (!zone?.vertices.length) return []
          return [{
            latitude: zone.vertices.reduce((sum, point) => sum + point.latitude, 0) / zone.vertices.length,
            longitude: zone.vertices.reduce((sum, point) => sum + point.longitude, 0) / zone.vertices.length,
          }]
        })
        const route = [
          assignment.start_location ?? start,
          ...zoneCenters,
          ...(assignment.waypoints ?? []),
          assignment.destination ?? destination,
        ].filter((point): point is GeographicPoint => point !== null)
        if (route.length >= 2) viewer.entities.add({
          id: `${ENTITY_PREFIX}route:${assignment.uav_id}`,
          polyline: {
            positions: route.map(point => toDegrees(Cesium, point, 20)),
            width: 2,
            material: new Cesium.PolylineDashMaterialProperty({
              color: Cesium.Color.fromCssColorString(ROUTE_COLORS[index % ROUTE_COLORS.length]).withAlpha(0.8),
              dashLength: 12,
            }),
          },
        })
        const addAircraftPoint = (kind: string, point: GeographicPoint, label: string) => viewer.entities.add({
          id: `${ENTITY_PREFIX}aircraft:${assignment.uav_id}:${kind}`,
          position: toDegrees(Cesium, point, 12),
          point: {
            pixelSize: 10,
            color: Cesium.Color.fromCssColorString(ROUTE_COLORS[index % ROUTE_COLORS.length]),
            outlineColor: Cesium.Color.fromCssColorString('#07111d'),
            outlineWidth: 2,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
          label: {
            text: `${assignment.uav_id} ${label}`,
            font: '10px sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.fromCssColorString('#07111d'),
            outlineWidth: 3,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            pixelOffset: new Cesium.Cartesian2(0, -16),
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        })
        if (assignment.start_location) addAircraftPoint('start', assignment.start_location, 'START')
        if (assignment.destination) addAircraftPoint('destination', assignment.destination, 'DEST')
        const waypoints = assignment.waypoints ?? []
        waypoints.forEach((point, waypointIndex) => {
          addAircraftPoint(`waypoint:${waypointIndex}`, point, `WP${waypointIndex + 1}`)
        })
      })
    }).catch(() => setMapError('Cesium could not update the proposed mission overlay.'))
  }, [start, destination, zones, draftVertices, assignments, viewerReady])

  return <div className={`mission-planner-map ${mode === 'navigate' ? '' : 'map-picking'}`} aria-label="Geographic mission planning map">
    <div className="mission-planner-map-host" ref={hostRef}/>
    <div className="mission-map-controls">
      <label className="mission-map-style">MAP STYLE
        <select aria-label="Map style" value={mapStyle} onChange={event => setMapStyle(event.target.value as MapStyle)}>
          {MAP_STYLES.map(style => <option key={style.value} value={style.value}>{style.label}</option>)}
        </select>
      </label>
      <form className="mission-map-search" role="search" onSubmit={event => { void searchPlaces(event) }}>
        <input
          aria-label="Search cities, addresses, and landmarks"
          type="search"
          value={searchText}
          maxLength={200}
          onChange={event => setSearchText(event.target.value)}
          placeholder="Search a city, address, or landmark"
        />
        <button type="submit" disabled={searchLoading}>{searchLoading ? 'SEARCHING' : 'SEARCH'}</button>
      </form>
      {searchResults.length > 0 && <ul className="mission-map-search-results" aria-label="Geographic search results">
        {searchResults.map(result => <li key={result.id}>
          <button type="button" onClick={() => goToSearchResult(result)}>
            <b>{result.label}</b>{result.detail && <span>{result.detail}</span>}
          </button>
        </li>)}
      </ul>}
      {searchError && <p className="mission-map-search-error" role="alert">{searchError}</p>}
      <small className="mission-map-geocoder-credit">
        Search: <a href="https://photon.komoot.io/" target="_blank" rel="noreferrer">Photon by Komoot</a> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a>
      </small>
    </div>
    {imageryLoading && <div className="mission-map-loading" role="status">Loading {MAP_STYLES.find(style => style.value === mapStyle)?.label.toLowerCase()} map…</div>}
    {mapError && <div className="cesium-map-error" role="alert">{mapError}</div>}
    {renderNotice && !mapError && <div className="cesium-map-notice" role="status">{renderNotice}</div>}
    <div className="mission-map-caption">WGS84 · {mapStyle === 'terrain3d' ? 'terrain' : 'ellipsoid'} · proposed routes only · no flight feasibility validation</div>
  </div>
}
