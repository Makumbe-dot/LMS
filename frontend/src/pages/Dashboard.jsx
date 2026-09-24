import { useState } from 'react'
import { Link } from 'react-router-dom'

import GroupedBars from '../components/GroupedBars.jsx'
import HBars from '../components/HBars.jsx'
import { useToast } from '../components/Toast.jsx'
import { Badge, ErrorBanner, Kpi, Loading, PageHeader } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { money, pct, today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

const BUCKET_LABEL = {
  current: 'Current',
  '1-30': '1-30 days',
  '31-60': '31-60 days',
  '61-90': '61-90 days',
  '91-180': '91-180 days',
  '180+': '180+ days',
}

export default function Dashboard() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const { activeBranches } = useOrg()
  const [asOf, setAsOf] = useState(today())
  const [branchId, setBranchId] = useState('')
  const { data, error, loading, reload } = useApi(
    `/api/reports/dashboard${qs({ as_of: asOf, branch_id: branchId })}`,
  )
  const [running, setRunning] = useState(false)

  async function runPenalties() {
    setRunning(true)
    try {
      const result = await post('/api/reports/run-penalties')
      toast(
        `Penalties accrued on ${result.loans_penalised} loans: ${money(result.total_penalties)}`,
      )
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setRunning(false)
    }
  }

  if (loading) return <Loading what="Loading the dashboard" />
  if (error) return <ErrorBanner error={error} onRetry={reload} />
  if (!data) return null

  const buckets = Object.entries(data.arrears_buckets).map(([key, value]) => ({
    label: BUCKET_LABEL[key] || key,
    value,
  }))

  return (
    <>
      <PageHeader title="Dashboard" meta={`Portfolio as at ${data.as_of}`}>
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
        {can('admin', 'loan_officer') ? (
          <>
            <Link className="btn" to="/borrowers/new">
              New borrower
            </Link>
            <Link className="btn primary" to="/loans/new">
              New loan
            </Link>
            <button type="button" className="btn" onClick={runPenalties} disabled={running}>
              {running ? 'Running…' : 'Run penalties'}
            </button>
          </>
        ) : null}
      </PageHeader>

      <div className="grid cols-4">
        <Kpi
          label="Portfolio outstanding"
          value={money(data.portfolio_outstanding)}
          sub={`Principal ${money(data.principal_outstanding)}`}
        />
        <Kpi
          label="Active loans"
          value={data.active_loans}
          sub={`${data.pending_applications} pending applications`}
        />
        <Kpi
          label="PAR > 30 days"
          value={pct(data.par_30_pct)}
          sub={money(data.par_30_amount)}
        />
        <Kpi label="Borrowers" value={data.borrowers} sub="on the register" />
        <Kpi
          label="Disbursed this month"
          value={money(data.disbursed_this_month)}
          sub="principal advanced"
        />
        <Kpi
          label="Collected this month"
          value={money(data.collected_this_month)}
          sub={`Due ${money(data.due_this_month)}`}
        />
        <Kpi
          label="Collection rate"
          value={pct(data.collection_rate_pct)}
          sub="collected / due this month"
        />
        <Kpi
          label="Written off"
          value={data.status_counts.written_off ?? 0}
          sub={`${data.status_counts.closed ?? 0} settled in full`}
        />
      </div>

      <div className="grid cols-3" style={{ marginTop: 16, alignItems: 'start' }}>
        <div className="card span-2">
          <GroupedBars
            title="Disbursements and collections"
            subtitle="Last 12 months, USD. Hover a month for the exact figures."
            data={data.monthly_series}
          />
        </div>
        <div>
          <div className="card">
            <h3>Principal by arrears bucket</h3>
            <HBars rows={buckets} />
          </div>
          <div className="card">
            <h3>Loans by status</h3>
            <div className="status-list">
              {Object.entries(data.status_counts).map(([status, count]) => (
                <div className="status-row" key={status}>
                  <Badge value={status} />
                  <b>{count}</b>
                </div>
              ))}
            </div>
            <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
              Status counts cover the whole book, not only active loans.
            </p>
          </div>
        </div>
      </div>
    </>
  )
}
