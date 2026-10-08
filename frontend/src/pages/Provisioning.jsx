import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import {
  Badge,
  ExportButtons,
  ErrorBanner,
  Field,
  KeyValues,
  Kpi,
  Loading,
  PageHeader,
  StageTag,
} from '../components/ui.jsx'
import { get, post, qs, rowsOf } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateOnly, fmt, money, num, pct, today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

/** A movement, shown with its sign and said in words as well as colour. */
function Movement({ value }) {
  const n = num(value) ?? 0
  if (n === 0) return <span className="tag-ok">nothing to book</span>
  return (
    <span className={n > 0 ? 'tag-danger' : 'tag-ok'}>
      {n > 0 ? '+' : ''}
      {money(value)} {n > 0 ? 'to charge to 5100 Impairment' : 'to release from 1900 Provision'}
    </span>
  )
}

/** IFRS 9 staging, the expected credit loss, and booking it to the ledger. */
export default function Provisioning() {
  const navigate = useNavigate()
  const { can } = useAuth()
  const { toastError, toast } = useToast()
  const { activeBranches } = useOrg()
  const [asOf, setAsOf] = useState(today())
  const [branchId, setBranchId] = useState('')
  const [action, setAction] = useState(null)
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)

  const path = `/api/reports/ecl${qs({ as_of: asOf, branch_id: branchId })}`
  const { data, error, loading, reload } = useApi(path)
  const runs = useApi('/api/provisions?page_size=12')

  const wholeBook = !branchId
  const canAccount = can('accounting')

  async function run(promise, message) {
    setBusy(true)
    try {
      await promise
      setAction(null)
      toast(message)
      reload()
      runs.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function openBook() {
    try {
      // Re-read, so the figure shown is the figure booked even if the page has
      // been sitting open.
      setPreview(await get(`/api/provisions/preview${qs({ as_of: asOf })}`))
      setAction({ kind: 'book' })
    } catch (err) {
      toastError(err)
    }
  }

  return (
    <>
      <PageHeader
        title="Impairment and provisioning"
        meta="IFRS 9 staging on days past due, and the expected credit loss booked to the ledger"
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
        <ExportButtons path={path} name={'ecl_provision'} />
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? <Loading what="Calculating provisions" /> : null}

      {data ? (
        <>
          <div className="grid cols-4">
            <Kpi
              label="Gross exposure"
              value={money(data.total_exposure)}
              sub="active book, incl. unearned interest"
            />
            <Kpi
              label="ECL on gross exposure"
              value={money(data.total_provision)}
              sub={`${pct(data.coverage_pct)} coverage`}
            />
            <Kpi
              label="Provision required"
              value={money(data.total_provision_required)}
              sub="on the recognised carrying amount"
            />
            <Kpi
              label="Provision booked"
              value={
                data.total_provision_booked === null
                  ? '—'
                  : money(data.total_provision_booked)
              }
              sub={
                !wholeBook ? (
                  'whole book only'
                ) : data.ledger_agrees ? (
                  <span className="tag-ok">agrees with account 1900</span>
                ) : (
                  <span className="tag-danger">
                    account 1900 shows {money(data.ledger_provision)}
                  </span>
                )
              }
            />
          </div>

          <div className="card" style={{ marginTop: 16 }}>
            <h3>Month-end provision run</h3>
            {wholeBook ? (
              <>
                <p style={{ marginTop: 0 }}>
                  <Movement value={data.total_provision_movement} />
                </p>
                <p className="muted" style={{ fontSize: 12 }}>
                  {data.last_run
                    ? `Last booked: ${data.last_run.run_no} for ${dateOnly(
                        data.last_run.period_end,
                      )}, movement ${fmt(data.last_run.movement)}.`
                    : 'Nothing has been booked yet.'}{' '}
                  Only the movement is posted, so running a period twice posts nothing.
                </p>
                {canAccount ? (
                  <button type="button" className="btn primary" onClick={openBook} disabled={busy}>
                    Book the movement
                  </button>
                ) : (
                  <p className="muted" style={{ marginBottom: 0 }}>
                    An administrator books the provision.
                  </p>
                )}
              </>
            ) : (
              <p className="muted" style={{ marginBottom: 0 }}>
                Provisioning runs across the whole book, because account 1900 is an
                institution-level balance. Clear the branch filter to book the movement.
              </p>
            )}
          </div>

          <div className="card">
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
                { key: 'exposure', header: 'Gross exposure', num: true, render: (r) => fmt(r.exposure) },
                {
                  key: 'carrying',
                  header: 'Carrying amount',
                  num: true,
                  render: (r) => fmt(r.carrying_amount),
                },
                { key: 'rate', header: 'Rate', num: true, render: (r) => pct(r.rate_pct) },
                { key: 'gross', header: 'ECL (gross)', num: true, render: (r) => fmt(r.provision) },
                {
                  key: 'required',
                  header: 'Provision (bookable)',
                  num: true,
                  render: (r) => <strong>{fmt(r.provision_required)}</strong>,
                },
              ]}
            />
            <p className="muted" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
              Stage 2 begins after {data.rates.stage_2_days} days past due and stage 3 after{' '}
              {data.rates.stage_3_days}. Rates are {pct(data.rates.stage_1)} /{' '}
              {pct(data.rates.stage_2)} / {pct(data.rates.stage_3)} and are editable under
              Settings. The gross column is the ECL on everything contractually outstanding,
              including interest not yet earned. Only the provision on the recognised carrying
              amount — principal, penalties and charges — is booked, because interest is
              recognised when it is collected.
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
              { key: 'exposure', header: 'Gross', num: true, render: (r) => fmt(r.exposure) },
              {
                key: 'carrying',
                header: 'Carrying',
                num: true,
                render: (r) => fmt(r.carrying_amount),
              },
              { key: 'rate', header: 'Rate', num: true, render: (r) => pct(r.provision_rate_pct) },
              {
                key: 'required',
                header: 'Required',
                num: true,
                render: (r) => fmt(r.provision_required),
              },
              { key: 'booked', header: 'Booked', num: true, render: (r) => fmt(r.provision_booked) },
              {
                key: 'movement',
                header: 'Movement',
                num: true,
                render: (r) => {
                  const n = num(r.provision_movement) ?? 0
                  if (n === 0) return '-'
                  return (
                    <span className={n > 0 ? 'tag-danger' : 'tag-ok'}>
                      {n > 0 ? '+' : ''}
                      {fmt(r.provision_movement)}
                    </span>
                  )
                },
              },
            ]}
          />

          <div className="card">
            <h3>Provision runs</h3>
            <DataTable
              caption="Provision runs"
              rows={rowsOf(runs.data)}
              rowKey={(r) => r.id}
              empty="No provision has been booked yet"
              columns={[
                { key: 'run_no', header: 'Run', render: (r) => r.run_no },
                { key: 'period', header: 'Period', render: (r) => dateOnly(r.period_end) },
                { key: 'status', header: 'Status', render: (r) => <Badge value={r.status} /> },
                {
                  key: 'required',
                  header: 'Required',
                  num: true,
                  render: (r) => fmt(r.provision_required),
                },
                {
                  key: 'before',
                  header: 'Carried before',
                  num: true,
                  render: (r) => fmt(r.provision_before),
                },
                {
                  key: 'movement',
                  header: 'Movement',
                  num: true,
                  render: (r) => {
                    const n = num(r.movement) ?? 0
                    return (
                      <span className={n > 0 ? 'tag-danger' : n < 0 ? 'tag-ok' : undefined}>
                        {n > 0 ? '+' : ''}
                        {fmt(r.movement)}
                      </span>
                    )
                  },
                },
                { key: 'entry', header: 'Entry', render: (r) => r.entry_no || '-' },
                { key: 'by', header: 'Booked by', render: (r) => r.run_by_name || 'system' },
                {
                  key: 'actions',
                  header: '',
                  render: (r) =>
                    canAccount && r.status === 'posted' ? (
                      <button
                        type="button"
                        className="btn small"
                        onClick={(event) => {
                          event.stopPropagation()
                          setAction({ kind: 'reverse', run: r })
                        }}
                      >
                        Reverse
                      </button>
                    ) : null,
                },
              ]}
            />
          </div>
        </>
      ) : null}

      {action?.kind === 'book' && preview ? (
        <FormModal
          title="Book the provision movement"
          submitLabel="Book the movement"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post('/api/provisions/run', { as_of: asOf, narration: v.narration }),
              'Provision movement booked',
            )
          }
        >
          <KeyValues
            items={[
              ['Period', dateOnly(preview.period_end)],
              ['Entry dated', dateOnly(preview.entry_date)],
              ['Active loans assessed', preview.loans_assessed],
              ['Loans swept off the book', preview.loans_released],
              ['Provision required', money(preview.provision_required)],
              ['Provision already carried', money(preview.provision_booked)],
              ['Movement to post', <Movement key="m" value={preview.movement} />],
              [
                'The entry raised',
                (num(preview.movement) ?? 0) >= 0
                  ? 'Dr 5100 Impairment charge / Cr 1900 Provision for credit losses'
                  : 'Dr 1900 Provision for credit losses / Cr 5100 Impairment charge',
              ],
            ]}
          />
          {preview.already_posted ? (
            <p className="error">
              {preview.existing_run_no} has already been booked for this period. Booking again
              posts nothing.
            </p>
          ) : null}
          <Field label="Narration" name="narration" />
        </FormModal>
      ) : null}

      {action?.kind === 'reverse' ? (
        <FormModal
          title={`Reverse ${action.run.run_no}`}
          submitLabel="Reverse the run"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(post(`/api/provisions/${action.run.id}/reverse`, v), 'Provision run reversed')
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            The journal entry is mirrored on its own date, and every loan's provision goes back
            to what it carried before the run. It is refused if a later run exists, or if any
            loan has moved on since.
          </p>
          <Field label="Reason" name="narration" required />
        </FormModal>
      ) : null}
    </>
  )
}
