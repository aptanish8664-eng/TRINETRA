import { useEffect, type ReactNode } from 'react'

type Props = {
  /** Accessible name; tests and the user find the dialog by it. */
  label: string
  title: string
  children: ReactNode
  onCancel: () => void
}

/**
 * The in-page confirmation (never a native dialog), shown centred over the page so it is always fully visible without scrolling.
 * Escape and a click on the dimmed area cancel it; the buttons are the caller's.
 */
export function ConfirmDialog({ label, title, children, onCancel }: Props) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])
  return <div className="mc-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onCancel() }}>
    <div className="mc-exec-confirm mc-modal" role="dialog" aria-modal="true" aria-label={label}>
      <b>{title}</b>
      {children}
    </div>
  </div>
}
