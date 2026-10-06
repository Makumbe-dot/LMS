import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Field, KeyValues, Kpi, Loading, PageHeader } from '../components/ui.jsx'
import { del, get, post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime, fmt, money, num, today } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const rate6 = (value) => (num(value) === null ? '-' : Number(value).toFixed(6))

/** A movement, said in words as well as colour. */
function Movement({ value, currency }) {
  const n = num(value) ?? 0
  if (n === 0) return <span className="muted">nothing to post</span>
  return (
    <span className={n > 0 ? 'tag-ok' : 'tag-danger'}>
      {n > 0 ? '+' : ''}
      {money(value, currency)} {n > 0 ? 'unrealised gain' : 'unrealised loss'}
    </span>
  )
}

/**
 * The exchange-rate table and the month-end revaluation of foreign-currency
 * loans. The ledger is kept in the organisation's currency; a loan in another
 * is carried at its booked rate until a run here moves it to the closing rate.
 */
export default function Currencies() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const overview = useApi('/api/currencies')
  const rates = useApi('/api/currencies/rates?page_size=100')
  const runs = useApi('/api/currencies/revaluations?page_size=12')
  const [adding, setAdding] = useState(false)
  const [asOf, setAsOf] = useState(today())
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const isAdmin = can('admin')

  const base = overview.data?.base_currency

  async function addRate(values) {
    setBusy(true)
    try {
      await post('/api/currencies/rates', values)
      setAdding(false)
      rates.reload()
      overview.reload()
      toast(`${values.code.toUpperCase()} rate saved`)
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function removeRate(row) {
    if (!window.confirm(`Delete the ${row.code} rate of ${row.rate_date}?`)) return
    try {
      await del(`/api/currencies/rates/${row.id}`)
      rates.reload()
      overview.reload()
    } catch (err) {
      toastError(err)
    }
  }

  async function loadPreview() {
    setBusy(true)
    try {
      setPreview(await get(`/api/currencies/revaluations/preview${qs({ as_of: asOf })}`))
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function postRevaluation() {
    if (!window.confirm(`Restate every open foreign-currency loan at the rates for ${asOf}?`)) return
    setBusy(true)
    try {
      const run = await post('/api/currencies/revaluations', { as_of: asOf })
      toast(`${run.run_no}: ${run.loans_revalued} loans restated, movement ${money(run.movement)}`)
      setPreview(null)
      runs.reload()
      overview.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  if (overview.loading && !overview.data) return <Loading what="Loading currencies" />
  if (overview.error) return <ErrorBanner error={overview.error} onRetry={overview.reload} />
  if (!overview.data) return null

  const others = overview.data.currencies || []

  return (
    <>
      <PageHeader
        title="Currencies"
        meta={`The ledger is kept in ${base}. A loan in another currency is carried at its booked rate until a revaluation moves it to the closing rate.`}
      >
        {isAdmin ? (
          <button type="button" className="btn primary" onClick={() => setAdding(true)}>
            Add a rate
          </button>
        ) : null}
      </PageHeader>

      <div className="grid cols-4">
        <Kpi icon="coins" tone="brand" label="Base currency" value={base} sub="ledger, savings and tills" />
        {others.map((c) => (
          <Kpi
            key={c.code}
            icon="percent"
            tone={c.rate ? 'slate' : 'amber'}
            label={`${c.code} rate`}
            value={c.rate ? rate6(c.rate) : 'No rate'}
            sub={
              c.rate
                ? `${base} per ${c.code}, from ${c.rate_date} · ${c.open_loans} open loan${c.open_loans === 1 ? '' : 's'}`
                : 'add one before a loan is quoted'
            }
          />
        ))}
      </div>
      {others.length === 0 ? (
        <p className="muted">
          Every product lends in {base}. Give a product another currency on the Products page once a
          rate for it is here.
        </p>
      ) : null}

      <div className="grid cols-2" style={{ alignItems: 'start' }}>
        <div className="card">
          <h3>Exchange rates</h3>
          <p className="chart-sub">
            {base} per one unit of the currency. The rate for a date is the latest on or before it.
          </p>
          {rates.loading && !rates.data ? (
            <Loading what="Loading rates" />
          ) : (
            <DataTable
              caption="Exchange rates"
              rows={rates.data?.results || rates.data?.rows || []}
              empty="No rates yet"
              columns={[
                { key: 'code', header: 'Currency', render: (r) => r.code },
                { key: 'date', header: 'From', render: (r) => r.rate_date },
                { key: 'rate', header: `${base} per 1`, num: true, render: (r) => rate6(r.rate) },
                { key: 'note', header: 'Note', render: (r) => r.note || '-' },
                { key: 'by', header: 'Set by', render: (r) => r.set_by_name || '-' },
                {
                  key: 'actions',
                  header: '',
                  render: (r) =>
                    isAdmin ? (
                      <button type="button" className="btn small" onClick={() => removeRate(r)}>
                        Delete
                      </button>
                    ) : null,
                },
              ]}
            />
          )}
        </div>

        <div className="card">
          <h3>Month-end revaluation</h3>
          <p className="chart-sub">
            Restates every open foreign-currency loan at the closing rate; the difference goes to
            4800 Exchange differences. Run it after the month's closing rates are entered.
          </p>
          <div className="row" style={{ marginBottom: 12 }}>
            <label className="field inline">
              <span className="label-text">As at</span>
              <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
            </label>
            <button type="button" className="btn" onClick={loadPreview} disabled={busy}>
              Preview
            </button>
            {isAdmin && preview && preview.lines.length > 0 && !preview.missing_rates.length ? (
              <button type="button" className="btn primary" onClick={postRevaluation} disabled={busy}>
                Post revaluation
              </button>
            ) : null}
          </div>
          {preview ? (
            <>
              {preview.missing_rates.length ? (
                <p className="banner">
                  No rate on or before {preview.as_of} for {preview.missing_rates.join(', ')}.
                </p>
              ) : null}
              <KeyValues
                items={[
                  ['Loans to restate', preview.loans],
                  ['Movement', <Movement key="m" value={preview.movement} currency={base} />],
                ]}
              />
              {preview.lines.length ? (
                <DataTable
                  caption="Revaluation preview"
                  rows={preview.lines}
                  rowKey={(r) => r.loan_id}
                  columns={[
                    { key: 'loan', header: 'Loan', render: (r) => r.loan_no },
                    { key: 'borrower', header: 'Borrower', render: (r) => r.borrower },
                    {
                      key: 'outstanding',
                      header: 'Outstanding',
                      num: true,
                      render: (r) =>
                        `${r.currency} ${fmt(num(r.principal_outstanding) + num(r.penalties_outstanding) + num(r.charges_outstanding))}`,
                    },
                    { key: 'old', header: 'Booked at', num: true, render: (r) => rate6(r.old_rate) },
                    { key: 'new', header: 'Closing', num: true, render: (r) => rate6(r.new_rate) },
                    { key: 'move', header: `Movement (${base})`, num: true, render: (r) => fmt(r.movement) },
                  ]}
                />
              ) : (
                <p className="muted">No open foreign-currency loans.</p>
              )}
            </>
          ) : null}
        </div>
      </div>

      <div className="card">
        <h3>Revaluation runs</h3>
        {runs.loading && !runs.data ? (
          <Loading what="Loading runs" />
        ) : (
          <DataTable
            caption="Revaluation runs"
            rows={runs.data?.results || runs.data?.rows || []}
            empty="No revaluation has been run yet"
            columns={[
              { key: 'run', header: 'Run', render: (r) => r.run_no },
              { key: 'as_of', header: 'As at', render: (r) => r.as_of },
              { key: 'loans', header: 'Loans', num: true, render: (r) => r.loans_revalued },
              {
                key: 'movement',
                header: 'Movement',
                num: true,
                render: (r) => <Movement value={r.movement} currency={r.base_currency} />,
              },
              { key: 'entry', header: 'Entry', render: (r) => r.entry_no || '-' },
              { key: 'by', header: 'Run by', render: (r) => r.run_by_name || '-' },
              { key: 'when', header: 'When', render: (r) => dateTime(r.created_at) },
            ]}
          />
        )}
      </div>

      {adding ? (
        <FormModal
          title="Add an exchange rate"
          submitLabel="Save rate"
          busy={busy}
          onClose={() => setAdding(false)}
          onSubmit={addRate}
        >
          <div className="grid cols-3">
            <Field label="Currency" name="code" required maxLength={8} placeholder="ZWG" />
            <Field label="From" name="rate_date" type="date" required defaultValue={today()} />
            <Field
              label={`${base} per 1`}
              name="rate"
              type="number"
              step="0.000001"
              min="0"
              required
              hint={`How much ${base} one unit of the currency is worth`}
            />
          </div>
          <Field label="Note" name="note" placeholder="Reserve Bank mid-rate" />
        </FormModal>
      ) : null}
    </>
  )
}
