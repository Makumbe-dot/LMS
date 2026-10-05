import { useState } from 'react'
import { Link } from 'react-router-dom'

import GroupedBars from '../components/GroupedBars.jsx'
import HBars from '../components/HBars.jsx'
import { useToast } from '../components/Toast.jsx'
import Icon from '../components/Icons.jsx'
import { Badge, ErrorBanner, Kpi, Loading } from '../components/ui.jsx'
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

function greeting(hour) {
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

/** Colour a rate by whether it is good news: higher is better unless `lowerIsBetter`. */
function toneFor(value, good, fair, lowerIsBetter = false) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 'slate'
  if (lowerIsBetter) return n <= good ? 'green' : n <= fair ? 'amber' : 'red'
  return n >= good ? 'green' : n >= fair ? 'amber' : 'red'
}

export default function Dashboard() {
  const { can, user } = useAuth()
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
      <section className="hero" aria-labelledby="dash-title">
        <div>
          <div className="hero-eyebrow">
            {new Date(`${data.as_of}T00:00:00`).toLocaleDateString(undefined, {
              weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
            })}
          </div>
          <h2 id="dash-title">
            {greeting(new Date().getHours())}
            {user?.full_name ? `, ${user.full_name.split(' ')[0]}` : ''}
          </h2>
          <p>
            {data.active_loans} active loans worth {money(data.portfolio_outstanding)}.{' '}
            {pct(data.collection_rate_pct)} of what fell due this month has been collected.
          </p>
        </div>
        {can('admin', 'loan_officer') ? (
          <div className="hero-actions">
            <Link className="btn primary" to="/loans/new">
              <Icon name="plus" size={16} /> New loan
            </Link>
            <Link className="btn" to="/borrowers/new">
              <Icon name="user" size={16} /> New borrower
            </Link>
            <button type="button" className="btn" onClick={runPenalties} disabled={running}>
              <Icon name="play" size={14} /> {running ? 'Running…' : 'Run penalties'}
            </button>
          </div>
        ) : null}
      </section>

      <div className="toolbar">
        <label className="check" style={{ margin: 0 }}>
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
      </div>

      <div className="grid cols-4">
        <Kpi
          label="Portfolio outstanding"
          icon="loans"
          value={money(data.portfolio_outstanding)}
          sub={`Principal ${money(data.principal_outstanding)}`}
        />
        <Kpi
          label="Active loans"
          icon="list"
          tone="violet"
          value={data.active_loans}
          sub={`${data.pending_applications} pending applications`}
        />
        <Kpi
          label="PAR > 30 days"
          icon="alert"
          tone={toneFor(data.par_30_pct, 5, 10, true)}
          value={pct(data.par_30_pct)}
          sub={money(data.par_30_amount)}
        />
        <Kpi label="Borrowers" icon="users" tone="slate" value={data.borrowers} sub="on the register" />
        <Kpi
          label="Disbursed this month"
          icon="arrowUp"
          value={money(data.disbursed_this_month)}
          sub="principal advanced"
        />
        <Kpi
          label="Collected this month"
          icon="arrowDown"
          tone="green"
          value={money(data.collected_this_month)}
          sub={`Due ${money(data.due_this_month)}`}
        />
        <Kpi
          label="Collection rate"
          icon="chart"
          tone={toneFor(data.collection_rate_pct, 90, 70)}
          value={pct(data.collection_rate_pct)}
          sub="collected / due this month"
        />
        <Kpi
          label="Written off"
          icon="history"
          tone="slate"
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
