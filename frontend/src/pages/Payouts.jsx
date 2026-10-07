import { useState } from 'react'
import { Link } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import Icon from '../components/Icons.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { post, postDownload, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime, fmt } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const STATUS = { pending: 'To pay', sent: 'Sent', paid: 'Paid', failed: 'Failed' }
const BADGE = { pending: 'pending', sent: 'approved', paid: 'active', failed: 'failed' }

/**
 * Money disbursed loans still owe the borrower: by mobile money through the
 * provider, or by bank through a bulk-payment file uploaded to internet banking.
 * The disbursement is already in the books; this is the transfer itself.
 */
export default function Payouts() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const [status, setStatus] = useState('pending')
  const [busy, setBusy] = useState(false)
  const [marking, setMarking] = useState(null) // { payout, action }
  const { data, error, loading, reload } = useApi(`/api/payouts${qs({ status })}`)
  const mayPay = can('disburse')
  const rows = data?.results || []
  const pendingWallet = rows.filter((p) => p.status === 'pending' && p.method === 'mobile_money')
  const pendingBank = rows.filter((p) => p.status === 'pending' && p.method === 'bank_transfer' && p.account)

  async function act(promise, message) {
    setBusy(true)
    try {
      const result = await promise
      if (message) toast(message(result))
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
        title="Payouts"
        meta="Paying disbursed loans out to borrowers, by mobile money or bank transfer."
      >
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Show">
          <option value="pending">To pay</option>
          <option value="sent">Sent</option>
          <option value="failed">Failed</option>
          <option value="paid">Paid</option>
          <option value="">All</option>
        </select>
        {mayPay ? (
          <>
            <button
              type="button"
              className="btn"
              disabled={busy || !pendingBank.length}
              onClick={() =>
                act(postDownload('/api/payouts/bank-file', {}, 'bank-payments.csv'), () =>
                  'Bank file downloaded. Upload it to internet banking, then mark each payment paid.',
                )
              }
            >
              <Icon name="sheet" size={15} />
              Bank file ({pendingBank.length})
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={busy || !pendingWallet.length}
              onClick={() =>
                act(post('/api/payouts/send-mobile'), (r) =>
                  r.live
                    ? `${r.sent} sent to mobile money${r.failed ? `, ${r.failed} failed` : ''}`
                    : `${r.sent} logged only: no mobile-money provider is set up yet`,
                )
              }
            >
              <Icon name="phone" size={15} />
              Send mobile money ({pendingWallet.length})
            </button>
          </>
        ) : null}
      </PageHeader>

      {data && !data.live ? (
        <div className="banner" role="status">
          <strong>No mobile-money provider is connected. </strong>
          Wallet payouts are only logged until <code>PAYOUT_HTTP_URL</code> and the provider's fields are set in{' '}
          <code>backend/.env</code> (from EcoCash, OneMoney or your aggregator's API documentation). Bank payouts work now
          through the bank file.
        </div>
      ) : null}

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading payouts" />
      ) : (
        <DataTable
          caption="Payouts"
          rows={rows}
          empty={status === 'pending' ? 'Nothing waiting to be paid out.' : 'None.'}
          columns={[
            { key: 'when', header: 'Disbursed', render: (p) => dateTime(p.created_at) },
            { key: 'loan', header: 'Loan', render: (p) => <Link to={`/loans/${p.loan_id}`}>{p.loan_no}</Link> },
            { key: 'payee', header: 'Pay to', render: (p) => p.payee_name },
            {
              key: 'where',
              header: 'Where',
              render: (p) =>
                p.method === 'mobile_money' ? (
                  <>Mobile money · {p.account}</>
                ) : (
                  <>
                    {p.bank_name || 'Bank'} {p.bank_branch ? `(${p.bank_branch})` : ''} · {p.account || '—'}
                  </>
                ),
            },
            { key: 'amount', header: 'Amount', num: true, render: (p) => `${p.currency} ${fmt(p.amount)}` },
            {
              key: 'status',
              header: 'Status',
              wrap: true,
              render: (p) => (
                <>
                  <span className={`badge ${BADGE[p.status]}`}>{STATUS[p.status]}</span>
                  {p.batch ? <div className="muted" style={{ fontSize: 12 }}>{p.batch}</div> : null}
                  {p.provider_reference ? <div className="muted" style={{ fontSize: 12 }}>Ref {p.provider_reference}</div> : null}
                  {p.error ? <div className={p.status === 'failed' ? 'tag-danger' : 'muted'} style={{ fontSize: 12 }}>{p.error}</div> : null}
                </>
              ),
            },
            {
              key: 'act',
              header: '',
              render: (p) =>
                !mayPay ? null : p.status === 'failed' ? (
                  <button type="button" className="btn small" disabled={busy}
                          onClick={() => act(post(`/api/payouts/${p.id}/retry`), () => 'Back in the queue with the borrower’s current details')}>
                    Try again
                  </button>
                ) : p.status !== 'paid' ? (
                  <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                    <button type="button" className="btn small" onClick={() => setMarking({ payout: p, action: 'paid' })}>
                      Paid
                    </button>
                    <button type="button" className="btn small" onClick={() => setMarking({ payout: p, action: 'failed' })}>
                      Failed
                    </button>
                  </div>
                ) : null,
            },
          ]}
        />
      )}

      {marking ? (
        <FormModal
          title={marking.action === 'paid' ? `Mark ${marking.payout.loan_no} paid` : `${marking.payout.loan_no} did not arrive`}
          submitLabel={marking.action === 'paid' ? 'Mark paid' : 'Mark failed'}
          busy={busy}
          onClose={() => setMarking(null)}
          onSubmit={(values) =>
            act(
              post(`/api/payouts/${marking.payout.id}/${marking.action}`,
                marking.action === 'paid' ? { reference: values.reference } : { reason: values.reason }),
              () => {
                setMarking(null)
                return marking.action === 'paid' ? 'Marked paid' : 'Marked failed'
              },
            )
          }
        >
          {marking.action === 'paid' ? (
            <Field label="Bank or provider reference (optional)" name="reference" />
          ) : (
            <Field as="textarea" label="What went wrong" name="reason" rows={3} required
                   placeholder="e.g. Account number wrong; returned by the bank" />
          )}
        </FormModal>
      ) : null}
    </>
  )
}
