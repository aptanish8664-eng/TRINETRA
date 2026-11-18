import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RestartDialog } from './RestartDialog'

vi.mock('../services/api', () => ({ api: { startRestart: vi.fn(), restartJob: vi.fn(), latestRestart: vi.fn() } }))
afterEach(cleanup)

const open = (mission?: number, running?: number) => render(<RestartDialog deploymentId="d" missionLabel="M v1" purpose="restart" onClose={() => undefined} onSucceeded={() => undefined} missionAircraft={mission} runningAircraft={running}/>)

describe('RestartDialog aircraft count note', () => {
  it('says which running aircraft a smaller mission will stop', () => {
    open(1, 5)
    expect(screen.getByRole('note').textContent).toMatch(/1 aircraft and 5 are running now.*the other 4 running aircraft are stopped/)
  })
  it('says when a larger mission starts more aircraft, and is silent when the numbers match', () => {
    open(3, 1)
    expect(screen.getByRole('note').textContent).toMatch(/starts all 3/)
    cleanup(); open(2, 2)
    expect(screen.queryByRole('note')).toBeNull()
  })
})
