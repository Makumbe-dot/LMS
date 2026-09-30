import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Field, KeyValues, Kpi, Loading, PageHeader } from '../components/ui.jsx'
import { get, post } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateOnly, fmt, money } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** One pre-close check, said in words as well as colour. */
function CheckRow({ check }) {
  const tone = check.passed ? 'tag-ok' : check.blocking ? 'tag-danger' : 'tag-warn'
  const word = check.passed ? 'Pass' : check.blocking ? 'Fail' : 'Note'
  return (
    <tr>
      <td>
        <span className={tone}>{word}</span>
      </td>
      <td>
        {check.label}
        {!check.blocking ? <span className="muted"> (advisory)</span> : null}
        {check.blocking && !check.overridable ? (
          <span className="muted"> (cannot be overridden)</span>
        ) : null}
      </td>
      <td className="muted">{check.detail}</td>
    </tr>
  )
}

/** A month's state, saying whether it is shut in its own right or by a later close. */
function StateTag({ month }) {
  if (month.state === 'open') {
    return <span className={month.has_ended ? 'tag-warn' : 'tag-ok'}>Open</span>
  }
  return (
    <span className="tag-ok" title={month.closed_by_implication ? 'Shut by a later close' : ''}>
      Closed{month.closed_by_implication ? ' (by a later close)' : ''}
    </span>
  )
}

/** The period register: what has been signed off, and closing the next month. */
export default function Periods() {
  const { can } = useAuth()
  const { reload: reloadOrg } = useOrg()
  const { toast, toastError } = useToast()
  const [year, setYear] = useState(new Date().getFullYear())
  const [action, setAction] = useState(null)
  const [preflight, setPreflight] = useState(null)
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)

  const { data, error, loading, reload } = useApi(`/api/periods?year=${year}`)
  const isAdmin = can('admin')

  async function run(promise, message) {
    setBusy(true)
    try {
      await promise
      setAction(null)
      setPreflight(null)
      setForce(false)
      toast(message)
      reload()
      reloadOrg() // every date field reads the posting window from here
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function openClose(month) {
    try {
      // Re-read the checks at the moment of closing, not when the page loaded.
      setPreflight(await get(`/api/periods/${month.year}-${month.month}/preflight`))
      setForce(false)
      setAction({ kind: 'close', month })
    } catch (err) {
      toastError(err)
    }
  }

  const years = []
  for (let y = new Date().getFullYear() + 1; y >= new Date().getFullYear() - 5; y -= 1) {
    years.push(y)
  }

  return (
    <>
      <PageHeader
        title="Period close"
        meta="Freeze a month so nothing new can be posted into it, and keep the trial balance it was signed off on"
      >
        <select value={year} onChange={(e) => setYear(Number(e.target.value))} aria-label="Year">
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
        <button type="button" className="btn" onClick={reload}>
          Refresh
        </button>
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? <Loading what="Reading the register" /> : null}

      {data ? (
        <>
          <div className="grid cols-4">
            <Kpi
              label="Closed through"
              value={data.closed_through ? dateOnly(data.closed_through) : '—'}
              sub={data.closed_through ? 'signed off' : 'nothing closed yet'}
            />
            <Kpi
              label="Earliest postable date"
              value={
                data.earliest_postable_date ? dateOnly(data.earliest_postable_date) : 'any date'
              }
              sub="enforced on every posting"
            />
            <Kpi
              label="Next month to close"
              value={data.next_to_close ? data.next_to_close.label : '—'}
              sub={
                data.next_to_close
                  ? 'the smallest step; a later month absorbs the ones between'
                  : 'nothing has ended and is open'
              }
            />
            <Kpi
              label="Reopened periods"
              value={data.reopened_periods}
              sub={data.reopened_periods ? 'each one is in the audit trail' : 'none'}
            />
          </div>

          <div className="card" style={{ marginTop: 16 }}>
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>
              Months close in order and only forwards, so closing a month closes everything
              before it. That is what makes the earliest postable date a real answer rather than
              a guess. A closed month refuses new postings — repayments, disbursements, charges,
              savings movements, penalty and interest runs — but it does not make the month
              immutable: rescheduling a loan rebuilds its schedule, including instalments dated
              inside a closed month, and posts its capitalisation today.
            </p>
          </div>

          <DataTable
            caption={`Accounting periods in ${data.year}`}
            rows={data.months}
            rowKey={(r) => r.month}
            columns={[
              { key: 'month', header: 'Month', render: (r) => MONTHS[r.month - 1] },
              { key: 'range', header: 'Ends', render: (r) => dateOnly(r.end_date) },
              { key: 'state', header: 'State', render: (r) => <StateTag month={r} /> },
              {
                key: 'txns',
                header: 'Postings',
                num: true,
                render: (r) => (r.transactions ? r.transactions : '-'),
              },
              {
                key: 'closed_by',
                header: 'Closed by',
                render: (r) =>
                  r.period?.closed_at ? (
                    <>
                      {r.period.closed_by_name || 'system'}
                      <br />
                      <span className="muted">{dateOnly(r.period.closed_at)}</span>
                    </>
                  ) : (
                    '-'
                  ),
              },
              {
                key: 'snapshot',
                header: 'Frozen trial balance',
                num: true,
                render: (r) =>
                  r.period?.closed_at ? (
                    <>
                      {fmt(r.period.snapshot_debits)}
                      <br />
                      <span className="muted">{r.period.snapshot_entries} entries</span>
                    </>
                  ) : (
                    '-'
                  ),
              },
              {
                key: 'reopened',
                header: 'Reopened',
                render: (r) =>
                  r.period?.reopen_count ? (
                    <span className="tag-danger" title={r.period.reopen_reason}>
                      {r.period.reopen_count}×
                    </span>
                  ) : null,
              },
              {
                key: 'actions',
                header: '',
                render: (r) => {
                  if (!isAdmin) return null
                  if (r.closable) {
                    const isNext =
                      data.next_to_close &&
                      data.next_to_close.year === r.year &&
                      data.next_to_close.month === r.month
                    return (
                      <button
                        type="button"
                        className={isNext ? 'btn small primary' : 'btn small'}
                        onClick={() => openClose(r)}
                      >
                        Close
                      </button>
                    )
                  }
                  const isLatestClosed =
                    data.last_closed &&
                    data.last_closed.year === r.year &&
                    data.last_closed.month === r.month
                  if (isLatestClosed) {
                    return (
                      <button
                        type="button"
                        className="btn small"
                        onClick={() => setAction({ kind: 'reopen', month: r })}
                      >
                        Reopen
                      </button>
                    )
                  }
                  return null
                },
              },
            ]}
          />

          {!isAdmin ? (
            <p className="muted">An administrator closes and reopens periods.</p>
          ) : null}
        </>
      ) : null}

      {action?.kind === 'close' && preflight ? (
        <FormModal
          title={`Close ${preflight.label}`}
          submitLabel={force ? 'Close anyway' : 'Close the period'}
          busy={busy}
          submitDisabled={!preflight.can_close && !force}
          onClose={() => {
            setAction(null)
            setPreflight(null)
          }}
          onSubmit={(v) =>
            run(
              post(`/api/periods/${preflight.year}-${preflight.month}/close`, {
                note: v.note,
                force,
              }),
              `${preflight.label} closed`,
            )
          }
        >
          <KeyValues
            items={[
              ['Period', `${dateOnly(preflight.start_date)} to ${dateOnly(preflight.end_date)}`],
              [
                'Trial balance to freeze',
                `${money(preflight.trial_balance.total_debit)} Dr / ${money(
                  preflight.trial_balance.total_credit,
                )} Cr`,
              ],
              ['Entries in the month', preflight.entries],
              [
                'After closing',
                `nothing can be posted on or before ${dateOnly(preflight.end_date)}`,
              ],
            ]}
          />

          <table className="table" style={{ marginTop: 12 }}>
            <caption className="sr-only">Pre-close checks</caption>
            <thead>
              <tr>
                <th scope="col">Result</th>
                <th scope="col">Check</th>
                <th scope="col">Found</th>
              </tr>
            </thead>
            <tbody>
              {preflight.checks.map((check) => (
                <CheckRow key={check.key} check={check} />
              ))}
            </tbody>
          </table>

          {!preflight.can_close ? (
            preflight.can_force ? (
              <label className="check" style={{ marginTop: 10 }}>
                <input
                  type="checkbox"
                  checked={force}
                  onChange={(e) => setForce(e.target.checked)}
                />
                &nbsp;Close anyway. Which checks were overridden is recorded on the period and in
                the audit trail.
              </label>
            ) : (
              <p className="error" style={{ marginBottom: 0 }}>
                This month cannot be closed: a check that cannot be overridden has failed. A month
                that has not ended, one already closed, and one with an earlier month still open
                are all refused outright.
              </p>
            )
          ) : null}

          <Field label="Note" name="note" hint="Kept on the period row, e.g. what was issued" />
        </FormModal>
      ) : null}

      {action?.kind === 'reopen' ? (
        <FormModal
          title={`Reopen ${MONTHS[action.month.month - 1]} ${action.month.year}`}
          submitLabel="Reopen the period"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(`/api/periods/${action.month.year}-${action.month.month}/reopen`, v),
              'Period reopened',
            )
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            The trial balance the month was closed on is kept, so whatever changes from here is
            visible against it. Only the most recently closed month can be reopened.
          </p>
          <Field
            label="Reason"
            name="reason"
            required
            minLength={10}
            hint="At least 10 characters. Goes in the audit trail with the numbers being superseded."
          />
        </FormModal>
      ) : null}
    </>
  )
}
