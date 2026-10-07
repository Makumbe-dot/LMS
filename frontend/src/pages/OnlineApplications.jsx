import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Badge, ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime, fmt } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

/**
 * Loans asked for online: from the public Apply page (new clients) or the portal
 * (existing borrowers). Accepting makes the applicant a borrower and opens the
 * New loan form with their figures; the loan is created, scored and approved there
 * as any other. Declining records why.
 */
export default function OnlineApplications() {
  const { can } = useAuth()
  const navigate = useNavigate()
  const { toast, toastError } = useToast()
  const [status, setStatus] = useState('new')
  const [declining, setDeclining] = useState(null)
  const [busy, setBusy] = useState(false)
  const { data, error, loading, reload } = useApi(`/api/online-applications${qs({ status })}`)
  const mayAccept = can('borrowers') && can('loans')

  async function accept(row) {
    setBusy(true)
    try {
      const result = await post(`/api/online-applications/${row.id}/accept`)
      toast(
        result.created_borrower
          ? `${row.first_name} ${row.last_name} is now borrower ${result.borrower_no}. Check the loan, then submit it.`
          : `Matched existing borrower ${result.borrower_no}. Check the loan, then submit it.`,
      )
      navigate(
        `/loans/new${qs({
          borrower: result.borrower_id,
          product: result.product_id,
          principal: result.principal,
          term: result.term_months,
          purpose: result.purpose,
        })}`,
      )
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Online applications"
        meta="Loans asked for on the public Apply page or in the borrower portal. Call the applicant, then accept or decline."
      >
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Show">
          <option value="new">Waiting</option>
          <option value="accepted">Accepted</option>
          <option value="declined">Declined</option>
          <option value="">All</option>
        </select>
        <a className="btn" href="/apply" target="_blank" rel="noreferrer">
          Open the Apply page
        </a>
      </PageHeader>
      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading applications" />
      ) : (
        <DataTable
          caption="Online applications"
          rows={data || []}
          empty={status === 'new' ? 'No applications waiting.' : 'None.'}
          columns={[
            { key: 'when', header: 'Applied', render: (r) => dateTime(r.created_at) },
            {
              key: 'who',
              header: 'Applicant',
              render: (r) => (
                <>
                  <strong>
                    {r.first_name} {r.last_name}
                  </strong>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {r.national_id} · {r.phone}
                    {r.borrower_no ? ` · ${r.borrower_no}` : ' · new client'}
                  </div>
                </>
              ),
            },
            { key: 'from', header: 'From', render: (r) => (r.source === 'portal' ? 'Portal' : 'Apply page') },
            { key: 'product', header: 'Loan', render: (r) => r.product_name },
            { key: 'amount', header: 'Amount', num: true, render: (r) => fmt(r.amount) },
            { key: 'term', header: 'Months', num: true, render: (r) => r.term_months },
            {
              key: 'salary',
              header: 'Net salary',
              num: true,
              render: (r) => (r.net_salary ? fmt(r.net_salary) : '-'),
            },
            { key: 'employer', header: 'Employer', render: (r) => r.employer || '-' },
            {
              key: 'status',
              header: 'Status',
              render: (r) => (
                <>
                  <Badge value={r.status === 'new' ? 'pending' : r.status === 'accepted' ? 'approved' : 'rejected'} />
                  {r.outcome ? <div className="muted" style={{ fontSize: 12 }}>{r.outcome}</div> : null}
                </>
              ),
            },
            {
              key: 'act',
              header: '',
              render: (r) =>
                r.status === 'new' && mayAccept ? (
                  <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                    <button type="button" className="btn small primary" disabled={busy} onClick={() => accept(r)}>
                      Accept
                    </button>
                    <button type="button" className="btn small" disabled={busy} onClick={() => setDeclining(r)}>
                      Decline
                    </button>
                  </div>
                ) : null,
            },
          ]}
        />
      )}

      {declining ? (
        <FormModal
          title={`Decline ${declining.first_name} ${declining.last_name}'s application`}
          busy={busy}
          submitLabel="Decline"
          onClose={() => setDeclining(null)}
          onSubmit={async (form) => {
            setBusy(true)
            try {
              await post(`/api/online-applications/${declining.id}/decline`, { reason: form.reason })
              toast('Application declined')
              setDeclining(null)
              reload()
            } catch (err) {
              toastError(err)
            } finally {
              setBusy(false)
            }
          }}
        >
          <Field as="textarea" label="Why" name="reason" rows={3} required />
        </FormModal>
      ) : null}
    </>
  )
}
