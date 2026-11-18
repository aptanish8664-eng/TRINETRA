import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SavedScenarios } from './SavedScenarios'
import { api } from '../services/api'
import type { GeneralScenarioRow, ScenarioDeletePreview } from '../services/auth'

vi.mock('../services/api', () => ({ api: { generalScenarios: vi.fn(), generalScenarioExport: vi.fn(), generalDeletePreview: vi.fn(), generalDeleteScenario: vi.fn() } }))
const apiMock = vi.mocked(api)

const row = (patch: Partial<GeneralScenarioRow>): GeneralScenarioRow => ({
  id: 's1', title: 'Harbour search', owner_user_id: 'u1', owner_username: 'ops', owner_name: 'Olive Operator', owner_role: 'OPERATOR', latest_version_number: 2, status: 'draft', approval_status: null,
  created_at: '2026-11-15T08:00:00Z', updated_at: '2026-11-15T09:00:00Z', source: 'manual', deployments: 0, executions: 0, active_execution: false,
  versions: [{ id: 'v2', number: 2, hash: 'h2', source: 'manual', approval_status: null, deployed: false, created_at: 'x' }, { id: 'v1', number: 1, hash: 'h1', source: 'quick', approval_status: null, deployed: false, created_at: 'x' }], ...patch,
})
const preview = (patch: Partial<ScenarioDeletePreview> = {}): ScenarioDeletePreview => ({ scenario_id: 's1', title: 'Harbour search', blockers: [],
  counts: { versions: 2, approvals: 1, deployments: 1, executions: 1, execution_events: 12, run_records: 0, experiments: 1, experiment_results: 1 }, ...patch })

beforeEach(() => {
  apiMock.generalScenarios.mockResolvedValue([row({}), row({ id: 's2', title: 'Ridge quick', owner_user_id: 'g1', owner_name: 'Gina General', owner_role: 'GENERAL', source: 'quick', status: 'deployed', deployments: 1, executions: 2 })])
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('General: saved scenarios', () => {
  it('lists every user scenario with owner, status, source and counts, and filters by owner, status and name', async () => {
    render(<SavedScenarios onOpen={vi.fn()} onOpenQuick={vi.fn()}/>)
    expect(await screen.findByText('Harbour search')).toBeTruthy()
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(within(items[0]).getByText(/Olive Operator · OPERATOR/)).toBeTruthy()
    expect(within(items[1]).getByText('Quick Mission')).toBeTruthy()
    expect(within(items[1]).getByText(/1 deployment · 2 executions/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Filter by status'), { target: { value: 'deployed' } })
    expect(screen.getAllByRole('listitem')).toHaveLength(1); expect(screen.getByText('Ridge quick')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Filter by status'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Filter by owner'), { target: { value: 'u1' } })
    expect(screen.getAllByRole('listitem')).toHaveLength(1); expect(screen.getByText('Harbour search')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Filter by owner'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Search scenario names'), { target: { value: 'ridge' } })
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('opens the latest or a chosen version in Scenario Lab, and a quick scenario also in Quick Mission', async () => {
    const open = vi.fn(), quick = vi.fn()
    render(<SavedScenarios onOpen={open} onOpenQuick={quick}/>)
    await screen.findByText('Harbour search')
    const first = screen.getAllByRole('listitem')[0]
    fireEvent.click(within(first).getByRole('button', { name: /OPEN \/ EDIT/ }))
    expect(open).toHaveBeenCalledWith('s1', 2)
    fireEvent.change(within(first).getByLabelText('Version of Harbour search'), { target: { value: '1' } })
    fireEvent.click(within(first).getByRole('button', { name: /OPEN \/ EDIT/ }))
    expect(open).toHaveBeenLastCalledWith('s1', 1)
    expect(within(first).queryByRole('button', { name: /EDIT IN QUICK MISSION/ })).toBeNull()              // a manual scenario has no quick values
    fireEvent.click(within(screen.getAllByRole('listitem')[1]).getByRole('button', { name: /EDIT IN QUICK MISSION/ }))
    expect(quick).toHaveBeenCalledWith('s2')
  })

  it('exports the full record as a JSON download', async () => {
    apiMock.generalScenarioExport.mockResolvedValue({ exported_format: 'trinetra-scenario-record-1' })
    const created: Blob[] = []
    const original = URL.createObjectURL
    URL.createObjectURL = (blob: Blob | MediaSource) => { created.push(blob as Blob); return 'blob:x' }
    URL.revokeObjectURL = vi.fn()
    try {
      render(<SavedScenarios onOpen={vi.fn()} onOpenQuick={vi.fn()}/>)
      await screen.findByText('Harbour search')
      fireEvent.click(within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: /EXPORT/ }))
      await waitFor(() => expect(created).toHaveLength(1))
      expect(created[0].type).toBe('application/json')
    } finally { URL.createObjectURL = original }
  })

  it('delete shows the counts, needs the typed name, keeps results unless asked, and refreshes the list', async () => {
    apiMock.generalDeletePreview.mockResolvedValue(preview())
    apiMock.generalDeleteScenario.mockResolvedValue({ scenario_id: 's1', title: 'Harbour search', counts: { versions: 2, experiment_results_kept: 1 }, content_hash: 'x'.repeat(64) })
    render(<SavedScenarios onOpen={vi.fn()} onOpenQuick={vi.fn()}/>)
    await screen.findByText('Harbour search')
    fireEvent.click(within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: /DELETE/ }))
    const form = await screen.findByRole('group', { name: 'Confirm delete' })
    await within(form).findByLabelText('Type the scenario name')
    expect(form.textContent).toContain('2 versions, 1 approvals, 1 deployments, 1 executions, 12 execution events, 1 experiments, 1 experiment results')
    expect(form.textContent).toContain('from a deleted scenario')
    const confirm = within(form).getByRole('button', { name: 'DELETE FOR GOOD' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    fireEvent.change(within(form).getByLabelText('Type the scenario name'), { target: { value: 'harbour search' } })
    expect(confirm.disabled).toBe(true)                                                          // the name must match exactly
    fireEvent.change(within(form).getByLabelText('Type the scenario name'), { target: { value: 'Harbour search' } })
    expect(confirm.disabled).toBe(false)
    apiMock.generalScenarios.mockResolvedValue([row({ id: 's2', title: 'Ridge quick' })])
    fireEvent.click(confirm)
    await waitFor(() => expect(apiMock.generalDeleteScenario).toHaveBeenCalledWith('s1', 'Harbour search', false))
    expect(await screen.findByText(/Deleted Harbour search/)).toBeTruthy()
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(1))
  })

  it('deleting with the results checkbox asks the backend to delete them too', async () => {
    apiMock.generalDeletePreview.mockResolvedValue(preview())
    apiMock.generalDeleteScenario.mockResolvedValue({ scenario_id: 's1', title: 'Harbour search', counts: {}, content_hash: 'x' })
    render(<SavedScenarios onOpen={vi.fn()} onOpenQuick={vi.fn()}/>)
    await screen.findByText('Harbour search')
    fireEvent.click(within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: /DELETE/ }))
    const form = await screen.findByRole('group', { name: 'Confirm delete' })
    fireEvent.click(await within(form).findByRole('checkbox'))
    fireEvent.change(within(form).getByLabelText('Type the scenario name'), { target: { value: 'Harbour search' } })
    fireEvent.click(within(form).getByRole('button', { name: 'DELETE FOR GOOD' }))
    await waitFor(() => expect(apiMock.generalDeleteScenario).toHaveBeenCalledWith('s1', 'Harbour search', true))
  })

  it('a blocked delete (flying, or pinned in Mission Control) shows the reason and cannot be confirmed', async () => {
    apiMock.generalDeletePreview.mockResolvedValue(preview({ blockers: [{ code: 'execution_active', message: 'A flight of this scenario is still active (executing on px4-1). Abort it and wait until it is final first.' }] }))
    render(<SavedScenarios onOpen={vi.fn()} onOpenQuick={vi.fn()}/>)
    await screen.findByText('Harbour search')
    fireEvent.click(within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: /DELETE/ }))
    const form = await screen.findByRole('group', { name: 'Confirm delete' })
    expect(await within(form).findByText(/Abort it and wait until it is final first/)).toBeTruthy()
    fireEvent.change(within(form).getByLabelText('Type the scenario name'), { target: { value: 'Harbour search' } })
    expect((within(form).getByRole('button', { name: 'DELETE FOR GOOD' }) as HTMLButtonElement).disabled).toBe(true)
    expect(apiMock.generalDeleteScenario).not.toHaveBeenCalled()
  })
})
