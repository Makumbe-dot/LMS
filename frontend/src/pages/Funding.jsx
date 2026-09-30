import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import Modal, { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import {
  Badge,
  ErrorBanner,
  Field,
  KeyValues,
  Kpi,
  Loading,
  PageHeader,
  Pager,
} from '../components/ui.jsx'
import { downloadCsv, get, post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateOnly, fmt, getCurrency, money, num, pct, today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { PeriodNotice, useMinPostingDate } from '../lib/periods.jsx'
import { useApi } from '../lib/useApi.js'

const TABS = [
  { key: 'facilities', label: 'Facilities' },
  { key: 'capital', label: 'Capital' },
]

/** Funder borrowings and shareholders' capital: where the money to lend comes from. */
export default function Funding() {
  const { can } = useAuth()
  const { activeBranches } = useOrg()
  const minPostingDate = useMinPostingDate()
  const { toast, toastError } = useToast()

  const [tab, setTab] = useState('facilities')
  const [page, setPage] = useState(1)
  const [action, setAction] = useState(null)
  const [detail, setDetail] = useState(null)
  const [busy, setBusy] = useState(false)

  const paths = {
    facilities: `/api/funding/facilities${qs({ page, page_size: 25 })}`,
    capital: `/api/funding/capital${qs({ page, page_size: 25 })}`,
  }
  const { data, error, loading, reload } = useApi(paths[tab])
  const summary = useApi('/api/funding/summary')

  // Both tabs are paginated and so answer the same shape; guard on the first row's
  // fields rather than on the tab, so a switch cannot read a stale payload.
  const shapes = {
    facilities: (d) => Array.isArray(d?.results) && (!d.results.length || d.results[0].facility_no),
    capital: (d) => Array.isArray(d?.results) && (!d.results.length || d.results[0].contributor),
  }
  const ready = shapes[tab](data)
  const isAdmin = can('admin')
  const kpis = summary.data

  async function run(promise, message) {
    setBusy(true)
    try {
      await promise
      setAction(null)
      toast(message)
      reload()
      summary.reload()
      if (detail) openDetail(detail.id)
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function openDetail(facilityId) {
    try {
      setDetail(await get(`/api/funding/facilities/${facilityId}`))
    } catch (err) {
      toastError(err)
    }
  }

  return (
    <>
      <PageHeader
        title="Funding and capital"
        meta="Shareholders' capital and funder borrowings — what pays for the loan book"
      >
        <button type="button" className="btn" onClick={reload}>
          Refresh
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => downloadCsv(paths[tab], tab).catch(toastError)}
        >
          Export CSV
        </button>
        {isAdmin ? (
          <>
            <button type="button" className="btn" onClick={() => setAction({ kind: 'accrue' })}>
              Accrue interest
            </button>
            {tab === 'facilities' ? (
              <button
                type="button"
                className="btn primary"
                onClick={() => setAction({ kind: 'open' })}
              >
                Open a facility
              </button>
            ) : (
              <button
                type="button"
                className="btn primary"
                onClick={() => setAction({ kind: 'capital' })}
              >
                Record capital
              </button>
            )}
          </>
        ) : null}
      </PageHeader>

      {kpis ? (
        <div className="grid cols-4">
          <Kpi
            label="Cash and bank"
            value={money(kpis.cash)}
            sub={
              (num(kpis.cash) ?? 0) < 0 ? (
                <span className="tag-danger">overdrawn — record the funding</span>
              ) : (
                'account 1000'
              )
            }
          />
          <Kpi
            label="Drawn on facilities"
            value={money(kpis.drawn)}
            sub={`${pct(kpis.utilisation_pct)} of ${money(kpis.total_limit)}`}
          />
          <Kpi
            label="Available to draw"
            value={money(kpis.available)}
            sub={`${kpis.open_facilities} open facility/ies`}
          />
          <Kpi
            label="Capital contributed"
            value={money(kpis.capital.net_capital)}
            sub={
              (num(kpis.capital.dividends) ?? 0) > 0
                ? `less ${money(kpis.capital.dividends)} paid out`
                : 'injected less returned'
            }
          />
        </div>
      ) : null}

      {kpis?.maturing_soon?.length ? (
        <div className="banner">
          <strong>Maturing within 90 days: </strong>
          {kpis.maturing_soon
            .map(
              (f) =>
                `${f.facility_no} (${f.funder_name}) on ${dateOnly(f.maturity_date)}, ` +
                `${money(f.principal_outstanding)} outstanding`,
            )
            .join('; ')}
          .
        </div>
      ) : null}

      {(num(kpis?.accrued_interest) ?? 0) > 0 ? (
        <p className="muted" style={{ fontSize: 12 }}>
          {money(kpis.accrued_interest)} of interest has accrued to account 2110 and is not yet
          paid. Unlike loan interest — which is recognised only when collected — borrowing interest
          is accrued monthly, because an unrecognised asset is prudent and an unrecognised
          liability is not.
        </p>
      ) : null}

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => {
              setTab(t.key)
              setPage(1)
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <ErrorBanner error={error} onRetry={reload} />
      {!ready && !error ? <Loading what="Loading the funding book" /> : null}

      {ready && tab === 'facilities' ? (
        <>
          <Pager meta={data} onPage={setPage} noun="facilities" />
          <DataTable
            caption="Funding facilities"
            rows={data.results}
            rowKey={(r) => r.id}
            onRowClick={(r) => openDetail(r.id)}
            empty="No facility yet — open one to record what the institution has borrowed"
            columns={[
              { key: 'no', header: 'Facility', render: (r) => r.facility_no },
              { key: 'funder', header: 'Funder', render: (r) => r.funder_name },
              { key: 'name', header: 'Line', render: (r) => r.name },
              {
                key: 'state',
                header: 'State',
                render: (r) =>
                  r.is_open ? (
                    <span className="tag-ok">{r.is_revolving ? 'Revolving' : 'Term'}</span>
                  ) : (
                    <span className="muted">Closed {dateOnly(r.closed_on)}</span>
                  ),
              },
              { key: 'limit', header: 'Limit', num: true, render: (r) => fmt(r.facility_limit) },
              {
                key: 'drawn',
                header: 'Outstanding',
                num: true,
                render: (r) => <strong>{fmt(r.principal_outstanding)}</strong>,
              },
              { key: 'avail', header: 'Available', num: true, render: (r) => fmt(r.available) },
              {
                key: 'accrued',
                header: 'Interest owed',
                num: true,
                render: (r) => (Number(r.interest_accrued) ? fmt(r.interest_accrued) : '-'),
              },
              { key: 'rate', header: 'Rate', num: true, render: (r) => pct(r.interest_rate_pct_pa) },
              {
                key: 'maturity',
                header: 'Matures',
                render: (r) => (r.maturity_date ? dateOnly(r.maturity_date) : '-'),
              },
            ]}
          />
        </>
      ) : null}

      {ready && tab === 'capital' ? (
        <>
          <Pager meta={data} onPage={setPage} noun="movements" />
          <DataTable
            caption="Capital movements"
            rows={data.results}
            rowKey={(r) => r.id}
            empty="No capital recorded yet"
            columns={[
              { key: 'date', header: 'Date', render: (r) => dateOnly(r.txn_date) },
              { key: 'type', header: 'Movement', render: (r) => <Badge value={r.txn_type} /> },
              { key: 'who', header: 'Contributor', render: (r) => r.contributor },
              {
                key: 'amount',
                header: `Amount (${getCurrency()})`,
                num: true,
                render: (r) => (
                  <strong className={r.reversed ? 'muted' : undefined}>{fmt(r.amount)}</strong>
                ),
              },
              { key: 'ref', header: 'Reference', render: (r) => r.reference || '-' },
              { key: 'entry', header: 'Entry', render: (r) => r.entry_no || '-' },
              {
                key: 'state',
                header: '',
                render: (r) =>
                  r.reversed ? (
                    <span className="tag-danger">Reversed</span>
                  ) : isAdmin && r.txn_type !== 'reversal' ? (
                    <button
                      type="button"
                      className="btn small"
                      onClick={(event) => {
                        event.stopPropagation()
                        setAction({ kind: 'reverse-capital', row: r })
                      }}
                    >
                      Reverse
                    </button>
                  ) : null,
              },
            ]}
          />
        </>
      ) : null}

      {!isAdmin ? (
        <p className="muted">An administrator records capital and draws on facilities.</p>
      ) : null}

      {/* ---------------------------------------------------------------- detail */}
      {detail ? (
        <Modal title={`${detail.facility_no} — ${detail.funder_name}`} onClose={() => setDetail(null)} wide>
          <KeyValues
            items={[
              ['Line', detail.name],
              ['Limit', money(detail.facility_limit)],
              ['Principal outstanding', money(detail.principal_outstanding)],
              ['Interest accrued and unpaid', money(detail.interest_accrued)],
              ['Available to draw', money(detail.available)],
              ['Rate', `${pct(detail.interest_rate_pct_pa)} a year on the drawn balance`],
              ['Type', detail.is_revolving ? 'Revolving' : 'Term'],
              ['Started', dateOnly(detail.start_date)],
              ['Matures', detail.maturity_date ? dateOnly(detail.maturity_date) : '—'],
              ['Terms', detail.repayment_terms || '—'],
              ['Interest accrued to', detail.last_accrual_date ? dateOnly(detail.last_accrual_date) : 'never'],
              ['Total drawn', money(detail.totals.total_drawn)],
              ['Total repaid', money(detail.totals.total_repaid)],
              ['Total interest paid', money(detail.totals.total_interest_paid)],
              ['Total fees', money(detail.totals.total_fees)],
            ]}
          />

          {isAdmin && detail.is_open ? (
            <div className="row" style={{ marginTop: 12, marginBottom: 12 }}>
              <button
                type="button"
                className="btn primary"
                onClick={() => setAction({ kind: 'drawdown', facility: detail })}
              >
                Draw down
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setAction({ kind: 'repay', facility: detail })}
              >
                Repay principal
              </button>
              <button
                type="button"
                className="btn"
                disabled={!Number(detail.interest_accrued)}
                title={
                  Number(detail.interest_accrued)
                    ? undefined
                    : 'Nothing has accrued yet. Run the accrual first — paying ahead of it would charge the same period twice.'
                }
                onClick={() => setAction({ kind: 'interest', facility: detail })}
              >
                Pay interest
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setAction({ kind: 'fee', facility: detail })}
              >
                Pay a fee
              </button>
              {/* Not "Close": the dialog's own dismiss button says that, and two
                  buttons a few pixels apart, one of which retires a facility for
                  good, is a trap. */}
              <button
                type="button"
                className="btn"
                onClick={() => setAction({ kind: 'close-facility', facility: detail })}
              >
                Retire the facility
              </button>
            </div>
          ) : null}

          <DataTable
            caption={`Movements on ${detail.facility_no}`}
            rows={detail.transactions}
            rowKey={(r) => r.id}
            empty="Nothing drawn yet"
            columns={[
              { key: 'date', header: 'Date', render: (r) => dateOnly(r.txn_date) },
              { key: 'type', header: 'Movement', render: (r) => r.txn_type_label },
              {
                key: 'amount',
                header: 'Amount',
                num: true,
                render: (r) => (
                  <span className={r.reversed ? 'muted' : undefined}>{fmt(r.amount)}</span>
                ),
              },
              { key: 'principal', header: 'Principal after', num: true, render: (r) => fmt(r.principal_after) },
              { key: 'accrued', header: 'Interest owed after', num: true, render: (r) => fmt(r.accrued_after) },
              { key: 'entry', header: 'Entry', render: (r) => r.entry_no || '-' },
              {
                key: 'state',
                header: '',
                render: (r) =>
                  r.reversed ? (
                    <span className="tag-danger">Reversed</span>
                  ) : isAdmin &&
                    r.txn_type !== 'reversal' &&
                    r.txn_type !== 'interest_accrual' ? (
                    <button
                      type="button"
                      className="btn small"
                      onClick={() => setAction({ kind: 'reverse-movement', facility: detail, row: r })}
                    >
                      Reverse
                    </button>
                  ) : null,
              },
            ]}
          />
        </Modal>
      ) : null}

      {/* ---------------------------------------------------------------- actions */}
      {action?.kind === 'open' ? (
        <FormModal
          title="Open a funding facility"
          submitLabel="Open the facility"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post('/api/funding/facilities', v), 'Facility opened')}
        >
          <div className="grid cols-2">
            <Field label="Funder" name="funder_name" required placeholder="e.g. CBZ Bank Wholesale" />
            <Field label="Name of the line" name="name" required placeholder="On-lending line" />
            <Field
              label={`Limit (${getCurrency()})`}
              type="number"
              step="0.01"
              min="0.01"
              name="facility_limit"
              required
            />
            <Field
              label="Rate (% a year)"
              type="number"
              step="0.001"
              min="0"
              name="interest_rate_pct_pa"
              defaultValue="0"
              hint="Simple, on the drawn balance. Accrued monthly to account 2110."
            />
            <Field label="Starts" type="date" name="start_date" defaultValue={today()} />
            <Field label="Matures" type="date" name="maturity_date" />
          </div>
          <label className="check">
            <input type="checkbox" name="is_revolving" />
            &nbsp;Revolving — repaying principal frees the limit to draw again
          </label>
          <Field label="Repayment terms" name="repayment_terms" />
          <Field as="textarea" label="Notes" name="notes" rows={2} />
        </FormModal>
      ) : null}

      {['drawdown', 'repay', 'interest', 'fee'].includes(action?.kind) ? (
        <FormModal
          title={
            {
              drawdown: `Draw on ${action.facility.facility_no}`,
              repay: `Repay principal on ${action.facility.facility_no}`,
              interest: `Pay interest on ${action.facility.facility_no}`,
              fee: `Pay a fee on ${action.facility.facility_no}`,
            }[action.kind]
          }
          submitLabel="Post it"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(
                `/api/funding/facilities/${action.facility.id}/${
                  action.kind === 'interest' ? 'interest' : action.kind
                }`,
                v,
              ),
              'Posted',
            )
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            {
              {
                drawdown: `${money(action.facility.available)} available to draw. Cash goes up and account 2100 records what is owed.`,
                repay: `${money(action.facility.principal_outstanding)} of principal outstanding. Refused if there is not the cash for it.`,
                interest: `${money(action.facility.interest_accrued)} has accrued. More than that is refused — paying ahead of the accrual charges the same period to 5300 twice.`,
                fee: 'An arrangement, commitment or non-utilisation fee. Goes straight to expense; it is not a drawdown.',
              }[action.kind]
            }
          </p>
          <PeriodNotice date={today()} />
          <div className="grid cols-2">
            <Field
              label={`Amount (${getCurrency()})`}
              type="number"
              step="0.01"
              min="0.01"
              max={
                action.kind === 'repay'
                  ? action.facility.principal_outstanding
                  : action.kind === 'interest'
                    ? action.facility.interest_accrued
                    : action.kind === 'drawdown'
                      ? action.facility.available
                      : undefined
              }
              name="amount"
              required
            />
            <Field
              label="Date"
              type="date"
              name="txn_date"
              min={minPostingDate}
              defaultValue={today()}
            />
            <Field as="select" label="Method" name="method" defaultValue="bank_transfer">
              <option value="bank_transfer">Bank transfer</option>
              <option value="cash">Cash</option>
              <option value="mobile_money">Mobile money</option>
            </Field>
            <Field label="Reference" name="reference" />
          </div>
          <Field label="Narration" name="narration" />
        </FormModal>
      ) : null}

      {action?.kind === 'capital' ? (
        <FormModal
          title="Record a capital movement"
          submitLabel="Record it"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post('/api/funding/capital', v), 'Capital movement recorded')}
        >
          <PeriodNotice date={today()} />
          <Field as="select" label="Movement" name="txn_type" defaultValue="injection">
            <option value="injection">Capital injection — money in, equity up</option>
            <option value="return_of_capital">Return of capital — money out, equity down</option>
            <option value="dividend">Dividend — a distribution of the surplus</option>
          </Field>
          <div className="grid cols-2">
            <Field
              label={`Amount (${getCurrency()})`}
              type="number"
              step="0.01"
              min="0.01"
              name="amount"
              required
            />
            <Field label="Contributor" name="contributor" required />
            <Field
              label="Date"
              type="date"
              name="txn_date"
              min={minPostingDate}
              defaultValue={today()}
            />
            <Field as="select" label="Method" name="method" defaultValue="bank_transfer">
              <option value="bank_transfer">Bank transfer</option>
              <option value="cash">Cash</option>
              <option value="mobile_money">Mobile money</option>
            </Field>
            <Field label="Reference" name="reference" />
            {activeBranches.length > 1 ? (
              <Field as="select" label="Branch" name="branch" defaultValue="">
                <option value="">Institution-wide</option>
                {activeBranches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </Field>
            ) : null}
          </div>
          <Field label="Narration" name="narration" />
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            A return of capital cannot exceed what was contributed, and a dividend is refused when
            there is not the equity or the cash for it.
          </p>
        </FormModal>
      ) : null}

      {action?.kind === 'accrue' ? (
        <FormModal
          title="Accrue interest on borrowings"
          submitLabel="Run the accrual"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(`/api/funding/accrue-interest${qs({ as_of: v.as_of })}`),
              'Interest accrued',
            ).then(() => summary.reload())
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            Posts Dr 5300 Interest on borrowings / Cr 2110 Accrued interest for each whole month
            since the last accrual, catching up any that were missed. A second run in the same
            month posts nothing.
          </p>
          <Field label="Accrue up to" type="date" name="as_of" defaultValue={today()} />
        </FormModal>
      ) : null}

      {action?.kind === 'close-facility' ? (
        <FormModal
          title={`Retire ${action.facility.facility_no}`}
          submitLabel="Retire the facility"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(`/api/funding/facilities/${action.facility.id}/close`, v),
              'Facility closed',
            ).then(() => setDetail(null))
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            Refused while any principal or accrued interest is outstanding. A retired facility
            cannot be drawn on again.
          </p>
          <Field label="Note" name="narration" />
        </FormModal>
      ) : null}

      {action?.kind === 'reverse-movement' ? (
        <FormModal
          title={`Reverse ${action.row.txn_type_label.toLowerCase()} of ${money(action.row.amount)}`}
          submitLabel="Reverse it"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(
                `/api/funding/facilities/${action.facility.id}/transactions/${action.row.id}/reverse`,
                v,
              ),
              'Movement reversed',
            )
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            The journal entry is mirrored and the facility's balances go back to where they were.
            Dated today, not on the original — a closed month is not reached back into.
          </p>
          <Field label="Reason" name="narration" required />
        </FormModal>
      ) : null}

      {action?.kind === 'reverse-capital' ? (
        <FormModal
          title={`Reverse ${money(action.row.amount)} from ${action.row.contributor}`}
          submitLabel="Reverse it"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(`/api/funding/capital/${action.row.id}/reverse`, v),
              'Capital movement reversed',
            )
          }
        >
          <Field label="Reason" name="narration" required />
        </FormModal>
      ) : null}
    </>
  )
}
