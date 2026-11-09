import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { ColladaLoader } from 'three/examples/jsm/loaders/ColladaLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { AlertTriangle, Box, Rotate3D } from 'lucide-react'
import { api, ApiError } from '../services/api'
import type { GazeboPoseModel, GazeboPoseSnapshot, GazeboPoseStreamSnapshot } from '../types'
import { mergeGazeboPoseStream } from '../services/telemetryStreams'

type GazeboPoseMirrorProps = {
  selectedModel: string | null
  onSelect: (modelName: string) => void
}

const MODEL_ASSET = '/gazebo/x500_base/meshes/'

async function loadX500Model(): Promise<THREE.Group> {
  const collada = new ColladaLoader()
  const stl = new STLLoader()
  const [body, motor, bell, propCcw, propCw] = await Promise.all([
    collada.loadAsync(`${MODEL_ASSET}NXP-HGD-CF.dae`),
    collada.loadAsync(`${MODEL_ASSET}5010Base.dae`),
    collada.loadAsync(`${MODEL_ASSET}5010Bell.dae`),
    stl.loadAsync(`${MODEL_ASSET}1345_prop_ccw.stl`),
    stl.loadAsync(`${MODEL_ASSET}1345_prop_cw.stl`),
  ])
  const model = new THREE.Group()
  const bodyMesh = body.scene
  bodyMesh.position.set(0, 0, 0.265)
  bodyMesh.rotation.z = Math.PI
  model.add(bodyMesh)

  const motorOffsets: Array<[number, number]> = [
    [0.174, 0.174], [-0.174, 0.174], [0.174, -0.174], [-0.174, -0.174],
  ]
  motorOffsets.forEach(([x, y], index) => {
    const base = motor.scene.clone(true)
    base.position.set(x, y, 0.272)
    base.rotation.z = -0.45
    model.add(base)

    const motorBell = bell.scene.clone(true)
    motorBell.position.set(x, y, 0.268)
    model.add(motorBell)

    const propGeometry = (index < 2 ? propCcw : propCw).clone()
    const propeller = new THREE.Mesh(
      propGeometry,
      new THREE.MeshStandardMaterial({ color: '#bbcbd2', metalness: 0.25, roughness: 0.55, side: THREE.DoubleSide }),
    )
    propeller.position.set(x, y, 0.284)
    model.add(propeller)
  })
  return model
}

function modelLabel(name: string, selected: boolean) {
  const canvas = document.createElement('canvas')
  canvas.width = 320
  canvas.height = 72
  const context = canvas.getContext('2d')
  if (!context) return null
  context.fillStyle = selected ? '#123b52' : '#07121de8'
  context.fillRect(0, 0, canvas.width, canvas.height)
  context.strokeStyle = selected ? '#55d9ff' : '#34546a'
  context.lineWidth = 4
  context.strokeRect(2, 2, canvas.width - 4, canvas.height - 4)
  context.fillStyle = '#d9f5ff'
  context.font = 'bold 28px monospace'
  context.fillText(name.toUpperCase(), 16, 46)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false }))
  sprite.position.set(0, 0, 0.9)
  sprite.scale.set(1.15, 0.26, 1)
  return sprite
}

export function GazeboPoseMirror({ selectedModel, onSelect }: GazeboPoseMirrorProps) {
  const [snapshot, setSnapshot] = useState<GazeboPoseSnapshot | null>(null)
  const [status, setStatus] = useState<'checking' | 'connecting' | 'connected' | 'stale' | 'offline' | 'disabled' | 'unauthorized'>('checking')
  const [error, setError] = useState('')
  const [viewerError, setViewerError] = useState('')
  const [viewerReady, setViewerReady] = useState(false)
  const [meshLoaded, setMeshLoaded] = useState(false)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const modelGroupsRef = useRef(new Map<string, THREE.Group>())
  const modelTemplateRef = useRef<THREE.Group | null>(null)
  const hasFramedModelsRef = useRef(false)
  const trailsRef = useRef(new Map<string, THREE.Line>())
  const selectedRef = useRef<string | null>(selectedModel)
  selectedRef.current = selectedModel

  useEffect(() => {
    let stopped = false
    let hasSnapshot = false
    let socket: WebSocket | null = null
    let retryTimer: number | undefined
    let currentSnapshot: GazeboPoseSnapshot | null = null
    const apply = (update: GazeboPoseStreamSnapshot) => {
      if (update.source !== 'gazebo_pose_info' || !Array.isArray(update.models)) return
      const next = mergeGazeboPoseStream(currentSnapshot, update)
      hasSnapshot = true
      currentSnapshot = next
      setSnapshot(next)
      setStatus(next.transport.status === 'receiving' ? 'connected' : next.transport.status === 'stale' || next.transport.status === 'disconnected' ? 'stale' : 'connecting')
      setError('')
    }
    const connect = (since: string) => {
      if (stopped) return
      if (!hasSnapshot) setStatus('connecting')
      socket = api.gazeboPoseSocket(since)
      socket.onmessage = event => {
        try {
          apply(JSON.parse(String(event.data)) as GazeboPoseStreamSnapshot)
        } catch {
          if (!stopped) setError('Gazebo returned an invalid pose snapshot.')
        }
      }
      socket.onerror = () => { if (!stopped) setStatus('offline') }
      socket.onclose = event => {
        if (stopped) return
        if (event.code === 4401) {
          setStatus('unauthorized')
          window.dispatchEvent(new Event('trinetra:unauthorized'))
          return
        }
        if (event.code === 4403) {
          setStatus('unauthorized')
          setError('Your account does not have permission to view Gazebo telemetry.')
          return
        }
        if (event.code === 1013) {
          setStatus('disabled')
          return
        }
        setStatus('offline')
        retryTimer = window.setTimeout(() => connect(currentSnapshot?.generated_at ?? since), 2000)
      }
    }

    void api.gazeboPoses().then(initial => {
      apply(initial)
      connect(initial.generated_at)
    }).catch(cause => {
      if (stopped) return
      if (cause instanceof ApiError && cause.status === 503) {
        setStatus('disabled')
        setError('Gazebo pose bridge is disabled. Start the read-only WSL Gazebo pose relay and restart the backend.')
      } else if (cause instanceof ApiError && cause.status === 401) {
        setStatus('unauthorized')
      } else {
        setStatus('offline')
        setError(cause instanceof Error ? cause.message : 'Could not load Gazebo pose data.')
      }
    })

    return () => {
      stopped = true
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
      socket?.close()
    }
  }, [])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let renderer: THREE.WebGLRenderer
    try {
      const scene = new THREE.Scene()
      scene.background = new THREE.Color('#07101a')
      const camera = new THREE.PerspectiveCamera(44, 1, 0.05, 500)
      camera.up.set(0, 0, 1)
      camera.position.set(12, -16, 11)
      camera.lookAt(0, 0, 0)
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
      renderer.outputColorSpace = THREE.SRGBColorSpace
      host.appendChild(renderer.domElement)

      const grid = new THREE.GridHelper(40, 40, '#294257', '#162b3d')
      grid.rotation.x = Math.PI / 2
      scene.add(grid)
      const axes = new THREE.AxesHelper(2)
      scene.add(axes)
      scene.add(new THREE.HemisphereLight('#d4f2ff', '#172535', 2))
      const keyLight = new THREE.DirectionalLight('#ffffff', 2.8)
      keyLight.position.set(-4, -6, 12)
      scene.add(keyLight)
      const controls = new OrbitControls(camera, renderer.domElement)
      controls.target.set(0, 0, 0.5)
      controls.enableDamping = true
      controls.dampingFactor = 0.08
      const raycaster = new THREE.Raycaster()
      const pointer = new THREE.Vector2()
      const selectAtPointer = (event: PointerEvent) => {
        const bounds = renderer.domElement.getBoundingClientRect()
        pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1
        pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1
        raycaster.setFromCamera(pointer, camera)
        const hit = raycaster.intersectObjects([...modelGroupsRef.current.values()], true)[0]
        let target: THREE.Object3D | null = hit?.object ?? null
        while (target && typeof target.userData.modelName !== 'string') target = target.parent
        if (target) onSelect(String(target.userData.modelName))
      }
      renderer.domElement.addEventListener('pointerup', selectAtPointer)
      sceneRef.current = scene
      cameraRef.current = camera
      rendererRef.current = renderer
      controlsRef.current = controls
      setViewerError('')
      setViewerReady(true)

      const resize = () => {
        if (!host || host.clientWidth < 1 || host.clientHeight < 1) return
        camera.aspect = host.clientWidth / host.clientHeight
        camera.updateProjectionMatrix()
        renderer.setSize(host.clientWidth, host.clientHeight, false)
      }
      const observer = new ResizeObserver(resize)
      observer.observe(host)
      resize()
      let frame = 0
      const render = () => {
        controls.update()
        renderer.render(scene, camera)
        frame = requestAnimationFrame(render)
      }
      render()
      let stoppedForRender = true
      void loadX500Model().then(template => {
        if (!stoppedForRender) return
        modelTemplateRef.current = template
        setMeshLoaded(true)
      }).catch(() => setViewerError('PX4 X500 mesh assets could not be loaded.'))

      return () => {
        stoppedForRender = false
        cancelAnimationFrame(frame)
        observer.disconnect()
        renderer.domElement.removeEventListener('pointerup', selectAtPointer)
        controls.dispose()
        trailsRef.current.forEach(line => {
          line.geometry.dispose()
          ;(line.material as THREE.Material).dispose()
        })
        trailsRef.current.clear()
        modelGroupsRef.current.forEach(group => disposeObject(group))
        modelGroupsRef.current.clear()
        if (modelTemplateRef.current) disposeObject(modelTemplateRef.current)
        modelTemplateRef.current = null
        hasFramedModelsRef.current = false
        setMeshLoaded(false)
        renderer.dispose()
        if (renderer.domElement.parentElement === host) host.removeChild(renderer.domElement)
        scene.clear()
        sceneRef.current = null
        rendererRef.current = null
        controlsRef.current = null
      }
    } catch {
      setViewerError('This browser could not initialize the Gazebo 3D pose mirror.')
      setViewerReady(false)
      return
    }
  }, [onSelect])

  useEffect(() => {
    const scene = sceneRef.current
    const template = modelTemplateRef.current
    if (!scene || !template || !snapshot) return
    const currentNames = new Set(snapshot.models.map(model => model.model_name))
    for (const [name, group] of modelGroupsRef.current) {
      if (!currentNames.has(name)) {
        scene.remove(group)
        modelGroupsRef.current.delete(name)
        const trail = trailsRef.current.get(name)
        if (trail) {
          scene.remove(trail)
          trail.geometry.dispose()
          ;(trail.material as THREE.Material).dispose()
          trailsRef.current.delete(name)
        }
      }
    }
    for (const model of snapshot.models) updateModel(scene, template, model, modelGroupsRef.current, trailsRef.current, selectedModel)
    if (!hasFramedModelsRef.current && snapshot.models.length > 0 && cameraRef.current && controlsRef.current) {
      const positions = snapshot.models.map(model => new THREE.Vector3(
        model.position_m.x,
        model.position_m.y,
        model.position_m.z,
      ))
      const bounds = new THREE.Box3().setFromPoints(positions)
      const center = bounds.getCenter(new THREE.Vector3())
      const radius = Math.max(0.75, bounds.getBoundingSphere(new THREE.Sphere()).radius)
      const distance = Math.max(3.5, radius * 1.8)
      const viewDirection = new THREE.Vector3(1, -1, 0.75).normalize()
      cameraRef.current.position.copy(center).addScaledVector(viewDirection, distance)
      controlsRef.current.target.copy(center)
      controlsRef.current.update()
      hasFramedModelsRef.current = true
    }
  }, [snapshot, selectedModel, viewerReady, meshLoaded])

  const models = useMemo(() => snapshot?.models ?? [], [snapshot])
  const statusLabel = status === 'connected' ? 'LIVE GAZEBO POSES' : status === 'stale' ? 'POSE STREAM STALE' : status === 'disabled' ? 'BRIDGE DISABLED' : status === 'unauthorized' ? 'ACCESS REQUIRED' : status === 'offline' ? 'STREAM OFFLINE' : status === 'checking' ? 'CHECKING BACKEND' : 'AWAITING GAZEBO'

  return <section className="panel gazebo-mirror-panel" aria-label="Gazebo live pose mirror">
    <div className="panel-heading">
      <div><span className="eyebrow">GAZEBO SIMULATION MIRROR · READ-ONLY POSE STREAM</span><h3><Box size={16}/> X500 models <span className="count-tag">{models.length}</span></h3></div>
      <span className={`gazebo-stream-state ${status}`}><i/>{statusLabel}</span>
    </div>
    {error && <div className="gazebo-mirror-error" role="status"><AlertTriangle size={14}/>{error}</div>}
    <div className="gazebo-mirror-layout">
      <div className="gazebo-mirror-view">
        <div className="gazebo-mirror-canvas" ref={hostRef}/>
        {!snapshot?.models.length && <div className="gazebo-mirror-wait" role="status">Waiting for actual Gazebo /world/{snapshot?.world ?? 'default'}/pose/info data</div>}
        {viewerError && <div className="gazebo-mirror-error viewer" role="alert"><AlertTriangle size={14}/>{viewerError}</div>}
        <div className="gazebo-view-controls"><Rotate3D size={12}/> Drag to orbit · scroll to zoom</div>
      </div>
      <div className="gazebo-model-list" aria-label="Gazebo model selection">
        <span className="eyebrow">SIMULATOR MODEL POSES · WORLD {snapshot?.world?.toUpperCase() ?? '—'}</span>
        {models.length === 0 ? <p className="gazebo-model-empty">No X500 model poses received.</p> : models.map(model => <GazeboModelRow key={model.model_name} model={model} selected={selectedModel === model.model_name} onSelect={() => onSelect(model.model_name)}/>)}
      </div>
    </div>
    <p className="gazebo-mirror-note">Position and orientation come from Gazebo's live pose topic. The renderer uses the PX4 X500 model mesh assets; Gazebo world geometry, lighting, camera/video, and physics overlays are not streamed.</p>
  </section>
}

function updateModel(
  scene: THREE.Scene,
  template: THREE.Group,
  model: GazeboPoseModel,
  groups: Map<string, THREE.Group>,
  trails: Map<string, THREE.Line>,
  selected: string | null,
) {
  let group = groups.get(model.model_name)
  if (!group) {
    group = template.clone(true)
    group.userData.modelName = model.model_name
    group.userData.selected = selected === model.model_name
    const label = modelLabel(model.model_name, selected === model.model_name)
    if (label) group.add(label)
    groups.set(model.model_name, group)
    scene.add(group)
  }
  group.position.set(model.position_m.x, model.position_m.y, model.position_m.z)
  group.quaternion.set(
    model.orientation_quaternion.x,
    model.orientation_quaternion.y,
    model.orientation_quaternion.z,
    model.orientation_quaternion.w,
  ).normalize()
  if (group.userData.selected !== (selected === model.model_name)) {
    const label = group.children.find(child => child instanceof THREE.Sprite) as THREE.Sprite | undefined
    if (label) {
      label.material.map?.dispose()
      label.material.dispose()
      group.remove(label)
    }
    group.userData.selected = selected === model.model_name
    const next = modelLabel(model.model_name, selected === model.model_name)
    if (next) group.add(next)
  }
  const lastTrailPoint = model.flight_trail[model.flight_trail.length - 1]
  const trailVersion = lastTrailPoint?.received_at ?? ''
  const selectionVersion = selected === model.model_name
  if (group.userData.trailVersion === trailVersion && group.userData.trailSelected === selectionVersion) return
  group.userData.trailVersion = trailVersion
  group.userData.trailSelected = selectionVersion
  const priorTrail = trails.get(model.model_name)
  if (priorTrail) {
    scene.remove(priorTrail)
    priorTrail.geometry.dispose()
    ;(priorTrail.material as THREE.Material).dispose()
    trails.delete(model.model_name)
  }
  if (model.flight_trail.length > 1) {
    const positions = model.flight_trail.map(point => new THREE.Vector3(point.x, point.y, point.z))
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(positions),
      new THREE.LineBasicMaterial({ color: selected === model.model_name ? '#55d9ff' : '#54758a', transparent: true, opacity: 0.8 }),
    )
    scene.add(line)
    trails.set(model.model_name, line)
  }
}

function disposeObject(object: THREE.Object3D) {
  object.traverse(child => {
    const mesh = child as THREE.Mesh
    if (mesh.geometry) mesh.geometry.dispose()
    if (Array.isArray(mesh.material)) mesh.material.forEach(material => material.dispose())
    else if (mesh.material) mesh.material.dispose()
  })
}

function GazeboModelRow({ model, selected, onSelect }: { model: GazeboPoseModel; selected: boolean; onSelect: () => void }) {
  return <button type="button" className={`gazebo-model-row ${selected ? 'selected' : ''}`} aria-pressed={selected} onClick={onSelect}>
    <span className={`gazebo-model-dot ${model.status}`}/><b>{model.model_name.toUpperCase()}</b><small>{model.status.toUpperCase()} · XYZ {model.position_m.x.toFixed(2)}, {model.position_m.y.toFixed(2)}, {model.position_m.z.toFixed(2)} m</small>
  </button>
}
