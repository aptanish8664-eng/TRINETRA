import { useEffect, useRef, useState } from 'react'
import type { Px4TelemetryVehicle } from '../types'
import type { Entity, Viewer } from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'

type Props = {
  vehicles: Px4TelemetryVehicle[]
  selected: string | null
  onSelect: (vehicleId: string) => void
}

const UAV_ICON = `data:image/svg+xml;charset=UTF-8,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path fill="#a7e8ff" stroke="#07111d" stroke-width="3" d="M32 4 39 27 58 35 57 42 38 38 35 59 29 59 26 38 7 42 6 35 25 27z"/><circle cx="32" cy="32" r="5" fill="#55d9ff" stroke="#07111d" stroke-width="2"/></svg>')}`

function validPosition(vehicle: Px4TelemetryVehicle) {
  const position = vehicle.position
  return Boolean(position)
    && position!.valid === true
    && Number.isFinite(position!.latitude)
    && Number.isFinite(position!.longitude)
    && position!.latitude >= -90 && position!.latitude <= 90
    && position!.longitude >= -180 && position!.longitude <= 180
}

export function CesiumLiveMap({ vehicles, selected, onSelect }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<Viewer | null>(null)
  const lastFocusedVehicleIdRef = useRef<string | null>(null)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const [viewerReady, setViewerReady] = useState(false)
  const [mapError, setMapError] = useState('')

  useEffect(() => {
    let cancelled = false
    let viewer: Viewer | null = null
    let clickHandler: import('cesium').ScreenSpaceEventHandler | null = null

    void (async () => {
      const Cesium = await import('cesium')
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
      })
      viewerRef.current = viewer
      viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(77.5946, 12.9716, 2_500_000),
      })
      const imagery = await Cesium.TileMapServiceImageryProvider.fromUrl(
        Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
      )
      if (cancelled || viewer.isDestroyed()) return
      viewer.imageryLayers.addImageryProvider(imagery)
      clickHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
      clickHandler.setInputAction((event: import('cesium').ScreenSpaceEventHandler.PositionedEvent) => {
        const picked = viewer?.scene.pick(event.position)
        const entity = picked?.id as Entity | undefined
        if (entity?.id && typeof entity.id === 'string' && entity.id.startsWith('px4-')) onSelectRef.current(entity.id)
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK)
      setViewerReady(true)
    })().catch(() => {
      if (!cancelled) setMapError('Cesium could not initialize. Check WebGL support and the local Cesium assets.')
    })

    return () => {
      cancelled = true
      clickHandler?.destroy()
      if (viewer && !viewer.isDestroyed()) viewer.destroy()
      viewerRef.current = null
      setViewerReady(false)
    }
  }, [])

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
        const id = vehicle.id
        let entity = viewer.entities.getById(id)
        if (!entity) {
          entity = viewer.entities.add({
            id,
            position: point,
            billboard: {
              image: UAV_ICON,
              width: 34,
              height: 34,
              rotation: -heading,
              alignedAxis: Cesium.Cartesian3.UNIT_Z,
              disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            label: {
              text: `PX4 SYS ${vehicle.system_id}`,
              font: '12px sans-serif',
              fillColor: Cesium.Color.fromCssColorString('#e8f7ff'),
              outlineColor: Cesium.Color.fromCssColorString('#06101b'),
              outlineWidth: 3,
              style: Cesium.LabelStyle.FILL_AND_OUTLINE,
              pixelOffset: new Cesium.Cartesian2(0, -24),
              distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 50000),
            },
            point: {
              pixelSize: selected === vehicle.id ? 13 : 8,
              color: vehicle.status === 'connected'
                ? Cesium.Color.fromCssColorString('#56e3b1')
                : Cesium.Color.fromCssColorString('#f5b94c'),
              outlineColor: Cesium.Color.fromCssColorString('#07111d'),
              outlineWidth: 2,
              disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
          })
        } else {
          entity.position = new Cesium.ConstantPositionProperty(point)
          if (entity.billboard) {
            entity.billboard.rotation = new Cesium.ConstantProperty(-heading)
            entity.billboard.width = new Cesium.ConstantProperty(selected === vehicle.id ? 40 : 34)
            entity.billboard.height = new Cesium.ConstantProperty(selected === vehicle.id ? 40 : 34)
          }
          if (entity.point) {
            entity.point.pixelSize = new Cesium.ConstantProperty(selected === vehicle.id ? 13 : 8)
            entity.point.color = new Cesium.ConstantProperty(vehicle.status === 'connected'
              ? Cesium.Color.fromCssColorString('#56e3b1')
              : Cesium.Color.fromCssColorString('#f5b94c'))
          }
        }
        const trailId = `${id}-trail`
        const trail = vehicle.flight_trail
          .filter(point => Number.isFinite(point.latitude) && Number.isFinite(point.longitude)
            && point.latitude >= -90 && point.latitude <= 90
            && point.longitude >= -180 && point.longitude <= 180)
          .map(point => Cesium.Cartesian3.fromDegrees(point.longitude, point.latitude, Math.max(0, point.altitude_m)))
        const path = viewer.entities.getById(trailId)
        if (trail.length > 1) {
          if (path) path.polyline!.positions = new Cesium.ConstantProperty(trail)
          else viewer.entities.add({
            id: trailId,
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
        if (entity.id.startsWith('px4-') && !entity.id.endsWith('-trail') && !present.has(entity.id)) {
          viewer.entities.remove(entity)
          const path = viewer.entities.getById(`${entity.id}-trail`)
          if (path) viewer.entities.remove(path)
        }
      }
      if (focusTarget && lastFocusedVehicleIdRef.current !== focusTarget.id) {
        lastFocusedVehicleIdRef.current = focusTarget.id
        void viewer.flyTo(focusTarget, { duration: 0.8 })
      }
    }).catch(() => setMapError('Cesium could not update the live vehicle layer.'))
  }, [vehicles, selected, viewerReady])

  return <div className="cesium-live-map" aria-label="Live PX4 geographic map">
    <div className="cesium-live-map-host" ref={hostRef}/>
    {mapError && <div className="cesium-map-error" role="alert">{mapError}</div>}
    {!mapError && !vehicles.some(validPosition) && <div className="cesium-map-waiting" role="status">Waiting for valid PX4 position telemetry</div>}
    <div className="cesium-map-scale">WGS84 Â· ellipsoid view Â· height shown relative to home</div>
  </div>
}
