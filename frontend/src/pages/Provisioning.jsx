import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Kpi, Loading, PageHeader, StageTag } from '../components/ui.jsx'
import { downloadCsv, qs } from '../lib/api.js'
import { fmt, money, pct, today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

/** IFRS 9 staging and expected credit loss. */
export default function Provisioning() {
  const navigate = useNavigate()
  const { toastError } = useToast()
  const { activeBranches } = useOrg()
  const [asOf, setAsOf] = useState(today())
  const [branchId, setBranchId] = useState('')

  const path = `/api/reports/ecl${qs({ as_of: asOf, branch_id: branchId })}`
  const { data, error, loading, reload } = useApi(path)

  return (
    <>
      <PageHeader
        title="Impairment and provisioning"
        meta="IFRS 9 staging on days past due, with expected credit loss at the rates held in settings"
      >
        <label className="check">
          As at&nbsp;
          <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
        </label>
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
        <button
          type="button"
          className="btn"
          onClick={() => downloadCsv(path, 'ecl_provision').catch(toastError)}
        >
          Export CSV
        </button>
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? <Loading what="Calculating provisions" /> : null}

      {data ? (
        <>
          <div className="grid cols-3">
            <Kpi label="Gross exposure" value={money(data.total_exposure)} sub="active book" />
            <Kpi
              label="Expected credit loss"
              value={money(data.total_provision)}
              sub={`${pct(data.coverage_pct)} coverage`}
            />
            <Kpi
              label="Net exposure"
              value={money(Number(data.total_exposure) - Number(data.total_provision))}
              sub="after provision"
            />
          </div>

          <div className="card" style={{ marginTop: 16 }}>
            <h3>By stage</h3>
            <DataTable
              caption="Provision by stage"
              rows={data.summary}
              rowKey={(r) => r.stage}
              columns={[
                {
                  key: 'stage',
                  header: 'Stage',
                  render: (r) => <StageTag stage={r.stage} label={r.label} />,
                },
                { key: 'loans', header: 'Loans', num: true, render: (r) => r.loans },
                { key: 'exposure', header: 'Exposure', num: true, render: (r) => fmt(r.exposure) },
                { key: 'rate', header: 'Rate', num: true, render: (r) => pct(r.rate_pct) },
                {
                  key: 'provision',
                  header: 'Provision',
                  num: true,
                  render: (r) => <strong>{fmt(r.provision)}</strong>,
                },
              ]}
            />
            <p className="muted" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
              Stage 2 begins after {data.rates.stage_2_days} days past due and stage 3 after{' '}
              {data.rates.stage_3_days}. Rates are {pct(data.rates.stage_1)} /{' '}
              {pct(data.rates.stage_2)} / {pct(data.rates.stage_3)} and are editable under
              Settings.
            </p>
          </div>

          <DataTable
            caption="Provision by loan"
            rows={data.rows}
            rowKey={(r) => r.loan_id}
            onRowClick={(r) => navigate(`/loans/${r.loan_id}`)}
            empty="No active loans to provision"
            columns={[
              { key: 'loan', header: 'Loan', render: (r) => r.loan_no },
              { key: 'borrower', header: 'Borrower', render: (r) => r.borrower },
              { key: 'product', header: 'Product', render: (r) => r.product },
              { key: 'branch', header: 'Branch', render: (r) => r.branch },
              { key: 'dpd', header: 'Days past due', num: true, render: (r) => r.days_past_due },
              {
                key: 'stage',
                header: 'Stage',
                render: (r) => <StageTag stage={r.stage} label={r.stage_label} />,
              },
              { key: 'exposure', header: 'Exposure', num: true, render: (r) => fmt(r.exposure) },
              {
                key: 'rate',
                header: 'Rate',
                num: true,
                render: (r) => pct(r.provision_rate_pct),
              },
              { key: 'provision', header: 'Provision', num: true, render: (r) => fmt(r.provision) },
              {
                key: 'net',
                header: 'Net exposure',
                num: true,
                render: (r) => fmt(r.net_exposure),
              },
            ]}
          />
        </>
      ) : null}
    </>
  )
}
