import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ExportButtons, ErrorBanner, Kpi, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { fmt, humanise, money, today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi, useDebounced } from '../lib/useApi.js'

const TABS = [
  { key: 'trial', label: 'Trial balance' },
  { key: 'balance', label: 'Balance sheet' },
  { key: 'income', label: 'Income statement' },
  { key: 'ties', label: 'Reconciliation' },
  { key: 'journal', label: 'Journal' },
  { key: 'accounts', label: 'Chart of accounts' },
]

// Tabs that take a single date rather than a range.
const AS_OF_TABS = new Set(['balance', 'ties'])

const startOfYear = () => `${today().slice(0, 4)}-01-01`

/** The general ledger: trial balance, income statement, journal and chart of accounts. */
export default function Ledger() {
  const navigate = useNavigate()
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const { activeBranches, settings } = useOrg()

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
    balance: `/api/ledger/balance-sheet${qs({ as_of: end, branch_id: branchId })}`,
    income: `/api/ledger/income-statement${range}`,
    ties: `/api/ledger/reconciliation${qs({ as_of: end })}`,
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

  // The tabs answer with different shapes, and `data` still holds the previous
  // tab's response until the new one lands. Render on the shape, not on the tab, so
  // a tab switch can never read a field the old payload lacks.
  const shapes = {
    trial: (d) => Array.isArray(d?.rows) && d?.total_debit !== undefined,
    balance: (d) => Array.isArray(d?.assets) && d?.total_assets !== undefined,
    income: (d) => Array.isArray(d?.income),
    ties: (d) => Array.isArray(d?.rows) && d?.agrees !== undefined,
    journal: (d) => Array.isArray(d?.results),
    accounts: (d) => Array.isArray(d),
  }
  const ready = shapes[tab](data)

  async function accrueInterest() {
    setBusy(true)
    try {
      const result = await post(`/api/ledger/accrue-interest${qs({ as_of: end })}`)
      toast(
        `Interest accrued on ${result.loans_accrued} loan(s) to ${result.as_of}: ${money(result.interest_income)}`,
      )
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

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
            {!AS_OF_TABS.has(tab) ? (
              <label className="check">
                From&nbsp;
                <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
              </label>
            ) : null}
            <label className="check">
              {AS_OF_TABS.has(tab) ? 'As at' : 'To'}&nbsp;
              <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
            {activeBranches.length > 1 && tab !== 'ties' ? (
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
          <ExportButtons path={paths[tab]} name={tab} />
        ) : null}
        {can('accounting') && settings?.interest_method === 'effective' ? (
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={accrueInterest}
            title="Recognise interest at the effective rate for every instalment period ended by the date above"
          >
            {busy ? 'Working…' : 'Accrue interest'}
          </button>
        ) : null}
        {can('accounting') ? (
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

      {ready && tab === 'balance' ? (
        <>
          <div className="grid cols-3">
            <Kpi label="Total assets" value={money(data.total_assets)} sub={`as at ${data.as_of}`} />
            <Kpi
              label="Liabilities + equity"
              value={money(data.total_liabilities_and_equity)}
              sub={`${money(data.total_liabilities)} owed, ${money(data.total_equity)} equity`}
            />
            <Kpi
              label="Retained earnings"
              value={money(data.retained_earnings)}
              sub="income less expense since inception"
            />
          </div>
          {data.branch_id ? (
            <div className="banner">
              <strong>This is a branch sub-book, not the institution's balance sheet. </strong>
              Capital and funder borrowings carry no branch, so they are absent from it.
            </div>
          ) : null}
          {!data.balanced ? (
            <div className="banner" role="alert">
              <strong>Out by {money(data.difference)}. </strong>
              Assets should equal liabilities plus equity for any set of balanced entries, so a
              difference here means the journal itself is damaged. Check the Reconciliation tab.
            </div>
          ) : null}
          {[
            ['Assets', data.assets, 'what the institution owns and is owed'],
            ['Liabilities', data.liabilities, 'what it owes to savers and funders'],
            ['Equity', data.equity, "shareholders' capital and accumulated result"],
          ].map(([heading, rows, blurb]) => (
            <div className="card" key={heading}>
              <h3>{heading}</h3>
              <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>{blurb}</p>
              <DataTable
                caption={heading}
                rows={rows}
                rowKey={(r) => r.code}
                empty={`No ${heading.toLowerCase()} yet`}
                columns={[
                  { key: 'code', header: 'Code', render: (r) => r.code },
                  {
                    key: 'name',
                    header: 'Account',
                    render: (r) => (
                      <>
                        {r.name}
                        {r.code === '3000' ? (
                          <span className="muted"> · derived, income less expense to date</span>
                        ) : null}
                      </>
                    ),
                  },
                  {
                    key: 'balance',
                    header: 'Balance',
                    num: true,
                    render: (r) => (
                      <strong className={Number(r.balance) < 0 ? 'tag-danger' : undefined}>
                        {fmt(r.balance)}
                      </strong>
                    ),
                  },
                ]}
              />
            </div>
          ))}
          <p className="muted" style={{ fontSize: 12 }}>
            Retained earnings is derived rather than posted: nothing in this system writes a
            year-end closing entry to 3000, so without the derivation the surplus would simply be
            missing from equity. That also means a balanced sheet follows from balanced entries —
            it is arithmetic, not evidence. The Reconciliation tab holds the checks that can
            genuinely fail.
          </p>
        </>
      ) : null}

      {ready && tab === 'ties' ? (
        <>
          <div className="grid cols-3">
            <Kpi
              label="Sub-ledger ties"
              value={
                data.agrees ? (
                  <span className="tag-ok">All agree</span>
                ) : (
                  <span className="tag-danger">{data.breaks.length} break(s)</span>
                )
              }
              sub={`${data.rows.length} accounts checked as at ${data.as_of}`}
            />
            <Kpi
              label="Double entry"
              value={
                data.trial_balance_balanced ? (
                  <span className="tag-ok">Balanced</span>
                ) : (
                  <span className="tag-danger">Out of balance</span>
                )
              }
              sub="debits equal credits"
            />
            <Kpi
              label="Balance sheet"
              value={
                data.balance_sheet_balanced ? (
                  <span className="tag-ok">Adds up</span>
                ) : (
                  <span className="tag-danger">Out</span>
                )
              }
              sub="follows from the above"
            />
          </div>
          <p className="muted" style={{ fontSize: 12 }}>
            Each account below carries a balance that is claimed to equal something counted
            elsewhere. These are the checks worth reading before trusting a set of numbers: unlike
            a balanced trial balance, they can break — a migration, a hand-edit in SSMS or a
            service that moves a balance without posting would all show up here.
          </p>
          <DataTable
            caption="Ledger against the sub-ledgers"
            rows={data.rows}
            rowKey={(r) => r.code}
            columns={[
              { key: 'code', header: 'Code', render: (r) => r.code },
              { key: 'name', header: 'Account', render: (r) => r.name },
              { key: 'sub', header: 'Checked against', render: (r) => r.sub_ledger },
              { key: 'ledger', header: 'Ledger', num: true, render: (r) => fmt(r.ledger) },
              { key: 'book', header: 'Sub-ledger', num: true, render: (r) => fmt(r.book) },
              {
                key: 'agrees',
                header: 'Result',
                render: (r) =>
                  r.agrees ? (
                    <span className="tag-ok">Agrees</span>
                  ) : (
                    <span className="tag-danger">Out by {fmt(r.difference)}</span>
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
