import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, resolveApiBaseUrl } from './api'
import type { UserSession } from './auth'

const session = (csrf_token: string): UserSession => ({
  expires_at: '2026-10-27T00:00:00Z',
  csrf_token,
  permissions: ['mission.create'],
  user: {
    id: 'general-user',
    username: 'trinetra-general',
    full_name: 'TRINETRA General',
    email: null,
    role: 'GENERAL',
    account_status: 'APPROVED',
  },
})

function jsonResponse(status: number, value: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 403 ? 'Forbidden' : 'OK',
    json: async () => value,
  } as Response
}

describe('shared API CSRF handling', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('aligns loopback API and frontend hostnames during development', () => {
    expect(resolveApiBaseUrl('http://127.0.0.1:8000', 'http:', 'localhost', true))
      .toBe('http://localhost:8000')
    expect(resolveApiBaseUrl('http://localhost:8000', 'http:', '127.0.0.1', true))
      .toBe('http://127.0.0.1:8000')
    expect(resolveApiBaseUrl(undefined, 'http:', 'localhost', true))
      .toBe('http://localhost:8000')
    expect(resolveApiBaseUrl('https://api.example.com', 'https:', 'localhost', true))
      .toBe('https://api.example.com')
    expect(resolveApiBaseUrl('http://127.0.0.1:8000', 'http:', 'localhost', false))
      .toBe('http://127.0.0.1:8000')
  })

  it('does not treat authenticated authorization failures as expired sessions', async () => {
    const unauthorized = vi.fn()
    window.addEventListener('trinetra:unauthorized', unauthorized)
    fetchMock
      .mockResolvedValueOnce(jsonResponse(403, { detail: 'Permission required: audit.read' }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'Authentication required' }))

    await expect(api.state('run-1')).rejects.toMatchObject({ status: 403 })
    expect(unauthorized).not.toHaveBeenCalled()
    await expect(api.state('run-1')).rejects.toMatchObject({ status: 401 })
    expect(unauthorized).toHaveBeenCalledTimes(1)
    window.removeEventListener('trinetra:unauthorized', unauthorized)
  })

  it('uses the session token for protected writes and never requires it for safe reads', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, session('session-csrf')))
      .mockResolvedValueOnce(jsonResponse(200, { status: 'signed_out' }))
      .mockResolvedValueOnce(jsonResponse(200, session('refreshed-csrf')))
      .mockResolvedValueOnce(jsonResponse(200, { id: 'run-1', state: {} }))
      .mockResolvedValueOnce(jsonResponse(200, { status: 'ready', active_simulations: 0, database: 'sqlite' }))

    await api.login({ username: 'trinetra-general', password: 'not-used-in-test' })
    await api.logout()
    await api.create({})
    await api.health()

    expect(fetchMock.mock.calls[1][1]?.credentials).toBe('include')
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('X-CSRF-Token')).toBe('session-csrf')
    expect(String(fetchMock.mock.calls[2][0])).toContain('/api/auth/session')
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get('X-CSRF-Token')).toBe('refreshed-csrf')
    expect(new Headers(fetchMock.mock.calls[4][1]?.headers).has('X-CSRF-Token')).toBe(false)
  })

  it('refreshes a stale session token and retries a rejected protected write once', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, session('stale-token')))
      .mockResolvedValueOnce(jsonResponse(403, { detail: 'CSRF validation required' }))
      .mockResolvedValueOnce(jsonResponse(200, session('rotated-token')))
      .mockResolvedValueOnce(jsonResponse(200, { id: 'run-2', state: {} }))

    await api.login({ username: 'trinetra-general', password: 'not-used-in-test' })
    await api.create({})

    expect(fetchMock).toHaveBeenCalledTimes(4)
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('X-CSRF-Token')).toBe('stale-token')
    expect(String(fetchMock.mock.calls[2][0])).toContain('/api/auth/session')
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get('X-CSRF-Token')).toBe('rotated-token')
  })

  it('does not retry indefinitely when the refreshed token is rejected', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, session('initial-token')))
      .mockResolvedValueOnce(jsonResponse(403, { detail: 'CSRF validation failed' }))
      .mockResolvedValueOnce(jsonResponse(200, session('new-token')))
      .mockResolvedValueOnce(jsonResponse(403, { detail: 'CSRF validation failed' }))

    await api.login({ username: 'trinetra-general', password: 'not-used-in-test' })
    await expect(api.create({})).rejects.toMatchObject({ status: 403, message: 'CSRF validation failed' })
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('keeps the message and per-check detail of structured fleet activation errors', async () => {
    const detail = {
      code: 'verification_timeout', message: 'PX4 and Gazebo are running, but no telemetry arrived.',
      checks: { telemetry_bridge: { status: 'not_running', detail: 'Bridge process is not running.' } },
    }
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, session('fleet-token')))
      .mockResolvedValueOnce(jsonResponse(504, { detail }))

    await api.login({ username: 'trinetra-general', password: 'not-used-in-test' })
    await expect(api.activateFleet(1)).rejects.toMatchObject({ status: 504, message: detail.message, detail })
  })

  it('formats structured validation errors without exposing submitted values', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(422, {
      detail: [{
        type: 'string_too_short',
        loc: ['body', 'password'],
        msg: 'String should have at least 12 characters',
        input: 'SensitiveSubmittedValue123',
      }],
    }))

    await expect(api.register({
      full_name: 'Pending User',
      username: 'pending-user',
      email: '',
      password: 'SensitiveSubmittedValue123',
      requested_role: 'OPERATOR',
      justification: '',
    })).rejects.toMatchObject({
      status: 422,
      message: 'password: String should have at least 12 characters',
    })
  })
})
