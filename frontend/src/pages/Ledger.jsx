import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Kpi, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { downloadCsv, post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { fmt, humanise, money, today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi, useDebounced } from '../lib/useApi.js'

const TABS = [
  { key: 'trial', label: 'Trial balance' },
  { key: 'income', label: 'Income statement' },
  { key: 'journal', label: 'Journal' },
  { key: 'accounts', label: 'Chart of accounts' },
]

const startOfYear = () => `${today().slice(0, 4)}-01-01`

/** The general ledger: trial balance, income statement, journal and chart of accounts. */
export default function Ledger() {
  const navigate = useNavigate()
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const { activeBranches } = useOrg()

  const [tab, setTab] = useState('trial')
  const [start, setStart] = useState(startOfYear())
  const [end, setEnd] = useState(today())
  const [branchId, setBranchId] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [busy, setBusy] = useState(false)
  const debounced = useDebounced(search)

  const range = qs({ start, end, branch_id: branchId })
  const paths = {
    trial: `/api/ledger/trial-balance${range}`,
    income: `/api/ledger/income-statement${range}`,
    journal: `/api/ledger/journal${qs({
      start,
      end,
      branch_id: branchId,
      q: debounced,
      page,
      page_size: 25,
    })}`,
    accounts: '/api/ledger/accounts',
  }
  const { data, error, loading, reload } = useApi(paths[tab])

  // The four tabs answer with four different shapes, and `data` still holds the
  // previous tab's response until the new one lands. Render on the shape, not on
  // the tab, so a tab switch can never read a field the old payload lacks.
  const shapes = {
    trial: (d) => Array.isArray(d?.rows) && d?.total_debit !== undefined,
    income: (d) => Array.isArray(d?.income),
    journal: (d) => Array.isArray(d?.results),
    accounts: (d) => Array.isArray(d),
  }
  const ready = shapes[tab](data)

  async function rebuild() {
    setBusy(true)
    try {
      const result = await post('/api/ledger/rebuild')
      toast(
        `${result.accounts_created} account(s) created, ${result.posted} entry/entries posted`,
      )
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="General ledger"
        meta="Every money movement in the loan book raises one balanced double-entry posting"
      >
        {tab !== 'accounts' ? (
          <>
            <label className="check">
              From&nbsp;
              <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label className="check">
              To&nbsp;
              <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
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
          </>
        ) : null}
        <button type="button" className="btn" onClick={reload}>
          Refresh
        </button>
        {tab !== 'accounts' ? (
          <button
            type="button"
            className="btn"
            onClick={() => downloadCsv(paths[tab], tab).catch(toastError)}
          >
            Export CSV
          </button>
        ) : null}
        {can('admin') ? (
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={rebuild}
            title="Create any missing account and post entries for transactions that have none"
          >
            {busy ? 'Rebuilding…' : 'Rebuild'}
          </button>
        ) : null}
      </PageHeader>

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
      {!ready && !error ? <Loading what="Loading the ledger" /> : null}

      {ready && tab === 'trial' ? (
        <>
          <div className="grid cols-3">
            <Kpi label="Total debits" value={money(data.total_debit)} />
            <Kpi label="Total credits" value={money(data.total_credit)} />
            <Kpi
              label="Double entry"
              value={
                data.balanced ? (
                  <span className="tag-ok">Balanced</span>
                ) : (
                  <span className="tag-danger">Out of balance</span>
                )
              }
              sub={data.balanced ? 'debits equal credits' : 'investigate before reporting'}
            />
          </div>
          <DataTable
            caption="Trial balance"
            rows={data.rows}
            rowKey={(r) => r.code}
            empty="Nothing posted in this period"
            columns={[
              { key: 'code', header: 'Code', render: (r) => r.code },
              { key: 'name', header: 'Account', render: (r) => r.name },
              { key: 'type', header: 'Type', render: (r) => humanise(r.type) },
              { key: 'debit', header: 'Debits', num: true, render: (r) => fmt(r.debit) },
              { key: 'credit', header: 'Credits', num: true, render: (r) => fmt(r.credit) },
              {
                key: 'balance',
                header: 'Balance',
                num: true,
                render: (r) => (
                  <strong>
                    {fmt(r.balance)} {r.side === 'debit' ? 'Dr' : 'Cr'}
                  </strong>
                ),
              },
            ]}
          />
        </>
      ) : null}

      {ready && tab === 'income' ? (
        <>
          <div className="grid cols-3">
            <Kpi label="Income" value={money(data.total_income)} sub="interest, fees, penalties" />
            <Kpi label="Expense" value={money(data.total_expense)} sub="write-offs, impairment" />
            <Kpi
              label="Surplus"
              value={money(data.surplus)}
              sub={`${data.start || 'start'} to ${data.end || 'today'}`}
            />
          </div>
          <div className="card">
            <h3>Income</h3>
            <DataTable
              caption="Income"
              rows={data.income}
              rowKey={(r) => r.code}
              empty="No income in this period"
              columns={[
                { key: 'code', header: 'Code', render: (r) => r.code },
                { key: 'name', header: 'Account', render: (r) => r.name },
                { key: 'balance', header: 'Amount', num: true, render: (r) => fmt(r.balance) },
              ]}
            />
          </div>
          <div className="card">
            <h3>Expense</h3>
            <DataTable
              caption="Expense"
              rows={data.expense}
              rowKey={(r) => r.code}
              empty="No expense in this period"
              columns={[
                { key: 'code', header: 'Code', render: (r) => r.code },
                { key: 'name', header: 'Account', render: (r) => r.name },
                { key: 'balance', header: 'Amount', num: true, render: (r) => fmt(r.balance) },
              ]}
            />
          </div>
        </>
      ) : null}

      {ready && tab === 'journal' ? (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <input
              type="search"
              placeholder="Search entry no., narration or loan"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value)
                setPage(1)
              }}
              aria-label="Search the journal"
              style={{ minWidth: 280 }}
            />
          </div>
          <Pager meta={data} onPage={setPage} noun="entries" />
          {data.results.length === 0 ? (
            <p className="muted">Nothing posted in this period.</p>
          ) : (
            data.results.map((entry) => (
              <div className="card" key={entry.id}>
                <div className="row between" style={{ marginBottom: 8 }}>
                  <div>
                    <strong>{entry.entry_no}</strong>{' '}
                    <span className="muted">
                      {entry.entry_date} · {humanise(entry.source)}
                      {entry.loan_no ? ' · ' : ''}
                    </span>
                    {entry.loan_no ? (
                      <button
                        type="button"
                        className="btn-link"
                        onClick={() => navigate(`/loans/${entry.loan_id}`)}
                      >
                        {entry.loan_no}
                      </button>
                    ) : null}
                  </div>
                  <span className="muted">
                    {entry.branch_name || '-'}
                    {entry.posted_by_name ? ` · ${entry.posted_by_name}` : ''}
                  </span>
                </div>
                <p className="muted" style={{ marginTop: 0 }}>{entry.narration}</p>
                <DataTable
                  caption={`Lines for ${entry.entry_no}`}
                  rows={entry.lines}
                  empty="No lines"
                  columns={[
                    { key: 'code', header: 'Account', render: (l) => `${l.account_code} ${l.account_name}` },
                    { key: 'desc', header: 'Description', render: (l) => l.description || '' },
                    {
                      key: 'debit',
                      header: 'Debit',
                      num: true,
                      render: (l) => (Number(l.debit) ? fmt(l.debit) : ''),
                    },
                    {
                      key: 'credit',
                      header: 'Credit',
                      num: true,
                      render: (l) => (Number(l.credit) ? fmt(l.credit) : ''),
                    },
                  ]}
                />
              </div>
            ))
          )}
        </>
      ) : null}

      {ready && tab === 'accounts' ? (
        <DataTable
          caption="Chart of accounts"
          rows={data}
          empty="No accounts yet — use Rebuild to create the defaults"
          columns={[
            { key: 'code', header: 'Code', render: (r) => r.code },
            { key: 'name', header: 'Name', render: (r) => r.name },
            { key: 'type', header: 'Type', render: (r) => humanise(r.type) },
            { key: 'description', header: 'What it holds', render: (r) => r.description || '' },
            {
              key: 'active',
              header: 'Active',
              render: (r) =>
                r.is_active ? (
                  <span className="tag-ok">Yes</span>
                ) : (
                  <span className="tag-danger">No</span>
                ),
            },
          ]}
        />
      ) : null}
    </>
  )
}
