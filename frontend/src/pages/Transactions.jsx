import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ExportButtons, Badge, ErrorBanner, Loading, PageHeader } from '../components/ui.jsx'
import { qs } from '../lib/api.js'
import { firstOfMonth, fmt, humanise, money, num, today } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const TYPES = ['disbursement', 'repayment', 'penalty', 'fee', 'waiver', 'write_off', 'reversal']

export default function Transactions() {
  const navigate = useNavigate()
  const { toastError } = useToast()
  const [start, setStart] = useState(firstOfMonth())
  const [end, setEnd] = useState(today())
  const [type, setType] = useState('')

  const path = `/api/reports/transactions${qs({ start, end, txn_type: type })}`
  const { data, error, loading, reload } = useApi(path)

  const rows = data || []
  const total = rows
    .filter((r) => !r.reversed)
    .reduce((sum, r) => sum + (num(r.amount) ?? 0), 0)

  return (
    <>
      <PageHeader
        title="Transactions"
        meta={data ? `${rows.length} postings, ${money(total)} excluding reversed entries` : undefined}
      >
        <label className="check">
          From&nbsp;
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="check">
          To&nbsp;
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Filter by type">
          <option value="">All types</option>
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {humanise(t)}
            </option>
          ))}
        </select>
        <button type="button" className="btn" onClick={reload}>
          Refresh
        </button>
        <ExportButtons path={path} name={'transactions'} />
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading transactions" />
      ) : (
        <DataTable
          caption="Transactions"
          rows={rows}
          onRowClick={(r) => navigate(`/loans/${r.loan_id}`)}
          empty="No postings in this window"
          columns={[
            { key: 'date', header: 'Date', render: (r) => r.date },
            { key: 'loan', header: 'Loan', render: (r) => r.loan_no },
            { key: 'borrower', header: 'Borrower', render: (r) => r.borrower },
            { key: 'type', header: 'Type', render: (r) => <Badge value={r.type} /> },
            { key: 'amount', header: 'Amount', num: true, render: (r) => fmt(r.amount) },
            { key: 'principal', header: 'Principal', num: true, render: (r) => fmt(r.principal) },
            { key: 'interest', header: 'Interest', num: true, render: (r) => fmt(r.interest) },
            { key: 'penalty', header: 'Penalty', num: true, render: (r) => fmt(r.penalty) },
            { key: 'method', header: 'Method', render: (r) => humanise(r.method) || '-' },
            { key: 'ref', header: 'Ref', render: (r) => r.reference || '-' },
            {
              key: 'reversed',
              header: '',
              render: (r) => (r.reversed ? <span className="tag-danger">Reversed</span> : ''),
            },
          ]}
        />
      )}
    </>
  )
}
