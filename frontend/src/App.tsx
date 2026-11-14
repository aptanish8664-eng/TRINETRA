import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, ArrowDown, ArrowLeft, ArrowRight, AlertTriangle, Antenna, BarChart3, ChevronDown, Clock3, Compass, Download, Focus, Layers3, LoaderCircle, Pause, Play, Radio, RotateCcw, Shield, Sparkles, Square, WifiOff, Wind } from 'lucide-react'
import { BrandMark } from './components/BrandMark'
import { Map2D } from './components/Map2D'
import { AccessPortal, type AccessPage } from './components/AccessPortal'
import { CommandDecisionPanel } from './components/CommandDecisionPanel'
import { DetectorStatus } from './components/DetectorStatus'
import { Px4MissionControl } from './components/Px4MissionControl'
import { ScenarioWorkspace } from './components/ScenarioWorkspace'
import { AttackLab } from './components/AttackLab'
import { ThreatReplayPanel } from './components/ThreatReplayPanel'
import { api } from './services/api'
import type { MissionDeployment, UserSession } from './services/auth'
import type { MissionState, RunRecord, UAV } from './types'

gsap.registerPlugin(ScrollTrigger)
const ThreeEnvironment = lazy(() => import('./components/ThreeEnvironment').then(module => ({ default: module.ThreeEnvironment })))

type Tab = 'Overview' | 'Mission Control' | 'Scenario Lab' | 'Attack Lab' | 'Fleet' | 'Analytics' | 'History' | 'Research'
type Config = { uav_count: number; task_count: number; seed: number; duration: number; spoofing_type: string; target_uav: string; spoof_start: number; spoof_duration: number; spoof_magnitude: number; response_policy: string; detector_threshold: number | null }
const initialConfig: Config = { uav_count: 4, task_count: 10, seed: 21, duration: 180, spoofing_type: 'sudden', target_uav: 'UAV-02', spoof_start: 15, spoof_duration: 35, spoof_magnitude: 3, response_policy: 'adaptive', detector_threshold: null }
const tabs: Tab[] = ['Overview', 'Mission Control', 'Scenario Lab', 'Attack Lab', 'Fleet', 'Analytics', 'History', 'Research']
const tabRoutes: Record<Tab, string> = {
  Overview: '/',
  'Mission Control': '/mission-control',
  'Scenario Lab': '/scenario-lab',
  'Attack Lab': '/attack-lab',
  Fleet: '/fleet',
  Analytics: '/analytics',
  History: '/history',
  Research: '/research',
}
const routeTabs: Record<string, Tab> = Object.fromEntries(
  Object.entries(tabRoutes).map(([tab, route]) => [route, tab]),
) as Record<string, Tab>

function accessPageForPath(path: string, authenticated: boolean): AccessPage | null {
  if (path === '/general') return 'general'
  if (path === '/register') return authenticated ? null : 'register'
  if (path === '/login') return authenticated ? null : 'login'
  if (path !== '/' && !authenticated) return 'login'
  return null
}

function SectionTitle({ eyebrow, title, note }: { eyebrow: string; title: string; note?: string }) {
  return <div className="section-title"><div><div className="eyebrow">{eyebrow}</div><h2>{title}</h2></div>{note && <p>{note}</p>}</div>
}

function Metric({ label, value, hint, tone = '' }: { label: string; value: string | number; hint?: string; tone?: string }) {
  return <div className={`metric-card ${tone}`}><span className="metric-label">{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</div>
}

function TelemetryChart({ values, threshold, title = 'Trajectory residual' }: { values: number[]; threshold: number; title?: string }) {
  const data = values.map((residual, index) => ({ step: index + 1, residual }))
  const maximum = Math.max(threshold * 2, ...values, .1)
  return <div className="chart-card"><div className="chart-head"><div><span className="eyebrow">LIVE TELEMETRY</span><h3>{title}</h3></div><span className="chart-unit">RESIDUAL · SIM UNITS</span></div>
    <div className="telemetry-chart" role="img" aria-label={`${title} chart`}><ResponsiveContainer width="100%" height="100%"><LineChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}><CartesianGrid stroke="#24394d" strokeDasharray="2 7"/><XAxis dataKey="step" tick={{ fill: '#71899b', fontSize: 8 }} axisLine={{ stroke: '#34485b' }} tickLine={false}/><YAxis domain={[0, maximum]} tick={{ fill: '#71899b', fontSize: 8 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: '#0b1727', border: '1px solid #31506a', color: '#eaf5fc', fontSize: 10 }} labelStyle={{ color: '#90a9bb' }}/><ReferenceLine y={threshold} stroke="#f5b94c" strokeDasharray="5 5"/><Line type="monotone" dataKey="residual" stroke="#55d9ff" strokeWidth={2} dot={false} activeDot={{ r: 4, fill: '#a7e8ff' }} isAnimationActive={false}/></LineChart></ResponsiveContainer></div><div className="chart-foot"><span><i className="chart-key cyan" /> OBSERVED RESIDUAL</span><span><i className="chart-key amber" /> CALIBRATED LIMIT {threshold.toFixed(3)}</span><span>{values.length} SAMPLES</span></div>
  </div>
}

function Overview({ onNavigate, onCreate }: { onNavigate: (tab: Tab) => void; onCreate: () => void }) {
  const story = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !story.current) return
    const ctx = gsap.context(() => {
      gsap.utils.toArray<HTMLElement>('.story-beat').forEach((beat) => gsap.fromTo(beat, { y: 44, opacity: .25 }, { y: 0, opacity: 1, duration: .8, ease: 'power2.out', scrollTrigger: { trigger: beat, start: 'top 78%', end: 'top 40%', scrub: .7 } }))
      gsap.to('.hero-orbit', { rotation: 15, duration: 24, ease: 'none', repeat: -1, yoyo: true })
    }, story)
    return () => ctx.revert()
  }, [])
  return <div className="overview-page" ref={story}>
    <section className="hero">
      <div className="hero-image" aria-hidden="true"><picture><source srcSet="/images/qilian-aerial.webp" type="image/webp"/><img src="/images/terrain-horizon.svg" alt="" /></picture></div>
      <div className="hero-shade" />
      <motion.div className="hero-content" initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .8, ease: 'easeOut' }}>
        <div className="hero-kicker"><span className="live-dot" /> CYBER-RESILIENT MULTI-UAV RESEARCH PLATFORM</div>
        <h1>See the signal.<br /><em>Keep the mission.</em></h1>
        <p className="hero-descriptor">AUTONOMOUS AERIAL INTELLIGENCE</p>
        <p className="hero-copy">When navigation data turns uncertain, mission continuity is engineered through sensing, assessment, and adaptation.</p>
        <div className="hero-actions"><button className="button-primary" onClick={onCreate}><Play size={15} fill="currentColor" /> CREATE A LIVE MISSION</button><button className="button-quiet" onClick={() => onNavigate('Research')}>EXPLORE THE RESEARCH <ArrowRight size={15} /></button></div>
        <div className="hero-wordmark"><BrandMark compact /><span>SURVEIL. DETECT. PROTECT.</span><i /> <span>MISSION CONTINUITY. ENGINEERED.</span></div>
      </motion.div>
      <div className="hero-telemetry"><span>FIELD MODEL / 00</span><span>SIMULATION READY</span><span>LATENCY —</span></div>
      <button className="scroll-cue" onClick={() => document.getElementById('story')?.scrollIntoView({ behavior: 'smooth' })}><span>SCROLL TO EXPLORE</span><ArrowDown size={14} /></button>
      <div className="hero-orbit" aria-hidden="true"><span /><span /><span /><i /></div>
    </section>
    <section className="story-intro" id="story"><div className="eyebrow">THE RESILIENCE LOOP <span>— 01 / 04</span></div><h2>One altered coordinate<br />can change the whole picture.</h2><p>TRINETRA is a software-only research demonstrator. It makes position deception visible, evaluates telemetry, and measures what happens when a fleet adapts.</p></section>
    <section className="story-grid">
      <article className="story-beat"><div className="story-index">01 <span>DEPLOY</span></div><div className="story-graphic fleet-graphic"><img className="story-photo" src="/images/drone-flight.webp" alt="A small camera drone in flight beneath a sunset sky" loading="lazy"/><div className="graphic-grid"/><span className="mini-uav u1">◢</span><span className="mini-uav u2">◢</span><span className="mini-uav u3">◢</span><i className="graphic-route r1"/><i className="graphic-route r2"/><b>FLEET INITIALISED</b><small>WAYPOINTS ASSIGNED · TELEMETRY STREAM</small></div><div className="story-copy"><h3>Mission geometry,<br />made legible.</h3><p>Seeded starts and task locations create repeatable runs on a clearly labeled abstract coordinate plane.</p></div></article>
      <article className="story-beat"><div className="story-index">02 <span>DETECT</span></div><div className="story-graphic spoof-graphic"><div className="graphic-grid"/><i className="expected-path"/><i className="reported-path"/><span className="spoof-aircraft">◢</span><span className="spoof-pulse"/><b>POSITION DIVERGENCE</b><small>REPORTED GPS / GROUND-TRUTH VIEW</small></div><div className="story-copy"><h3>Deception, shown<br />as evidence.</h3><p>The simulator separates the hidden truth state from reported GPS. Detection consumes reported trajectory and mission telemetry only.</p></div></article>
      <article className="story-beat"><div className="story-index">03 <span>ADAPT</span></div><div className="story-graphic adapt-graphic"><div className="graphic-grid"/><span className="fleet-node n1">01</span><span className="fleet-node n2">02</span><span className="fleet-node n3">03</span><i className="handoff-line"/><b>CAPACITY-AWARE RESPONSE</b><small>ASSESS · ISOLATE · REASSIGN</small></div><div className="story-copy"><h3>Continuity,<br />measured honestly.</h3><p>Compare baseline and adaptive policies on matched seeds. Results reflect each run; improvement is not assumed.</p></div></article>
    </section>
    <section className="overview-cta"><div><span className="eyebrow">ENTER THE SYSTEM</span><h2>A real simulation.<br /><em>A live operational view.</em></h2></div><div><p>Deploy a deterministic fleet, configure a GPS anomaly, inspect its response in 2D or 3D, and replay the recorded run.</p><button className="button-primary" onClick={onCreate}>OPEN MISSION CONTROL <ArrowRight size={15} /></button></div><div className="cta-glow" /></section>
  </div>
}

function App() {
  const [tab, setTab] = useState<Tab>(() => routeTabs[window.location.pathname] ?? 'Overview')
  const [offlineOpen, setOfflineOpen] = useState(false)
  const [accessPage, setAccessPage] = useState<AccessPage | null>(() =>
    accessPageForPath(window.location.pathname, false),
  )
  const [operatorSession, setOperatorSession] = useState<UserSession | null>(null)
  const [config, setConfig] = useState<Config>(initialConfig)
  const [sharedScenarioId, setSharedScenarioId] = useState<string | null>(null)
  const [deployNotice, setDeployNotice] = useState('')
  const [runId, setRunId] = useState<string | null>(null)
  const [state, setState] = useState<MissionState | null>(null)
  const [trajectory, setTrajectory] = useState<MissionState[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [mapMode, setMapMode] = useState<'2D' | '3D'>('2D')
  const [connected, setConnected] = useState(false)
  const [apiReady, setApiReady] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [actionNotice, setActionNotice] = useState('')
  const [commandRefresh, setCommandRefresh] = useState(0)
  const [series, setSeries] = useState<Record<string, number[]>>({})
  const [history, setHistory] = useState<RunRecord[]>([])
  const [experiment, setExperiment] = useState<Record<string, unknown> | null>(null)
  const [replayId, setReplayId] = useState<string | null>(null)
  const [replayFrames, setReplayFrames] = useState<MissionState[]>([])
  const [replayIndex, setReplayIndex] = useState(0)
  const [replayPlaying, setReplayPlaying] = useState(false)
  const [researchOpen, setResearchOpen] = useState('Detector')
  const storyTopRef = useRef<HTMLDivElement>(null)
  const lastWsTick = useRef(-1)
  const apiReadyRef = useRef<boolean | null>(null)
  const canCreateMission = operatorSession?.permissions.includes('mission.create') ?? false
  const canControlSimulation = operatorSession?.permissions.includes('simulation.control') ?? false
  const canCreateExperiment = operatorSession?.permissions.includes('experiment.create') ?? false
  const canExportAnalytics = operatorSession?.permissions.includes('analytics.export') ?? false

  useEffect(() => {
    let disposed = false
    let timer = 0
    const check = async () => {
      const controller = new AbortController()
      const timeout = window.setTimeout(() => controller.abort(), 5000)
      try {
        const health = await api.health(controller.signal)
        if (!disposed) {
          apiReadyRef.current = health.status === 'ready'
          setApiReady(apiReadyRef.current)
        }
      } catch {
        if (!disposed) {
          apiReadyRef.current = false
          setApiReady(false)
        }
      } finally {
        window.clearTimeout(timeout)
        if (!disposed) timer = window.setTimeout(() => { void check() }, apiReadyRef.current ? 30000 : 15000)
      }
    }
    void check()
    return () => { disposed = true; window.clearTimeout(timer) }
  }, [])
  useEffect(() => {
    let disposed = false
    void api.session().then(session => {
      if (!disposed) {
        setOperatorSession(session)
        const route = window.location.pathname
        setTab(routeTabs[route] ?? 'Overview')
        setAccessPage(accessPageForPath(route, true))
      }
    }).catch(() => {
      if (!disposed) {
        setOperatorSession(null)
        const route = window.location.pathname
        setAccessPage(accessPageForPath(route, false) ?? 'login')
      }
    })
    return () => { disposed = true }
  }, [])
  useEffect(() => {
    const syncRoute = () => {
      const route = window.location.pathname
      setAccessPage(accessPageForPath(route, Boolean(operatorSession)))
      if (operatorSession) setTab(routeTabs[route] ?? 'Overview')
    }
    window.addEventListener('popstate', syncRoute)
    return () => window.removeEventListener('popstate', syncRoute)
  }, [operatorSession])
  useEffect(() => {
    const handleUnauthorized = () => {
      setOperatorSession(null); setRunId(null); setState(null); setTrajectory([]); setHistory([])
      setReplayId(null); setReplayFrames([]); setExperiment(null); setAccessPage('login')
      window.history.replaceState({}, '', '/login')
      window.scrollTo({ top: 0, behavior: 'smooth' })
    }
    window.addEventListener('trinetra:unauthorized', handleUnauthorized)
    return () => window.removeEventListener('trinetra:unauthorized', handleUnauthorized)
  }, [])
  useEffect(() => {
    if (!runId) return
    lastWsTick.current = -1
    let socket: WebSocket | null = null
    let retryTimer = 0
    let disposed = false
    const connect = () => {
      socket = api.socket(runId)
      socket.onopen = () => setConnected(true)
      socket.onclose = () => {
        setConnected(false)
        if (!disposed) retryTimer = window.setTimeout(connect, 1500)
      }
      socket.onerror = () => setConnected(false)
      socket.onmessage = (message) => {
        const next = JSON.parse(message.data) as MissionState
        setState(next)
        const isNewStep = next.tick !== lastWsTick.current
        lastWsTick.current = next.tick
        if (isNewStep) setTrajectory(previous => previous.length && previous[previous.length - 1].tick === next.tick ? previous : [...previous, next].slice(-180))
        setSeries(previous => {
          const updated = { ...previous }
          for (const [id, uav] of Object.entries(next.uavs)) {
            const old = updated[id] ?? []
            updated[id] = isNewStep ? [...old, uav.residual].slice(-96) : old
          }
          return updated
        })
      }
    }
    connect()
    return () => { disposed = true; window.clearTimeout(retryTimer); socket?.close(); setConnected(false) }
  // Each run owns one observer connection; disconnecting never changes the authoritative simulation.
  }, [runId])

  useEffect(() => {
    if (!replayPlaying || replayFrames.length < 2) return
    const timer = window.setInterval(() => setReplayIndex(index => {
      if (index >= replayFrames.length - 1) { setReplayPlaying(false); return index }
      return index + 1
    }), 250)
    return () => window.clearInterval(timer)
  }, [replayPlaying, replayFrames.length])

  const currentState = replayId ? replayFrames[replayIndex] ?? null : state
  const selectedUav: UAV | undefined = currentState?.uavs[selected ?? ''] ?? Object.values(currentState?.uavs ?? {})[0]
  const changeTab = (next: Tab) => {
    if (!operatorSession) {
      navigateAccess('login')
      return
    }
    window.history.pushState({}, '', tabRoutes[next])
    setAccessPage(null)
    setTab(next)
    window.scrollTo({ top: next === 'Overview' ? 0 : 110, behavior: 'smooth' })
  }
  const navigateAccess = (page: AccessPage) => {
    window.history.pushState({}, '', page === 'general' ? '/general' : page === 'register' ? '/register' : '/login')
    setAccessPage(page)
  }
  const openAccess = () => { navigateAccess('login'); window.scrollTo({ top: 0, behavior: 'smooth' }) }
  const handleAuthenticated = (session: UserSession) => {
    setOperatorSession(session)
    const route = window.location.pathname
    setTab(routeTabs[route] ?? 'Overview')
    setAccessPage(accessPageForPath(route, true))
    if (!routeTabs[route] && route !== '/general') window.history.replaceState({}, '', '/')
    setError('')
  }
  const signOut = async () => {
    try { await api.logout() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not sign out') }
    finally {
      window.dispatchEvent(new Event('trinetra:logout'))
      setOperatorSession(null); setRunId(null); setState(null); setTrajectory([]); setHistory([])
      setReplayId(null); setReplayFrames([]); setExperiment(null); setAccessPage('login'); setTab('Overview'); window.history.replaceState({}, '', '/login')
    }
  }
  const createMission = useCallback(async (start = false) => {
    if (!operatorSession) { openAccess(); return }
    if (!operatorSession.permissions.includes('mission.create')) { setError('Your application role does not allow mission creation.'); return }
    setBusy(true); setError(''); setReplayId(null); setReplayFrames([])
    try {
      const result = await api.create(config)
      setRunId(result.id); setState(result.state); setTrajectory([result.state]); setSelected(config.target_uav); setSeries({}); setTab('Mission Control')
      if (start) {
        const started = await api.control(result.id, 'start')
        setState(started)
      }
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not create mission') }
    finally { setBusy(false) }
  }, [config, operatorSession])
  const control = async (action: string) => {
    if (!runId) return
    setBusy(true); setError(''); setActionNotice('')
    try {
      if (action === 'stop' || action === 'reset') {
        await api.requestCommandDecision(action, runId)
        setActionNotice(`${action.toUpperCase()} request submitted for General review.`)
        setCommandRefresh(value => value + 1)
        return
      }
      const next = await api.control(runId, action)
      setState(next)
      setReplayId(null)
      if (action === 'reset') setTrajectory([next])
      else if (action === 'step') setTrajectory(previous => previous.length && previous[previous.length - 1].tick === next.tick ? previous : [...previous, next].slice(-180))
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Simulation control failed') }
    finally { setBusy(false) }
  }
  const loadHistory = async () => {
    try { setHistory((await api.history()).runs) } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not load history') }
  }
  const loadReplay = async (id: string) => {
    setError(''); setBusy(true)
    try { const record = await api.replay(id); setReplayId(id); setReplayFrames(record.snapshots); setTrajectory(record.snapshots); setReplayIndex(0); setReplayPlaying(false); setRunId(null); setState(null); setTab('History') }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not load replay') }
    finally { setBusy(false) }
  }
  const runComparison = async () => {
    setBusy(true); setError('')
    try { setExperiment(await api.compare(config)); setTab('Analytics') }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Experiment failed') }
    finally { setBusy(false) }
  }
  const exportRun = async (id: string) => {
    try { const result = await api.export(id); const url = URL.createObjectURL(new Blob([result.content], { type: 'text/csv' })); const link = document.createElement('a'); link.href = url; link.download = result.filename; link.click(); URL.revokeObjectURL(url) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Export failed') }
  }
  const exportExperiment = async (id: string) => {
    try { const result = await api.exportExperiment(id); const url = URL.createObjectURL(new Blob([result.content], { type: 'text/csv' })); const link = document.createElement('a'); link.href = url; link.download = result.filename; link.click(); URL.revokeObjectURL(url) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Experiment export failed') }
  }
  const metricSeries = useMemo(() => replayId
    ? replayFrames.slice(0, replayIndex + 1).flatMap(frame => frame.uavs[selectedUav?.id ?? ''] ? [frame.uavs[selectedUav?.id ?? ''].residual] : []).slice(-96)
    : series[selectedUav?.id ?? ''] ?? [], [replayId, replayFrames, replayIndex, series, selectedUav?.id])

  return <div className={`app-shell ${!canExportAnalytics ? 'no-analytics-export' : ''}`}>
    <header className="topbar">
      <button className="brand-button" onClick={() => operatorSession && accessPage !== 'general' ? changeTab('Overview') : navigateAccess(operatorSession?.user.role === 'GENERAL' ? 'general' : 'login')}><BrandMark /></button>
      {operatorSession && accessPage !== 'general' && <nav className="main-nav" aria-label="Main navigation">{tabs.map(item => <button key={item} className={tab === item && !accessPage ? 'active' : ''} onClick={() => changeTab(item)}>{item}</button>)}</nav>}
      <div className="topbar-right">
        {accessPage !== 'general' && <span className={`connection-pill ${apiReady && (!runId || connected) ? 'online' : apiReady === false || runId ? 'offline' : ''}`}>{apiReady ? runId && !connected ? <><WifiOff size={13}/> STREAM OFFLINE · RETRYING</> : <><i /> SYSTEM {connected ? 'LIVE' : 'READY'}</> : apiReady === false ? <><WifiOff size={13}/> API OFFLINE</> : <><LoaderCircle size={13} className="spin"/> CONNECTING</>}</span>}
        {operatorSession && accessPage === 'general' && <span className="mono tiny">{operatorSession.user.full_name} · {operatorSession.user.role}</span>}
        {operatorSession && accessPage === 'general' && <button className="access-nav-button" onClick={() => changeTab('Overview')}><ArrowLeft size={14}/> RETURN TO HOME</button>}
        {operatorSession?.user.role === 'GENERAL' && <button className={`access-nav-button ${accessPage === 'general' ? 'active' : ''}`} onClick={() => navigateAccess('general')}>GENERAL</button>}
        {operatorSession && <button className={`access-nav-button ${accessPage ? 'active' : ''}`} onClick={() => void signOut()}><Shield size={14}/> SIGN OUT</button>}
        {operatorSession && accessPage !== 'general' && <button className="icon-button" title="Go to mission control" onClick={() => changeTab('Mission Control')}><Focus size={17}/></button>}
      </div>
    </header>
    {error && <div className="error-banner"><AlertTriangle size={16}/><span>{error}</span><button onClick={() => setError('')}>DISMISS</button></div>}

    {accessPage && <AccessPortal page={accessPage} onPageChange={navigateAccess} onExit={() => operatorSession ? changeTab('Overview') : navigateAccess('login')} onAuthenticated={handleAuthenticated} userSession={operatorSession} apiReady={apiReady} streamConnected={connected} hasActiveRun={Boolean(runId)}/>}
    {!accessPage && tab === 'Overview' && <Overview onNavigate={changeTab} onCreate={() => changeTab('Scenario Lab')} />}

    {!accessPage && tab !== 'Overview' && <main className="workspace" ref={storyTopRef}>
      <div className="workspace-head"><div><div className="breadcrumb"><span>TRINETRA</span><span>/</span><b>{tab.toUpperCase()}</b></div><SectionTitle eyebrow={tab === 'Mission Control' ? 'LIVE OPERATIONS' : tab === 'Scenario Lab' ? 'UNIFIED MISSION WORKSPACE' : tab === 'Attack Lab' ? 'SIMULATED ATTACK EXPERIMENTS' : 'RESEARCH WORKSPACE'} title={tab === 'Mission Control' ? 'Mission Control' : tab} note={tab === 'Mission Control' ? 'Authenticated PX4 telemetry · live geographic view' : tab === 'Research' ? 'Research methods and the offline abstract simulation' : undefined}/></div><div className="workspace-head-right">{currentState && tab !== 'Mission Control' && <span className={`mission-state ${currentState.status}`}><i/>{currentState.status.toUpperCase()}</span>}<span className="mono tiny">TRN / LAB / 01</span></div></div>

      {tab === 'Mission Control' && <Px4MissionControl notice={deployNotice} onDismissNotice={() => setDeployNotice('')} onOpenScenarioLab={() => changeTab('Scenario Lab')}
        canManageFleet={operatorSession?.permissions.includes('scenario.create') ?? false}/>}

      {!accessPage && <div hidden={tab !== 'Scenario Lab'}>
      <ScenarioWorkspace
        key={`${operatorSession?.user.id ?? 'anonymous'}:${operatorSession?.user.role ?? 'none'}`}
        userId={operatorSession?.user.id ?? ''}
        config={config}
        onLoadConfig={setConfig}
        sharedScenarioId={sharedScenarioId}
        onScenarioSelected={setSharedScenarioId}
        canManage={operatorSession?.permissions.includes('scenario.create') ?? false}
        isOperator={operatorSession?.user.role === 'OPERATOR'}
        active={tab === 'Scenario Lab'}
        onOpenAttackLab={() => changeTab('Attack Lab')}
        onMissionDeployed={(deployment: MissionDeployment, note?: string) => {
          setSharedScenarioId(deployment.scenario_id)
          const version = typeof deployment.scenario_version_number === 'number' ? ` version ${deployment.scenario_version_number}` : ''
          setDeployNotice(`Deployed ${deployment.scenario_title ?? 'the mission'}${version} to Mission Control. Nothing was armed, flown or uploaded to PX4.${note ? ` ${note}` : ''}`)
          changeTab('Mission Control')  // also updates the address bar, so Back returns to Scenario Lab
        }}
      />
      </div>}

      {tab === 'Attack Lab' && <AttackLab canEdit={operatorSession?.user.role === 'GENERAL'} initialScenarioId={sharedScenarioId}
        onOpenScenarioLab={scenarioId => { if (scenarioId) setSharedScenarioId(scenarioId); changeTab('Scenario Lab') }}/>}

      {tab === 'Fleet' && <section className="panel fleet-page"><div className="panel-heading"><div><span className="eyebrow">LIVE STATE INSPECTOR</span><h3>{currentState ? `${Object.keys(currentState.uavs).length} active fleet records` : 'No active mission'}</h3></div>{currentState && <span className="mission-state running"><i/>TICK {currentState.tick}</span>}</div>{currentState ? <div className="fleet-table">{Object.values(currentState.uavs).map(uav => <button className={`fleet-record ${selected === uav.id ? 'chosen' : ''}`} key={uav.id} onClick={() => setSelected(uav.id)}><span className="uav-glyph"><Wind size={16}/></span><b>{uav.id}</b><span>{uav.status.toUpperCase()}</span><span>GT {uav.position.x.toFixed(2)}, {uav.position.y.toFixed(2)}</span><span>GPS {uav.gps.x.toFixed(2)}, {uav.gps.y.toFixed(2)}</span><span>{uav.task_id ?? 'NO TASK'}</span><span>{uav.anomaly_score.toFixed(1)}×</span></button>)}</div> : <button className="button-primary" onClick={() => void createMission(true)}>CREATE A MISSION</button>}</section>}

      {tab === 'Analytics' && <div className="analytics-layout"><section className="panel analytics-run"><span className="eyebrow">PAIRED POLICY STUDY</span><h3>Baseline versus adaptive response</h3><p>Both runs use the same seed, initial coordinates, task locations, and spoof scenario. Metrics are computed from simulation traces and are not expected to favor either policy.</p><div className="study-meta"><span>SEED <b>{config.seed}</b></span><span>FLEET <b>{config.uav_count}</b></span><span>SCENARIO <b>{config.spoofing_type.toUpperCase()}</b></span></div><button className="button-primary" onClick={() => void runComparison()} disabled={busy || !apiReady || (Boolean(operatorSession) && !canCreateExperiment)}>{busy ? <LoaderCircle className="spin" size={15}/> : <BarChart3 size={15}/>} RUN PAIRED EXPERIMENT</button>{operatorSession && !canCreateExperiment && <p className="permission-note">Your role is read-only for experiment creation.</p>}<p className="formula-note">Completion rate = completed tasks / configured tasks. Detection delay is measured in simulation ticks from configured onset to first persistent detection event. Precision and recall are run-level indicators in this initial demonstrator.</p></section>
        {experiment ? <ExperimentResults result={experiment} onExport={canExportAnalytics ? id => void exportExperiment(id) : undefined}/> : <div className="analytics-placeholder panel"><BarChart3 size={27}/><b>Measured outcomes appear here.</b><span>Run the paired experiment to compute result tables and compare mission continuity.</span></div>}</div>}

      {tab === 'History' && <section className="panel history-panel"><div className="panel-heading"><div><span className="eyebrow">PERSISTED RUNS · SQLITE</span><h3>Mission history & replay</h3></div><button className="button-secondary" onClick={() => void loadHistory()}><RotateCcw size={14}/> REFRESH HISTORY</button></div>{replayId && currentState && <div className="replay-current"><span className="eyebrow">REPLAYING STORED SNAPSHOT</span><b>{replayId} · tick {currentState.tick} · {currentState.progress.completed}/{currentState.progress.total} tasks</b><div className="replay-inline-controls"><button onClick={() => setReplayPlaying(!replayPlaying)}>{replayPlaying ? <Pause size={14}/> : <Play size={14}/>} {replayPlaying ? 'PAUSE' : 'PLAY'}</button><input type="range" min="0" max={Math.max(0,replayFrames.length-1)} value={replayIndex} onChange={event => setReplayIndex(Number(event.target.value))}/><button onClick={() => void exportRun(replayId)}><Download size={14}/> CSV</button></div></div>}{history.length ? <div className="history-list">{history.map(record => <article key={record.id} className="history-row"><span className="history-symbol"><Clock3 size={16}/></span><div><b>{record.id}</b><small>{new Date(record.created_at).toLocaleString()} · seed {String(record.config.seed)} · {String(record.config.spoofing_type)}</small></div><span className={`mission-state ${record.status}`}><i/>{record.status}</span><button className="button-secondary" onClick={() => void loadReplay(record.id)}>REPLAY <Play size={13}/></button><button className="small-icon" title="Export run CSV" onClick={() => void exportRun(record.id)}><Download size={15}/></button></article>)}</div> : <div className="empty-inline history-empty">{apiReady ? 'No persisted runs loaded. Refresh to check the local SQLite history.' : 'Connect the backend to inspect persisted runs.'}</div>}</section>}

      {tab === 'Research' && <>
        <Research open={researchOpen} setOpen={setResearchOpen}/>
        <ThreatReplayPanel canRecord={operatorSession?.permissions.includes('scenario.create') ?? false}/>
        <section className="panel offline-simulation" aria-label="Offline research simulation">
          <div className="panel-heading"><div><span className="eyebrow">OFFLINE RESEARCH SIMULATION</span><h3>Deterministic abstract simulator <span className="count-tag">NOT LIVE PX4</span></h3></div>
            <button type="button" className="button-secondary" aria-expanded={offlineOpen} onClick={() => setOfflineOpen(value => !value)}>{offlineOpen ? 'HIDE SIMULATION' : 'OPEN SIMULATION'}</button></div>
          <p className="permission-note">Software-only abstract coordinates, unchanged from the original simulator: detection, threat scoring and task reassignment are validated here only, never on live PX4 data.</p>
          {offlineOpen && <>
        {!replayId && <CommandDecisionPanel
          runId={runId ?? ''}
          currentUserId={operatorSession?.user.id ?? ''}
          canRequest={operatorSession?.permissions.includes('command.request') ?? false}
          canApprove={operatorSession?.permissions.includes('command.approve') ?? false}
          refreshKey={commandRefresh}
          onExecuted={next => {
            if (runId) {
              setState(next)
              setTrajectory(previous => [...previous.slice(-179), next])
            }
          }}
        />}
        {!currentState ? <section className="empty-mission panel"><div className="empty-orbit"><Antenna size={30}/><span/><span/></div><div className="eyebrow">READY TO DEPLOY</div><h2>Build a repeatable mission scenario.</h2><p>Configure a deterministic fleet run. The live map, telemetry, event stream, and recorded replay will all use the simulation backend.</p><button className="button-primary" onClick={() => void createMission(true)} disabled={busy || !apiReady || (Boolean(operatorSession) && !canCreateMission)}>{busy ? <LoaderCircle className="spin" size={15}/> : <Play size={15} fill="currentColor"/>} CREATE & DEPLOY FLEET</button>{operatorSession && !canCreateMission && <p className="permission-note">Your role is read-only for mission creation.</p>}<button className="inline-link" onClick={() => changeTab('Scenario Lab')}>CONFIGURE SCENARIO <ArrowRight size={14}/></button></section> : <>
          <div className="mission-toolbar panel"><div className="toolbar-run"><div className="status-orb"><Radio size={18}/></div><div><span className="eyebrow">ACTIVE RUN <b>{runId ?? replayId}</b></span><strong>{currentState.config.spoofing_type} · seed {currentState.seed}</strong></div></div><div className="toolbar-controls">
            {replayId ? <button className="control-button" onClick={() => setReplayPlaying(value => !value)}>{replayPlaying ? <Pause size={15}/> : <Play size={15}/>} {replayPlaying ? 'PAUSE REPLAY' : 'PLAY REPLAY'}</button> : !canControlSimulation ? <span className="permission-note">Observer access is read-only.</span> : <>
              {currentState.status === 'running' ? <button className="control-button" onClick={() => void control('pause')} disabled={busy}><Pause size={15}/> PAUSE</button> : currentState.status === 'paused' || currentState.status === 'ready' ? <button className="control-button" onClick={() => void control(currentState.status === 'paused' ? 'resume' : 'start')} disabled={busy}><Play size={15} fill="currentColor"/> {currentState.status === 'paused' ? 'RESUME' : 'START'}</button> : <button className="control-button" disabled><Activity size={15}/> {currentState.status.toUpperCase()}</button>}
              <button className="control-button subtle" onClick={() => void control('step')} disabled={busy || ['completed', 'stopped'].includes(currentState.status)}><ChevronDown size={15}/> STEP</button>{!['completed', 'stopped'].includes(currentState.status) && <button className="control-button subtle" onClick={() => void control('stop')} disabled={busy}><Square size={13}/> STOP</button>}<button className="control-button subtle" onClick={() => void control('reset')} disabled={busy}><RotateCcw size={15}/> RESET</button>
            </>}
          </div></div>
          <DetectorStatus detector={currentState.detector} detection={selectedUav?.ml_detection}/>
          {replayId && <div className="replay-ruler panel"><span>RECORDED REPLAY</span><input type="range" min="0" max={Math.max(0, replayFrames.length - 1)} value={replayIndex} onChange={event => setReplayIndex(Number(event.target.value))}/><b>T+{currentState.time.toFixed(1)}s</b><small>{replayIndex + 1} / {replayFrames.length}</small></div>}
          <div className="metrics-row"><Metric label="MISSION PROGRESS" value={`${Math.round(100 * currentState.progress.completed / Math.max(1, currentState.progress.total))}%`} hint={`${currentState.progress.completed} / ${currentState.progress.total} tasks`}/><Metric label="FLEET READY" value={`${Object.values(currentState.uavs).filter(u => u.status === 'active').length} / ${Object.keys(currentState.uavs).length}`} hint="active aircraft"/><Metric label="THREAT EVENTS" value={currentState.events.filter(event => event.type === 'anomaly_detected' || event.type === 'uav_isolated').length} hint="detected from telemetry" tone={currentState.events.some(event => event.type === 'uav_isolated') ? 'metric-alert' : ''}/><Metric label="SIMULATION TIME" value={`${currentState.time.toFixed(1)}s`} hint={`tick ${currentState.tick} · seed ${currentState.seed}`}/></div>
          <div className="mission-grid"><section className="map-panel panel"><div className="panel-heading"><div><span className="eyebrow">TACTICAL PICTURE <span className="live-dot"/></span><h3>Fleet & task geometry</h3></div><div className="view-switch"><button className={mapMode === '2D' ? 'selected' : ''} onClick={() => setMapMode('2D')}><Layers3 size={14}/> 2D</button><button className={mapMode === '3D' ? 'selected' : ''} onClick={() => setMapMode('3D')}><Compass size={14}/> 3D</button></div></div>{mapMode === '2D' ? <Map2D state={currentState} frames={replayId ? replayFrames.slice(0, replayIndex + 1) : trajectory} selected={selected} onSelect={setSelected}/> : <Suspense fallback={<div className="three-loading">INITIALIZING LOCAL 3D ENVIRONMENT...</div>}><ThreeEnvironment state={currentState} frames={replayId ? replayFrames.slice(0, replayIndex + 1) : trajectory} selected={selected} onSelect={setSelected}/></Suspense>}</section>
            <aside className="fleet-panel panel"><div className="panel-heading"><div><span className="eyebrow">AUTONOMOUS FLEET</span><h3>Aircraft status <span className="count-tag">{Object.keys(currentState.uavs).length}</span></h3></div><button className="small-icon" onClick={() => changeTab('Fleet')} title="Fleet detail"><ArrowRight size={15}/></button></div><div className="fleet-list">{Object.values(currentState.uavs).map(uav => <button className={`fleet-item ${selectedUav?.id === uav.id ? 'chosen' : ''}`} key={uav.id} onClick={() => setSelected(uav.id)}><span className={`uav-glyph ${uav.status}`}><Wind size={15}/></span><span className="fleet-primary"><b>{uav.id}</b><small>{uav.task_id ? `TASK ${uav.task_id}` : 'NO ACTIVE TASK'} · {uav.speed.toFixed(2)} u/s</small></span><span className={`threat-tag ${uav.threat_level}`}>{uav.status === 'isolated' ? 'ISOLATED' : uav.threat_level.toUpperCase()}</span></button>)}</div><div className="selected-uav"><div className="selected-head"><div><span className="eyebrow">SELECTED AIRCRAFT</span><h4>{selectedUav?.id ?? '—'}</h4></div><span className={`mission-state ${selectedUav?.status ?? ''}`}><i/>{selectedUav?.status}</span></div>{selectedUav && <div className="telemetry-pairs"><div><span>GROUND TRUTH</span><b>{selectedUav.position.x.toFixed(2)}, {selectedUav.position.y.toFixed(2)}</b></div><div><span>GPS REPORTED</span><b className={Math.hypot(selectedUav.position.x-selectedUav.gps.x, selectedUav.position.y-selectedUav.gps.y) > .28 ? 'text-danger' : ''}>{selectedUav.gps.x.toFixed(2)}, {selectedUav.gps.y.toFixed(2)}</b></div><div><span>RESIDUAL / SCORE</span><b>{selectedUav.residual.toFixed(3)} / {selectedUav.anomaly_score.toFixed(1)}×</b></div><div><span>ASSIGNED TASK</span><b>{selectedUav.task_id ?? 'NONE'}</b></div><div><span>ABSTRACT RESOURCES</span><b>{selectedUav.battery_pct?.toFixed(0) ?? '—'}% · workload {selectedUav.workload ?? '—'}/{selectedUav.workload_capacity ?? '—'}</b></div><div><span>CAPABILITY / LINK</span><b>{selectedUav.capabilities?.join(', ') ?? '—'} · {selectedUav.communications_available === false ? 'NO LINK' : 'LINKED'}</b></div><div><span>PAYLOAD / ENDURANCE</span><b>{selectedUav.payload_capacity_kg?.toFixed(1) ?? '—'} kg · {selectedUav.endurance_remaining_ticks ?? '—'} ticks</b></div></div>}</div></aside>
          </div>
          {!replayId && runId && <ThreatResponsePanel
            runId={runId}
            state={currentState}
            uav={selectedUav}
            canRespond={operatorSession?.permissions.includes('threat.isolate') ?? false}
            onDecisionRequested={() => setCommandRefresh(value => value + 1)}
            onUpdated={next => {
              setState(next)
              setTrajectory(previous => [...previous.slice(-179), next])
            }}
          />}
          {actionNotice && <div className="assistant-message" role="status">{actionNotice}</div>}
          <div className="lower-grid"><TelemetryChart values={metricSeries} threshold={currentState.calibration.threshold}/><section className="event-panel panel"><div className="panel-heading"><div><span className="eyebrow">MISSION EVENT STREAM</span><h3>System decisions</h3></div><span className="event-count">{currentState.events.length} EVENTS</span></div><div className="event-list">{[...currentState.events].slice(-8).reverse().map(event => <article className="event-row" key={event.id}><span className={`event-marker ${event.severity ?? 'info'}`}>{event.type === 'uav_isolated' || event.type === 'anomaly_detected' ? <AlertTriangle size={13}/> : <Activity size={13}/>}</span><div><b>{event.type.replace(/_/g, ' ').toUpperCase()}</b><p>{event.reason ?? `${event.uav_id ?? ''} ${event.task_id ?? ''}`}</p></div><time>T+{event.time.toFixed(0)}</time></article>)}{currentState.events.length === 0 && <div className="empty-inline">No mission events recorded yet.</div>}</div></section></div>
        </>}
          </>}
        </section>
      </>}
    </main>}
    {accessPage !== 'general' && <footer className="footer"><BrandMark compact/><span>TRINETRA · SOFTWARE-ONLY RESEARCH DEMONSTRATOR</span><span>MISSION CONTINUITY. ENGINEERED.</span><span>LOCAL SIMULATION BUILD 0.1</span></footer>}
  </div>
}

function ThreatResponsePanel({ runId, state, uav, canRespond, onUpdated, onDecisionRequested }: {
  runId: string
  state: MissionState
  uav: UAV | undefined
  canRespond: boolean
  onUpdated: (state: MissionState) => void
  onDecisionRequested: () => void
}) {
  const [taskId, setTaskId] = useState('')
  const [targetUavId, setTargetUavId] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const pendingTasks = Object.values(state.tasks).filter(task => task.status === 'pending')
  const activeUavs = Object.values(state.uavs).filter(candidate => candidate.status === 'active')

  useEffect(() => {
    if (!pendingTasks.some(task => task.id === taskId)) setTaskId(pendingTasks[0]?.id ?? '')
    if (!activeUavs.some(candidate => candidate.id === targetUavId)) setTargetUavId(activeUavs[0]?.id ?? '')
  }, [state.tasks, state.uavs, taskId, targetUavId])

  const runAction = async (action: 'acknowledge' | 'isolate' | 'reassign') => {
    if (!uav) return
    setBusy(true); setMessage('')
    try {
      const result = action === 'acknowledge'
        ? await api.acknowledgeAlert(runId, uav.id)
        : action === 'isolate'
          ? null
          : null
      if (action !== 'acknowledge') {
        await api.requestCommandDecision(
          action === 'isolate' ? 'isolate_uav' : 'reassign_task',
          runId,
          action === 'isolate' ? { uav_id: uav.id } : { task_id: taskId, target_uav_id: targetUavId },
        )
        onDecisionRequested()
        setMessage(`${action === 'isolate' ? 'Isolation' : 'Task reassignment'} request submitted for General review.`)
      } else if (result) {
        onUpdated(result.state)
        setMessage('Validated alert acknowledged.')
      }
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : 'Threat response was rejected.')
    } finally { setBusy(false) }
  }

  const activeAlert = Boolean(uav && uav.threat_level !== 'low' && uav.status !== 'isolated')
  const alertNeedsAcknowledgement = activeAlert && !uav?.alert_acknowledged
  const selectedTask = pendingTasks.find(task => task.id === taskId)
  const missionAllowsResponse = ['ready', 'running', 'paused'].includes(state.status)
  return <section className="panel threat-response-panel">
    <div className="panel-heading"><div><span className="eyebrow">SIMULATED THREAT RESPONSE</span><h3>Validated operator actions</h3></div><span className="mono tiny">{uav?.id ?? 'NO AIRCRAFT'}</span></div>
    {!canRespond ? <p className="permission-note">These simulated threat-response actions require the General role.</p> : <>
      <div className="threat-actions">
        <button className="button-secondary" type="button" disabled={busy || !missionAllowsResponse || !alertNeedsAcknowledgement} onClick={() => void runAction('acknowledge')}>ACKNOWLEDGE ALERT</button>
        <button className="button-secondary" type="button" disabled={busy || !missionAllowsResponse || !activeAlert || uav?.status === 'isolated'} onClick={() => void runAction('isolate')}>ISOLATE AIRCRAFT</button>
        <label>Pending task<select value={taskId} onChange={event => setTaskId(event.target.value)} disabled={!pendingTasks.length || busy}>{pendingTasks.map(task => <option value={task.id} key={task.id}>{task.id} · {task.uav_id ?? 'unassigned'}</option>)}</select></label>
        <label>Active aircraft<select value={targetUavId} onChange={event => setTargetUavId(event.target.value)} disabled={!activeUavs.length || busy}>{activeUavs.map(candidate => <option value={candidate.id} key={candidate.id}>{candidate.id}</option>)}</select></label>
        <button className="button-secondary" type="button" disabled={busy || !missionAllowsResponse || !taskId || !targetUavId || selectedTask?.uav_id === targetUavId} onClick={() => void runAction('reassign')}>REASSIGN TASK</button>
      </div>
      {!activeAlert && <small className="permission-note">Alert acknowledgement and isolation require an active validated telemetry alert for the selected aircraft.</small>}
      {message && <div className="assistant-message" role="status">{message}</div>}
    </>}
  </section>
}

function ExperimentResults({ result, onExport }: { result: Record<string, unknown>; onExport?: (id: string) => void }) {
  const baseline = result.baseline as Record<string, unknown>, adaptive = result.adaptive as Record<string, unknown>
  const rows: [string, string, string][] = [['Tasks completed', `${baseline.tasks_completed}/${baseline.tasks_total}`, `${adaptive.tasks_completed}/${adaptive.tasks_total}`], ['Mission completion', `${(Number(baseline.completion_rate)*100).toFixed(1)}%`, `${(Number(adaptive.completion_rate)*100).toFixed(1)}%`], ['Mission continuity / loss', `${(Number(baseline.continuity_score)*100).toFixed(1)}% / ${(Number(baseline.continuity_loss)*100).toFixed(1)}%`, `${(Number(adaptive.continuity_score)*100).toFixed(1)}% / ${(Number(adaptive.continuity_loss)*100).toFixed(1)}%`], ['Completion time', baseline.completion_time_ticks == null ? 'incomplete' : `${baseline.completion_time_ticks} ticks`, adaptive.completion_time_ticks == null ? 'incomplete' : `${adaptive.completion_time_ticks} ticks`], ['Mean task completion tick', baseline.mean_task_completion_tick == null ? '—' : Number(baseline.mean_task_completion_tick).toFixed(1), adaptive.mean_task_completion_tick == null ? '—' : Number(adaptive.mean_task_completion_tick).toFixed(1)], ['Detection delay', baseline.detection_delay_ticks == null ? '—' : `${baseline.detection_delay_ticks} ticks`, adaptive.detection_delay_ticks == null ? '—' : `${adaptive.detection_delay_ticks} ticks`], ['Precision / recall / F1', `${Number(baseline.precision).toFixed(2)} / ${Number(baseline.recall).toFixed(2)} / ${Number(baseline.f1).toFixed(2)}`, `${Number(adaptive.precision).toFixed(2)} / ${Number(adaptive.recall).toFixed(2)} / ${Number(adaptive.f1).toFixed(2)}`], ['Isolated / unnecessary rate', `${baseline.isolated_uavs} / ${(Number(baseline.unnecessary_isolation_rate)*100).toFixed(0)}%`, `${adaptive.isolated_uavs} / ${(Number(adaptive.unnecessary_isolation_rate)*100).toFixed(0)}%`], ['Reassigned / unassigned', `${baseline.reassigned_tasks} / ${baseline.unassigned_tasks}`, `${adaptive.reassigned_tasks} / ${adaptive.unassigned_tasks}`]]
  const baselineRate = Number(baseline.completion_rate) * 100
  const adaptiveRate = Number(adaptive.completion_rate) * 100
  return <section className="panel result-panel">
    <div className="panel-heading"><div><span className="eyebrow">MEASURED RUN OUTPUT</span><h3>Paired experiment results</h3></div><div className="result-actions"><span className="mono tiny">SEED {String((result.metadata as Record<string, unknown>).seed)}</span>{onExport && <button className="small-icon" onClick={() => onExport(String(result.experiment_id))} title="Export paired experiment CSV"><Download size={14}/></button>}</div></div>
    <div className="completion-comparison" aria-label="Task completion comparison"><div><span>BASELINE COMPLETION <b>{baselineRate.toFixed(1)}%</b></span><i><b style={{ width: `${baselineRate}%` }}/></i></div><div><span>ADAPTIVE COMPLETION <b>{adaptiveRate.toFixed(1)}%</b></span><i><b style={{ width: `${adaptiveRate}%` }}/></i></div></div>
    <div className="result-table"><div className="result-head"><span>METRIC</span><b>BASELINE</b><b>ADAPTIVE</b></div>{rows.map(row => <div className="result-row" key={row[0]}><span>{row[0]}</span><b>{row[1]}</b><b>{row[2]}</b></div>)}</div>
    <p className="formula-note">Actual output for this seeded run. The current precision/recall estimate uses run-level anomaly event presence; per-timestep confusion metrics and confidence intervals remain future research work.</p>
  </section>
}

function Research({ open, setOpen }: { open: string; setOpen: (value: string) => void }) {
  const entries = [
    ['Detector', 'A trajectory-residual baseline predicts the next reported coordinate from the previous reported coordinate, assigned destination, speed, and timestep. The detector compares the observation with this prediction. It does not receive ground truth or the scenario attack label. A synthetic clean sensor-noise distribution calibrates the 99.5th-percentile threshold; the calibration distribution and threshold are displayed in Mission Control.'],
            ['Threat & response', 'An anomaly persists over consecutive steps before the threat rises. The adaptive policy degrades the aircraft after two consecutive threshold exceedances and isolates it after three. Remaining tasks are ordered by priority and reassigned to the nearest eligible abstract-simulator aircraft with workload, payload, capability, communications, and endurance reserves; tasks without a suitable recipient remain explicitly unassigned. Resource values are simulated and are not aircraft measurements or flight guarantees.'],
    ['Study design', 'Baseline and adaptive policies share the same seed and scenario configuration. Completion rate is completed tasks divided by configured tasks. Detection delay is expressed in fixed simulation ticks. Current precision, recall, and F1 are run-level event-presence indicators; they are not yet per-timestep classification metrics. One seeded comparison is illustrative and does not establish a general policy advantage.'],
    ['Boundaries & limitations', 'All behavior is a software simulation on abstract coordinate units. The detector currently uses a known commanded destination and a simple movement model; it is not validated against field telemetry. There is no RF transmission, operational navigation interface, physical UAV control, or real GPS coordinate data. The 3D view is an original local terrain-like visualization, not a geographic terrain product.'],
  ]
  return <div className="research-layout"><section className="panel research-intro"><span className="eyebrow">METHODS & LIMITATIONS</span><h3>A demonstrator that shows its assumptions.</h3><p>TRINETRA currently implements a deterministic trajectory-residual baseline and a transparent persistence-based response. Use these notes to interpret observed results.</p><div className="research-cite"><Sparkles size={16}/><span>Build metadata <b>TRN-LAB-0.1</b><small>Fixed timestep · seeded local simulation · no external terrain service</small></span></div></section><section className="research-accordions">{entries.map(([title, body], index) => <article className={`research-entry panel ${open === title ? 'expanded' : ''}`} key={title}><button onClick={() => setOpen(open === title ? '' : title)}><span className="research-number">0{index+1}</span><b>{title}</b><ChevronDown size={16}/></button>{open === title && <p>{body}</p>}</article>)}</section></div>
}

export default App
