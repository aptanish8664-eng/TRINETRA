import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SimulationLocationNotice } from './SimulationLocationNotice'
import type { SimulationLocation } from '../services/api'

const apiMock = vi.hoisted(() => ({ simulationLocation: vi.fn(), relocateSimulation: vi.fn() }))
vi.mock('../services/api', () => ({ api: apiMock }))

const start = { latitude: 12.9716, longitude: 77.5946, altitude_m: 488, uav_id: 'px4-1', role: 'primary', definition: 'Take-off point of the primary aircraft.' }
const location = (distance: number, patch: Partial<SimulationLocation> = {}): SimulationLocation => ({
  scenario_id: 's1', scenario_version_id: 'v1', mission_start: start, aircraft: [], max_distance_to_start_m: distance, max_distance_to_area_m: distance,
  position_known: true, relocation_recommended: distance > 1000, far_from_mission_area: distance > 50_000,
  message: distance > 50_000 ? 'The aircraft is about 7594 km from the mission area. The simulation is running at a different location than this mission. Use Move simulation to mission start.'
    : distance > 1000 ? `The aircraft is ${(distance / 1000).toFixed(1)} km from the mission start. Use Move simulation to mission start to restart the simulation there.`
      : `The aircraft is ${distance.toFixed(0)} m from the mission start.`,
  thresholds: { relocation_notice_m: 1000, far_from_area_m: 50_000, home_tolerance_m: 50 }, ...patch,
})
const notice = (props: Partial<Parameters<typeof SimulationLocationNotice>[0]> = {}) =>
  render(<SimulationLocationNotice scenarioId="s1" versionId="v1" canRelocate {...props}/>)

beforeEach(() => { apiMock.simulationLocation.mockResolvedValue(location(3)) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('SimulationLocationNotice', () => {
  it('shows a quiet line and no action while the aircraft is at the mission start', async () => {
    notice()
    expect(await screen.findByText('The aircraft is 3 m from the mission start.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('button', { name: /MOVE SIMULATION/ })).toBeNull()
    expect(apiMock.simulationLocation).toHaveBeenCalledWith('s1', 'v1')
  })

  it('shows an amber notice with the action above 1 km and the stronger message above 50 km', async () => {
    apiMock.simulationLocation.mockResolvedValue(location(3200))
    const view = notice()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/THE AIRCRAFT IS NOT AT THE MISSION START/)
    expect(alert.textContent).toMatch(/3\.2 km from the mission start/)
    expect(alert.textContent).toMatch(/This does not block deployment/)
    expect(within(alert).getByRole('button', { name: /MOVE SIMULATION TO MISSION START/ })).toBeTruthy()
    view.unmount()
    apiMock.simulationLocation.mockResolvedValue(location(7_594_000))
    notice()
    expect((await screen.findByRole('alert')).textContent).toMatch(/THE SIMULATION IS AT A DIFFERENT LOCATION THAN THIS MISSION/)
  })

  it('never restarts by itself: it needs the press and the in-page confirmation, which says no flight is commanded', async () => {
    apiMock.simulationLocation.mockResolvedValue(location(7_594_000))
    apiMock.relocateSimulation.mockImplementation(async () => {
      apiMock.simulationLocation.mockResolvedValue(location(0.6))
      return { relocated: true, verified: true, mission_start: start, max_distance_to_start_m: 0.6, commands_sent_to_aircraft: false,
        note: 'The PX4 and Gazebo simulation was restarted at the mission start. No arm, take-off or mission command was sent.',
        checks: { px4: { status: 'ok', detail: '1 of 1 PX4 SITL process(es) present.' }, simulation_home: { status: 'ok', detail: 'Within 50 m.' } } }
    })
    const nativeConfirm = vi.spyOn(window, 'confirm')
    const onRelocated = vi.fn()
    notice({ onRelocated })
    fireEvent.click(await screen.findByRole('button', { name: /MOVE SIMULATION TO MISSION START/ }))
    expect(apiMock.relocateSimulation).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog', { name: 'Confirm moving the simulation' })
    expect(dialog.textContent).toMatch(/stops and restarts the PX4 and Gazebo simulation at the mission start point \(12\.97160, 77\.59460\)/)
    expect(dialog.textContent).toMatch(/It commands no flight/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'CANCEL' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(apiMock.relocateSimulation).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /MOVE SIMULATION TO MISSION START/ }))
    fireEvent.click(screen.getByRole('button', { name: 'RESTART SIMULATION AT MISSION START' }))
    await waitFor(() => expect(apiMock.relocateSimulation).toHaveBeenCalledWith('s1', 'v1'))
    expect(await screen.findByText(/Simulation moved to the mission start \(aircraft 0\.6 m from it\)/)).toBeTruthy()
    expect(within(screen.getByRole('list', { name: 'Relocation checks' })).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByText(/No arm, take-off or mission command was sent/)).toBeTruthy()
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(onRelocated).toHaveBeenCalledOnce()
    expect(nativeConfirm).not.toHaveBeenCalled()
  })

  it('shows the structured error, each check and how to recover when the restart fails', async () => {
    apiMock.simulationLocation.mockResolvedValue(location(7_594_000))
    apiMock.relocateSimulation.mockRejectedValue(Object.assign(new Error('PX4 and Gazebo restarted, but the reported position is not at the mission start.'), {
      status: 502, detail: { code: 'home_not_applied', recovery: 'Run Activate and verify to start the simulation again, then retry.',
        checks: { px4: { status: 'ok', detail: 'present' }, simulation_home: { status: 'failed', detail: 'An aircraft reports a position 7594000 m from the mission start; the limit is 50 m.' } } },
    }))
    notice()
    fireEvent.click(await screen.findByRole('button', { name: /MOVE SIMULATION TO MISSION START/ }))
    fireEvent.click(screen.getByRole('button', { name: 'RESTART SIMULATION AT MISSION START' }))
    expect(await screen.findByText(/The simulation was not moved\./)).toBeTruthy()
    expect(screen.getByText(/the reported position is not at the mission start/)).toBeTruthy()
    expect(screen.getByText(/HTTP 502 · home_not_applied/)).toBeTruthy()
    expect(screen.getByText(/limit is 50 m/)).toBeTruthy()
    expect(screen.getByText(/What to do: Run Activate and verify/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /MOVE SIMULATION TO MISSION START/ })).toBeTruthy()  // still available to retry
  })

  it('shows the notice but no action to an account that cannot restart the simulation, and does not poll while inactive', async () => {
    apiMock.simulationLocation.mockResolvedValue(location(7_594_000))
    const view = notice({ canRelocate: false })
    expect(await screen.findByText('Your account cannot restart the simulation.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /MOVE SIMULATION/ })).toBeNull()
    view.unmount()
    apiMock.simulationLocation.mockClear()
    notice({ active: false })
    await new Promise(resolve => setTimeout(resolve, 30))
    expect(apiMock.simulationLocation).not.toHaveBeenCalled()
  })
})
