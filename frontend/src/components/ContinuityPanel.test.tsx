import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ContinuityList, ContinuityPanel } from './ContinuityPanel'
import type { Reconfiguration } from './attackLabTypes'

const apiMock = vi.hoisted(() => ({ reconfigurations: vi.fn() }))
vi.mock('../services/api', () => ({ api: apiMock }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

const item = (patch: Partial<Reconfiguration> = {}): Reconfiguration => ({
  id: 'r1', kind: 'handover', previous_primary: 'px4-1', new_primary: 'px4-3', started_at: 't', finished_at: null, status: 'verifying', notes: [],
  checks: { new_primary_flying: { ok: true, detail: 'px4-3 is flying its own route (run state executing).' }, previous_aircraft_safe: null, mission_progressing: { ok: true, detail: 'px4-3 route progress 12%' } }, ...patch,
})

describe('ContinuityPanel', () => {
  it('shows each measured check and the undecided ones as not decided yet', () => {
    render(<ContinuityList items={[item()]}/>)
    expect(screen.getByText(/Hand over: px4-1 → px4-3/)).toBeTruthy()
    expect(screen.getByText('Checking…')).toBeTruthy()
    expect(screen.getByText(/not decided yet/)).toBeTruthy()
    expect(screen.getByText(/px4-3 route progress 12%/)).toBeTruthy()
  })

  it('shows the final outcome and why a mission is only degraded', () => {
    render(<ContinuityList items={[item({ status: 'degraded', notes: ['The new primary finished its route, but the previous aircraft is not confirmed safe.'], checks: { new_primary_flying: { ok: true, detail: 'ok' }, previous_aircraft_safe: { ok: false, detail: 'px4-1 is not confirmed on the ground and disarmed 120 s after its run ended.' }, mission_progressing: { ok: true, detail: 'done' } } })]}/>)
    expect(screen.getByText('Degraded')).toBeTruthy()
    expect(screen.getByText(/not confirmed safe/)).toBeTruthy()
    expect(screen.getByText(/✗/)).toBeTruthy()
  })

  it('renders nothing without reconfigurations and polls the backend otherwise', async () => {
    apiMock.reconfigurations.mockResolvedValueOnce([])
    const { container, rerender } = render(<ContinuityPanel/>)
    await waitFor(() => expect(apiMock.reconfigurations).toHaveBeenCalled())
    expect(container.textContent).toBe('')
    apiMock.reconfigurations.mockResolvedValue([item({ status: 'completed' })])
    rerender(<ContinuityPanel key="again"/>)
    expect(await screen.findByLabelText('Mission continuity after a response')).toBeTruthy()
    expect(screen.getByText('Completed')).toBeTruthy()
  })
})
