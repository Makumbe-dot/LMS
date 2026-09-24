import { useEffect, useRef } from 'react'

export default function Modal({ title, onClose, wide = false, children, footer }) {
  const panel = useRef(null)

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    panel.current?.querySelector('input, select, textarea, button')?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        className={`modal-panel ${wide ? 'wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={panel}
      >
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>{title}</h3>
          <div className="row">
            {footer}
            <button type="button" className="btn small" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
        {children}
      </div>
    </div>
  )
}

/**
 * A modal that wraps a form: renders `fields`, then Confirm / Cancel.
 * onSubmit receives the form values as a plain object.
 */
export function FormModal({ title, onClose, onSubmit, submitLabel = 'Confirm', busy, children, wide }) {
  return (
    <Modal title={title} onClose={onClose} wide={wide}>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          const form = event.currentTarget
          const values = {}
          for (const element of form.elements) {
            if (!element.name) continue
            if (element.type === 'checkbox') values[element.name] = element.checked
            else values[element.name] = element.value === '' ? null : element.value
          }
          onSubmit(values)
        }}
      >
        {children}
        <div className="row" style={{ marginTop: 8 }}>
          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? 'Working…' : submitLabel}
          </button>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  )
}
