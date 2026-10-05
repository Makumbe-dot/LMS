import { useState } from 'react'
import { Link } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Badge, ErrorBanner, Field, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateOnly, humanise, money } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const STATUSES = ['unmatched', 'posted', 'dismissed']

/**
 * Payments that EcoCash, a bank or another provider reported as they arrived. One
 * that names a loan is posted on arrival; this page is mostly the queue of the ones
 * that could not be placed safely, for someone to put on the right loan or set aside.
 */
export default function IncomingPayments() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const [status, setStatus] = useState('unmatched')
  const [page, setPage] = useState(1)
  const [placing, setPlacing] = useState(null)
  const [dismissing, setDismissing] = useState(null)
  const [busy, setBusy] = useState(false)

  const { data, error, loading, reload } = useApi(
    `/api/payments/incoming${qs({ status, page, page_size: 25 })}`,
  )
  const mayDismiss = can('admin', 'loan_officer')

  async function act(promise, message, close) {
    setBusy(true)
    try {
      const result = await promise
      close(null)
      toast(message(result))
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
        title="Incoming payments"
        meta="Mobile-money and bank payments reported by the provider. Those naming a loan are posted on arrival."
      />

      {data?.unmatched ? (
        <div className="banner" role="status">
          <strong>{data.unmatched} payment{data.unmatched === 1 ? '' : 's'} waiting. </strong>
          Money has been received that is not on any loan yet. Place each one on the right
          loan, or set it aside with the reason.
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
      </div>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading payments" />
      ) : (
        <>
          <Pager meta={data} onPage={setPage} noun="payments" />
          <DataTable
            caption="Incoming payments"
            rows={data?.results || []}
            empty={
              status === 'unmatched'
                ? 'Nothing waiting. Every payment received has been placed.'
                : 'No payments'
            }
            columns={[
              { key: 'received_on', header: 'Received', render: (r) => dateOnly(r.received_on) },
              { key: 'provider', header: 'From', render: (r) => r.provider },
              { key: 'reference', header: 'Reference', render: (r) => r.reference },
              { key: 'amount', header: 'Amount', render: (r) => money(r.amount) },
              {
                key: 'payer',
                header: 'Payer',
                render: (r) =>
                  [r.payer_name, r.payer_phone, r.account && `ref ${r.account}`]
                    .filter(Boolean)
                    .join(' · ') || '-',
              },
              {
                key: 'loan',
                header: 'Loan',
                render: (r) =>
                  r.loan ? <Link to={`/loans/${r.loan}`}>{r.loan_no}</Link> : '-',
              },
              { key: 'status', header: 'Status', render: (r) => <Badge value={r.status} /> },
              {
                key: 'note',
                header: 'Note',
                render: (r) => (
                  <span style={{ display: 'inline-block', maxWidth: 260, whiteSpace: 'normal' }}>
                    {r.note || '-'}
                  </span>
                ),
              },
              {
                key: 'act',
                header: '',
                render: (r) =>
                  r.status === 'unmatched' ? (
                    <div className="row" style={{ gap: 6 }}>
                      <button type="button" className="btn small" onClick={() => setPlacing(r)}>
                        Place on loan
                      </button>
                      {mayDismiss ? (
                        <button
                          type="button"
                          className="btn small"
                          onClick={() => setDismissing(r)}
                        >
                          Set aside
                        </button>
                      ) : null}
                    </div>
                  ) : null,
              },
            ]}
          />
        </>
      )}

      {placing ? (
        <FormModal
          title={`Place ${money(placing.amount)} (${placing.reference})`}
          submitLabel="Post to loan"
          busy={busy}
          onClose={() => setPlacing(null)}
          onSubmit={(values) =>
            act(
              post(`/api/payments/incoming/${placing.id}/assign`, values),
              (r) => `Posted to ${r.loan_no}`,
              setPlacing,
            )
          }
        >
          <Field
            label="Loan number"
            name="loan_no"
            required
            defaultValue={placing.loan_no || ''}
            hint="Posted through the usual order: penalties, charges, interest, then principal"
          />
        </FormModal>
      ) : null}

      {dismissing ? (
        <FormModal
          title={`Set aside ${money(dismissing.amount)} (${dismissing.reference})`}
          submitLabel="Set aside"
          busy={busy}
          onClose={() => setDismissing(null)}
          onSubmit={(values) =>
            act(
              post(`/api/payments/incoming/${dismissing.id}/dismiss`, values),
              () => 'Payment set aside',
              setDismissing,
            )
          }
        >
          <Field
            as="textarea"
            label="Why"
            name="note"
            rows={2}
            required
            maxLength={255}
            hint="For example: refunded to the payer on 12 March, or not a payment to us"
          />
        </FormModal>
      ) : null}
    </>
  )
}
