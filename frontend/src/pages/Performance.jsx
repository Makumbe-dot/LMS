import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Loading, PageHeader } from '../components/ui.jsx'
import { downloadCsv, qs } from '../lib/api.js'
import { fmt, pct, rateMethodLabel } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

const TABS = [
  { key: 'officer', label: 'By officer', path: 'officer-performance', noun: 'Officer' },
  { key: 'product', label: 'By product', path: 'product-performance', noun: 'Product' },
  { key: 'branch', label: 'By branch', path: 'branch-performance', noun: 'Branch' },
]

const SHARED = [
  { key: 'loans', header: 'Active loans', num: true, render: (r) => r.active_loans },
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
  { key: 'arrears_n', header: 'Loans in arrears', num: true, render: (r) => r.loans_in_arrears },
  {
    key: 'arrears',
    header: 'Arrears',
    num: true,
    render: (r) =>
      Number(r.arrears_amount) > 0 ? (
        <span className="tag-danger">{fmt(r.arrears_amount)}</span>
      ) : (
        '-'
      ),
  },
  {
    key: 'par',
    header: 'PAR > 30',
    num: true,
    render: (r) => (
      <span className={Number(r.par_30_pct) > 10 ? 'tag-danger' : undefined}>
        {pct(r.par_30_pct)}
      </span>
    ),
  },
]

export default function Performance() {
  const { toastError } = useToast()
  const { activeBranches } = useOrg()
  const [tab, setTab] = useState('officer')
  const [branchId, setBranchId] = useState('')

  const active = TABS.find((t) => t.key === tab)
  const path = `/api/reports/${active.path}${qs({
    branch_id: tab === 'branch' ? '' : branchId,
  })}`
  const { data, error, loading, reload } = useApi(path)

  const columns = [
    { key: 'name', header: active.noun, render: (r) => r.name },
    ...(tab === 'officer'
      ? [
          {
            key: 'originated',
            header: 'Originated',
            num: true,
            render: (r) => r.loans_originated,
          },
          {
            key: 'disbursed',
            header: 'Total disbursed',
            num: true,
            render: (r) => fmt(r.total_disbursed),
          },
        ]
      : []),
    ...(tab === 'product'
      ? [
          { key: 'rate', header: 'Rate/month', num: true, render: (r) => pct(r.rate_pct) },
          { key: 'method', header: 'Method', render: (r) => rateMethodLabel(r.rate_method) },
        ]
      : []),
    ...SHARED,
  ]

  return (
    <>
      <PageHeader
        title="Performance"
        meta="Where the book sits and where it is slipping, by the people and products behind it"
      >
        {tab !== 'branch' && activeBranches.length > 1 ? (
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
        <button
          type="button"
          className="btn"
          onClick={() => downloadCsv(path, active.path).catch(toastError)}
        >
          Export CSV
        </button>
      </PageHeader>

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading performance" />
      ) : (
        <DataTable
          caption={`Performance ${active.label}`}
          rows={data || []}
          rowKey={(r) => r.key ?? r.name}
          empty="No active loans to report on"
          columns={columns}
        />
      )}
    </>
  )
}
