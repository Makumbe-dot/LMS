import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ExportButtons, ErrorBanner, Loading, PageHeader } from '../components/ui.jsx'
import { qs } from '../lib/api.js'
import { useOrg } from '../lib/org.jsx'
import { addMonthsIso, firstOfMonth, fmt, money, num } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

/** The deduction schedule to send an employer for a pay period. */
export default function Payroll() {
  const navigate = useNavigate()
  const { toastError } = useToast()
  const { activeBranches } = useOrg()
  const [start, setStart] = useState(firstOfMonth())
  const [end, setEnd] = useState(addMonthsIso(firstOfMonth(), 1))
  const [employer, setEmployer] = useState('')
  const [branchId, setBranchId] = useState('')

  const employers = useApi(`/api/reports/employers${qs({ branch_id: branchId })}`)
  const path = `/api/reports/payroll${qs({ start, end, employer, branch_id: branchId })}`
  const { data, error, loading, reload } = useApi(path)

  const rows = data || []
  const total = rows.reduce((sum, r) => sum + (num(r.deduct) ?? 0), 0)

  return (
    <>
      <PageHeader
        title="Payroll deductions"
        meta={
          data
            ? `${rows.length} deductions totalling ${money(total)} in this pay period`
            : 'Instalments falling due in a pay period, grouped for the employer’s payroll office'
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
        <select
          value={employer}
          onChange={(e) => setEmployer(e.target.value)}
          aria-label="Filter by employer"
        >
          <option value="">All employers</option>
          {(employers.data || []).map((row) => (
            <option key={row.employer} value={row.employer}>
              {row.employer} ({row.active_loans})
            </option>
          ))}
        </select>
        {activeBranches.length > 1 ? (
          <select
            value={branchId}
            onChange={(e) => setBranchId(e.target.value)}
            aria-label="Filter by branch"
          >
            <option value="">All branches</option>
            {activeBranches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        ) : null}
        <button type="button" className="btn" onClick={reload}>
          Refresh
        </button>
        <ExportButtons path={path} name={'payroll_deduction'} />
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />

      {employers.data && employers.data.length > 0 && !employer ? (
        <div className="card">
          <h3>Employers with running loans</h3>
          <DataTable
            caption="Employers"
            rows={employers.data}
            rowKey={(r) => r.employer}
            onRowClick={(r) => setEmployer(r.employer)}
            columns={[
              { key: 'employer', header: 'Employer', render: (r) => r.employer },
              { key: 'loans', header: 'Active loans', num: true, render: (r) => r.active_loans },
              {
                key: 'outstanding',
                header: 'Total outstanding',
                num: true,
                render: (r) => fmt(r.total_outstanding),
              },
            ]}
          />
        </div>
      ) : null}

      {loading && !data ? (
        <Loading what="Building the deduction schedule" />
      ) : (
        <DataTable
          caption="Payroll deduction schedule"
          rows={rows}
          rowKey={(r) => `${r.loan_id}-${r.instalment_no}`}
          onRowClick={(r) => navigate(`/loans/${r.loan_id}`)}
          empty="Nothing falls due for these employers in this window"
          columns={[
            { key: 'employer', header: 'Employer', render: (r) => r.employer },
            { key: 'employee_no', header: 'Employee no.', render: (r) => r.employee_no },
            { key: 'borrower', header: 'Borrower', render: (r) => r.borrower },
            { key: 'national_id', header: 'National ID', render: (r) => r.national_id },
            { key: 'loan', header: 'Loan', render: (r) => r.loan_no },
            { key: 'n', header: '#', num: true, render: (r) => r.instalment_no },
            { key: 'due', header: 'Due', render: (r) => r.due_date },
            { key: 'amount', header: 'Instalment', num: true, render: (r) => fmt(r.amount_due) },
            { key: 'paid', header: 'Already paid', num: true, render: (r) => fmt(r.already_paid) },
            {
              key: 'deduct',
              header: 'Deduct',
              num: true,
              render: (r) => <strong>{fmt(r.deduct)}</strong>,
            },
          ]}
        />
      )}
    </>
  )
}
