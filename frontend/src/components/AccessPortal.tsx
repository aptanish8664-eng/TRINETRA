import { useEffect, useRef, useState, type FormEvent } from 'react'
import { LoopingVideo } from '../visuals/VideoBackdrop'
import { useVisuals } from '../visuals/useVisuals'
import '../visuals/login-scene.css'
import { motion } from 'framer-motion'
import gsap from 'gsap'
import { Activity, ArrowLeft, ArrowRight, Download, Eye, EyeOff, Fingerprint, KeyRound, LockKeyhole, Search, ShieldAlert, ShieldCheck, UserRound, Wifi } from 'lucide-react'
import { BrandMark } from './BrandMark'
import { formatDateTime } from '../services/format'
import type { AccountStatus, ApplicationRole, AuditLogEntry, ManagedUser, RegistrationApplication, RegistrationStatus, ScenarioApprovalRequest, UserSession } from '../services/auth'
import { api } from '../services/api'
import { SavedScenarios } from './SavedScenarios'
import './access.css'

export type AccessPage = 'login' | 'register' | 'general'

type ServiceState = 'checking' | 'online' | 'offline' | 'unverified'

type AccessPortalProps = {
  page: AccessPage
  onPageChange: (page: AccessPage) => void
  onExit: () => void
  onAuthenticated: (session: UserSession) => void
  userSession: UserSession | null
  apiReady: boolean | null
  streamConnected: boolean
  hasActiveRun: boolean
  /** General: open a scenario (and a version) in Scenario Lab, or a quick-made one in Quick Mission. */
  onOpenScenario?: (scenarioId: string, versionNumber?: number) => void
  onOpenQuickMission?: (scenarioId: string) => void
  /** The server ended the signed-in session (expiry or revocation): the sign-in page says so. */
  sessionEnded?: boolean
}

function EyeStages({ active = 0, failed = false }: { active?: number; failed?: boolean }) {
  const stages = ['IDENTITY', 'VERIFICATION', 'AUTHORISATION']
  return <ol className="eye-stages" aria-label="Authentication progress">
    {stages.map((label, index) => <li className={`eye-stage ${failed && index === active ? 'failure' : index === active ? 'active' : 'inactive'}`} key={label}>
      <span className="eye-stage-mark"><i>{['I', 'II', 'III'][index]}</i></span>
      <span><b>EYE {['I', 'II', 'III'][index]}</b><small>{label}</small></span>
      <span className="eye-stage-status">{failed && index === active ? 'CHECK DETAILS' : index === active ? 'IN PROGRESS' : 'AWAITING API'}</span>
    </li>)}
  </ol>
}

function SystemTerminal({ apiReady, streamConnected, hasActiveRun }: Pick<AccessPortalProps, 'apiReady' | 'streamConnected' | 'hasActiveRun'>) {
  const services: { name: string; state: ServiceState; detail: string }[] = [
    { name: 'TRINETRA CORE', state: 'online', detail: 'CLIENT INTERFACE LOADED' },
    { name: 'BACKEND API', state: apiReady === null ? 'checking' : apiReady ? 'online' : 'offline', detail: apiReady === null ? 'CHECKING HEALTH ENDPOINT' : apiReady ? 'HEALTH ENDPOINT REACHABLE' : 'HEALTH CHECK UNAVAILABLE' },
    { name: 'DATABASE', state: apiReady ? 'online' : apiReady === false ? 'offline' : 'checking', detail: apiReady ? 'BACKEND DATABASE AVAILABLE' : 'DATABASE STATUS UNAVAILABLE' },
    { name: 'SIMULATION ENGINE', state: apiReady ? 'online' : apiReady === false ? 'offline' : 'checking', detail: 'STATUS VIA BACKEND HEALTH' },
    { name: 'WEBSOCKET STREAM', state: streamConnected ? 'online' : hasActiveRun ? 'offline' : 'unverified', detail: streamConnected ? 'ACTIVE MISSION STREAM' : hasActiveRun ? 'ACTIVE RUN NOT CONNECTED' : 'NO ACTIVE STREAM TO VERIFY' },
    { name: 'AUTHENTICATION SERVICE', state: apiReady === null ? 'checking' : apiReady ? 'online' : 'offline', detail: apiReady ? 'SERVER-MANAGED SESSION API AVAILABLE' : 'AUTH API UNREACHABLE' },
  ]
  return <section className="access-terminal" aria-label="Live system status">
    <div className="access-terminal-head"><span><Activity size={13}/> SYSTEM DIAGNOSTICS</span><span className="terminal-readonly">READ ONLY</span></div>
    <div className="access-terminal-title">TRINETRA CORE <span>STATUS / 06</span></div>
    <div className="access-terminal-list">{services.map(service => <div className="access-terminal-row" key={service.name}>
      <span className="terminal-indicator" aria-hidden="true" />
      <span className="terminal-service">{service.name}<small>{service.detail}</small></span>
      <b className={`terminal-state ${service.state}`}>{service.state.toUpperCase()}</b>
    </div>)}</div>
    <div className="access-terminal-foot"><Wifi size={12}/><span>STATUS SOURCE: BACKEND HEALTH + CLIENT OBSERVATION</span></div>
  </section>
}

function LoginForm({ onPageChange, onAuthenticated, onOutcome, pane = false }: Pick<AccessPortalProps, 'onPageChange' | 'onAuthenticated'> & { onOutcome?: (outcome: 'success' | 'failed') => void; pane?: boolean }) {
  const { animate } = useVisuals()
  const [identity, setIdentity] = useState({ username: '', password: '' })
  const [passwordVisible, setPasswordVisible] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  // The server refused these exact credentials: the stage list says so until the user changes them (it used to keep saying "in progress").
  const [refusedFor, setRefusedFor] = useState<string | null>(null)
  const refused = refusedFor !== null && refusedFor === JSON.stringify(identity)
  const invalid = submitted && (!identity.username.trim() || !identity.password)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSubmitted(true)
    if (!identity.username.trim() || !identity.password) {
      setMessage('Enter your username and password.')
      return
    }
    setBusy(true)
    setMessage('')
    try {
      const session = await api.login(identity)
      setIdentity({ username: '', password: '' })
      onOutcome?.('success')
      if (animate) await new Promise(resolve => window.setTimeout(resolve, 650))      // the drone flies up and away before the page changes
      onAuthenticated(session)
    } catch (cause) {
      onOutcome?.('failed')
      setMessage(cause instanceof Error ? cause.message : 'Sign-in failed. Try again.')
      setRefusedFor(JSON.stringify(identity))
    } finally {
      setBusy(false)
    }
  }

  return <div className={`access-form-wrap ${pane ? 'login-pane-body' : ''}`}>
    {pane && <div className="login-pane-brand"><BrandMark surface="dark" height={50}/></div>}
    {pane ? <h1 className="login-headline">Identify <em>yourself.</em></h1>
      : <div className="access-form-heading"><span className="eyebrow">USER ACCESS / 01</span><h1>Identify<br/><em>yourself.</em></h1><p>One secure sign-in for all permitted TRINETRA modules.</p></div>}
    <EyeStages active={identity.username || identity.password ? 1 : 0} failed={Boolean(invalid) || refused}/>
    <form className="access-form" onSubmit={submit} noValidate>
      <label className="access-field"><span>USERNAME</span><span className={`access-input ${invalid && !identity.username.trim() ? 'invalid' : ''}`}><UserRound size={15}/><input autoComplete="username" value={identity.username} onChange={event => setIdentity({ ...identity, username: event.target.value })} aria-label="Username" placeholder="Enter your username"/></span></label>
      <label className="access-field"><span>PASSWORD</span><span className={`access-input ${invalid && !identity.password ? 'invalid' : ''}`}><LockKeyhole size={15}/><input type={passwordVisible ? 'text' : 'password'} autoComplete="current-password" value={identity.password} onChange={event => setIdentity({ ...identity, password: event.target.value })} aria-label="Password" placeholder="Enter password"/><button className="visibility-toggle" type="button" onClick={() => setPasswordVisible(value => !value)} aria-label={passwordVisible ? 'Hide password' : 'Show password'}>{passwordVisible ? <EyeOff size={15}/> : <Eye size={15}/>}</button></span></label>
      <button className="access-submit" type="submit" disabled={busy}>{busy ? 'SIGNING IN…' : <><KeyRound size={15}/> SIGN IN <ArrowRight size={15}/></>}</button>
      <div className={`access-form-message ${refused || (message && submitted) ? 'is-error' : ''}`} role="status" aria-live="polite">{message || (pane ? '' : 'The backend verifies credentials and returns a revocable session.')}</div>
    </form>
    {!pane && <div className="access-form-links"><span>Need an account?</span><button type="button" onClick={() => onPageChange('register')}>REGISTER <ArrowRight size={13}/></button></div>}
  </div>
}

function RegistrationForm({ onPageChange }: Pick<AccessPortalProps, 'onPageChange'>) {
  const [application, setApplication] = useState<Omit<RegistrationApplication, 'email'> & { email: string; confirmPassword: string }>({
    full_name: '', username: '', email: '', password: '', confirmPassword: '',
    requested_role: 'OPERATOR', justification: '',
  })
  const [passwordVisible, setPasswordVisible] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [applicationId, setApplicationId] = useState('')
  const [status, setStatus] = useState<RegistrationStatus | null>(null)
  const [statusMessage, setStatusMessage] = useState('')
  const [statusBusy, setStatusBusy] = useState(false)
  const [copyMessage, setCopyMessage] = useState('')
  const passwordValid = application.password.length >= 12 && /[a-z]/.test(application.password) && /[A-Z]/.test(application.password) && /\d/.test(application.password)
  const emailValid = !application.email || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(application.email)

  const checkStatus = async (id: string) => {
    if (!id.trim()) return
    setStatusBusy(true)
    try {
      setStatus(await api.registrationStatus(id.trim()))
      setStatusMessage('')
    } catch (cause) {
      setStatus(null)
      setStatusMessage(cause instanceof Error ? cause.message : 'Could not retrieve request status.')
    } finally {
      setStatusBusy(false)
    }
  }

  const copyApplicationId = async () => {
    try {
      await navigator.clipboard.writeText(applicationId)
      setCopyMessage('ID copied. Save it to check your registration status later.')
    } catch {
      setCopyMessage('Copy is unavailable here. Select the ID above and copy it manually.')
    }
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSubmitted(true)
    if (!application.full_name.trim() || !application.username.trim() || !emailValid || !passwordValid || application.password !== application.confirmPassword) {
      setMessage('Review the highlighted fields and password requirements before continuing.')
      return
    }
    setBusy(true)
    setMessage('')
    try {
      const { confirmPassword: _confirmation, ...applicationPayload } = application
      const payload = {
        ...applicationPayload,
        email: applicationPayload.email.trim() || null,
      }
      const receipt = await api.register(payload)
      setApplicationId(receipt.application_id)
      setStatus({
        application_id: receipt.application_id,
        status: receipt.status,
        rejection_message: null,
        assigned_role: null,
      })
      setStatusMessage('')
      setCopyMessage('')
      setApplication(current => ({ ...current, password: '', confirmPassword: '' }))
      setMessage('Registration received. Your application ID below is for checking status; the General approval queue lists your submitted full name. Access remains blocked until approval.')
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : 'Registration failed. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return <div className="access-registration-layout">
    <div className="access-form-wrap">
      <div className="access-form-heading"><span className="eyebrow">USER ACCESS / 02</span><h1>Request<br/><em>an account.</em></h1><p>Every registration remains pending until General review.</p></div>
      <EyeStages active={1} failed={submitted && (!application.full_name.trim() || !application.username.trim())}/>
      <form className="access-form registration-form" onSubmit={submit} noValidate>
        <label className="access-field"><span>FULL NAME</span><span className={`access-input ${submitted && !application.full_name.trim() ? 'invalid' : ''}`}><UserRound size={15}/><input autoComplete="name" value={application.full_name} onChange={event => setApplication({ ...application, full_name: event.target.value })} aria-label="Full name" placeholder="Your full name"/></span></label>
        <label className="access-field"><span>USERNAME</span><span className={`access-input ${submitted && !application.username.trim() ? 'invalid' : ''}`}><Fingerprint size={15}/><input autoComplete="username" value={application.username} onChange={event => setApplication({ ...application, username: event.target.value })} aria-label="Username" placeholder="Choose a username"/></span></label>
        <label className="access-field"><span>EMAIL ADDRESS · OPTIONAL</span><span className={`access-input ${submitted && !emailValid ? 'invalid' : ''}`}><Activity size={15}/><input type="email" autoComplete="email" value={application.email} onChange={event => setApplication({ ...application, email: event.target.value })} aria-label="Email address" placeholder="name@organization.org"/></span></label>
        <label className="access-field"><span>PASSWORD</span><span className={`access-input ${submitted && !passwordValid ? 'invalid' : ''}`}><LockKeyhole size={15}/><input type={passwordVisible ? 'text' : 'password'} autoComplete="new-password" value={application.password} onChange={event => setApplication({ ...application, password: event.target.value })} aria-label="Password" placeholder="Create a password"/><button className="visibility-toggle" type="button" onClick={() => setPasswordVisible(value => !value)} aria-label={passwordVisible ? 'Hide password' : 'Show password'}>{passwordVisible ? <EyeOff size={15}/> : <Eye size={15}/>}</button></span></label>
        <label className="access-field"><span>CONFIRM PASSWORD</span><span className={`access-input ${submitted && application.password !== application.confirmPassword ? 'invalid' : ''}`}><LockKeyhole size={15}/><input type={passwordVisible ? 'text' : 'password'} autoComplete="new-password" value={application.confirmPassword} onChange={event => setApplication({ ...application, confirmPassword: event.target.value })} aria-label="Confirm password" placeholder="Re-enter password"/></span></label>
        <label className="access-field"><span>REQUESTED ROLE</span><span className="access-input"><UserRound size={15}/><select value={application.requested_role} onChange={event => setApplication({ ...application, requested_role: event.target.value as ApplicationRole })}><option value="OPERATOR">OPERATOR</option><option value="GENERAL">GENERAL</option></select></span></label>
        <label className="access-field registration-justification"><span>JUSTIFICATION · OPTIONAL</span><span className="access-input"><Activity size={15}/><textarea maxLength={1000} value={application.justification} onChange={event => setApplication({ ...application, justification: event.target.value })} aria-label="Registration justification" placeholder="Share why you are requesting access."/></span></label>
        <div className="password-rules" aria-label="Password requirements"><b>PASSWORD REQUIREMENTS</b><span className={application.password.length >= 12 ? 'met' : ''}>12 characters minimum</span><span className={/[a-z]/.test(application.password) ? 'met' : ''}>One lowercase and one uppercase letter</span><span className={/\d/.test(application.password) ? 'met' : ''}>One number</span><span className={application.password && application.password === application.confirmPassword ? 'met' : ''}>Confirmation matches</span></div>
        <button className="access-submit" type="submit" disabled={busy}>{busy ? 'SUBMITTING…' : <><ShieldCheck size={15}/> SUBMIT REGISTRATION <ArrowRight size={15}/></>}</button>
        <div className="access-form-message" role="status" aria-live="polite">{message || 'Requested role is not active until a General assigns the final role and approves your account.'}</div>
      </form>
      <section className="application-status panel">
        <b>CHECK REGISTRATION STATUS</b>
        {status && <div className="application-id-receipt" role="status">
          <span>YOUR REGISTRATION ID</span>
          <code>{applicationId}</code>
          <button className="button-secondary" type="button" onClick={() => void copyApplicationId()}>COPY ID</button>
        </div>}
        <div className="application-status-controls"><input aria-label="Application ID" value={applicationId} onChange={event => { setApplicationId(event.target.value); setStatus(null); setStatusMessage(''); setCopyMessage('') }} placeholder="Paste your registration ID"/><button className="button-secondary" disabled={statusBusy || !applicationId.trim()} onClick={() => void checkStatus(applicationId)}>{statusBusy ? 'CHECKING…' : 'CHECK'}</button></div>
        {status && <p role="status">STATUS: <strong>{status.status}</strong>{status.assigned_role && <span> · ROLE: {status.assigned_role}</span>}{status.rejection_message && <span> — {status.rejection_message}</span>}</p>}
        {statusMessage && <p className="application-status-error" role="alert">{statusMessage}</p>}
        {copyMessage && <p role="status">{copyMessage}</p>}
      </section>
      <div className="access-form-links"><span>Already registered?</span><button type="button" onClick={() => onPageChange('login')}>RETURN TO SIGN IN <ArrowRight size={13}/></button></div>
    </div>
    <aside className="registration-note"><span className="eyebrow">APPLICATION PROCESS</span><h2>Access is never self-assigned.</h2><p>Your request enters a General's review queue. Only approved, active users may access protected modules.</p><div className="pending-stamp"><b>GENERAL REVIEW REQUIRED</b><span>NO ACCESS UNTIL APPROVED</span><small>The requested role is a preference only; the General assigns the effective role.</small></div><div className="clearance-list"><div><span>ROLE 01</span><b>GENERAL</b></div><div><span>ROLE 02</span><b>OPERATOR</b></div></div></aside>
  </div>
}

type AdminTab = 'users' | 'reviews' | 'saved' | 'audit'

function GeneralConsole({ session, onPageChange, onOpenScenario, onOpenQuick }: { session: UserSession; onPageChange: (page: AccessPage) => void; onOpenScenario?: (id: string, version?: number) => void; onOpenQuick?: (id: string) => void }) {
  const [adminTab, setAdminTab] = useState<AdminTab>('users')
  const [users, setUsers] = useState<ManagedUser[]>([])
  const [approvalRequests, setApprovalRequests] = useState<ScenarioApprovalRequest[]>([])
  const [audit, setAudit] = useState<AuditLogEntry[]>([])
  const [statusFilter, setStatusFilter] = useState<AccountStatus | 'ALL'>('ALL')
  const [selectedRoles, setSelectedRoles] = useState<Record<string, ApplicationRole>>({})
  const [search, setSearch] = useState('')
  const [eventFilter, setEventFilter] = useState('')
  const [outcomeFilter, setOutcomeFilter] = useState('')
  const [usernameFilter, setUsernameFilter] = useState('')
  const [sinceFilter, setSinceFilter] = useState('')
  const [untilFilter, setUntilFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [totalAudit, setTotalAudit] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pageSize = 25

  const refresh = async () => {
    setBusy(true)
    setError('')
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) })
    if (search.trim()) params.set('search', search.trim())
    if (eventFilter.trim()) params.set('event_type', eventFilter.trim())
    if (outcomeFilter) params.set('outcome', outcomeFilter)
    if (usernameFilter.trim()) params.set('username', usernameFilter.trim())
    if (sinceFilter) params.set('since', new Date(sinceFilter).toISOString())
    if (untilFilter) params.set('until', new Date(untilFilter).toISOString())
    try {
      const [usersResult, approvalsResult, auditResult] = await Promise.allSettled([
        api.generalUsers(statusFilter === 'ALL' ? undefined : statusFilter),
        api.pendingScenarioApprovals(),
        api.generalAudit(params),
      ])
      const errors: string[] = []
      if (usersResult.status === 'fulfilled') setUsers(usersResult.value)
      else errors.push(`User registrations: ${usersResult.reason instanceof Error ? usersResult.reason.message : 'Could not load.'}`)
      if (approvalsResult.status === 'fulfilled') setApprovalRequests(approvalsResult.value)
      else errors.push(`Scenario approvals: ${approvalsResult.reason instanceof Error ? approvalsResult.reason.message : 'Could not load.'}`)
      if (auditResult.status === 'fulfilled') {
        setAudit(auditResult.value.events)
        setTotalAudit(auditResult.value.total)
      } else errors.push(`Audit log: ${auditResult.reason instanceof Error ? auditResult.reason.message : 'Could not load.'}`)
      if (errors.length) setError(errors.join(' '))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => { void refresh() }, [statusFilter, offset])

  // Rejecting, changing a role and (de)activating are confirmed in the page, never with a native browser dialog.
  const [userAction, setUserAction] = useState<{ id: string; kind: 'reject' | 'role' | 'activate' | 'deactivate'; role?: ApplicationRole; note: string } | null>(null)
  const userActionPanel = (user: ManagedUser) => {
    if (userAction?.id !== user.id) return null
    const text = userAction.kind === 'reject' ? `Reject the registration of ${user.username}?`
      : userAction.kind === 'role' ? `Change ${user.username}'s role to ${userAction.role}? Their active sessions will be revoked.`
        : `${userAction.kind === 'deactivate' ? 'Deactivate' : 'Activate'} ${user.username}?`
    const confirm = () => {
      const action = userAction
      setUserAction(null)
      if (action.kind === 'reject') void decideRegistration(user, 'reject', action.note.trim())
      else if (action.kind === 'role') void saveRole(user, action.role)
      else void toggleActive(user)
    }
    return <div className="scenario-review-form" role="group" aria-label="Confirm account change">
      <b>{text}</b>
      {userAction.kind === 'reject' && <label>OPTIONAL REASON<textarea value={userAction.note} maxLength={500} rows={2} onChange={event => setUserAction({ ...userAction, note: event.target.value })}/></label>}
      <div className="admin-action-buttons"><button className="button-primary" disabled={busy} onClick={confirm}>CONFIRM</button>
        <button className="button-quiet" disabled={busy} onClick={() => setUserAction(null)}>CANCEL</button></div></div>
  }

  const decideRegistration = async (user: ManagedUser, decision: 'approve' | 'reject' | 'hold' | 'resume', rejection?: string) => {
    setBusy(true)
    setError('')
    try {
      await api.decideRegistration(
        user.id,
        decision,
        decision === 'approve' ? selectedRoles[user.id] ?? user.requested_role ?? 'OPERATOR' : undefined,
        rejection || undefined,
      )
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Registration decision failed.')
      setBusy(false)
    }
  }

  const saveRole = async (user: ManagedUser, chosen?: ApplicationRole) => {
    const role = chosen ?? selectedRoles[user.id] ?? user.role
    if (!role || role === user.role) return
    setBusy(true)
    setError('')
    try {
      await api.setUserRole(user.id, role)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Role update failed.')
      setBusy(false)
    }
  }

  const toggleActive = async (user: ManagedUser) => {
    const active = user.account_status === 'APPROVED'
    setBusy(true)
    setError('')
    try {
      await api.setUserActive(user.id, !active)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Account status update failed.')
      setBusy(false)
    }
  }

  // The review note is asked in the page (no native dialog): the decision buttons open a small form on the card.
  const [reviewDraft, setReviewDraft] = useState<{ id: string; decision: 'approve' | 'reject' | 'request_changes'; note: string } | null>(null)
  const startReview = (item: ScenarioApprovalRequest, decision: 'approve' | 'reject' | 'request_changes') =>
    setReviewDraft({ id: item.id, decision, note: decision === 'approve' ? 'Approved after review.' : '' })
  const reviewScenario = async () => {
    if (!reviewDraft || !reviewDraft.note.trim()) return
    setBusy(true)
    setError('')
    try {
      await api.reviewScenario(reviewDraft.id, reviewDraft.decision, reviewDraft.note.trim())
      setReviewDraft(null)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Scenario review failed.')
      setBusy(false)
    }
  }

  const exportAudit = async () => {
    try {
      const params = new URLSearchParams()
      if (search.trim()) params.set('search', search.trim())
      if (eventFilter.trim()) params.set('event_type', eventFilter.trim())
      if (outcomeFilter) params.set('outcome', outcomeFilter)
      if (usernameFilter.trim()) params.set('username', usernameFilter.trim())
      if (sinceFilter) params.set('since', new Date(sinceFilter).toISOString())
      if (untilFilter) params.set('until', new Date(untilFilter).toISOString())
      const blob = await api.exportAuditCsv(params)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'trinetra-audit.csv'
      link.click()
      URL.revokeObjectURL(url)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Audit export failed.')
    }
  }

  if (session.user.role !== 'GENERAL') return <section className="admin-console">
    <div className="admin-api-warning"><ShieldAlert size={17}/><span><b>GENERAL ROLE REQUIRED</b><small>This control centre is available only to General users.</small></span></div>
    <button className="button-secondary" onClick={() => onPageChange('login')}>SIGN IN WITH A GENERAL ACCOUNT</button>
  </section>

  return <section className="admin-console">
    <div className="access-form-heading"><span className="eyebrow">GENERAL CONTROL CENTRE</span><h1>Review.<br/><em>Protect.</em></h1><p>Manage registrations, review exact scenario versions, and inspect persistent audit events.</p></div>
    <div className="admin-console-actions"><span>{session.user.full_name} · {session.user.username} · GENERAL</span><button className="button-secondary" disabled={busy} onClick={() => void refresh()}>{busy ? 'REFRESHING…' : 'REFRESH DATA'}</button></div>
    {error && <div className="admin-inline-error" role="alert">{error}</div>}
    <nav className="admin-tabs" aria-label="General control centre sections">
      {([['users', 'Users and requests'], ['reviews', 'Scenario reviews'], ['saved', 'Saved scenarios'], ['audit', 'Audit log']] as [AdminTab, string][]).map(([key, label]) =>
        <button key={key} type="button" className={adminTab === key ? 'active' : ''} aria-current={adminTab === key ? 'page' : undefined} onClick={() => setAdminTab(key)}>{label}</button>)}
    </nav>
    {adminTab === 'saved' && <SavedScenarios onOpen={(id, version) => onOpenScenario?.(id, version)} onOpenQuick={id => onOpenQuick?.(id)}/>}

    {adminTab === 'users' && <section className="admin-queue panel">
      <div className="admin-queue-head"><div><span className="eyebrow">USER MANAGEMENT</span><h2>Registered users and requests</h2></div><select aria-label="Filter users by status" value={statusFilter} onChange={event => { setOffset(0); setStatusFilter(event.target.value as AccountStatus | 'ALL') }}><option value="ALL">ALL STATUSES</option><option value="PENDING">PENDING</option><option value="ON_HOLD">ON HOLD</option><option value="APPROVED">APPROVED</option><option value="REJECTED">REJECTED</option><option value="DEACTIVATED">DEACTIVATED</option></select></div>
      {!users.length ? <div className="admin-empty"><ShieldCheck size={22}/><b>No users in this view</b><span>Change the status filter or refresh the list.</span></div> : <div className="admin-accounts">{users.map(user => <article className="admin-account-row" key={user.id}>
        <div className="admin-account-identity"><b>{user.full_name}</b><span>{user.username}{user.email ? ` · ${user.email}` : ''}</span><small>{user.account_status}{user.role ? ` · ${user.role}` : ''}</small>{user.registration_note && <small>{user.registration_note}</small>}</div>
        {user.account_status === 'PENDING' && <div className="admin-action-buttons">
          <label>ASSIGN ROLE<select value={selectedRoles[user.id] ?? user.requested_role ?? 'OPERATOR'} disabled={busy} onChange={event => setSelectedRoles(previous => ({ ...previous, [user.id]: event.target.value as ApplicationRole }))}><option value="GENERAL">GENERAL</option><option value="OPERATOR">OPERATOR</option></select></label>
          <button className="button-secondary" disabled={busy} onClick={() => void decideRegistration(user, 'approve')}>APPROVE</button><button className="button-secondary" disabled={busy} onClick={() => void decideRegistration(user, 'hold')}>HOLD</button><button className="button-secondary" disabled={busy} onClick={() => setUserAction({ id: user.id, kind: 'reject', note: '' })}>REJECT</button>
        </div>}
        {user.account_status === 'ON_HOLD' && <button className="button-secondary" disabled={busy} onClick={() => void decideRegistration(user, 'resume')}>RETURN TO PENDING</button>}
        {user.account_status === 'APPROVED' && <div className="admin-action-buttons">
          <label>ROLE<select value={selectedRoles[user.id] ?? user.role ?? 'OPERATOR'} disabled={busy || user.id === session.user.id} onChange={event => setSelectedRoles(previous => ({ ...previous, [user.id]: event.target.value as ApplicationRole }))}><option value="GENERAL">GENERAL</option><option value="OPERATOR">OPERATOR</option></select></label>
          {user.id !== session.user.id && <><button className="button-secondary" disabled={busy} onClick={() => { const role = selectedRoles[user.id] ?? user.role; if (role && role !== user.role) setUserAction({ id: user.id, kind: 'role', role, note: '' }) }}>SAVE ROLE</button><button className="button-secondary" disabled={busy} onClick={() => setUserAction({ id: user.id, kind: 'deactivate', note: '' })}>DEACTIVATE</button></>}
        </div>}
        {user.account_status === 'DEACTIVATED' && <button className="button-secondary" disabled={busy || user.id === session.user.id} onClick={() => setUserAction({ id: user.id, kind: 'activate', note: '' })}>ACTIVATE</button>}
        {userActionPanel(user)}
        <time>{formatDateTime(user.created_at)}</time>
      </article>)}</div>}
    </section>}

    {adminTab === 'reviews' && <section className="admin-queue panel">
      <div className="admin-queue-head"><div><span className="eyebrow">SCENARIO APPROVAL REQUESTS</span><h2>Immutable version review</h2></div><span className="terminal-state online">{approvalRequests.length} PENDING</span></div>
      {!approvalRequests.length ? <div className="admin-empty"><ShieldCheck size={22}/><b>No scenario versions await review</b><span>Operator submissions will appear here with their exact configuration hash.</span></div> : approvalRequests.map(item => <article className="admin-request-card" key={item.id}>
        <div className="admin-request-heading"><div><b>{item.scenario_title} · v{item.version_number}</b><span>{item.owner_name} · {item.config_hash.slice(0, 16)}</span></div><time>{formatDateTime(item.submitted_at)}</time></div>
        <p>Version status: {item.latest_version ? 'CURRENT' : 'SUPERSEDED'}{item.sandbox_run_id ? ` · SANDBOX RUN ${item.sandbox_run_id}` : ' · NO SAVED SANDBOX RUN'}</p>
        {(() => {
          const plan = (item.intent as unknown as { mission_plan?: { objectives?: string[]; aircraft?: { uav_id: string; role: string | null; task_type: string | null; speed_mps: number | null; altitude_m: number | null; route?: { waypoints: unknown[]; length_m: number; duration_s: number } | null }[] } }).mission_plan
          if (!plan?.aircraft) return null
          return <div className="scenario-review-summary" aria-label="Mission summary">
            <b>Mission plan</b>
            <span>{(plan.objectives ?? []).join(', ').replace(/_/g, ' ') || 'no objectives'}</span>
            <ul>{plan.aircraft.map(aircraft => <li key={aircraft.uav_id}>{aircraft.uav_id} · {aircraft.role ?? 'no role'} · {aircraft.task_type?.replace(/_/g, ' ') ?? 'no task'} · {aircraft.altitude_m ?? '?'} m at {aircraft.speed_mps ?? '?'} m/s
              {aircraft.route ? ` · ${aircraft.route.waypoints.length} waypoints · ${(aircraft.route.length_m / 1000).toFixed(2)} km · about ${(aircraft.route.duration_s / 60).toFixed(1)} min` : ' · no generated route'}</li>)}</ul>
          </div>
        })()}
        <details className="scenario-review-intent"><summary>SIMULATOR CONFIGURATION (legacy fields)</summary><pre className="scenario-review-config">{JSON.stringify(item.config, null, 2)}</pre></details>
        <details className="scenario-review-intent"><summary>ADVISORY MISSION INTENT</summary><pre className="scenario-review-config">{JSON.stringify(item.intent, null, 2)}</pre><small>These notes are not simulator inputs. The approved version hash covers both configuration and intent.</small></details>
        {reviewDraft?.id === item.id ? <div className="scenario-review-form" role="group" aria-label="Review note">
          <label>{reviewDraft.decision === 'approve' ? 'REVIEW NOTE FOR APPROVING THIS VERSION' : 'REASON FOR THIS DECISION'}
            <textarea value={reviewDraft.note} maxLength={500} rows={2} onChange={event => setReviewDraft({ ...reviewDraft, note: event.target.value })}/></label>
          <div className="admin-action-buttons">
            <button className="button-primary" disabled={busy || !reviewDraft.note.trim()} onClick={() => void reviewScenario()}>{reviewDraft.decision === 'approve' ? 'CONFIRM APPROVAL' : reviewDraft.decision === 'reject' ? 'CONFIRM REJECTION' : 'SEND CHANGE REQUEST'}</button>
            <button className="button-quiet" disabled={busy} onClick={() => setReviewDraft(null)}>CANCEL</button></div>
          {!reviewDraft.note.trim() && <small>A note is required.</small>}
        </div> : <div className="admin-action-buttons"><button className="button-secondary" disabled={busy || !item.latest_version} onClick={() => startReview(item, 'approve')}>APPROVE VERSION</button><button className="button-secondary" disabled={busy || !item.latest_version} onClick={() => startReview(item, 'request_changes')}>REQUEST CHANGES</button><button className="button-secondary" disabled={busy || !item.latest_version} onClick={() => startReview(item, 'reject')}>REJECT</button></div>}
      </article>)}
    </section>}

    {adminTab === 'audit' && <section className="admin-audit panel">
      <div className="admin-queue-head"><div><span className="eyebrow">AUDIT LOGS</span><h2>Persistent security and operations events</h2></div><button className="button-secondary" disabled={busy} onClick={() => void exportAudit()}><Download size={14}/> EXPORT CSV</button></div>
      <div className="audit-filters">
        <label><Search size={14}/><input aria-label="Search audit events" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search text"/></label>
        <input aria-label="Filter audit event type" value={eventFilter} onChange={event => setEventFilter(event.target.value)} placeholder="Event type"/>
        <input aria-label="Filter by username" value={usernameFilter} onChange={event => setUsernameFilter(event.target.value)} placeholder="Username"/>
        <select aria-label="Filter audit outcome" value={outcomeFilter} onChange={event => setOutcomeFilter(event.target.value)}><option value="">ALL OUTCOMES</option><option value="success">SUCCESS</option><option value="failed">FAILED</option><option value="denied">DENIED</option></select>
        <label className="audit-date-filter"><span>FROM</span><input aria-label="Audit events from date" type="datetime-local" value={sinceFilter} onChange={event => setSinceFilter(event.target.value)}/></label>
        <label className="audit-date-filter"><span>TO</span><input aria-label="Audit events to date" type="datetime-local" value={untilFilter} onChange={event => setUntilFilter(event.target.value)}/></label>
        <button className="button-secondary" disabled={busy} onClick={() => { if (offset !== 0) setOffset(0); else void refresh() }}>FILTER</button>
      </div>
      {!audit.length ? <p>No audit events match these filters.</p> : <div className="admin-audit-list">{audit.map(entry => <article key={entry.id}><time dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time><b>{entry.event_type.replace(/_/g, ' ').toUpperCase()}</b><span>{entry.actor_name ?? 'ANONYMOUS'} · {entry.actor_role ?? 'ROLE NOT RECORDED'} · {entry.target_type ?? '—'} {entry.target_id ?? ''}</span><small>{entry.reason} · {entry.outcome}</small></article>)}</div>}
      <div className="audit-pagination"><span>{totalAudit ? `${offset + 1}–${Math.min(offset + pageSize, totalAudit)} of ${totalAudit}` : '0 events'}</span><div><button className="button-secondary" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}>PREVIOUS</button><button className="button-secondary" disabled={busy || offset + pageSize >= totalAudit} onClick={() => setOffset(offset + pageSize)}>NEXT</button></div></div>
    </section>}
  </section>
}

export function AccessPortal({ page, onPageChange, onExit, onAuthenticated, userSession, apiReady, streamConnected, hasActiveRun, sessionEnded = false, onOpenScenario, onOpenQuickMission }: AccessPortalProps) {
  const rootRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!rootRef.current || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const context = gsap.context(() => {
      gsap.fromTo('.access-reveal', { opacity: .35, y: 20 }, { opacity: 1, y: 0, duration: .6, stagger: .08, ease: 'power2.out' })
    }, rootRef)
    return () => context.revert()
  }, [page])

  const [outcome, setOutcome] = useState<{ kind: 'success' | 'failed'; at: number } | null>(null)
  const [shaking, setShaking] = useState(false)
  useEffect(() => {
    if (outcome?.kind !== 'failed') return
    setShaking(true)
    const timer = window.setTimeout(() => setShaking(false), 600)
    return () => window.clearTimeout(timer)
  }, [outcome])
  const isGeneralPage = page === 'general'
  const mode = page === 'register' ? 'register' : page === 'general' ? 'general' : 'login'
  return <main className={`access-portal-page ${isGeneralPage ? 'admin-console-only' : ''} ${page === 'login' ? 'is-login' : ''} ${outcome?.kind === 'success' ? 'leaving' : ''}`} ref={rootRef}>
    {isGeneralPage ? <><div className="access-backdrop"/><div className="access-grid" aria-hidden="true"/><div className="access-glow" aria-hidden="true"/></> : <LoopingVideo leaving={outcome?.kind === 'success'}/>}
    {sessionEnded && page === 'login' && !userSession && <div className="access-session-ended" role="status">Your session ended (it expired or was closed from elsewhere). Sign in again to continue; anything unsaved in Scenario Lab is kept on this browser and offered back.</div>}
    <div className="access-topline">{page === 'login' ? <span/> : <div className="access-brand"><BrandMark/><span className="access-brand-tag">SURVEIL. DETECT. PROTECT.</span></div>}<div className="access-top-actions">{!isGeneralPage && <button className="access-exit" type="button" onClick={onExit}><ArrowLeft size={14}/> RETURN TO OVERVIEW</button>}</div></div>
    <div className={`access-layout ${isGeneralPage ? 'admin-access-layout' : ''}`}>
      <motion.section className={`access-card ${isGeneralPage ? 'wide' : ''} access-reveal ${shaking ? 'shake-card' : ''}`} initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: .5 }}>
        {!isGeneralPage && page !== 'login' && <div className="access-route-tabs" aria-label="User access routes"><button className={mode === 'login' ? 'active' : ''} onClick={() => onPageChange('login')}>SIGN IN</button><button className={mode === 'register' ? 'active' : ''} onClick={() => onPageChange('register')}>REGISTER</button></div>}
        {page === 'login' && <LoginForm pane onPageChange={onPageChange} onAuthenticated={onAuthenticated} onOutcome={kind => setOutcome({ kind, at: Date.now() })}/>}
        {page === 'register' && <RegistrationForm onPageChange={onPageChange}/>}
        {page === 'general' && userSession && <GeneralConsole session={userSession} onPageChange={onPageChange} onOpenScenario={onOpenScenario} onOpenQuick={onOpenQuickMission}/>}
        {page === 'general' && !userSession && <div className="access-form-wrap"><div className="access-form-heading"><span className="eyebrow">GENERAL CONTROL CENTRE</span><h1>General<br/><em>sign in.</em></h1><p>Sign in with a provisioned General account to manage users and scenario reviews.</p></div><LoginForm onPageChange={onPageChange} onAuthenticated={onAuthenticated}/></div>}
      </motion.section>
      {page === 'login' && <p className="login-aside">Need an account? <button type="button" onClick={() => onPageChange('register')}>REGISTER <ArrowRight size={13}/></button></p>}
    </div>
    {!isGeneralPage && <details className="access-diagnostics"><summary>System diagnostics (read only)</summary><SystemTerminal apiReady={apiReady} streamConnected={streamConnected} hasActiveRun={hasActiveRun}/></details>}
    {!isGeneralPage && <footer className="access-footer"><span>TRINETRA / SOFTWARE-ONLY RESEARCH DEMONSTRATOR</span><span className="footage-credit">BACKGROUND FOOTAGE: “MOUNTAIN LANDSCAPE WITH SNOW”, FREE HD VIDEOS · docs/MEDIA_CREDITS.md</span><span>LOCAL ENVIRONMENT · NO OPERATIONAL FLIGHT CONTROL</span></footer>}
  </main>
}
