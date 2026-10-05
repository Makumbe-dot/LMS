import { useState } from 'react'
import { Link } from 'react-router-dom'

import Donut from '../components/Donut.jsx'
import GroupedBars from '../components/GroupedBars.jsx'
import HBars from '../components/HBars.jsx'
import Icon from '../components/Icons.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Kpi, Loading } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { fmt, getCurrency, humanise, money, pct, today } from '../lib/format.js'
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

const STATUS_TONE = {
  pending: 'amber',
  approved: 'blue',
  active: 'green',
  closed: 'slate',
  rejected: 'red',
  written_off: 'red',
}

function greeting(hour) {
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

/** Colour a rate by whether it is good news: higher is better unless `lowerIsBetter`. */
export function toneFor(value, good, fair, lowerIsBetter = false) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 'slate'
  if (lowerIsBetter) return n <= good ? 'green' : n <= fair ? 'amber' : 'red'
  return n >= good ? 'green' : n >= fair ? 'amber' : 'red'
}

function longDate(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
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
  const statuses = Object.entries(data.status_counts)
    .filter(([, count]) => count > 0)
    .map(([status, count]) => ({
      key: status,
      label: humanise(status),
      value: count,
      tone: STATUS_TONE[status] || 'slate',
    }))
  const totalLoans = statuses.reduce((sum, s) => sum + s.value, 0)
  const parTone = toneFor(data.par_30_pct, 5, 10, true)
  const rateTone = toneFor(data.collection_rate_pct, 90, 75)
  const firstName = (user?.full_name || '').split(' ')[0]

  return (
    <>
      <section className="hero" aria-labelledby="dash-title">
        <div className="hero-text">
          <p className="hero-eyebrow">{longDate(data.as_of)}</p>
          <h2 id="dash-title">
            {greeting(new Date().getHours())}
            {firstName ? `, ${firstName}` : ''}
          </h2>
          <p className="hero-sub">
            {data.active_loans} active loans worth {money(data.portfolio_outstanding)} across{' '}
            {data.borrowers} borrowers
            {data.pending_applications ? (
              <>
                {' · '}
                <Link to="/loans?status=pending">
                  {data.pending_applications} application
                  {data.pending_applications === 1 ? '' : 's'} waiting
                </Link>
              </>
            ) : null}
          </p>
        </div>
        <div className="hero-tools">
          <label className="field inline">
            <span className="label-text">As at</span>
            <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          </label>
          {activeBranches.length > 1 ? (
            <label className="field inline">
              <span className="label-text">Branch</span>
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
            </label>
          ) : null}
          {can('admin', 'loan_officer') ? (
            <div className="hero-actions">
              <button type="button" className="btn" onClick={runPenalties} disabled={running}>
                <Icon name="play" size={15} />
                {running ? 'Running…' : 'Run penalties'}
              </button>
              <Link className="btn" to="/borrowers/new">
                <Icon name="plus" size={15} />
                Borrower
              </Link>
              <Link className="btn primary" to="/loans/new">
                <Icon name="plus" size={15} />
                New loan
              </Link>
            </div>
          ) : null}
        </div>
      </section>

      <div className="stat-grid">
        <Kpi
          icon="wallet"
          tone="blue"
          label="Portfolio outstanding"
          value={money(data.portfolio_outstanding)}
          sub={`Principal ${money(data.principal_outstanding)}`}
        />
        <Kpi
          icon="alert"
          tone={parTone}
          label="PAR > 30 days"
          value={pct(data.par_30_pct)}
          sub={`${money(data.par_30_amount)} at risk`}
          to="/arrears"
        />
        <Kpi
          icon="percent"
          tone={rateTone}
          label="Collection rate"
          value={pct(data.collection_rate_pct)}
          sub={`${money(data.collected_this_month)} of ${money(data.due_this_month)} due`}
          to="/collections"
        />
        <Kpi
          icon="arrowUp"
          tone="violet"
          label="Disbursed this month"
          value={money(data.disbursed_this_month)}
          sub="principal advanced"
        />
        <Kpi
          icon="loans"
          tone="slate"
          label="Active loans"
          value={fmt(data.active_loans).replace('.00', '')}
          sub={`${data.pending_applications} pending application${
            data.pending_applications === 1 ? '' : 's'
          }`}
          to="/loans"
        />
        <Kpi
          icon="users"
          tone="slate"
          label="Borrowers"
          value={fmt(data.borrowers).replace('.00', '')}
          sub="on the register"
          to="/borrowers"
        />
        <Kpi
          icon="arrowDown"
          tone="slate"
          label="Collected this month"
          value={money(data.collected_this_month)}
          sub="repayments received"
        />
        <Kpi
          icon="ban"
          tone="slate"
          label="Written off"
          value={data.status_counts.written_off ?? 0}
          sub={`${data.status_counts.closed ?? 0} settled in full`}
        />
      </div>

      <div className="dash-grid">
        <div className="card chart-card">
          <GroupedBars
            title="Disbursements and collections"
            subtitle={`Last 12 months, ${getCurrency()}. Hover a month for the exact figures.`}
            data={data.monthly_series}
          />
        </div>
        <div className="dash-side">
          <div className="card">
            <div className="card-head">
              <h3>Arrears ageing</h3>
              <Link to="/arrears" className="card-link">
                Arrears report
                <Icon name="chevron" size={14} />
              </Link>
            </div>
            <p className="chart-sub">Principal outstanding by days overdue.</p>
            <HBars rows={buckets} />
          </div>
          <div className="card">
            <div className="card-head">
              <h3>Loans by status</h3>
              <Link to="/loans" className="card-link">
                All loans
                <Icon name="chevron" size={14} />
              </Link>
            </div>
            <Donut
              slices={statuses}
              total={totalLoans}
              centreLabel="loans"
              aria-label="Loans by status"
            />
            <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
              The whole book, not only active loans.
            </p>
          </div>
        </div>
      </div>
    </>
  )
}
