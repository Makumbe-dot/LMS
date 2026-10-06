import { useState } from 'react'
import { Link } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Field, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { post, postForm, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateOnly, dateTime, fmt } from '../lib/format.js'
import { useApi, useDebounced } from '../lib/useApi.js'

const TABS = [
  ['unmatched', 'Waiting'],
  ['posted', 'Posted'],
  ['rejected', 'Rejected'],
  ['', 'All'],
]

const MATCHED_BY = {
  loan_no: 'loan number',
  borrower_no: 'borrower number',
  national_id: 'national ID',
  phone: 'paying phone',
  staff: 'staff',
}

/**
 * Money a provider says it received: mobile-money notifications as they arrive,
 * and bank or mobile-money statements uploaded here. Whatever named exactly one
 * loan has already been posted; the rest waits on the first tab with the reason,
 * for someone with the cash right to assign to a loan or reject.
 */
export default function Payments() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const [status, setStatus] = useState('unmatched')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [action, setAction] = useState(null) // {kind: 'assign'|'reject', payment} | {kind: 'import'}
  const [busy, setBusy] = useState(false)
  const debounced = useDebounced(search)
  const mayAct = can('cash')

  const path = `/api/payments${qs({ status, q: debounced, page, page_size: 25 })}`
  const { data, error, loading, reload } = useApi(path)
  const rows = data?.results || []
  const waiting = data?.totals?.unmatched

  async function run(promise, message) {
    setBusy(true)
    try {
      const result = await promise
      toast(message(result))
      setAction(null)
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  function importStatement(values, form) {
    const body = new FormData()
    body.append('file', form.file)
    body.append('provider', values.provider || '')
    body.append('method', values.method)
    return run(
      postForm('/api/payments/import', body),
      (r) =>
        `${r.new} new: ${r.posted} posted, ${r.waiting} waiting` +
        (r.duplicates ? `; ${r.duplicates} already held` : '') +
        (r.errors.length ? `; ${r.errors.length} line(s) unreadable` : ''),
    )
  }

  return (
    <>
      <PageHeader
        title="Incoming payments"
        meta="Mobile money and bank transfers as the provider reports them. A payment that names one loan is posted on arrival; the rest wait here."
      >
        {mayAct ? (
          <>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() =>
                run(
                  post('/api/payments/retry'),
                  (r) => `${r.posted} posted; ${r.still_waiting} still waiting`,
                )
              }
            >
              Match again
            </button>
            <button
              type="button"
              className="btn primary"
              onClick={() => setAction({ kind: 'import' })}
            >
              Upload a statement
            </button>
          </>
        ) : null}
      </PageHeader>

      <div className="row" style={{ marginBottom: 12, gap: 8, flexWrap: 'wrap' }}>
        <div className="tabs" role="tablist" aria-label="Payment status">
          {TABS.map(([value, label]) => (
            <button
              key={value || 'all'}
              type="button"
              role="tab"
              aria-selected={status === value}
              onClick={() => {
                setStatus(value)
                setPage(1)
              }}
            >
              {label}
              {value === 'unmatched' && waiting?.count ? ` (${waiting.count})` : ''}
            </button>
          ))}
        </div>
        <input
          type="search"
          placeholder="Reference, phone, name or loan"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            setPage(1)
          }}
          aria-label="Search payments"
          style={{ minWidth: 240 }}
        />
      </div>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading payments" />
      ) : (
        <>
          <Pager meta={data} onPage={setPage} noun="payments" />
          <DataTable
            caption="Incoming payments"
            rows={rows}
            empty={
              status === 'unmatched'
                ? 'Nothing waiting: every payment received has been posted or rejected.'
                : 'No payments'
            }
            columns={[
              { key: 'paid', header: 'Paid on', render: (r) => dateOnly(r.paid_on) },
              {
                key: 'from',
                header: 'From',
                render: (r) => (
                  <>
                    <div>{r.payer_name || r.payer_phone || '-'}</div>
                    {r.payer_name && r.payer_phone ? (
                      <small className="muted">{r.payer_phone}</small>
                    ) : null}
                  </>
                ),
              },
              {
                key: 'ref',
                header: 'Account given',
                render: (r) => r.account_ref || <span className="muted">none</span>,
              },
              {
                key: 'provider',
                header: 'Provider',
                render: (r) => (
                  <>
                    <div>{r.provider}</div>
                    <small className="muted">{r.external_id}</small>
                  </>
                ),
              },
              {
                key: 'amount',
                header: 'Amount',
                num: true,
                render: (r) => `${r.currency ? `${r.currency} ` : ''}${fmt(r.amount)}`,
              },
              {
                key: 'status',
                header: 'Status',
                render: (r) => (
                  <>
                    <span
                      className={
                        r.status === 'posted'
                          ? 'tag-ok'
                          : r.status === 'rejected'
                            ? 'tag-danger'
                            : 'tag-warn'
                      }
                    >
                      {r.status_label}
                    </span>
                    {r.status === 'posted' ? (
                      <div>
                        <small className="muted">
                          to <Link to={`/loans/${r.loan_id}`}>{r.loan_no}</Link> by{' '}
                          {MATCHED_BY[r.matched_by] || r.matched_by}
                        </small>
                      </div>
                    ) : r.reason ? (
                      <div>
                        <small className="muted">{r.reason}</small>
                      </div>
                    ) : null}
                  </>
                ),
              },
              {
                key: 'received',
                header: 'Received',
                render: (r) => <small className="muted">{dateTime(r.received_at)}</small>,
              },
              ...(mayAct
                ? [
                    {
                      key: 'actions',
                      header: '',
                      render: (r) =>
                        r.status === 'unmatched' ? (
                          <div className="row" style={{ gap: 6 }}>
                            <button
                              type="button"
                              className="btn small primary"
                              onClick={() => setAction({ kind: 'assign', payment: r })}
                            >
                              Assign
                            </button>
                            <button
                              type="button"
                              className="btn small"
                              onClick={() => setAction({ kind: 'reject', payment: r })}
                            >
                              Reject
                            </button>
                          </div>
                        ) : null,
                    },
                  ]
                : []),
            ]}
          />
        </>
      )}

      {action?.kind === 'assign' ? (
        <FormModal
          title={`Assign ${action.payment.currency || ''} ${fmt(action.payment.amount)} to a loan`}
          submitLabel="Post to this loan"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(values) =>
            run(
              post(`/api/payments/${action.payment.id}/assign`, { loan_no: values.loan_no }),
              (r) => `Posted to ${r.loan_no}`,
            )
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            {action.payment.payer_name || action.payment.payer_phone || 'The payer'} gave
            {action.payment.account_ref ? ` “${action.payment.account_ref}”` : ' no account'}.
          </p>
          <Field
            label="Loan number"
            name="loan_no"
            required
            autoFocus
            defaultValue={action.payment.loan_no || ''}
            hint="Posted as a repayment on this loan, on the date it was paid"
          />
        </FormModal>
      ) : null}

      {action?.kind === 'reject' ? (
        <FormModal
          title="Reject this payment"
          submitLabel="Reject"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(values) =>
            run(
              post(`/api/payments/${action.payment.id}/reject`, { reason: values.reason }),
              () => 'Payment rejected',
            )
          }
        >
          <Field
            label="Why"
            name="reason"
            required
            autoFocus
            hint="Refunded to the sender, meant for another institution, a test… It stays on record and is never posted."
          />
        </FormModal>
      ) : null}

      {action?.kind === 'import' ? (
        <StatementUpload busy={busy} onClose={() => setAction(null)} onSubmit={importStatement} />
      ) : null}
    </>
  )
}

function StatementUpload({ busy, onClose, onSubmit }) {
  const [file, setFile] = useState(null)
  return (
    <FormModal
      title="Upload a statement"
      submitLabel="Upload and match"
      busy={busy}
      submitDisabled={!file}
      onClose={onClose}
      onSubmit={(values) => onSubmit(values, { file })}
    >
      <div className="grid cols-2">
        <Field label="Provider" name="provider" required placeholder="e.g. EcoCash, CBZ" />
        <Field as="select" label="Kind" name="method" defaultValue="mobile_money">
          <option value="mobile_money">Mobile money</option>
          <option value="bank_transfer">Bank transfer</option>
        </Field>
      </div>
      <label className="field">
        <span className="label-text">CSV file</span>
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        <span className="hint">
          Needs a transaction ID and an amount column; date, reference or narration, phone and
          name are used when present. A line already held is skipped, so uploading the same
          statement twice posts nothing twice.
        </span>
      </label>
    </FormModal>
  )
}
