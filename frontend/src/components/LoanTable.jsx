import { useNavigate } from 'react-router-dom'

import { fmt, num, pct } from '../lib/format.js'
import DataTable from './DataTable.jsx'
import { Badge } from './ui.jsx'

const GRADE_CLASS = { A: 'tag-ok', B: 'tag-ok', C: 'tag-warn', D: 'tag-warn', E: 'tag-danger' }

/** The loan listing, shared by the loans page and a borrower's history. */
export default function LoanTable({ loans, empty = 'No loans' }) {
  const navigate = useNavigate()

  const columns = [
    { key: 'loan_no', header: 'Loan no.', render: (r) => r.loan_no },
    { key: 'borrower', header: 'Borrower', render: (r) => r.borrower_name },
    { key: 'product', header: 'Product', render: (r) => r.product_name },
    { key: 'status', header: 'Status', render: (r) => <Badge value={r.status} /> },
    {
      key: 'grade',
      header: 'Grade',
      render: (r) =>
        r.credit_grade ? (
          <span className={GRADE_CLASS[r.credit_grade] || ''} title={`Score ${r.credit_score}/100`}>
            {r.credit_grade}
          </span>
        ) : (
          '-'
        ),
    },
    { key: 'principal', header: 'Principal', num: true, render: (r) => fmt(r.principal) },
    { key: 'rate', header: 'Rate', num: true, render: (r) => pct(r.interest_rate_pct) },
    { key: 'term', header: 'Term', num: true, render: (r) => `${r.term_months}m` },
    { key: 'instalment', header: 'Instalment', num: true, render: (r) => fmt(r.instalment_amount) },
    {
      key: 'outstanding',
      header: 'Outstanding',
      num: true,
      render: (r) => fmt(r.total_outstanding),
    },
    {
      key: 'arrears',
      header: 'Arrears',
      num: true,
      render: (r) =>
        (num(r.arrears_amount) ?? 0) > 0 ? (
          <span className="tag-danger">
            {fmt(r.arrears_amount)} ({r.days_in_arrears}d)
          </span>
        ) : (
          '-'
        ),
    },
    { key: 'disbursed', header: 'Disbursed', render: (r) => r.disbursement_date || '-' },
    { key: 'maturity', header: 'Maturity', render: (r) => r.maturity_date || '-' },
  ]

  return (
    <DataTable
      caption="Loans"
      columns={columns}
      rows={loans}
      onRowClick={(row) => navigate(`/loans/${row.id}`)}
      empty={empty}
    />
  )
}
