import { useState } from 'react'
import { Link } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime, fmt } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

/** What borrowers asked for in the portal: top-ups and call-backs, for staff to answer. */
export default function PortalRequests() {
  const { can } = useAuth()
  const { settings } = useOrg()
  const { toast, toastError } = useToast()
  const [status, setStatus] = useState('open')
  const [closing, setClosing] = useState(null)
  const [busy, setBusy] = useState(false)
  const { data, error, loading, reload } = useApi(`/api/portal-requests${qs({ status })}`)
  const mayAct = can('loans')

  async function close(values) {
    setBusy(true)
    try {
      await post(`/api/portal-requests/${closing.id}/done`, { outcome: values.outcome })
      toast('Marked as dealt with')
      setClosing(null)
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
        title="Portal requests"
        meta={
          settings?.portal_enabled
            ? `Borrowers sign in at ${window.location.origin}/portal with their national ID, ` +
              'phone and a texted code.'
            : 'The borrower portal is closed. An administrator opens it in Settings.'
        }
      />
      <div className="tabs" role="tablist" aria-label="Request status">
        {[
          ['open', 'Waiting'],
          ['done', 'Dealt with'],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={status === key}
            onClick={() => setStatus(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading requests" />
      ) : (
        <DataTable
          caption="Portal requests"
          rows={data || []}
          empty={status === 'open' ? 'Nothing waiting.' : 'Nothing dealt with yet.'}
          columns={[
            { key: 'when', header: 'Asked', render: (r) => dateTime(r.created_at) },
            {
              key: 'who',
              header: 'Borrower',
              render: (r) => (
                <>
                  <Link to={`/borrowers/${r.borrower_id}`}>{r.borrower_name}</Link>
                  <div>
                    <small className="muted">{r.borrower_phone}</small>
                  </div>
                </>
              ),
            },
            { key: 'what', header: 'Asks for', render: (r) => r.kind_label },
            {
              key: 'loan',
              header: 'Loan',
              render: (r) => (r.loan ? <Link to={`/loans/${r.loan}`}>{r.loan_no}</Link> : '-'),
            },
            {
              key: 'amount',
              header: 'Amount',
              num: true,
              render: (r) => (r.amount ? fmt(r.amount) : '-'),
            },
            { key: 'message', header: 'Message', wrap: true, render: (r) => r.message || '-' },
            ...(status === 'done'
              ? [
                  {
                    key: 'outcome',
                    header: 'Outcome',
                    wrap: true,
                    render: (r) => `${r.outcome || '-'} (${r.handled_by_name || ''})`,
                  },
                ]
              : mayAct
                ? [
                    {
                      key: 'act',
                      header: '',
                      render: (r) => (
                        <button type="button" className="btn small" onClick={() => setClosing(r)}>
                          Dealt with
                        </button>
                      ),
                    },
                  ]
                : []),
          ]}
        />
      )}
      {closing ? (
        <FormModal
          title={`${closing.kind_label} for ${closing.borrower_name}`}
          submitLabel="Mark as dealt with"
          busy={busy}
          onClose={() => setClosing(null)}
          onSubmit={close}
        >
          <Field
            label="What was done"
            name="outcome"
            required
            autoFocus
            hint="The borrower sees this in the portal: e.g. “Top-up approved for 500”, “Called on 3 March”."
          />
        </FormModal>
      ) : null}
    </>
  )
}
