import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { MapErrorBoundary } from './MapErrorBoundary'

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error('map blew up')
  return <div>map ok</div>
}

function Form() {
  const [text, setText] = useState('')
  const [explode, setExplode] = useState(true)
  return <div>
    <input aria-label="unsaved field" value={text} onChange={event => setText(event.target.value)}/>
    <button onClick={() => setExplode(false)}>FIX MAP</button>
    <MapErrorBoundary label="The planning map"><Bomb explode={explode}/></MapErrorBoundary>
  </div>
}

describe('MapErrorBoundary', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('isolates a map failure: the form keeps its state and the map can be retried', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    render(<Form/>)
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toMatch(/The planning map failed to render\./)
    expect(alert.textContent).toMatch(/map blew up/)
    expect(alert.textContent).toMatch(/nothing you entered was lost/)
    fireEvent.change(screen.getByLabelText('unsaved field'), { target: { value: 'draft title' } })
    fireEvent.click(screen.getByRole('button', { name: 'FIX MAP' }))
    fireEvent.click(screen.getByRole('button', { name: 'RETRY MAP' }))
    expect(screen.getByText('map ok')).toBeTruthy()
    expect((screen.getByLabelText('unsaved field') as HTMLInputElement).value).toBe('draft title')
  })
})
