import { useEffect, useRef, useState } from 'react'
import type { Px4TelemetryVehicle } from '../types'
import type { Entity, Viewer } from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { THRESHOLDS, circleRing, isTelemetryStale, type MissionOverlay } from './missionControlModel'
import { DEFAULT_BASEMAP } from './basemapPreference'
import { SAFE_VIEWER_OPTIONS, attachRenderGuard, hasRenderableSize, whenRenderable } from './cesiumRenderGuard'

export type MapLayers = { routes: boolean; geofence: boolean; trails: boolean; labels: boolean }
export type Basemap = 'offline' | 'street' | 'satellite'
export const DEFAULT_LAYERS: MapLayers = { routes: true, geofence: true, trails: true, labels: true }

type Props = {
  vehicles: Px4TelemetryVehicle[]
  selected: string | null
  onSelect: (vehicleId: string) => void
  /** Geometry from the deployed mission plan only (area outline, routes, geofence, drop points). */
  overlay?: MissionOverlay
  layers?: MapLayers
  basemap?: Basemap
  follow?: boolean
  /** Increment to fit the camera to the mission and aircraft. */
  fitSignal?: number
}

const UAV_ICON = `data:image/svg+xml;charset=UTF-8,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path fill="#a7e8ff" stroke="#07111d" stroke-width="3" d="M32 4 39 27 58 35 57 42 38 38 35 59 29 59 26 38 7 42 6 35 25 27z"/><circle cx="32" cy="32" r="5" fill="#55d9ff" stroke="#07111d" stroke-width="2"/></svg>')}`
const STREET_URL = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer'
const SATELLITE_URL = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer'
const OVERLAY_PREFIX = 'mc:'

function validPosition(vehicle: Px4TelemetryVehicle) {
  const position = vehicle.position
  return Boolean(position)
    && position!.valid === true
    && Number.isFinite(position!.latitude)
    && Number.isFinite(position!.longitude)
    && position!.latitude >= -90 && position!.latitude <= 90
    && position!.longitude >= -180 && position!.longitude <= 180
}

function vehicleLabel(vehicle: Px4TelemetryVehicle): string {
  const altitude = vehicle.position?.relative_altitude_m
  const battery = vehicle.battery?.remaining_percent
  return [
    vehicle.id,
    typeof altitude === 'number' ? `${altitude.toFixed(0)} m` : 'alt —',
    typeof battery === 'number' ? `${battery.toFixed(0)}%` : 'batt —',
    isTelemetryStale(vehicle) ? 'STALE' : '',
  ].filter(Boolean).join(' · ')
}

export function CesiumLiveMap({
  vehicles, selected, onSelect, overlay, layers = DEFAULT_LAYERS, basemap = DEFAULT_BASEMAP, follow = false, fitSignal = 0,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<Viewer | null>(null)
  const lastFocusedVehicleIdRef = useRef<string | null>(null)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const vehiclesRef = useRef(vehicles)
  vehiclesRef.current = vehicles
  const overlayRef = useRef(overlay)
  overlayRef.current = overlay
  const [viewerReady, setViewerReady] = useState(false)
  const [mapError, setMapError] = useState('')
  const [mapNotice, setMapNotice] = useState('')
  const [renderNotice, setRenderNotice] = useState('')

  // The viewer is created once. Telemetry, overlay and layer changes below only update entities.
  useEffect(() => {
    let cancelled = false
    let viewer: Viewer | null = null
    let clickHandler: import('cesium').ScreenSpaceEventHandler | null = null
    let disposeGuard: (() => void) | null = null
    const sized = hostRef.current ? whenRenderable(hostRef.current) : null

    void (async () => {
      const Cesium = await import('cesium')
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
        shouldAnimate: true,
        scene3DOnly: true,
        ...SAFE_VIEWER_OPTIONS,
        useDefaultRenderLoop: hasRenderableSize(hostRef.current),
      })
      viewerRef.current = viewer
      disposeGuard = attachRenderGuard(viewer, hostRef.current, { onNotice: setRenderNotice })
      viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(77.5946, 12.9716, 2_500_000),
      })
      clickHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
      clickHandler.setInputAction((event: import('cesium').ScreenSpaceEventHandler.PositionedEvent) => {
        const picked = viewer?.scene.pick(event.position)
        const entity = picked?.id as Entity | undefined
        if (entity?.id && typeof entity.id === 'string' && /^px4-\d+$/.test(entity.id)) onSelectRef.current(entity.id)
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK)
      setViewerReady(true)
    })().catch(() => {
      if (!cancelled) setMapError('Cesium could not initialize. Check WebGL support and the local Cesium assets.')
    })

    return () => {
      cancelled = true
      sized?.cancel()
      disposeGuard?.()
      clickHandler?.destroy()
      if (viewer && !viewer.isDestroyed()) viewer.destroy()
      viewerRef.current = null
      setViewerReady(false)
    }
  }, [])

  // Basemap: street by default (DEFAULT_BASEMAP). Offline Natural Earth is the always-available fallback when an online provider fails.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewerReady || !viewer || viewer.isDestroyed()) return
    let cancelled = false
    void import('cesium').then(async Cesium => {
      const offline = () => Cesium.TileMapServiceImageryProvider.fromUrl(Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'))
      let provider: import('cesium').ImageryProvider
      setMapNotice('')
      if (basemap === 'offline') provider = await offline()
      else {
        try {
          provider = await Cesium.ArcGisMapServerImageryProvider.fromUrl(basemap === 'street' ? STREET_URL : SATELLITE_URL)
        } catch {
          if (!cancelled) setMapNotice('The online basemap is unavailable; showing offline imagery.')
          provider = await offline()
        }
      }
      if (cancelled || viewer.isDestroyed()) return
      viewer.imageryLayers.removeAll()
      viewer.imageryLayers.addImageryProvider(provider)
    }).catch(() => setMapError('Cesium could not load basemap imagery.'))
    return () => { cancelled = true }
  }, [basemap, viewerReady])

  // Live aircraft: entities are updated in place from the latest backend telemetry.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewerReady || !viewer || viewer.isDestroyed()) return
    void import('cesium').then(Cesium => {
      if (viewer.isDestroyed()) return
      const present = new Set<string>()
      let focusTarget: Entity | undefined
      for (const vehicle of vehicles) {
        if (!validPosition(vehicle) || !vehicle.position) continue
        present.add(vehicle.id)
        const position = vehicle.position
        const height = Math.max(0, position.relative_altitude_m)
        const point = Cesium.Cartesian3.fromDegrees(position.longitude, position.latitude, height)
        const heading = vehicle.attitude?.yaw_rad ?? (position.heading_deg ?? 0) * Math.PI / 180
        const stale = isTelemetryStale(vehicle)
        const tint = Cesium.Color.WHITE.withAlpha(stale ? 0.4 : 1)
        const dot = stale ? Cesium.Color.fromCssColorString('#91a4b9').withAlpha(0.6)
          : vehicle.status === 'connected' ? Cesium.Color.fromCssColorString('#56e3b1') : Cesium.Color.fromCssColorString('#f5b94c')
        const size = selected === vehicle.id ? 40 : 34
        const id = vehicle.id
        let entity = viewer.entities.getById(id)
        if (!entity) {
          entity = viewer.entities.add({
            id,
            position: point,
            billboard: {
              image: UAV_ICON, width: size, height: size, rotation: -heading, color: tint,
              alignedAxis: Cesium.Cartesian3.UNIT_Z, disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            label: {
              text: vehicleLabel(vehicle),
              font: '12px sans-serif',
              fillColor: Cesium.Color.fromCssColorString('#e8f7ff'),
              outlineColor: Cesium.Color.fromCssColorString('#06101b'),
              outlineWidth: 3,
              style: Cesium.LabelStyle.FILL_AND_OUTLINE,
              pixelOffset: new Cesium.Cartesian2(0, -26),
              distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 50000),
              show: layers.labels,
            },
            point: {
              pixelSize: selected === vehicle.id ? 13 : 8, color: dot,
              outlineColor: Cesium.Color.fromCssColorString('#07111d'), outlineWidth: 2,
              disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
          })
        } else {
          entity.position = new Cesium.ConstantPositionProperty(point)
          if (entity.billboard) {
            entity.billboard.rotation = new Cesium.ConstantProperty(-heading)
            entity.billboard.width = new Cesium.ConstantProperty(size)
            entity.billboard.height = new Cesium.ConstantProperty(size)
            entity.billboard.color = new Cesium.ConstantProperty(tint)
          }
          if (entity.label) {
            entity.label.text = new Cesium.ConstantProperty(vehicleLabel(vehicle))
            entity.label.show = new Cesium.ConstantProperty(layers.labels)
          }
          if (entity.point) {
            entity.point.pixelSize = new Cesium.ConstantProperty(selected === vehicle.id ? 13 : 8)
            entity.point.color = new Cesium.ConstantProperty(dot)
          }
        }
        const trailId = `${id}-trail`
        const trail = vehicle.flight_trail
          .slice(-THRESHOLDS.trailLimit)
          .filter(item => Number.isFinite(item.latitude) && Number.isFinite(item.longitude)
            && item.latitude >= -90 && item.latitude <= 90
            && item.longitude >= -180 && item.longitude <= 180)
          .map(item => Cesium.Cartesian3.fromDegrees(item.longitude, item.latitude, Math.max(0, item.altitude_m)))
        const path = viewer.entities.getById(trailId)
        if (trail.length > 1) {
          if (path) {
            path.polyline!.positions = new Cesium.ConstantProperty(trail)
            path.show = layers.trails
          } else viewer.entities.add({
            id: trailId,
            show: layers.trails,
            polyline: {
              positions: trail,
              width: selected === vehicle.id ? 3 : 2,
              material: Cesium.Color.fromCssColorString('#55d9ff').withAlpha(0.72),
              clampToGround: false,
            },
          })
        } else if (path) {
          viewer.entities.remove(path)
        }
        if (!focusTarget && selected === vehicle.id) focusTarget = entity
      }
      for (const entity of viewer.entities.values.slice()) {
        if (/^px4-\d+$/.test(entity.id) && !present.has(entity.id)) {
          viewer.entities.remove(entity)
          const path = viewer.entities.getById(`${entity.id}-trail`)
          if (path) viewer.entities.remove(path)
        }
      }
      if (follow) {
        if (focusTarget && viewer.trackedEntity !== focusTarget) viewer.trackedEntity = focusTarget
      } else {
        if (viewer.trackedEntity) viewer.trackedEntity = undefined
        if (focusTarget && lastFocusedVehicleIdRef.current !== focusTarget.id) {
          lastFocusedVehicleIdRef.current = focusTarget.id
          void viewer.flyTo(focusTarget, { duration: 0.8 })
        }
      }
    }).catch(() => setMapError('Cesium could not update the live vehicle layer.'))
  }, [vehicles, selected, viewerReady, layers.labels, layers.trails, follow])

  // Mission geometry: rebuilt only when the plan, selection or layers change (never on telemetry ticks).
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewerReady || !viewer || viewer.isDestroyed()) return
    void import('cesium').then(Cesium => {
      if (viewer.isDestroyed()) return
      for (const entity of viewer.entities.values.slice()) if (entity.id.startsWith(OVERLAY_PREFIX)) viewer.entities.remove(entity)
      if (!overlay) return
      const color = (css: string, alpha = 1) => Cesium.Color.fromCssColorString(css).withAlpha(alpha)
      const cart = (point: { latitude: number; longitude: number }, height = 0) => Cesium.Cartesian3.fromDegrees(point.longitude, point.latitude, height)
      if (overlay.areaOutline.length > 2) {
        viewer.entities.add({
          id: `${OVERLAY_PREFIX}area`,
          polyline: { positions: [...overlay.areaOutline, overlay.areaOutline[0]].map(point => cart(point)), width: 2, material: color('#55d9ff', 0.85), clampToGround: false },
        })
      }
      for (const route of overlay.routes) {
        const emphasis = selected === route.uavId
        if (layers.geofence && route.geofence) {
          viewer.entities.add({
            id: `${OVERLAY_PREFIX}${route.uavId}:geofence`,
            polyline: {
              positions: circleRing(route.geofence.centre, route.geofence.radius_m).map(point => cart(point)), width: 1.5,
              material: new Cesium.PolylineDashMaterialProperty({ color: color('#ff4d5a', 0.7), dashLength: 18 }),
            },
          })
        }
        if (!layers.routes) continue
        const path = (points: { latitude: number; longitude: number }[], heights: number[], alpha: number, idSuffix: string) => {
          if (points.length < 2) return
          viewer.entities.add({
            id: `${OVERLAY_PREFIX}${route.uavId}:${idSuffix}`,
            polyline: {
              positions: points.map((point, index) => cart(point, heights[index] ?? 0)), width: emphasis ? 4 : 2,
              material: new Cesium.PolylineDashMaterialProperty({ color: color(route.color, emphasis ? 1 : alpha), dashLength: 14 }),
            },
          })
        }
        const heights = route.waypoints.map(item => item.altitude_m)
        path(route.outbound, heights, 0.85, 'outbound')
        path(route.returnPath, heights.slice(Math.max(0, route.outbound.length - 1)), 0.5, 'return')
        if (route.home) {
          viewer.entities.add({
            id: `${OVERLAY_PREFIX}${route.uavId}:home`, position: cart(route.home),
            point: { pixelSize: 10, color: color(route.color), outlineColor: color('#07111d'), outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY },
            label: { text: `HOME ${route.uavId}`, font: '11px sans-serif', fillColor: color('#e8f7ff'), outlineColor: color('#06101b'), outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(0, 16), show: layers.labels },
          })
        }
        const labelled = route.waypoints.length <= 60
        for (const waypoint of route.waypoints) {
          if (waypoint.action === 'takeoff' || waypoint.action === 'land') continue
          viewer.entities.add({
            id: `${OVERLAY_PREFIX}${route.uavId}:wp:${waypoint.seq}`, position: cart(waypoint, waypoint.altitude_m),
            point: { pixelSize: 6, color: color(route.color), outlineColor: color('#07111d'), outlineWidth: 1 },
            label: { text: String(waypoint.seq), font: '10px sans-serif', fillColor: color('#e8f7ff'), outlineColor: color('#06101b'), outlineWidth: 2, style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(0, -12), show: layers.labels && labelled, distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 20000) },
          })
        }
        for (const drop of route.dropPoints) {
          viewer.entities.add({
            id: `${OVERLAY_PREFIX}${route.uavId}:drop:${drop.seq}`, position: cart(drop, route.waypoints.find(item => item.seq === drop.seq)?.altitude_m ?? 0),
            point: { pixelSize: 11, color: color('#f5b94c'), outlineColor: color('#07111d'), outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY },
            label: { text: `DROP POINT ${route.uavId} (simulated)`, font: '11px sans-serif', fillColor: color('#f5b94c'), outlineColor: color('#06101b'), outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(0, -18), show: layers.labels },
          })
        }
      }
    }).catch(() => setMapError('Cesium could not draw the mission layer.'))
  }, [overlay, selected, layers.routes, layers.geofence, layers.labels, viewerReady])

  // Fit to mission: runs only when the toolbar button increments `fitSignal`.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!fitSignal || !viewerReady || !viewer || viewer.isDestroyed()) return
    void import('cesium').then(Cesium => {
      if (viewer.isDestroyed()) return
      const points: { latitude: number; longitude: number }[] = []
      const mission = overlayRef.current
      if (mission) {
        points.push(...mission.areaOutline)
        for (const route of mission.routes) {
          points.push(...route.waypoints)
          if (route.home) points.push(route.home)
          if (route.geofence) points.push(...circleRing(route.geofence.centre, route.geofence.radius_m, 16))
        }
      }
      for (const vehicle of vehiclesRef.current) if (validPosition(vehicle) && vehicle.position) points.push(vehicle.position)
      if (!points.length) return
      const pad = 0.002
      const west = Math.min(...points.map(item => item.longitude)) - pad
      const east = Math.max(...points.map(item => item.longitude)) + pad
      const south = Math.min(...points.map(item => item.latitude)) - pad
      const north = Math.max(...points.map(item => item.latitude)) + pad
      viewer.trackedEntity = undefined
      void viewer.camera.flyTo({ destination: Cesium.Rectangle.fromDegrees(west, south, east, north), duration: 0.8 })
    }).catch(() => setMapError('Cesium could not fit the camera to the mission.'))
  }, [fitSignal, viewerReady])

  return <div className="cesium-live-map" aria-label="Live PX4 geographic map">
    <div className="cesium-live-map-host" ref={hostRef}/>
    {mapError && <div className="cesium-map-error" role="alert">{mapError}</div>}
    {mapNotice && !mapError && <div className="cesium-map-notice" role="status">{mapNotice}</div>}
    {renderNotice && !mapError && <div className="cesium-map-notice render" role="status">{renderNotice}</div>}
    {!mapError && !vehicles.some(validPosition) && <div className="cesium-map-waiting" role="status">Waiting for valid PX4 position telemetry</div>}
    <div className="cesium-map-scale">WGS84 · ellipsoid view · height shown relative to home</div>
  </div>
}
