import { useState } from 'react'

import CurrencySelect from '../components/CurrencySelect.jsx'
import DataTable from '../components/DataTable.jsx'
import Modal, { FormModal } from '../components/Modal.jsx'
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
import { get, patch, post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { currencyOf, isForeign } from '../lib/currency.js'
import { fmt, getCurrency, humanise, money, today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { PeriodNotice, useMinPostingDate } from '../lib/periods.jsx'
import { useApi, useDebounced } from '../lib/useApi.js'

/**
 * The savings products, for whoever holds the setup right: each with its rate,
 * minimum balance, fee and currency. A product's currency is fixed once an account
 * is open under it; the server says so if it is changed.
 */
function SavingsProducts({ onClose, onChanged }) {
  const { toast, toastError } = useToast()
  const list = useApi('/api/savings/products?include_inactive=1')
  const [editing, setEditing] = useState(null) // null | {} (new) | a product
  const [busy, setBusy] = useState(false)

  async function save(v) {
    setBusy(true)
    const body = {
      name: v.name,
      description: v.description,
      currency: v.currency || '',
      interest_rate_pct_pa: v.interest_rate_pct_pa,
      min_balance: v.min_balance,
      monthly_fee: v.monthly_fee,
      allow_withdrawals: v.allow_withdrawals,
      is_active: v.is_active,
    }
    try {
      if (editing.id) await patch(`/api/savings/products/${editing.id}`, body)
      else await post('/api/savings/products', { ...body, code: v.code })
      toast(editing.id ? 'Product saved' : 'Product created')
      setEditing(null)
      list.reload()
      onChanged()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  if (editing) {
    const p = editing
    return (
      <FormModal
        title={p.id ? `Edit ${p.code}` : 'New savings product'}
        submitLabel={p.id ? 'Save' : 'Create'}
        busy={busy}
        onClose={() => setEditing(null)}
        onSubmit={save}
      >
        <div className="grid cols-2">
          <Field label="Code" name="code" required={!p.id} disabled={Boolean(p.id)} defaultValue={p.code || ''} />
          <Field label="Name" name="name" required defaultValue={p.name || ''} />
          <CurrencySelect
            defaultValue={p.currency || ''}
            hint="Deposits, withdrawals, interest and fees are in it. Fixed once an account is open."
          />
          <Field
            label="Interest (% a year)"
            type="number"
            step="0.001"
            min="0"
            name="interest_rate_pct_pa"
            defaultValue={p.interest_rate_pct_pa ?? 0}
          />
          <Field label="Minimum balance" type="number" step="0.01" min="0" name="min_balance" defaultValue={p.min_balance ?? 0} />
          <Field label="Monthly fee" type="number" step="0.01" min="0" name="monthly_fee" defaultValue={p.monthly_fee ?? 0} />
        </div>
        <Field label="Description" name="description" defaultValue={p.description || ''} />
        <label className="check">
          <input type="checkbox" name="allow_withdrawals" defaultChecked={p.allow_withdrawals ?? true} />
          &nbsp;Withdrawals allowed
        </label>
        <label className="check">
          <input type="checkbox" name="is_active" defaultChecked={p.is_active ?? true} />
          &nbsp;Active
        </label>
      </FormModal>
    )
  }

  return (
    <Modal
      title="Savings products"
      wide
      onClose={onClose}
      footer={
        <button type="button" className="btn small primary" onClick={() => setEditing({})}>
          New product
        </button>
      }
    >
      <ErrorBanner error={list.error} onRetry={list.reload} />
      <DataTable
        caption="Savings products"
        rows={list.data || []}
        onRowClick={(row) => setEditing(row)}
        empty="No savings products yet"
        columns={[
          { key: 'code', header: 'Code', render: (r) => r.code },
          { key: 'name', header: 'Name', render: (r) => r.name },
          { key: 'currency', header: 'Currency', render: (r) => currencyOf(r.currency) },
          { key: 'rate', header: '% a year', num: true, render: (r) => r.interest_rate_pct_pa },
          { key: 'min', header: 'Minimum', num: true, render: (r) => fmt(r.min_balance) },
          { key: 'fee', header: 'Monthly fee', num: true, render: (r) => fmt(r.monthly_fee) },
          { key: 'active', header: 'Active', render: (r) => (r.is_active ? 'Yes' : 'No') },
        ]}
      />
    </Modal>
  )
}

/** The savings book: accounts, counter movements and the monthly interest run. */
export default function Savings() {
  const { can } = useAuth()
  const { activeBranches } = useOrg()
  const minPostingDate = useMinPostingDate()
  const { toast, toastError } = useToast()

  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [branchId, setBranchId] = useState('')
  const [page, setPage] = useState(1)
  const [action, setAction] = useState(null) // { kind, account? }
  const [detail, setDetail] = useState(null)
  const [busy, setBusy] = useState(false)
  const [openProduct, setOpenProduct] = useState(null) // the product an account is being opened under
  const debounced = useDebounced(search)

  const path = `/api/savings/accounts${qs({
    q: debounced,
    status,
    branch_id: branchId,
    page,
    page_size: 50,
  })}`
  const { data, error, loading, reload } = useApi(path)
  const summary = useApi(`/api/savings/portfolio${qs({ branch_id: branchId })}`)
  const products = useApi('/api/savings/products')
  const borrowers = useApi(action?.kind === 'open' ? '/api/borrowers?page_size=1000' : null)

  const canCash = can('cash')
  const canAccount = can('accounting')

  const onFilter = (setter) => (event) => {
    setter(event.target.value)
    setPage(1)
  }

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

  async function openDetail(accountId) {
    try {
      setDetail(await get(`/api/savings/accounts/${accountId}`))
    } catch (err) {
      toastError(err)
    }
  }

  return (
    <>
      <PageHeader
        title="Savings"
        meta="Members' balances are money the institution owes them, not income"
      >
        {canCash ? (
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              setOpenProduct(null)
              setAction({ kind: 'open' })
            }}
          >
            Open an account
          </button>
        ) : null}
        {canAccount ? (
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() =>
              run(
                post('/api/savings/run-interest'),
                'Interest credited and monthly fees taken',
              )
            }
          >
            Run monthly interest
          </button>
        ) : null}
        {can('setup') ? (
          <button type="button" className="btn" onClick={() => setAction({ kind: 'products' })}>
            Savings products
          </button>
        ) : null}
        <ExportButtons path={path} name={'savings_accounts'} />
      </PageHeader>

      {summary.data ? (
        <div className="grid cols-4">
          <Kpi
            label="Total balance"
            value={money(summary.data.total_balance)}
            sub="owed to members; other currencies at their booked rates"
          />
          <Kpi
            label="Active accounts"
            value={summary.data.active_accounts}
            sub={`${summary.data.dormant_accounts} dormant`}
          />
          <Kpi label="All accounts" value={summary.data.accounts} sub={`${summary.data.closed_accounts} closed`} />
          <div className="kpi">
            <div className="label">By product</div>
            {summary.data.by_product.map((row) => (
              <div className="status-row" key={row.product}>
                <span>{row.product}</span>
                <b>{money(row.balance, row.currency)}</b>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="row" style={{ margin: '16px 0 12px' }}>
        <input
          type="search"
          placeholder="Search account no. or member"
          value={search}
          onChange={onFilter(setSearch)}
          aria-label="Search savings accounts"
          style={{ minWidth: 250 }}
        />
        <select value={status} onChange={onFilter(setStatus)} aria-label="Filter by status">
          <option value="">Any status</option>
          <option value="active">Active</option>
          <option value="dormant">Dormant</option>
          <option value="closed">Closed</option>
        </select>
        {activeBranches.length > 1 ? (
          <select value={branchId} onChange={onFilter(setBranchId)} aria-label="Filter by branch">
            <option value="">All branches</option>
            {activeBranches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        ) : null}
      </div>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading savings accounts" />
      ) : (
        <>
          <Pager meta={data} onPage={setPage} noun="accounts" />
          <DataTable
            caption="Savings accounts"
            rows={data?.results || []}
            onRowClick={(row) => openDetail(row.id)}
            empty="No savings accounts yet"
            columns={[
              { key: 'no', header: 'Account', render: (r) => r.account_no },
              { key: 'member', header: 'Member', render: (r) => r.borrower_name },
              { key: 'product', header: 'Product', render: (r) => r.product_name },
              { key: 'branch', header: 'Branch', render: (r) => r.branch_name || '-' },
              { key: 'currency', header: 'Currency', render: (r) => currencyOf(r.currency) },
              { key: 'balance', header: 'Balance', num: true, render: (r) => <strong>{fmt(r.balance)}</strong> },
              {
                key: 'available',
                header: 'Available',
                num: true,
                render: (r) => fmt(r.available_balance),
              },
              { key: 'status', header: 'Status', render: (r) => <Badge value={r.status} /> },
              { key: 'opened', header: 'Opened', render: (r) => r.opened_on },
            ]}
          />
        </>
      )}

      {action?.kind === 'products' ? (
        <SavingsProducts onClose={() => setAction(null)} onChanged={products.reload} />
      ) : null}

      {action?.kind === 'open' ? (
        <FormModal
          title="Open a savings account"
          submitLabel="Open account"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post('/api/savings/accounts', {
                borrower_id: Number(v.borrower_id),
                product_id: Number(v.product_id),
                opening_deposit: v.opening_deposit,
                opened_on: v.opened_on,
                method: v.method,
                reference: v.reference,
              }),
              'Savings account opened',
            )
          }
        >
          <Field as="select" label="Member" name="borrower_id" required>
            <option value="">Select a member</option>
            {(borrowers.data?.results || []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.borrower_no} - {b.first_name} {b.last_name}
              </option>
            ))}
          </Field>
          <Field
            as="select"
            label="Product"
            name="product_id"
            required
            onChange={(e) => setOpenProduct(e.target.value)}
          >
            {(products.data || []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {p.interest_rate_pct_pa}% a year
                {p.allow_withdrawals ? '' : ', no withdrawals'}
                {isForeign(p.currency) ? `, in ${currencyOf(p.currency)}` : ''}
              </option>
            ))}
          </Field>
          <div className="grid cols-2">
            <Field
              label={`Opening deposit (${currencyOf(
                (products.data || []).find((p) => String(p.id) === String(openProduct))?.currency ??
                  products.data?.[0]?.currency,
              )})`}
              type="number"
              step="0.01"
              min="0"
              name="opening_deposit"
            />
            {/* An opening deposit writes a movement dated that day, so the
                posting window applies. */}
            <Field
              label="Opened on"
              type="date"
              name="opened_on"
              min={minPostingDate}
              defaultValue={today()}
            />
            <Field as="select" label="Method" name="method" defaultValue="cash">
              <option value="cash">Cash</option>
              <option value="bank_transfer">Bank transfer</option>
              <option value="mobile_money">Mobile money</option>
            </Field>
            <Field label="Reference" name="reference" />
          </div>
        </FormModal>
      ) : null}

      {action?.kind === 'deposit' || action?.kind === 'withdraw' ? (
        <FormModal
          title={
            action.kind === 'deposit'
              ? `Deposit to ${action.account.account_no}`
              : `Withdraw from ${action.account.account_no}`
          }
          submitLabel={action.kind === 'deposit' ? 'Take deposit' : 'Pay out'}
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(`/api/savings/accounts/${action.account.id}/${action.kind}`, v),
              action.kind === 'deposit' ? 'Deposit recorded' : 'Withdrawal paid out',
            )
          }
        >
          {action.kind === 'withdraw' ? (
            <p className="muted" style={{ marginTop: 0 }}>
              Available to draw: <strong>{money(action.account.available_balance, currencyOf(action.account.currency))}</strong>{' '}
              (balance {money(action.account.balance, currencyOf(action.account.currency))}, minimum balance{' '}
              {money(action.account.min_balance, currencyOf(action.account.currency))}).
            </p>
          ) : null}
          <PeriodNotice date={today()} />
          <div className="grid cols-2">
            <Field
              label={`Amount (${currencyOf(action.account.currency)})`}
              type="number"
              step="0.01"
              min="0.01"
              max={action.kind === 'withdraw' ? action.account.available_balance : undefined}
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
            <Field as="select" label="Method" name="method" defaultValue="cash">
              <option value="cash">Cash</option>
              <option value="bank_transfer">Bank transfer</option>
              <option value="mobile_money">Mobile money</option>
            </Field>
            <Field label="Reference" name="reference" />
          </div>
          <Field label="Narration" name="narration" />
        </FormModal>
      ) : null}

      {detail ? (
        <Modal
          title={`${detail.account_no} — ${detail.borrower_name}`}
          wide
          onClose={() => setDetail(null)}
          footer={
            canCash && detail.status === 'active' ? (
              <>
                <button
                  type="button"
                  className="btn small primary"
                  onClick={() => setAction({ kind: 'deposit', account: detail })}
                >
                  Deposit
                </button>
                {detail.allow_withdrawals ? (
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => setAction({ kind: 'withdraw', account: detail })}
                  >
                    Withdraw
                  </button>
                ) : null}
                {canAccount ? (
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => {
                      if (window.confirm('Pay out the balance and close this account?')) {
                        run(
                          post(`/api/savings/accounts/${detail.id}/close`, {
                            narration: 'Account closed',
                          }),
                          'Account closed',
                        ).then(() => setDetail(null))
                      }
                    }}
                  >
                    Close account
                  </button>
                ) : null}
              </>
            ) : null
          }
        >
          <KeyValues
            items={[
              ['Product', detail.product_name],
              [
                'Currency',
                isForeign(detail.currency)
                  ? `${currencyOf(detail.currency)}, carried at ${detail.fx_rate} ${getCurrency()}`
                  : currencyOf(detail.currency),
              ],
              ['Status', <Badge key="s" value={detail.status} />],
              ['Balance', <strong key="b">{money(detail.balance, currencyOf(detail.currency))}</strong>],
              ['Available to draw', money(detail.available_balance, currencyOf(detail.currency))],
              ['Minimum balance', money(detail.min_balance, currencyOf(detail.currency))],
              [
                'Withdrawals',
                detail.allow_withdrawals ? 'Allowed' : 'Locked until the account is closed',
              ],
              ['Opened', detail.opened_on],
              ['Last interest', detail.last_interest_date || 'Not yet credited'],
              ['Branch', detail.branch_name || '-'],
            ]}
          />
          <div className="row" style={{ justifyContent: 'space-between', marginTop: 16 }}>
            <h3 style={{ margin: 0 }}>Statement</h3>
            <ExportButtons
              small
              path={`/api/savings/accounts/${detail.id}/statement`}
              name={`statement_${detail.account_no}`}
              formats={['pdf', 'xlsx']}
            />
          </div>
          <DataTable
            caption="Savings statement"
            rows={[...detail.transactions].reverse()}
            empty="No movements yet"
            columns={[
              { key: 'date', header: 'Date', render: (t) => t.txn_date },
              { key: 'type', header: 'Type', render: (t) => humanise(t.txn_type) },
              { key: 'narration', header: 'Narration', render: (t) => t.narration || '' },
              { key: 'ref', header: 'Ref', render: (t) => t.reference || '-' },
              {
                key: 'in',
                header: 'In',
                num: true,
                render: (t) =>
                  ['deposit', 'interest'].includes(t.txn_type) ? fmt(t.amount) : '',
              },
              {
                key: 'out',
                header: 'Out',
                num: true,
                render: (t) =>
                  ['withdrawal', 'fee'].includes(t.txn_type) ? fmt(t.amount) : '',
              },
              {
                key: 'balance',
                header: 'Balance',
                num: true,
                render: (t) => fmt(t.balance_after),
              },
              {
                key: 'actions',
                header: '',
                render: (t) =>
                  t.reversed ? (
                    <span className="tag-danger">Reversed</span>
                  ) : canCash && ['deposit', 'withdrawal'].includes(t.txn_type) ? (
                    <button
                      type="button"
                      className="btn small"
                      onClick={() => {
                        const reason = window.prompt('Why is this being reversed?')
                        if (reason) {
                          run(
                            post(
                              `/api/savings/accounts/${detail.id}/transactions/${t.id}/reverse`,
                              { narration: reason },
                            ),
                            'Movement reversed',
                          )
                        }
                      }}
                    >
                      Reverse
                    </button>
                  ) : null,
              },
            ]}
          />
        </Modal>
      ) : null}
    </>
  )
}
