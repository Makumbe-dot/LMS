import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ExportButtons, Badge, ErrorBanner, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime, humanise } from '../lib/format.js'
import { useApi, useDebounced } from '../lib/useApi.js'

const STATUSES = ['queued', 'sent', 'cancelled', 'failed']
const CHANNELS = { sms: 'SMS', whatsapp: 'WhatsApp', email: 'Email' }
const KINDS = ['reminder', 'arrears', 'receipt', 'welcome', 'bulk', 'signing_code', 'portal_code']

/**
 * The borrower messaging outbox: generated here, delivered through the configured
 * gateway. The banner says which gateway, because "is this actually going
 * anywhere?" is the first thing anyone asks about an outbox — and for most of this
 * system's life the answer was no.
 */
export default function Notifications() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const [status, setStatus] = useState('queued')
  const [kind, setKind] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState([])
  const [busy, setBusy] = useState(false)
  const debounced = useDebounced(search)

  const path = `/api/notifications${qs({ status, kind, q: debounced, page, page_size: 25 })}`
  const { data, error, loading, reload } = useApi(path)
  const gateway = useApi('/api/notifications/gateway')
  const mayAct = can('messages')
  const delivers =
    gateway.data?.sms_delivers || gateway.data?.email_delivers || gateway.data?.whatsapp_delivers

  const rows = data?.results || []
  const allSelected = rows.length > 0 && selected.length === rows.length

  async function run(promise, message) {
    setBusy(true)
    try {
      const result = await promise
      toast(message(result))
      setSelected([])
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Outbox"
        meta="Every message to a borrower, by SMS, WhatsApp or email: waiting, sent, delivered or failed."
      >
        {mayAct ? (
          <>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() =>
                run(
                  post('/api/notifications/generate'),
                  (r) =>
                    `Queued ${r.reminders_queued} reminder(s) and ${r.arrears_notices_queued} arrears notice(s)`,
                )
              }
            >
              Generate
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              title={
                delivers
                  ? 'Hand each message to the gateway'
                  : 'The gateway is set to log rather than deliver; nothing will reach a borrower'
              }
              onClick={() =>
                run(
                  post('/api/notifications/send', selected.length ? { ids: selected } : {}),
                  (r) => {
                    const parts = [`${r.sent} sent`]
                    if (r.retrying) parts.push(`${r.retrying} will be retried`)
                    if (r.failed) parts.push(`${r.failed} failed`)
                    if (r.fell_back_to_sms) parts.push(`${r.fell_back_to_sms} resent by SMS`)
                    return parts.join(', ')
                  },
                )
              }
            >
              {selected.length ? `Send ${selected.length} selected` : 'Send all due'}
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy || selected.length === 0}
              onClick={() =>
                run(
                  post('/api/notifications/cancel', { ids: selected }),
                  (r) => `Cancelled ${r.cancelled} message(s)`,
                )
              }
            >
              Cancel selected
            </button>
          </>
        ) : null}
        <ExportButtons path={path} name={'notifications'} />
      </PageHeader>

      {gateway.data ? (
        <div className={delivers ? 'hint' : 'banner'} role={delivers ? undefined : 'status'}>
          {delivers ? (
            <>
              <strong>Delivering for real. </strong>
              SMS via <code>{gateway.data.sms_backend}</code>, WhatsApp via{' '}
              <code>{gateway.data.whatsapp_backend}</code>, email via{' '}
              <code>{gateway.data.email_backend}</code>. A message that fails is retried on
              the next run, up to {gateway.data.max_attempts} attempts, then marked failed; a
              WhatsApp message that cannot be delivered is resent by SMS.
            </>
          ) : (
            <>
              <strong>Nothing is being delivered. </strong>
              The gateway is set to <code>{gateway.data.sms_backend}</code>, which logs messages
              instead of sending them — deliberate on a development machine, so seeded data
              cannot text real numbers. To deliver, set <code>MESSAGE_SMS_BACKEND</code> (an SMS
              provider) and <code>MESSAGE_WHATSAPP_BACKEND=meta</code> in <code>backend/.env</code>.
              Meanwhile the <strong>WhatsApp</strong> button on each loan opens WhatsApp with the
              message typed in.
            </>
          )}
        </div>
      ) : null}

      <div className="row" style={{ marginBottom: 12 }}>
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value)
            setPage(1)
          }}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {humanise(s)}
            </option>
          ))}
        </select>
        <select
          value={kind}
          onChange={(e) => {
            setKind(e.target.value)
            setPage(1)
          }}
          aria-label="Filter by kind"
        >
          <option value="">All kinds</option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {humanise(k)}
            </option>
          ))}
        </select>
        <input
          type="search"
          placeholder="Search recipient or message"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            setPage(1)
          }}
          aria-label="Search messages"
          style={{ minWidth: 240 }}
        />
      </div>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading messages" />
      ) : (
        <>
          <Pager meta={data} onPage={setPage} noun="messages" />
          <DataTable
            caption="Message outbox"
            rows={rows}
            empty="Nothing queued. Use Generate to build reminders from the active book."
            columns={[
              ...(mayAct
                ? [
                    {
                      key: 'select',
                      header: (
                        <input
                          type="checkbox"
                          checked={allSelected}
                          aria-label="Select all on this page"
                          onChange={(e) =>
                            setSelected(e.target.checked ? rows.map((r) => r.id) : [])
                          }
                        />
                      ),
                      render: (r) => (
                        <input
                          type="checkbox"
                          checked={selected.includes(r.id)}
                          aria-label={`Select message ${r.id}`}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) =>
                            setSelected((current) =>
                              e.target.checked
                                ? [...current, r.id]
                                : current.filter((id) => id !== r.id),
                            )
                          }
                        />
                      ),
                    },
                  ]
                : []),
              { key: 'scheduled', header: 'Scheduled', render: (r) => r.scheduled_for },
              { key: 'kind', header: 'Kind', render: (r) => humanise(r.kind) },
              {
                key: 'channel',
                header: 'Channel',
                render: (r) => (
                  <>
                    {CHANNELS[r.channel] || r.channel}
                    {r.fallback_of_id ? <span className="muted"> (in place of WhatsApp)</span> : null}
                  </>
                ),
              },
              { key: 'to', header: 'To', render: (r) => r.to_address },
              { key: 'borrower', header: 'Borrower', render: (r) => r.borrower_name },
              { key: 'loan', header: 'Loan', render: (r) => r.loan_no || '-' },
              {
                key: 'body',
                header: 'Message',
                render: (r) => (
                  <span title={r.body} style={{ display: 'inline-block', maxWidth: 420,
                                                overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {r.body}
                  </span>
                ),
              },
              {
                key: 'status',
                header: 'Status',
                render: (r) => (
                  <>
                    <Badge value={r.status} />
                    {r.status === 'queued' && r.attempts > 0 ? (
                      <span className="muted" title={r.error || ''}>
                        {' '}
                        retrying, {r.attempts} attempt{r.attempts === 1 ? '' : 's'}
                      </span>
                    ) : null}
                  </>
                ),
              },
              {
                key: 'sent',
                header: 'Sent',
                render: (r) =>
                  r.sent_at ? (
                    <>
                      {dateTime(r.sent_at)}
                      {r.provider ? <span className="muted"> via {r.provider}</span> : null}
                      {r.test_redirect ? <span className="muted"> · test, to {r.test_redirect}</span> : null}
                      {r.delivery_status ? (
                        <span className="muted"> · {humanise(r.delivery_status)}</span>
                      ) : null}
                    </>
                  ) : (
                    '-'
                  ),
              },
              {
                key: 'error',
                header: 'Last error',
                render: (r) =>
                  r.error ? (
                    <span className="tag-danger" title={r.error}
                          style={{ display: 'inline-block', maxWidth: 260, overflow: 'hidden',
                                   textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.error}
                    </span>
                  ) : (
                    ''
                  ),
              },
            ]}
          />
        </>
      )}

      {gateway.data?.whatsapp_template_guide?.length ? (
        <details className="card" style={{ marginTop: 16 }}>
          <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
            WhatsApp templates: what to submit for approval
          </summary>
          <p className="muted" style={{ fontSize: 13 }}>
            WhatsApp only delivers a business's message to someone who has not written in the last
            24 hours if it is an approved template. Create one per message, category Utility, with
            the wording below: in Meta's WhatsApp Manager (Message templates) using the suggested
            name, then list the names in <code>META_WHATSAPP_TEMPLATES</code>; or in Twilio's Content
            Template Builder, then list the Content SIDs (HX…) in <code>TWILIO_WHATSAPP_TEMPLATES</code>.
            A message with no template goes as plain text and is resent by SMS if WhatsApp refuses it.
          </p>
          <DataTable
            caption="WhatsApp templates"
            rows={gateway.data.whatsapp_template_guide}
            rowKey={(r) => r.kind}
            columns={[
              { key: 'label', header: 'Message', render: (r) => r.label },
              { key: 'kind', header: 'Key', render: (r) => <code>{r.kind}</code> },
              { key: 'name', header: 'Suggested name', render: (r) => <code>{r.suggested_name}</code> },
              { key: 'text', header: 'Template wording', wrap: true, render: (r) => r.text },
              {
                key: 'sid',
                header: 'Set up',
                render: (r) =>
                  r.content_sid ? <span className="tag-ok">{r.content_sid}</span> : <span className="muted">not yet</span>,
              },
            ]}
          />
        </details>
      ) : null}
    </>
  )
}
