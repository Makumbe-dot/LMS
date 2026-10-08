import { useEffect, useState } from 'react'

import { put } from '../lib/api.js'
import { useApi } from '../lib/useApi.js'
import { useToast } from './Toast.jsx'
import { ErrorBanner, Loading } from './ui.jsx'

// Made-up values, only to show what a borrower would read.
const SAMPLE = {
  first_name: 'Tendai',
  last_name: 'Moyo',
  institution: 'Your institution',
  institution_phone: '0242 700 100',
  loan_no: 'LN-000123',
  number: '3',
  amount: 'USD 197.02',
  balance: 'USD 591.06',
  due_date: '25 Nov 2026',
  days: '12',
  promised_date: '30 Nov 2026',
  code: '482915',
  minutes: '10',
}

const preview = (text) => text.replace(/\{(\w+)\}/g, (all, name) => SAMPLE[name] ?? all)

/**
 * The wording of every message the system sends a borrower. A blank box sends the
 * built-in wording, shown greyed as its placeholder text.
 */
export default function MessageTemplates() {
  const { toast, toastError } = useToast()
  const { data, error, loading, reload } = useApi('/api/message-templates')
  const [texts, setTexts] = useState({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (data) setTexts(Object.fromEntries(data.map((t) => [t.kind, t.template || ''])))
  }, [data])

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    try {
      await put('/api/message-templates', { templates: texts })
      toast('Message wording saved')
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card" onSubmit={save}>
      <h3>Borrower messages</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        The wording of each SMS. Words in braces are filled in for each borrower; leave a box
        blank to send the standard wording.
      </p>
      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? <Loading what="Loading message wording" /> : null}
      {(data || []).map((t) => {
        const text = texts[t.kind] ?? ''
        const id = `template-${t.kind}`
        return (
          <div key={t.kind} className="template">
            <label className="label-text" htmlFor={id}>
              {t.label}
            </label>
            <textarea
              id={id}
              rows={3}
              maxLength={480}
              value={text}
              placeholder={t.default}
              onChange={(e) => setTexts((v) => ({ ...v, [t.kind]: e.target.value }))}
            />
            <div className="template-meta">
              <span className="muted">
                Uses:{' '}
                {t.placeholders.map((p) => (
                  <code key={p.name} title={p.about}>
                    {`{${p.name}}`}
                  </code>
                ))}
              </span>
              <span className="muted">{(text || t.default).length} characters</span>
            </div>
            <p className="template-preview" aria-label={`${t.label} preview`}>
              {preview(text || t.default)}
            </p>
          </div>
        )
      })}
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button type="submit" className="btn primary" disabled={busy || !data}>
          {busy ? 'Saving…' : 'Save wording'}
        </button>
      </div>
    </form>
  )
}
