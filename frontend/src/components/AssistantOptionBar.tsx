import { useEffect, useState } from 'react'
import type { AssistantOption } from './assistantOptions'

type Props = {
  options: AssistantOption[]
  disabled: boolean
  canManage: boolean
  /** Receives the message equivalent to what the user could have typed. */
  onSelect: (message: string) => void
}

export function AssistantOptionBar({ options, disabled, canManage, onSelect }: Props) {
  const [openId, setOpenId] = useState<string | null>(null)
  const [value, setValue] = useState('')
  const [field, setField] = useState('')
  const open = options.find(option => option.id === openId && option.input)

  useEffect(() => { if (disabled) setOpenId(null) }, [disabled])

  const openPicker = (option: AssistantOption) => {
    setOpenId(option.id)
    const input = option.input
    setValue(input?.type === 'select' ? input.choices[0]?.value ?? '' : '')
    setField(input?.type === 'field-number' ? input.fields[0]?.value ?? '' : '')
  }

  const submitPicker = (event: React.FormEvent) => {
    event.preventDefault()
    if (!open?.compose || !value.trim()) return
    const message = open.compose(value.trim(), field || undefined)
    setOpenId(null)
    onSelect(message)
  }

  const numeric = open?.input?.type === 'number' ? open.input
    : open?.input?.type === 'field-number' ? open.input.fields.find(item => item.value === field) ?? open.input.fields[0] : null

  return <div className="scenario-assistant-options">
    <div role="group" aria-label="Assistant options" className="scenario-assistant-chips">
      {options.map(option => <button
        key={option.id} type="button" className={`scenario-assistant-chip${openId === option.id ? ' active' : ''}`}
        disabled={disabled || (option.edits === true && !canManage)}
        aria-expanded={option.input ? openId === option.id : undefined}
        onClick={() => option.input ? (openId === option.id ? setOpenId(null) : openPicker(option)) : option.message && onSelect(option.message)}
      >{option.label}</button>)}
    </div>
    {open?.input && <form className="scenario-assistant-picker" aria-label={`${open.label} picker`} onSubmit={submitPicker}>
      {open.input.type === 'select' && <label>{open.input.label}
        <select value={value} onChange={event => setValue(event.target.value)} disabled={disabled}>
          {open.input.choices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
        </select></label>}
      {open.input.type === 'field-number' && <label>Setting
        <select value={field} onChange={event => { setField(event.target.value); setValue('') }} disabled={disabled}>
          {open.input.fields.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select></label>}
      {numeric && <label>{open.input.label}
        <input type="number" value={value} min={numeric.min} max={numeric.max} step={numeric.step} disabled={disabled} onChange={event => setValue(event.target.value)}/></label>}
      <div className="scenario-assistant-picker-actions">
        <button type="submit" disabled={disabled || !value.trim()}>PROPOSE</button>
        <button type="button" onClick={() => setOpenId(null)}>CLOSE</button>
      </div>
    </form>}
  </div>
}
