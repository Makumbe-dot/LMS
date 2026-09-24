import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Loading, PageHeader } from '../components/ui.jsx'
import { downloadCsv } from '../lib/api.js'
import { fmt, money, num } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const PATH = '/api/reports/par'

export default function Arrears() {
  const navigate = useNavigate()
  const { toastError } = useToast()
  const { data, error, loading, reload } = useApi(PATH)

  const rows = data || []
  const total = rows.reduce((sum, r) => sum + (num(r.arrears_amount) ?? 0), 0)

  return (
    <>
      <PageHeader
        title="Arrears and portfolio at risk"
        meta={data ? `${rows.length} loans in arrears, ${money(total)} overdue` : undefined}
      >
        <button type="button" className="btn" onClick={reload}>
          Refresh
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => downloadCsv(PATH, 'portfolio_at_risk').catch(toastError)}
        >
          Export CSV
        </button>
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading arrears" />
      ) : (
        <DataTable
          caption="Portfolio at risk"
          rows={rows}
          rowKey={(r) => r.loan_id}
          onRowClick={(r) => navigate(`/loans/${r.loan_id}`)}
          empty="Nothing is in arrears - the whole book is current"
          columns={[
            { key: 'loan', header: 'Loan', render: (r) => r.loan_no },
            { key: 'borrower', header: 'Borrower', render: (r) => r.borrower },
            { key: 'phone', header: 'Phone', render: (r) => r.phone },
            { key: 'employer', header: 'Employer', render: (r) => r.employer || '-' },
            { key: 'product', header: 'Product', render: (r) => r.product },
            { key: 'days', header: 'Days', num: true, render: (r) => r.days_in_arrears },
            {
              key: 'bucket',
              header: 'Bucket',
              render: (r) => (
                <span className={r.days_in_arrears > 30 ? 'tag-danger' : 'tag-warn'}>
                  {r.bucket} days
                </span>
              ),
            },
            {
              key: 'arrears',
              header: 'Arrears',
              num: true,
              render: (r) => <span className="tag-danger">{fmt(r.arrears_amount)}</span>,
            },
            {
              key: 'penalties',
              header: 'Penalties',
              num: true,
              render: (r) => fmt(r.penalties_outstanding),
            },
            {
              key: 'principal',
              header: 'Principal o/s',
              num: true,
              render: (r) => fmt(r.principal_outstanding),
            },
            {
              key: 'total',
              header: 'Total o/s',
              num: true,
              render: (r) => fmt(r.total_outstanding),
            },
          ]}
        />
      )}
    </>
  )
}
