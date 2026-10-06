import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { Swoosh } from '../brand/Brand.jsx'
import CountUp from '../components/CountUp.jsx'
import Donut from '../components/Donut.jsx'
import GroupedBars from '../components/GroupedBars.jsx'
import HBars from '../components/HBars.jsx'
import Icon from '../components/Icons.jsx'
import { useToast } from '../components/Toast.jsx'
import TrendLine from '../components/TrendLine.jsx'
import { ErrorBanner, Kpi } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { getCurrency, humanise, money, num, pct, today } from '../lib/format.js'
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

// The collection rate a lender would call healthy; the tile turns amber below it.
const RATE_TARGET = 90
// How often today's dashboard asks for fresh figures while its tab is showing.
const REFRESH_MS = 60_000

const clock = (date) =>
  date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })

const ACTIVITY = {
  disbursement: { icon: 'arrowUp', tone: 'ink' },
  repayment: { icon: 'arrowDown', tone: 'green' },
  recovery: { icon: 'coins', tone: 'green' },
  waiver: { icon: 'tag', tone: 'amber' },
  fee: { icon: 'percent', tone: 'blue' },
  charge: { icon: 'percent', tone: 'blue' },
  write_off: { icon: 'ban', tone: 'red' },
  reversal: { icon: 'history', tone: 'slate' },
}

const sentence = (s) => s.charAt(0).toUpperCase() + s.slice(1)

const count = (n) => Number(n ?? 0).toLocaleString('en-GB')

/** Percentage change for a stat tile, with the direction it moved. */
export function change(current, previous) {
  const c = num(current) ?? 0
  const p = num(previous) ?? 0
  if (p === 0 && c === 0) return { dir: 'flat', text: 'No change' }
  if (p === 0) return { dir: 'up', text: 'Up from nil', good: true }
  const d = ((c - p) / Math.abs(p)) * 100
  const dir = Math.abs(d) < 0.5 ? 'flat' : d > 0 ? 'up' : 'down'
  const size = Math.abs(d)
  return {
    dir,
    text: dir === 'flat' ? 'Level' : `${size.toFixed(size < 10 ? 1 : 0)}%`,
    good: dir === 'up',
  }
}

function share(part, whole) {
  const w = num(whole) ?? 0
  return w > 0 ? `${(((num(part) ?? 0) / w) * 100).toFixed(0)}%` : '-'
}

function sparkLabel(what, months, key, format) {
  if (months.length < 2) return what
  const first = months[0]
  const last = months[months.length - 1]
  return `${what}: ${format(first[key])} in ${first.month}, ${format(last[key])} in ${last.month}`
}

function shortDate(iso) {
  return new Date(`${String(iso).slice(0, 10)}T00:00:00`).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
  })
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
  const path = `/api/reports/dashboard${qs({ as_of: asOf, branch_id: branchId })}`
  const { data, error, loading, reload, loadedPath } = useApi(path)
  const [running, setRunning] = useState(false)
  const [updatedAt, setUpdatedAt] = useState(null)

  // Today's dashboard keeps itself current; a past date is a snapshot and stays put.
  const live = asOf === today()
  useEffect(() => {
    if (!live) return undefined
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') reload()
    }, REFRESH_MS)
    return () => clearInterval(timer)
  }, [live, reload])
  useEffect(() => {
    if (data) setUpdatedAt(new Date())
  }, [data])

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

  // What is on screen stays there while fresher figures load: only the very first
  // load shows placeholders, and a change of date or branch dims the old figures
  // rather than removing the controls the reader is in the middle of using.
  if (!data) {
    if (error) return <ErrorBanner error={error} onRetry={reload} />
    return loading ? <DashSkeleton /> : null
  }
  const switching = loading && loadedPath !== path

  // The ageing buckets, coloured by severity: "current" is neutral, then one hue
  // deepening with the days overdue. The count of loans sits under each label.
  const buckets = Object.entries(data.arrears_buckets).map(([key, value], index) => {
    const loans = data.arrears_bucket_loans?.[key] ?? 0
    return {
      label: BUCKET_LABEL[key] || key,
      value,
      color: `var(--sev-${Math.min(index, 5)})`,
      note: `${loans} loan${loans === 1 ? '' : 's'}`,
      share: share(value, data.principal_outstanding),
    }
  })
  const over30 = Object.entries(data.arrears_buckets)
    .filter(([key]) => !['current', '1-30'].includes(key))
    .reduce((sum, [, value]) => sum + (num(value) ?? 0), 0)
  const products = data.product_mix.map((p, index) => ({
    label: p.product,
    note: `${p.loans} loan${p.loans === 1 ? '' : 's'}`,
    value: p.principal,
    share: share(p.principal, data.principal_outstanding),
    color: index < 4 ? `var(--cat-${index + 1})` : 'var(--faint)',
  }))
  const topProduct = data.product_mix[0]
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
  const rateTone = toneFor(data.collection_rate_pct, RATE_TARGET, 75)
  const firstName = (user?.full_name || '').split(' ')[0]
  const prev = data.previous_month
  const soon = data.due_next_7_days
  // Completed months only: the month in progress would end every line in a dip.
  const settled = data.monthly_series.slice(0, -1)
  const asOfDate = new Date(`${data.as_of}T00:00:00`)
  const asOfDay = asOfDate.getDate()
  const prevMonth = new Date(asOfDate.getFullYear(), asOfDate.getMonth() - 1, 1).toLocaleDateString(
    'en-GB',
    { month: 'short' },
  )

  return (
    <>
      {error ? <ErrorBanner error={error} onRetry={reload} /> : null}
      <section className="hero-banner" aria-labelledby="dash-title">
        <span className="hero-swoosh swoosh-a" aria-hidden="true">
          <Swoosh width={260} />
        </span>
        <span className="hero-swoosh swoosh-b" aria-hidden="true">
          <Swoosh width={120} />
        </span>
        <div className="hero-text">
          <p className="hero-eyebrow">
            <span>{longDate(data.as_of)}</span>
            {live ? (
              <span className="live-chip" title="These figures refresh themselves every minute">
                <span className="live-dot" aria-hidden="true" />
                Live{updatedAt ? ` · updated ${clock(updatedAt)}` : ''}
              </span>
            ) : (
              <span className="live-chip past" title="A date other than today does not refresh">
                Snapshot
              </span>
            )}
          </p>
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
          {can('supervise', 'borrowers', 'loans') ? (
            <div className="hero-actions">
              {can('supervise') ? (
                <button type="button" className="btn" onClick={runPenalties} disabled={running}>
                  <Icon name="play" size={15} />
                  {running ? 'Running…' : 'Run penalties'}
                </button>
              ) : null}
              {can('borrowers') ? (
                <Link className="btn" to="/borrowers/new">
                  <Icon name="plus" size={15} />
                  Borrower
                </Link>
              ) : null}
              {can('loans') ? (
                <Link className="btn primary" to="/loans/new">
                  <Icon name="plus" size={15} />
                  New loan
                </Link>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>

      <div className={switching ? 'dash-body switching' : 'dash-body'} aria-busy={switching}>
      <div className="stat-grid">
        <Kpi
          icon="wallet"
          tone="brand"
          label="Portfolio outstanding"
          value={<CountUp value={data.portfolio_outstanding} format={money} />}
          sub={`Principal ${money(data.principal_outstanding)}`}
          foot={`${count(data.active_loans)} active loan${data.active_loans === 1 ? '' : 's'}`}
        />
        <Kpi
          icon="alert"
          tone={parTone}
          label="PAR > 30 days"
          value={<CountUp value={data.par_30_pct} format={pct} />}
          sub={`${money(data.par_30_amount)} at risk`}
          foot={`${count(data.par_30_loans)} loan${data.par_30_loans === 1 ? '' : 's'} · target under 5%`}
          to="/arrears"
        />
        <Kpi
          icon="percent"
          tone={rateTone}
          label="Collection rate"
          value={<CountUp value={data.collection_rate_pct} format={pct} />}
          sub={`${money(data.collected_this_month)} of ${money(data.due_this_month)} due`}
          foot={`${prevMonth} closed at ${pct(prev.collection_rate_pct)}`}
          trend={settled.map((m) => m.collection_rate_pct)}
          trendLabel={sparkLabel('Collection rate', settled, 'collection_rate_pct', pct)}
          to="/collections"
        />
        <Kpi
          icon="history"
          tone={Number(data.arrears_total) > 0 ? 'amber' : 'green'}
          label="Overdue now"
          value={<CountUp value={data.arrears_total} format={money} />}
          sub={`${count(data.loans_in_arrears)} loan${data.loans_in_arrears === 1 ? '' : 's'} behind on payments`}
          foot="instalments past their due date"
          to="/arrears"
        />
        <Kpi
          icon="arrowUp"
          tone="ink"
          label="Disbursed this month"
          value={<CountUp value={data.disbursed_this_month} format={money} />}
          sub="principal advanced"
          delta={{
            ...change(data.disbursed_this_month, prev.disbursed_to_date),
            good: null,
            vs: `vs ${prevMonth} 1–${asOfDay}`,
          }}
          trend={settled.map((m) => m.disbursed)}
          trendLabel={sparkLabel('Disbursed', settled, 'disbursed', money)}
        />
        <Kpi
          icon="arrowDown"
          tone="green"
          label="Collected this month"
          value={<CountUp value={data.collected_this_month} format={money} />}
          sub="repayments received"
          delta={{
            ...change(data.collected_this_month, prev.collected_to_date),
            vs: `vs ${prevMonth} 1–${asOfDay}`,
          }}
          trend={settled.map((m) => m.collected)}
          trendLabel={sparkLabel('Collected', settled, 'collected', money)}
        />
        <Kpi
          icon="calendar"
          tone="slate"
          label="Due in the next 7 days"
          value={<CountUp value={soon.amount} format={money} />}
          sub={
            soon.instalments
              ? `${count(soon.instalments)} instalment${soon.instalments === 1 ? '' : 's'} on ${count(soon.loans)} loan${soon.loans === 1 ? '' : 's'}`
              : 'nothing falls due this week'
          }
          foot="still unpaid"
          to="/collections"
        />
        <Kpi
          icon="users"
          tone="slate"
          label="Borrowers"
          value={<CountUp value={data.borrowers} format={(n) => count(Math.round(n))} />}
          sub={`${data.pending_applications} pending application${
            data.pending_applications === 1 ? '' : 's'
          }`}
          foot={`${count(data.status_counts.written_off ?? 0)} written off · ${count(
            data.status_counts.closed ?? 0,
          )} settled in full`}
          to="/borrowers"
        />
      </div>

      <div className="dash-grid">
        <div className="dash-main">
          <div className="card chart-card">
            <GroupedBars
              title="Disbursements and collections"
              subtitle={`Last 12 months, ${getCurrency()}. Hover a month for the exact figures.`}
              data={data.monthly_series}
            />
          </div>
          <div className="card chart-card">
            <TrendLine
              title="Collection rate"
              subtitle="Repayments received as a share of principal and interest due, per month. The current month is drawn hollow: it is still being collected."
              data={data.monthly_series}
              target={RATE_TARGET}
              partialLast
            />
          </div>
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
            <p className={`card-reading${over30 > 0 ? ' warn' : ' good'}`}>
              {over30 > 0 ? (
                <>
                  <strong>{money(over30)}</strong> ({share(over30, data.principal_outstanding)} of
                  principal) is more than 30 days overdue.
                </>
              ) : (
                <>Nothing is more than 30 days overdue.</>
              )}
            </p>
            <HBars rows={buckets} overview />
          </div>
          <div className="card">
            <div className="card-head">
              <h3>Loans by status</h3>
              <Link to="/loans" className="card-link">
                All loans
                <Icon name="chevron" size={14} />
              </Link>
            </div>
            <p className="chart-sub">The whole book, not only active loans.</p>
            <Donut
              slices={statuses}
              total={totalLoans}
              centreLabel="loans"
              aria-label="Loans by status"
            />
            <p className="card-reading">
              <strong>{count(data.active_loans)}</strong> running ·{' '}
              <strong>{count(data.pending_applications)}</strong> awaiting a decision ·{' '}
              <strong>{count(data.status_counts.closed ?? 0)}</strong> settled in full
            </p>
          </div>
          <div className="card">
            <div className="card-head">
              <h3>Portfolio by product</h3>
              <Link to="/products" className="card-link">
                Products
                <Icon name="chevron" size={14} />
              </Link>
            </div>
            <p className="chart-sub">Principal outstanding on active loans, {getCurrency()}.</p>
            {products.length ? (
              <>
                <p className="card-reading">
                  <strong>{topProduct.product}</strong> is {share(topProduct.principal, data.principal_outstanding)}{' '}
                  of the book across {count(products.length)} product{products.length === 1 ? '' : 's'}.
                </p>
                <HBars wide rows={products} overview />
              </>
            ) : (
              <p className="dash-empty">No active loans yet.</p>
            )}
          </div>
        </div>
      </div>

      <div className="dash-row">
        <div className="card">
          <div className="card-head">
            <h3>Most overdue</h3>
            <Link to="/arrears" className="card-link">
              Portfolio at risk
              <Icon name="chevron" size={14} />
            </Link>
          </div>
          <p className="chart-sub">Active loans longest in arrears.</p>
          {data.watchlist.length ? (
            <ul className="dash-list">
              {data.watchlist.map((row) => (
                <li key={row.loan_id}>
                  <Link to={`/loans/${row.loan_id}`} className="dash-list-main">
                    <span className="dash-list-title">{row.borrower}</span>
                    <span className="dash-list-sub">{row.loan_no}</span>
                  </Link>
                  <span className="dash-list-end">
                    <span className="dash-list-amount">
                      {money(row.arrears_amount, row.currency || undefined)}
                    </span>
                    <span className={`days-chip ${row.days_in_arrears > 90 ? 'bad' : 'warn'}`}>
                      {row.days_in_arrears} days
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="dash-empty">
              <Icon name="check" size={16} /> No active loan is in arrears.
            </p>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Recent activity</h3>
            <Link to="/transactions" className="card-link">
              Transactions
              <Icon name="chevron" size={14} />
            </Link>
          </div>
          <p className="chart-sub">The latest money movements on the book.</p>
          {data.recent_activity.length ? (
            <ul className="dash-list activity">
              {data.recent_activity.map((t) => {
                const kind = ACTIVITY[t.type] || { icon: 'list', tone: 'slate' }
                return (
                  <li key={t.id} className={t.reversed ? 'reversed' : ''}>
                    <span className={`activity-icon tone-${kind.tone}`} aria-hidden="true">
                      <Icon name={kind.icon} size={15} />
                    </span>
                    <Link to={`/loans/${t.loan_id}`} className="dash-list-main">
                      <span className="dash-list-title">
                        {sentence(humanise(t.type))} · {t.borrower}
                      </span>
                      <span className="dash-list-sub">
                        {t.loan_no} · {shortDate(t.date)}
                        {t.reversed ? ' · reversed' : ''}
                      </span>
                    </Link>
                    <span className="dash-list-amount">{money(t.amount, t.currency || undefined)}</span>
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="dash-empty">Nothing posted yet.</p>
          )}
        </div>
      </div>
      </div>
    </>
  )
}

/** The shape of the page while its first figures load. */
function DashSkeleton() {
  return (
    <div role="status" aria-label="Loading the dashboard">
      <div className="skel skel-hero" />
      <div className="stat-grid">
        {Array.from({ length: 8 }, (_, i) => (
          <div className="skel skel-tile" key={i} />
        ))}
      </div>
      <div className="dash-grid">
        <div className="skel skel-chart" />
        <div className="skel skel-chart" />
      </div>
    </div>
  )
}
