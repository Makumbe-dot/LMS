import { useState } from 'react'

import CurrencySelect from '../components/CurrencySelect.jsx'
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
  Pager,
} from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { closedDrawerOptions, currencyOf, openDrawers } from '../lib/currency.js'
import { dateTime, fmt, money, num } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

/** "-20.00" -> a red "Short 20.00"; "5.00" -> "Over 5.00"; 0 -> a green "Balanced". */
export function Variance({ value }) {
  const n = num(value)
  if (n === null) return <span>-</span>
  if (n === 0) return <span className="tag-ok">Balanced</span>
  return n < 0 ? (
    <span className="tag-danger">Short {fmt(-n)}</span>
  ) : (
    <span className="tag-warn">Over {fmt(n)}</span>
  )
}

/** A drawer's currency: every amount on a till is in it. */
const drawerCurrency = (till) => till.currency_label || currencyOf(till.currency)

/** The count: what the drawer holds, against what the postings say it should. */
function CountForm({ till, busy, onClose, onSubmit }) {
  const [counted, setCounted] = useState('')
  const expected = num(till.position.expected_cash) ?? 0
  const difference = counted === '' ? null : Math.round((Number(counted) - expected) * 100) / 100
  const cur = drawerCurrency(till)

  return (
    <FormModal
      title={`Count and close ${till.session_no}`}
      submitLabel="Close the till"
      busy={busy}
      onClose={onClose}
      onSubmit={(v) => onSubmit({ counted_cash: v.counted_cash, note: v.note })}
    >
      <p className="muted" style={{ marginTop: 0 }}>
        Count the drawer before looking at the figure below, then enter what is there. A
        supervisor verifies the count; any difference is posted to the ledger when they do.
      </p>
      <Field
        label={`Cash counted (${cur})`}
        type="number"
        step="0.01"
        min="0"
        name="counted_cash"
        required
        value={counted}
        onChange={(e) => setCounted(e.target.value)}
      />
      {difference !== null ? (
        <KeyValues
          items={[
            ['The postings say', money(expected, cur)],
            ['Difference', <Variance key="v" value={difference} />],
          ]}
        />
      ) : null}
      <Field
        as="textarea"
        label="Note"
        name="note"
        rows={2}
        required={difference !== null && difference !== 0}
        hint={difference ? 'Required when the count is short or over: what was checked?' : undefined}
      />
    </FormModal>
  )
}

/** The open form: a float, and the currency of the drawer it goes in. */
function OpenForm({ drawers, busy, onClose, onSubmit }) {
  const [currency, setCurrency] = useState(null)
  return (
    <FormModal title="Open a till" submitLabel="Open the till" busy={busy} onClose={onClose} onSubmit={onSubmit}>
      <CurrencySelect
        label="Drawer currency"
        value={currency ?? ''}
        onChange={setCurrency}
        only={(options) => closedDrawerOptions(options, drawers)}
        hint="One drawer per currency. Cash on a loan or savings account is counted in the drawer of its own currency."
      />
      <Field
        label={`Opening float (${currencyOf(currency)})`}
        type="number"
        step="0.01"
        min="0"
        name="opening_float"
        required
        hint="The cash handed to you from the vault"
      />
    </FormModal>
  )
}

/** One open drawer: its float, what came in and went out, and what it should hold. */
function Drawer({ till, onCount }) {
  const cur = drawerCurrency(till)
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h3 style={{ margin: 0 }}>
          {cur} drawer · {till.session_no}
        </h3>
        <button type="button" className="btn primary" onClick={onCount}>
          Count and close
        </button>
      </div>
      <div className="grid cols-4">
        <Kpi label="Opening float" value={money(till.opening_float, cur)} sub={`opened ${dateTime(till.opened_at)}`} />
        <Kpi label="Cash in" value={money(till.position.cash_in, cur)} />
        <Kpi label="Cash out" value={money(till.position.cash_out, cur)} />
        <Kpi label="Should be in the drawer" value={<strong>{money(till.position.expected_cash, cur)}</strong>} />
      </div>
      <DataTable
        caption={`Cash movements in the ${cur} drawer`}
        rows={till.position.movements}
        rowKey={(r, index) => `${r.at}-${index}`}
        empty="Nothing in cash yet. Bank, mobile-money and payroll postings never touch the drawer."
        columns={[
          { key: 'at', header: 'When', render: (r) => dateTime(r.at) },
          { key: 'kind', header: 'What', render: (r) => r.kind },
          { key: 'ref', header: 'Loan / account', render: (r) => r.reference },
          { key: 'detail', header: 'Reference', render: (r) => r.detail || '-' },
          {
            key: 'amount',
            header: `Amount (${cur})`,
            num: true,
            render: (r) =>
              num(r.amount) < 0 ? <span className="tag-warn">{fmt(r.amount)}</span> : fmt(r.amount),
          },
        ]}
      />
    </div>
  )
}

/** One teller's cash drawers: open with a float, count at close, verified by someone else. */
export default function Till() {
  const { user, can } = useAuth()
  const { toast, toastError } = useToast()
  const mine = useApi('/api/tills/current')
  const [page, setPage] = useState(1)
  const history = useApi(`/api/tills${qs({ page })}`)
  const waiting = useApi('/api/tills?status=counted&page_size=100')
  const [action, setAction] = useState(null) // 'open' | {count: till} | {verify: till}
  const [busy, setBusy] = useState(false)

  const canSupervise = can('supervise')
  const drawers = openDrawers(mine.data)

  async function run(promise, message) {
    setBusy(true)
    try {
      await promise
      toast(message)
      setAction(null)
      mine.reload()
      history.reload()
      waiting.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  const toVerify = (waiting.data?.results || []).filter((t) => t.teller_id !== user?.id)

  return (
    <>
      <PageHeader
        title="Teller till"
        meta="A float in the morning, a count at close, and someone else to check it"
      >
        {!mine.loading || mine.data ? (
          <button type="button" className="btn primary" onClick={() => setAction('open')}>
            {drawers.length ? 'Open a drawer in another currency' : 'Open my till'}
          </button>
        ) : null}
        <ExportButtons path={'/api/tills'} name={'tills'} />
      </PageHeader>

      <ErrorBanner error={mine.error || history.error} onRetry={mine.reload} />
      {mine.loading && !mine.data ? <Loading what="Loading your till" /> : null}

      {drawers.map((till) => (
        <Drawer key={till.id} till={till} onCount={() => setAction({ count: till })} />
      ))}
      {!drawers.length && !mine.loading ? (
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>
            You have no till open. Open one with the float you were given before taking or paying
            out any cash.
          </p>
        </div>
      ) : null}

      {canSupervise ? (
        <div className="card">
          <h3>Awaiting verification</h3>
          <DataTable
            caption="Tills awaiting verification"
            rows={toVerify}
            empty="Nothing to verify. You cannot verify a till you counted yourself."
            columns={[
              { key: 'no', header: 'Till', render: (t) => t.session_no },
              { key: 'teller', header: 'Teller', render: (t) => t.teller_name },
              { key: 'date', header: 'Date', render: (t) => t.business_date },
              { key: 'currency', header: 'Currency', render: (t) => drawerCurrency(t) },
              { key: 'expected', header: 'Expected', num: true, render: (t) => fmt(t.expected_cash) },
              { key: 'counted', header: 'Counted', num: true, render: (t) => fmt(t.counted_cash) },
              { key: 'variance', header: 'Difference', render: (t) => <Variance value={t.variance} /> },
              { key: 'note', header: "Teller's note", render: (t) => t.close_note || '-' },
              {
                key: 'actions',
                header: '',
                render: (t) => (
                  <button type="button" className="btn small" onClick={() => setAction({ verify: t })}>
                    Verify
                  </button>
                ),
              },
            ]}
          />
        </div>
      ) : null}

      <div className="card">
        <h3>Recent tills</h3>
        <DataTable
          caption="Recent tills"
          rows={history.data?.results || []}
          empty="No tills yet"
          columns={[
            { key: 'no', header: 'Till', render: (t) => t.session_no },
            { key: 'teller', header: 'Teller', render: (t) => t.teller_name },
            { key: 'date', header: 'Date', render: (t) => t.business_date },
            { key: 'currency', header: 'Currency', render: (t) => drawerCurrency(t) },
            { key: 'float', header: 'Float', num: true, render: (t) => fmt(t.opening_float) },
            { key: 'expected', header: 'Expected', num: true, render: (t) => fmt(t.expected_cash) },
            { key: 'counted', header: 'Counted', num: true, render: (t) => fmt(t.counted_cash) },
            { key: 'variance', header: 'Difference', render: (t) => <Variance value={t.variance} /> },
            { key: 'status', header: 'Status', render: (t) => <Badge value={t.status} /> },
            { key: 'verified', header: 'Verified by', render: (t) => t.verified_by_name || '-' },
            { key: 'entry', header: 'Posted as', render: (t) => t.variance_entry_no || '-' },
          ]}
        />
        <Pager meta={history.data} onPage={setPage} noun="tills" />
      </div>

      {action === 'open' ? (
        <OpenForm
          drawers={drawers}
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post('/api/tills', v), 'Till open')}
        />
      ) : null}

      {action?.count ? (
        <CountForm
          till={action.count}
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(body) =>
            run(post(`/api/tills/${action.count.id}/count`, body), 'Till counted and closed')
          }
        />
      ) : null}

      {action?.verify ? (
        <FormModal
          title={`Verify ${action.verify.session_no}`}
          submitLabel="Verify the count"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(post(`/api/tills/${action.verify.id}/verify`, v), 'Till verified')
          }
        >
          <KeyValues
            items={[
              ['Teller', action.verify.teller_name],
              ['Expected', money(action.verify.expected_cash, drawerCurrency(action.verify))],
              ['Counted', money(action.verify.counted_cash, drawerCurrency(action.verify))],
              ['Difference', <Variance key="v" value={action.verify.variance} />],
              ["Teller's note", action.verify.close_note || '-'],
            ]}
          />
          {num(action.verify.variance) ? (
            <p className="muted">
              Verifying posts the difference to the ledger
              {action.verify.currency ? ", at today's rate" : ''}:{' '}
              {num(action.verify.variance) < 0
                ? 'Dr 6800 Cash shortages, Cr 1000 Cash and bank.'
                : 'Dr 1000 Cash and bank, Cr 4900 Other income.'}
            </p>
          ) : null}
          <Field as="textarea" label="Note" name="note" rows={2} />
        </FormModal>
      ) : null}
    </>
  )
}
