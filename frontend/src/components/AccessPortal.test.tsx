import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AccessPortal } from './AccessPortal'
import { api } from '../services/api'

vi.mock('../services/api', () => ({
  api: {
    register: vi.fn(),
    registrationStatus: vi.fn(),
  },
}))

vi.mock('gsap', () => ({
  default: {
    context: vi.fn(() => ({ revert: vi.fn() })),
  },
}))

vi.mock('framer-motion', () => ({
  motion: { section: 'section' },
}))

const apiMock = vi.mocked(api)

describe('registration receipt and status', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
    apiMock.register.mockResolvedValue({ application_id: 'application-123', status: 'PENDING' })
    apiMock.registrationStatus.mockResolvedValue({
      application_id: 'application-123',
      status: 'PENDING',
      rejection_message: null,
      assigned_role: null,
    })
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('shows the issued application ID and lets the applicant check its status', async () => {
    render(<AccessPortal
      page="register"
      onPageChange={vi.fn()}
      onExit={vi.fn()}
      onAuthenticated={vi.fn()}
      userSession={null}
      apiReady={true}
      streamConnected={false}
      hasActiveRun={false}
    />)

    fireEvent.change(screen.getByRole('textbox', { name: 'Full name' }), { target: { value: 'Vanashree' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Username' }), { target: { value: 'TRINETRA-OPERATOR' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'StrongPassword123' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'StrongPassword123' } })
    fireEvent.click(screen.getByRole('button', { name: /SUBMIT REGISTRATION/ }))

    expect(await screen.findByText('application-123')).toBeTruthy()
    expect(screen.getByText('YOUR REGISTRATION ID')).toBeTruthy()
    expect(screen.getByText(/STATUS:/).textContent).toContain('PENDING')
    expect((screen.getByRole('textbox', { name: 'Application ID' }) as HTMLInputElement).value).toBe('application-123')
    expect(apiMock.registrationStatus).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'CHECK' }))
    await waitFor(() => expect(apiMock.registrationStatus).toHaveBeenCalledWith('application-123'))
    expect(await screen.findByText(/STATUS:/)).toBeTruthy()
  })
})
