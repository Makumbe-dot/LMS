import { useState } from 'react'
import { Link } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Badge, Check, ErrorBanner, Field, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateOnly, humanise, money, today } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const STATUSES = ['lodged', 'paid', 'rejected']
const CAUSES = [
  ['death', 'Death'],
  ['disability', 'Permanent disability'],
  ['retrenchment', 'Retrenchment'],
  ['other', 'Other'],
]

/**
 * Credit-life claims. An officer lodges one when a borrower dies, is disabled or is
 * retrenched; from then the loan is neither penalised nor sent reminders. An admin
 * records the insurer's answer: the payout posts to the loan like any repayment.
 */
export default function Claims() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const [status, setStatus] = useState('lodged')
  const [page, setPage] = useState(1)
  const [lodging, setLodging] = useState(false)
  const [paying, setPaying] = useState(null)
  const [rejecting, setRejecting] = useState(null)
  const [busy, setBusy] = useState(false)

  const { data, error, loading, reload } = useApi(`/api/claims${qs({ status, page, page_size: 25 })}`)
  const mayLodge = can('admin', 'loan_officer')
  const mayDecide = can('admin')

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
        title="Credit-life claims"
        meta="While a claim is open the loan accrues no penalties and the borrower is sent no reminders."
      >
        {mayLodge ? (
          <button type="button" className="btn primary" onClick={() => setLodging(true)}>
            Lodge a claim
          </button>
        ) : null}
      </PageHeader>

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
        <Loading what="Loading claims" />
      ) : (
        <>
          <Pager meta={data} onPage={setPage} noun="claims" />
          <DataTable
            caption="Credit-life claims"
            rows={data?.results || []}
            empty={status === 'lodged' ? 'No claims open' : 'No claims'}
            columns={[
              { key: 'claim_no', header: 'Claim', render: (r) => r.claim_no },
              {
                key: 'loan',
                header: 'Loan',
                render: (r) => <Link to={`/loans/${r.loan}`}>{r.loan_no}</Link>,
              },
              { key: 'borrower', header: 'Borrower', render: (r) => r.borrower_name },
              { key: 'cause', header: 'Cause', render: (r) => r.cause_label },
              { key: 'event', header: 'Event', render: (r) => dateOnly(r.event_date) },
              { key: 'claimed', header: 'Claimed', render: (r) => money(r.amount_claimed) },
              {
                key: 'paid',
                header: 'Paid',
                render: (r) => (r.amount_paid ? money(r.amount_paid) : '-'),
              },
              {
                key: 'written_off',
                header: 'Written off',
                render: (r) => (Number(r.remainder_written_off) ? money(r.remainder_written_off) : '-'),
              },
              { key: 'status', header: 'Status', render: (r) => <Badge value={r.status} /> },
              {
                key: 'note',
                header: 'Note',
                render: (r) => r.decision_note || r.notes || '-',
              },
              {
                key: 'act',
                header: '',
                render: (r) =>
                  r.status === 'lodged' && mayDecide ? (
                    <div className="row" style={{ gap: 6 }}>
                      <button type="button" className="btn small" onClick={() => setPaying(r)}>
                        Record payout
                      </button>
                      <button type="button" className="btn small" onClick={() => setRejecting(r)}>
                        Rejected
                      </button>
                    </div>
                  ) : null,
              },
            ]}
          />
        </>
      )}

      {lodging ? (
        <FormModal
          title="Lodge a credit-life claim"
          submitLabel="Lodge claim"
          busy={busy}
          onClose={() => setLodging(false)}
          onSubmit={(values) =>
            act(
              post('/api/claims', values),
              (r) => `${r.claim_no} lodged for ${money(r.amount_claimed)}`,
              setLodging,
            )
          }
        >
          <div className="grid cols-2">
            <Field label="Loan number" name="loan_no" required />
            <Field as="select" label="Cause" name="cause" required defaultValue="death">
              {CAUSES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Field>
            <Field label="Date of the event" name="event_date" type="date" required max={today()} />
            <Field label="Insurer's reference" name="insurer_reference" />
          </div>
          <Field as="textarea" label="Notes" name="notes" rows={2} />
        </FormModal>
      ) : null}

      {paying ? (
        <FormModal
          title={`Payout on ${paying.claim_no}`}
          submitLabel="Post payout"
          busy={busy}
          onClose={() => setPaying(null)}
          onSubmit={(values) =>
            act(
              post(`/api/claims/${paying.id}/pay`, values),
              (r) => `${r.claim_no} paid`,
              setPaying,
            )
          }
        >
          <div className="grid cols-2">
            <Field
              label="Amount the insurer paid"
              name="amount"
              type="number"
              step="0.01"
              min="0.01"
              required
              defaultValue={paying.amount_claimed}
            />
            <Field label="Received on" name="paid_on" type="date" defaultValue={today()} />
            <Field
              label="Insurer's payment reference"
              name="reference"
              defaultValue={paying.insurer_reference || ''}
            />
          </div>
          <Check label="Write off whatever the payout leaves on the loan" name="write_off_remainder" />
          <Field as="textarea" label="Note" name="note" rows={2} />
        </FormModal>
      ) : null}

      {rejecting ? (
        <FormModal
          title={`${rejecting.claim_no} rejected by the insurer`}
          submitLabel="Record rejection"
          busy={busy}
          onClose={() => setRejecting(null)}
          onSubmit={(values) =>
            act(
              post(`/api/claims/${rejecting.id}/reject`, values),
              (r) => `${r.claim_no} closed as rejected. Penalties resume on the loan.`,
              setRejecting,
            )
          }
        >
          <Field
            as="textarea"
            label="The insurer's reason"
            name="note"
            rows={2}
            required
            hint="The loan goes back to normal, and penalties resume from where they stopped"
          />
        </FormModal>
      ) : null}
    </>
  )
}
