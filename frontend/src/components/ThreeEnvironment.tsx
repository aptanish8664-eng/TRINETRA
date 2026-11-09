import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { MissionState } from '../types'

type Props = { state: MissionState; frames: MissionState[]; selected: string | null; onSelect: (id: string) => void }

export function ThreeEnvironment({ state, frames, selected, onSelect }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<{ scene: THREE.Scene; uavs: Map<string, THREE.Group>; controls: OrbitControls; camera: THREE.PerspectiveCamera; renderer: THREE.WebGLRenderer; frame: number } | null>(null)
  const selectRef = useRef(onSelect)
  selectRef.current = onSelect

  useEffect(() => {
    const element = host.current
    if (!element) return
    const scene = new THREE.Scene()
    scene.background = new THREE.Color('#07111d')
    scene.fog = new THREE.Fog('#07111d', 15, 35)
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100)
    camera.position.set(10, 11, 13)
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.8))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.setSize(element.clientWidth, element.clientHeight)
    element.appendChild(renderer.domElement)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.target.set(5, 0, 5); controls.enableDamping = true; controls.maxPolarAngle = Math.PI * .48; controls.minDistance = 7; controls.maxDistance = 24
    scene.add(new THREE.HemisphereLight('#c9edff', '#142233', 2.1))
    const key = new THREE.DirectionalLight('#8bdfff', 2.2); key.position.set(4, 10, 2); scene.add(key)
    const fill = new THREE.PointLight('#2a8cff', 12, 18); fill.position.set(9, 3, 10); scene.add(fill)
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(10, 10), new THREE.MeshStandardMaterial({ color: '#0a1a26', roughness: .88, metalness: .12 }))
    floor.rotation.x = -Math.PI / 2; floor.position.set(5, -.12, 5); scene.add(floor)
    const grid = new THREE.GridHelper(10, 20, '#31536b', '#183144'); grid.position.set(5, -.1, 5); scene.add(grid)
    const horizon = new THREE.Group(); scene.add(horizon)
    for (let i = 0; i < 9; i++) {
      const h = .35 + ((i * 17) % 11) * .09
      const ridge = new THREE.Mesh(new THREE.BoxGeometry(.09, h, 10), new THREE.MeshBasicMaterial({ color: '#17364a', transparent: true, opacity: .38 }))
      ridge.position.set(i * 1.25, h / 2 - .1, 5); horizon.add(ridge)
    }
    const uavs = new Map<string, THREE.Group>()
    const raycaster = new THREE.Raycaster(); const pointer = new THREE.Vector2()
    const onClick = (event: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect()
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1)
      raycaster.setFromCamera(pointer, camera)
      const hits = raycaster.intersectObjects(Array.from(uavs.values()), true)
      const root = hits[0]?.object.parent
      if (root?.userData.uavId) selectRef.current(root.userData.uavId as string)
    }
    renderer.domElement.addEventListener('click', onClick)
    const onResize = () => { if (!element.clientWidth || !element.clientHeight) return; camera.aspect = element.clientWidth / element.clientHeight; camera.updateProjectionMatrix(); renderer.setSize(element.clientWidth, element.clientHeight) }
    const observer = new ResizeObserver(onResize); observer.observe(element)
    let frame = 0
    const animate = () => { frame = requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera) }
    animate()
    sceneRef.current = { scene, uavs, controls, camera, renderer, frame }
    return () => { cancelAnimationFrame(frame); observer.disconnect(); controls.dispose(); renderer.domElement.removeEventListener('click', onClick); renderer.dispose(); element.removeChild(renderer.domElement); sceneRef.current = null }
  }, [])

  useEffect(() => {
    const runtime = sceneRef.current
    if (!runtime) return
    const { scene, uavs } = runtime
    const routeLayer = scene.getObjectByName('route-layer')
    if (routeLayer) { scene.remove(routeLayer); routeLayer.traverse(object => { if (object instanceof THREE.Mesh || object instanceof THREE.Line) { object.geometry.dispose(); const material = object.material; if (Array.isArray(material)) material.forEach(item => item.dispose()); else material.dispose() } }) }
    const routes = new THREE.Group(); routes.name = 'route-layer'; scene.add(routes)
    for (const task of Object.values(state.tasks)) {
      const marker = new THREE.Mesh(new THREE.OctahedronGeometry(.11, 0), new THREE.MeshStandardMaterial({ color: task.status === 'complete' ? '#56e3b1' : task.uav_id ? '#55d9ff' : '#f5b94c', emissive: task.status === 'complete' ? '#56e3b1' : '#15516a', emissiveIntensity: .7 }))
      marker.position.set(task.position.x, .08, 10 - task.position.y); routes.add(marker)
      const owner = task.uav_id ? state.uavs[task.uav_id] : undefined
      if (owner && task.status === 'pending') {
        const path = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(owner.position.x, .13, 10 - owner.position.y), new THREE.Vector3(task.position.x, .13, 10 - task.position.y)]), new THREE.LineDashedMaterial({ color: '#55d9ff', dashSize: .15, gapSize: .13, transparent: true, opacity: .42 }))
        path.computeLineDistances(); routes.add(path)
      }
    }
    for (const uav of Object.values(state.uavs)) {
      const trail = frames.flatMap(frame => frame.uavs[uav.id] ? [frame.uavs[uav.id]] : [])
      if (trail.length > 1) {
        const truthPoints = trail.map(point => new THREE.Vector3(point.position.x, .08, 10 - point.position.y))
        routes.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(truthPoints), new THREE.LineBasicMaterial({ color: '#55d9ff', transparent: true, opacity: .42 })))
        const spoofed = trail.some(point => Math.hypot(point.position.x - point.gps.x, point.position.y - point.gps.y) > .28)
        if (spoofed) {
          const gpsPoints = trail.map(point => new THREE.Vector3(point.gps.x, .1, 10 - point.gps.y))
          routes.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(gpsPoints), new THREE.LineDashedMaterial({ color: '#ff4d5a', dashSize: .14, gapSize: .11, transparent: true, opacity: .74 })))
          const discrepancy = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(uav.position.x, .2, 10-uav.position.y), new THREE.Vector3(uav.gps.x, .2, 10-uav.gps.y)]), new THREE.LineDashedMaterial({ color: '#ff4d5a', dashSize: .12, gapSize: .08 }))
          discrepancy.computeLineDistances(); routes.add(discrepancy)
        }
      }
      let model = uavs.get(uav.id)
      if (!model) {
        model = new THREE.Group(); model.userData.uavId = uav.id
        const cyan = new THREE.MeshStandardMaterial({ color: '#b8efff', metalness: .7, roughness: .25, emissive: '#225569', emissiveIntensity: .4 })
        const body = new THREE.Mesh(new THREE.CapsuleGeometry(.055, .38, 3, 7), cyan); body.rotation.x = Math.PI / 2; model.add(body)
        const wing = new THREE.Mesh(new THREE.BoxGeometry(.68, .035, .15), cyan); model.add(wing)
        const tail = new THREE.Mesh(new THREE.BoxGeometry(.25, .025, .09), cyan); tail.position.z = .2; model.add(tail)
        const light = new THREE.PointLight('#55d9ff', 1.2, 2); light.position.y = .22; model.add(light)
        const selection = new THREE.Mesh(new THREE.TorusGeometry(.32, .012, 5, 36), new THREE.MeshBasicMaterial({ color: '#a7e8ff', transparent: true, opacity: .75 })); selection.rotation.x = Math.PI / 2; selection.position.y = -.15; selection.name = 'selection-ring'; model.add(selection)
        const discrepancy = new THREE.Mesh(new THREE.SphereGeometry(.11, 10, 8), new THREE.MeshBasicMaterial({ color: '#ff4d5a', wireframe: true })); discrepancy.name = 'gps-marker'; model.add(discrepancy)
        scene.add(model); uavs.set(uav.id, model)
      }
      model.position.set(uav.position.x, .52 + Math.sin(state.time * .8 + Number(uav.id.slice(-2))) * .055, 10 - uav.position.y)
      model.rotation.y = -THREE.MathUtils.degToRad(uav.heading)
      const ring = model.getObjectByName('selection-ring') as THREE.Mesh | undefined
      if (ring) { (ring.material as THREE.MeshBasicMaterial).color.set(uav.status === 'isolated' ? '#ff4d5a' : selected === uav.id ? '#a7e8ff' : '#21485c'); (ring.material as THREE.MeshBasicMaterial).opacity = selected === uav.id ? .95 : .48 }
      const discrepancy = model.getObjectByName('gps-marker')
      const dx = uav.gps.x - uav.position.x, dz = -(uav.gps.y - uav.position.y)
      if (discrepancy) { discrepancy.position.set(dx, .18, dz); discrepancy.visible = Math.hypot(dx, dz) > .28 }
    }
  }, [state, frames, selected])

  return <div className="three-host" ref={host}><div className="three-caption">THREE.JS TERRAIN VIEW <span>·</span> LIVE SIMULATION STATE</div><div className="three-legend"><span><i className="truth"/> GROUND TRUTH</span><span><i className="reported"/> GPS REPORTED</span><span><i className="task"/> TASK / WAYPOINT</span></div><div className="three-coords">ABSTRACT LOCAL COORDINATES<br/>NO GEOGRAPHIC TERRAIN DATA</div></div>
}

export default ThreeEnvironment
