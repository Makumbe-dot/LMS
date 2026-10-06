import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ExportButtons, Badge, ErrorBanner, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime, humanise } from '../lib/format.js'
import { useApi, useDebounced } from '../lib/useApi.js'

const STATUSES = ['queued', 'sent', 'cancelled', 'failed']
const KINDS = ['reminder', 'arrears', 'receipt', 'welcome']

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
  const delivers = gateway.data?.sms_delivers || gateway.data?.email_delivers

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
        title="Messages"
        meta="Reminders, arrears notices and receipts. Nothing leaves the system until it is sent."
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
              SMS via <code>{gateway.data.sms_backend}</code>, email via{' '}
              <code>{gateway.data.email_backend}</code>. A message that fails is retried on
              the next run, up to {gateway.data.max_attempts} attempts, then marked failed.
            </>
          ) : (
            <>
              <strong>Nothing is being delivered. </strong>
              The gateway is set to <code>{gateway.data.sms_backend}</code>, which logs messages
              instead of sending them — deliberate on a development machine, so seeded data
              cannot text real numbers. Set <code>MESSAGE_SMS_BACKEND=http</code> and{' '}
              <code>MESSAGE_HTTP_URL</code> in <code>backend/.env</code> to deliver.
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
              { key: 'channel', header: 'Channel', render: (r) => r.channel.toUpperCase() },
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
    </>
  )
}
