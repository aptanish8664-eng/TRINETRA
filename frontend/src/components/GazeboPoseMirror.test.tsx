import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GazeboPoseMirror } from './GazeboPoseMirror'
import { api } from '../services/api'
import type { GazeboPoseSnapshot } from '../types'

vi.mock('../services/api', () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number) { super('API error'); this.status = status } },
  api: { gazeboPoses: vi.fn(), gazeboPoseSocket: vi.fn() },
}))

const apiMock = vi.mocked(api)
let socket: { onmessage: ((event: MessageEvent) => void) | null; onerror: (() => void) | null; onclose: ((event: CloseEvent) => void) | null; close: ReturnType<typeof vi.fn> }

function snapshot(names: string[]): GazeboPoseSnapshot {
  return {
    source: 'gazebo_pose_info', world: 'default', generated_at: '2026-11-08T00:00:00Z',
    transport: { status: 'receiving', datagrams_received: 8, invalid_datagrams: 0, last_datagram_at: '2026-11-08T00:00:00Z', last_datagram_age_seconds: 0.02 },
    models: names.map((model_name, index) => ({
      model_name,
      position_m: { x: index * 5, y: 0, z: -0.013 },
      orientation_quaternion: { x: 0, y: 0, z: 0, w: 1 },
      received_at: '2026-11-08T00:00:00Z', status: 'connected', telemetry_age_seconds: 0.02,
      flight_trail: [],
    })),
  }
}

describe('Gazebo live pose mirror', () => {
  beforeEach(() => {
    socket = { onmessage: null, onerror: null, onclose: null, close: vi.fn() }
    apiMock.gazeboPoses.mockResolvedValue(snapshot(['x500_0']))
    apiMock.gazeboPoseSocket.mockReturnValue(socket as unknown as WebSocket)
  })

  afterEach(() => { cleanup(); vi.clearAllMocks() })

  it('renders only Gazebo backend poses and applies live updates', async () => {
    render(<GazeboPoseMirror selectedModel="x500_0" onSelect={vi.fn()}/>)
    expect(await screen.findByRole('button', { name: /X500_0/ })).toBeTruthy()
    expect(screen.getByText(/XYZ 0\.00, 0\.00, -0\.01 m/)).toBeTruthy()
    await waitFor(() => expect(apiMock.gazeboPoseSocket).toHaveBeenCalledOnce())
    expect(screen.getByText(/LIVE GAZEBO POSES/)).toBeTruthy()
    act(() => socket.onmessage?.({ data: JSON.stringify(snapshot(['x500_0', 'x500_1'])) } as MessageEvent))
    expect(await screen.findByText('X500 models')).toBeTruthy()
    expect(screen.getByText(/X500 models/).parentElement?.textContent).toContain('2')
    expect(screen.getByText(/XYZ 5\.00, 0\.00, -0\.01 m/)).toBeTruthy()
  })
})
