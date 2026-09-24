import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { Badge, ErrorBanner, Loading, PageHeader } from '../components/ui.jsx'
import { downloadCsv, qs } from '../lib/api.js'
import { addMonthsIso, firstOfMonth, fmt, money, num } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

export default function Collections() {
  const navigate = useNavigate()
  const { toastError } = useToast()
  const [start, setStart] = useState(firstOfMonth())
  const [end, setEnd] = useState(addMonthsIso(firstOfMonth(), 1))

  const path = `/api/reports/collections-due${qs({ start, end })}`
  const { data, error, loading, reload } = useApi(path)

  const rows = data || []
  const totalDue = rows.reduce((sum, r) => sum + (num(r.balance) ?? 0), 0)

  return (
    <>
      <PageHeader
        title="Collections due"
        meta={
          data
            ? `${rows.length} instalments fall due in this window, ${money(totalDue)} still outstanding`
            : undefined
        }
      >
        <label className="check">
          From&nbsp;
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="check">
          To&nbsp;
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <button type="button" className="btn" onClick={reload}>
          Refresh
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => downloadCsv(path, 'collections_due').catch(toastError)}
        >
          Export CSV
        </button>
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading collections" />
      ) : (
        <DataTable
          caption="Collections due"
          rows={rows}
          rowKey={(r) => `${r.loan_id}-${r.instalment_no}`}
          onRowClick={(r) => navigate(`/loans/${r.loan_id}`)}
          empty="Nothing falls due in this window"
          columns={[
            { key: 'due', header: 'Due', render: (r) => r.due_date },
            { key: 'loan', header: 'Loan', render: (r) => r.loan_no },
            { key: 'borrower', header: 'Borrower', render: (r) => r.borrower },
            { key: 'employer', header: 'Employer', render: (r) => r.employer || '-' },
            { key: 'n', header: '#', num: true, render: (r) => r.instalment_no },
            { key: 'due_amt', header: 'Amount due', num: true, render: (r) => fmt(r.amount_due) },
            { key: 'paid', header: 'Paid', num: true, render: (r) => fmt(r.paid) },
            {
              key: 'balance',
              header: 'Balance',
              num: true,
              render: (r) =>
                (num(r.balance) ?? 0) > 0 ? fmt(r.balance) : <span className="tag-ok">0.00</span>,
            },
            { key: 'status', header: 'Status', render: (r) => <Badge value={r.status} /> },
          ]}
        />
      )}
    </>
  )
}
