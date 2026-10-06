import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ExportButtons, ErrorBanner, Loading, PageHeader } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateOnly, firstOfMonth, fmt, today } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const PROMISE_TONE = { kept: 'tag-ok', pending: 'tag-warn', broken: 'tag-danger' }

/**
 * The arrears work queue. A collector sees their own loans, follow-ups due today
 * first; whoever holds the supervise right hands loans out. The results tab says
 * what came in on each collector's loans and how many promises were kept.
 */
export default function CollectionsWork() {
  const { user, can } = useAuth()
  const isCollector = can('collections') && user?.role !== 'admin'
  const [tab, setTab] = useState(isCollector ? 'me' : 'all')

  return (
    <>
      <PageHeader
        title="Collections work"
        meta="Overdue loans, who is working each one, and the promises made."
      />
      <div className="tabs" role="tablist" aria-label="Collections views">
        {[
          ...(can('collections') ? [['me', 'My queue']] : []),
          ['none', 'Not assigned'],
          ['all', 'All overdue'],
          ['results', 'Results'],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'results' ? <Results /> : <Queue who={tab === 'all' ? '' : tab} />}
    </>
  )
}

function Queue({ who }) {
  const { can } = useAuth()
  const navigate = useNavigate()
  const { toast, toastError } = useToast()
  const mayAssign = can('supervise')
  const path = `/api/collections/queue${qs({ collector: who })}`
  const { data, error, loading, reload } = useApi(path)
  const collectors = useApi(mayAssign ? '/api/collections/collectors' : null)
  const [selected, setSelected] = useState([])
  const [busy, setBusy] = useState(false)
  const rows = data || []

  async function assign(collectorId) {
    setBusy(true)
    try {
      const result = await post('/api/collections/assign', {
        loan_ids: selected,
        collector_id: collectorId === '' ? null : Number(collectorId),
      })
      toast(`${result.changed} loan(s) reassigned`)
      setSelected([])
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  const toggle = (id, on) => setSelected((s) => (on ? [...s, id] : s.filter((x) => x !== id)))

  return (
    <>
      <div className="row" style={{ margin: '12px 0', gap: 8, flexWrap: 'wrap' }}>
        <span className="muted">
          {rows.length} loan{rows.length === 1 ? '' : 's'} in arrears
          {rows.some((r) => r.action_due)
            ? `, ${rows.filter((r) => r.action_due).length} with a follow-up due`
            : ''}
        </span>
        <span style={{ flex: 1 }} />
        {mayAssign && selected.length ? (
          <label className="check">
            Give {selected.length} to&nbsp;
            <select
              defaultValue="-"
              disabled={busy}
              aria-label="Assign selected loans to"
              onChange={(e) => e.target.value !== '-' && assign(e.target.value)}
            >
              <option value="-" disabled>
                choose…
              </option>
              <option value="">nobody (unassign)</option>
              {(collectors.data || []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.full_name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <ExportButtons path={path} name="collections_queue" small />
      </div>
      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Building the queue" />
      ) : (
        <DataTable
          caption="Collections queue"
          rows={rows}
          rowKey={(r) => r.loan_id}
          onRowClick={(r) => navigate(`/loans/${r.loan_id}?tab=notes`)}
          empty="Nothing overdue here."
          columns={[
            ...(mayAssign
              ? [
                  {
                    key: 'select',
                    header: (
                      <input
                        type="checkbox"
                        aria-label="Select all"
                        checked={rows.length > 0 && selected.length === rows.length}
                        onChange={(e) =>
                          setSelected(e.target.checked ? rows.map((r) => r.loan_id) : [])
                        }
                      />
                    ),
                    render: (r) => (
                      <input
                        type="checkbox"
                        aria-label={`Select ${r.loan_no}`}
                        checked={selected.includes(r.loan_id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => toggle(r.loan_id, e.target.checked)}
                      />
                    ),
                  },
                ]
              : []),
            {
              key: 'loan',
              header: 'Loan',
              render: (r) => (
                <>
                  <Link to={`/loans/${r.loan_id}`} onClick={(e) => e.stopPropagation()}>
                    {r.loan_no}
                  </Link>
                  <div>
                    <small className="muted">
                      {r.borrower} · {r.phone}
                    </small>
                  </div>
                </>
              ),
            },
            { key: 'days', header: 'Days', num: true, render: (r) => r.days_in_arrears },
            {
              key: 'arrears',
              header: 'Overdue',
              num: true,
              render: (r) => fmt(r.arrears_amount),
            },
            {
              key: 'next',
              header: 'Next follow-up',
              render: (r) =>
                r.next_action ? (
                  <span className={r.action_due ? 'tag-warn' : undefined}>
                    {dateOnly(r.next_action)}
                  </span>
                ) : (
                  <span className="muted">none set</span>
                ),
            },
            {
              key: 'promise',
              header: 'Promise',
              render: (r) =>
                r.promise ? (
                  <span className={PROMISE_TONE[r.promise.state]}>
                    {fmt(r.promise.amount)} by {dateOnly(r.promise.date)} · {r.promise.state}
                  </span>
                ) : (
                  <span className="muted">-</span>
                ),
            },
            {
              key: 'last',
              header: 'Last contact',
              render: (r) =>
                r.last_contact ? dateOnly(r.last_contact) : <span className="muted">never</span>,
            },
            { key: 'collector', header: 'Collector', render: (r) => r.collector || '-' },
          ]}
        />
      )}
    </>
  )
}

function Results() {
  const [start, setStart] = useState(firstOfMonth())
  const [end, setEnd] = useState(today())
  const path = `/api/collections/performance${qs({ start, end })}`
  const { data, error, loading, reload } = useApi(path)
  return (
    <>
      <div className="row" style={{ margin: '12px 0', gap: 8, flexWrap: 'wrap' }}>
        <label className="check">
          From&nbsp;
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="check">
          To&nbsp;
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <span style={{ flex: 1 }} />
        <ExportButtons path={path} name="collector_performance" small />
      </div>
      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Adding up results" />
      ) : (
        <DataTable
          caption="Results by collector"
          rows={data || []}
          rowKey={(r) => r.collector_id}
          empty="No loans have been given to a collector yet."
          columns={[
            { key: 'who', header: 'Collector', render: (r) => r.collector },
            { key: 'held', header: 'Loans held', num: true, render: (r) => r.loans_held },
            { key: 'late', header: 'In arrears', num: true, render: (r) => r.in_arrears },
            {
              key: 'arrears',
              header: 'Overdue now',
              num: true,
              render: (r) => fmt(r.arrears_now),
            },
            { key: 'in', header: 'Collected', num: true, render: (r) => fmt(r.collected) },
            { key: 'made', header: 'Promises', num: true, render: (r) => r.promises_made },
            { key: 'kept', header: 'Kept', num: true, render: (r) => r.promises_kept },
            { key: 'broken', header: 'Broken', num: true, render: (r) => r.promises_broken },
            {
              key: 'rate',
              header: 'Kept rate',
              num: true,
              render: (r) => (r.kept_rate_pct === null ? '-' : `${r.kept_rate_pct}%`),
            },
          ]}
        />
      )}
    </>
  )
}
