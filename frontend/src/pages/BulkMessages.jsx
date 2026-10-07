import { useEffect, useRef, useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import Icon from '../components/Icons.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { post } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime, today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

const CHANNEL_LABEL = { preferred: "Each borrower's own choice", sms: 'SMS', whatsapp: 'WhatsApp', email: 'Email' }

/**
 * One message to many borrowers. Pick who, how and what; the preview shows how
 * many it reaches and how three of them will read it. Each message goes through
 * the Outbox like any other, so retries, the SMS fallback and test mode apply.
 */
export default function BulkMessages() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const { activeBranches } = useOrg()
  const list = useApi('/api/communications/campaigns')
  const products = useApi('/api/products')
  const gateway = useApi('/api/notifications/gateway')
  const [form, setForm] = useState({
    name: '',
    who: 'active',
    branch_id: '',
    product_id: '',
    channel: 'preferred',
    subject: '',
    text: 'Dear {first_name}, ',
    scheduled_for: today(),
  })
  const [preview, setPreview] = useState(null)
  const [previewError, setPreviewError] = useState('')
  const [busy, setBusy] = useState(false)
  const textRef = useRef(null)
  const mayCreate = can('messages')

  const body = () => ({
    audience: { who: form.who, branch_id: form.branch_id || null, product_id: form.product_id || null },
    channel: form.channel,
    text: form.text,
  })

  // A fresh preview a moment after the form stops changing.
  useEffect(() => {
    const timer = setTimeout(async () => {
      try {
        setPreview(await post('/api/communications/campaigns/preview', body()))
        setPreviewError('')
      } catch (err) {
        setPreview(null)
        setPreviewError(err.message)
      }
    }, 400)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.who, form.branch_id, form.product_id, form.channel, form.text])

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  function insert(name) {
    const el = textRef.current
    const token = `{${name}}`
    if (!el) return setForm((f) => ({ ...f, text: f.text + token }))
    const start = el.selectionStart ?? form.text.length
    const end = el.selectionEnd ?? start
    const next = form.text.slice(0, start) + token + form.text.slice(end)
    setForm((f) => ({ ...f, text: next }))
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(start + token.length, start + token.length)
    })
  }

  async function send(event) {
    event.preventDefault()
    const now = form.scheduled_for === today()
    const ok = window.confirm(
      `${now ? 'Send' : 'Schedule'} this message to ${preview?.total - (preview?.skipped || 0)} borrower(s)${now ? ' now' : ` for ${form.scheduled_for}`}?`,
    )
    if (!ok) return
    setBusy(true)
    try {
      const made = await post('/api/communications/campaigns', {
        ...body(),
        name: form.name,
        subject: form.subject,
        scheduled_for: form.scheduled_for,
      })
      toast(
        `${made.queued} message(s) ${now ? 'on their way' : `scheduled for ${made.scheduled_for}`}` +
          (made.skipped ? `; ${made.skipped} skipped (no address for that channel)` : ''),
      )
      list.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  const testTo = gateway.data?.test_recipient
  const audiences = list.data?.audiences || {}
  const placeholders = list.data?.placeholders || {}

  return (
    <>
      <PageHeader
        title="Bulk message"
        meta="One message to many borrowers, by SMS, WhatsApp or email. Each goes through the Outbox, so failures retry and WhatsApp or email falls back to SMS."
      />

      {testTo ? (
        <div className="hint" role="status">
          <strong>Test mode is on. </strong>
          Every SMS and WhatsApp goes to <strong>{testTo}</strong> instead of the borrower
          {gateway.data?.test_email ? `, and every email to ${gateway.data.test_email}` : ''}, marked with whom it was for.
        </div>
      ) : null}

      {mayCreate ? (
        <form className="card" onSubmit={send}>
          <div className="grid cols-3">
            <Field as="select" label="Send to" value={form.who} onChange={set('who')}>
              {Object.entries(audiences).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </Field>
            {activeBranches.length > 1 ? (
              <Field as="select" label="Branch" value={form.branch_id} onChange={set('branch_id')}>
                <option value="">All branches</option>
                {activeBranches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </Field>
            ) : null}
            {form.who !== 'all' && form.who !== 'past' ? (
              <Field as="select" label="Product" value={form.product_id} onChange={set('product_id')}>
                <option value="">All products</option>
                {(products.data || []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Field>
            ) : null}
            <Field as="select" label="Channel" value={form.channel} onChange={set('channel')}>
              {Object.entries(CHANNEL_LABEL).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </Field>
            <Field label="Send on" type="date" min={today()} value={form.scheduled_for} onChange={set('scheduled_for')}
                   hint="Today sends straight away; a later date waits for that day's run" />
            <Field label="Name (for your records)" value={form.name} onChange={set('name')} placeholder="e.g. Christmas closing hours" />
          </div>

          {form.channel === 'email' || form.channel === 'preferred' ? (
            <Field label="Email subject" value={form.subject} onChange={set('subject')}
                   placeholder="Used for borrowers who get it by email" />
          ) : null}

          <label className="field">
            <span className="label-text">Message</span>
            <textarea ref={textRef} rows={5} value={form.text} maxLength={1000} required
                      onChange={(e) => setForm((f) => ({ ...f, text: e.target.value }))} />
            <span className="hint">
              {form.text.length} / 1000 characters
              {form.text.length > 160 ? ` · about ${Math.ceil(form.text.length / 153)} SMS each` : ' · one SMS each'}
            </span>
          </label>
          <div className="bulk-chips">
            <span className="muted">Insert:</span>
            {Object.entries(placeholders).map(([name, about]) => (
              <button type="button" key={name} className="btn small" title={about} onClick={() => insert(name)}>
                {`{${name}}`}
              </button>
            ))}
          </div>

          <div className="bulk-preview">
            {previewError ? (
              <p className="tag-danger">{previewError}</p>
            ) : preview ? (
              <>
                <p>
                  <strong>{preview.total - preview.skipped}</strong> borrower(s) will get this
                  {preview.sms ? ` · ${preview.sms} by SMS` : ''}
                  {preview.whatsapp ? ` · ${preview.whatsapp} by WhatsApp` : ''}
                  {preview.email ? ` · ${preview.email} by email` : ''}
                  {preview.skipped ? ` · ${preview.skipped} skipped (no ${form.channel === 'email' ? 'email address' : 'number'})` : ''}
                </p>
                {preview.samples.map((s) => (
                  <div className="bulk-sample" key={s.to}>
                    <span className="muted">
                      To {s.to} by {CHANNEL_LABEL[s.channel]}
                    </span>
                    <p>{s.text}</p>
                  </div>
                ))}
              </>
            ) : (
              <p className="muted">Working out who this reaches…</p>
            )}
          </div>

          <button className="btn primary" type="submit" disabled={busy || !preview || preview.total - preview.skipped < 1}>
            <Icon name="message" size={15} />
            {busy ? 'Sending…' : form.scheduled_for === today() ? 'Send now' : 'Schedule'}
          </button>
        </form>
      ) : null}

      <h3 style={{ marginTop: 20 }}>Sent and scheduled</h3>
      <ErrorBanner error={list.error} onRetry={list.reload} />
      {list.loading && !list.data ? (
        <Loading what="Loading" />
      ) : (
        <DataTable
          caption="Bulk messages"
          rows={list.data?.campaigns || []}
          empty="No bulk messages yet."
          columns={[
            { key: 'when', header: 'Created', render: (c) => dateTime(c.created_at) },
            { key: 'name', header: 'Name', render: (c) => <strong>{c.name}</strong> },
            { key: 'to', header: 'To', render: (c) => audiences[c.audience?.who] || c.audience?.who },
            { key: 'channel', header: 'Channel', render: (c) => CHANNEL_LABEL[c.channel] },
            { key: 'on', header: 'Send on', render: (c) => c.scheduled_for },
            { key: 'queued', header: 'Messages', num: true, render: (c) => c.queued },
            { key: 'sent', header: 'Sent', num: true, render: (c) => c.sent },
            { key: 'delivered', header: 'Delivered', num: true, render: (c) => c.delivered },
            { key: 'waiting', header: 'Waiting', num: true, render: (c) => c.waiting },
            {
              key: 'failed',
              header: 'Failed',
              num: true,
              render: (c) => (c.failed ? <span className="tag-danger">{c.failed}</span> : 0),
            },
            { key: 'by', header: 'By', render: (c) => c.created_by || '-' },
          ]}
        />
      )}
    </>
  )
}
