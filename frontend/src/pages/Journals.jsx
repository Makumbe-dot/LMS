import { useMemo, useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import Modal, { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Badge, ErrorBanner, Field, KeyValues, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { del, downloadCsv, post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateOnly, dateTime, fmt, getCurrency, money, num, today } from '../lib/format.js'
import { PeriodNotice, useMinPostingDate } from '../lib/periods.jsx'
import { useApi } from '../lib/useApi.js'

const TABS = [
  { key: 'draft', label: 'Awaiting approval' },
  { key: 'posted', label: 'Posted' },
  { key: '', label: 'All' },
]

const blankLine = () => ({ account_id: '', debit: '', credit: '', description: '' })

/** Accounts a journal may use: active, and not reconciled against a sub-ledger. */
function useJournalAccounts() {
  const accounts = useApi('/api/ledger/accounts')
  const usable = useMemo(
    () => (accounts.data || []).filter((a) => a.is_active && !a.controlled_by),
    [accounts.data],
  )
  return { ...accounts, usable }
}

/**
 * A full journal: any number of lines, each a debit or a credit. The totals are
 * shown as the lines are typed, so an unbalanced journal is seen before it is
 * refused.
 */
function JournalForm({ accounts, busy, onClose, onSubmit }) {
  const minDate = useMinPostingDate()
  const [entryDate, setEntryDate] = useState(today())
  const [narration, setNarration] = useState('')
  const [reference, setReference] = useState('')
  const [lines, setLines] = useState([blankLine(), blankLine()])

  const debits = lines.reduce((sum, l) => sum + (num(l.debit) ?? 0), 0)
  const credits = lines.reduce((sum, l) => sum + (num(l.credit) ?? 0), 0)
  const difference = Math.round((debits - credits) * 100) / 100
  const balanced = debits > 0 && difference === 0

  const setLine = (index, key) => (event) =>
    setLines((current) =>
      current.map((line, i) => (i === index ? { ...line, [key]: event.target.value } : line)),
    )

  return (
    <Modal title="New journal" onClose={onClose} wide>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit({
            entry_date: entryDate,
            narration,
            reference: reference || null,
            lines: lines
              .filter((l) => l.account_id)
              .map((l) => ({
                account_id: Number(l.account_id),
                debit: l.debit || null,
                credit: l.credit || null,
                description: l.description || null,
              })),
          })
        }}
      >
        <div className="grid cols-3">
          <Field
            label="Date"
            type="date"
            required
            min={minDate}
            value={entryDate}
            onChange={(e) => setEntryDate(e.target.value)}
          />
          <Field
            label="Reference"
            value={reference}
            onChange={(e) => setReference(e.target.value)}
            hint="Invoice, receipt or payslip number"
          />
        </div>
        <PeriodNotice date={entryDate} />
        <Field
          label="What it is for"
          required
          value={narration}
          onChange={(e) => setNarration(e.target.value)}
        />

        <div className="table-wrap" style={{ marginBottom: 10 }}>
          <table>
            <caption className="sr-only">Journal lines</caption>
            <thead>
              <tr>
                <th scope="col">Account</th>
                <th scope="col" className="num">
                  Debit
                </th>
                <th scope="col" className="num">
                  Credit
                </th>
                <th scope="col">Line description</th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line, index) => (
                <tr key={index}>
                  <td>
                    <select
                      aria-label={`Account on line ${index + 1}`}
                      value={line.account_id}
                      onChange={setLine(index, 'account_id')}
                    >
                      <option value="">Choose an account</option>
                      {accounts.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.code} {a.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="num">
                    <input
                      aria-label={`Debit on line ${index + 1}`}
                      type="number"
                      step="0.01"
                      min="0"
                      value={line.debit}
                      disabled={Boolean(line.credit)}
                      onChange={setLine(index, 'debit')}
                    />
                  </td>
                  <td className="num">
                    <input
                      aria-label={`Credit on line ${index + 1}`}
                      type="number"
                      step="0.01"
                      min="0"
                      value={line.credit}
                      disabled={Boolean(line.debit)}
                      onChange={setLine(index, 'credit')}
                    />
                  </td>
                  <td>
                    <input
                      aria-label={`Description on line ${index + 1}`}
                      value={line.description}
                      onChange={setLine(index, 'description')}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn small"
                      disabled={lines.length <= 2}
                      onClick={() => setLines((current) => current.filter((_, i) => i !== index))}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
              <tr>
                <td>
                  <strong>Totals</strong>
                </td>
                <td className="num">
                  <strong>{fmt(debits)}</strong>
                </td>
                <td className="num">
                  <strong>{fmt(credits)}</strong>
                </td>
                <td colSpan={2}>
                  {balanced ? (
                    <span className="tag-ok">Balanced</span>
                  ) : debits || credits ? (
                    <span className="tag-danger">Out by {fmt(Math.abs(difference))}</span>
                  ) : null}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="row">
          <button
            type="button"
            className="btn small"
            onClick={() => setLines((current) => [...current, blankLine()])}
          >
            Add a line
          </button>
        </div>
        <p className="muted" style={{ fontSize: 12 }}>
          Accounts that are reconciled against their own records — loans, savings, the provision,
          funding and capital — are not offered here. Post those through the loan, the account or
          the Funding page, so the books and the ledger cannot drift apart.
        </p>
        <div className="row" style={{ marginTop: 8 }}>
          <button type="submit" className="btn primary" disabled={busy || !balanced}>
            {busy ? 'Working…' : 'Send for approval'}
          </button>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** The everyday case: something was paid for. Builds a two-line journal. */
function ExpenseForm({ accounts, busy, onClose, onSubmit }) {
  const minDate = useMinPostingDate()
  const [entryDate, setEntryDate] = useState(today())
  const expenses = accounts.filter((a) => a.type === 'expense')
  const paidFrom = accounts.filter((a) => ['1000', '2900'].includes(a.code))

  return (
    <FormModal
      title="Record an expense"
      submitLabel="Send for approval"
      busy={busy}
      onClose={onClose}
      onSubmit={(v) =>
        onSubmit({
          entry_date: v.entry_date,
          narration: v.narration,
          reference: v.reference,
          lines: [
            { account_id: Number(v.expense_account), debit: v.amount, description: v.narration },
            { account_id: Number(v.paid_from), credit: v.amount, description: v.narration },
          ],
        })
      }
    >
      <div className="grid cols-2">
        <Field
          label="Date"
          type="date"
          name="entry_date"
          required
          min={minDate}
          value={entryDate}
          onChange={(e) => setEntryDate(e.target.value)}
        />
        <Field
          label={`Amount (${getCurrency()})`}
          type="number"
          step="0.01"
          min="0.01"
          name="amount"
          required
        />
        <Field as="select" label="What it was" name="expense_account" required defaultValue="">
          <option value="" disabled>
            Choose an expense
          </option>
          {expenses.map((a) => (
            <option key={a.id} value={a.id}>
              {a.code} {a.name}
            </option>
          ))}
        </Field>
        <Field
          as="select"
          label="Paid from"
          name="paid_from"
          required
          defaultValue={paidFrom.find((a) => a.code === '1000')?.id ?? ''}
          hint="Not yet paid? Choose accruals, and pay it with a journal later."
        >
          {paidFrom.map((a) => (
            <option key={a.id} value={a.id}>
              {a.code} {a.name}
            </option>
          ))}
        </Field>
      </div>
      <PeriodNotice date={entryDate} />
      <Field label="Paid to / what for" name="narration" required />
      <Field label="Reference" name="reference" hint="Invoice or receipt number" />
    </FormModal>
  )
}

function JournalDetail({ journal, canPost, canWithdraw, busy, onClose, onAction }) {
  const [asking, setAsking] = useState(null) // 'reject' | 'reverse'

  if (asking) {
    return (
      <FormModal
        title={asking === 'reject' ? `Reject ${journal.journal_no}` : `Reverse ${journal.journal_no}`}
        submitLabel={asking === 'reject' ? 'Reject' : 'Reverse'}
        busy={busy}
        onClose={() => setAsking(null)}
        onSubmit={async (v) => {
          if (await onAction(asking, v)) setAsking(null)
        }}
      >
        <p className="muted" style={{ marginTop: 0 }}>
          {asking === 'reject'
            ? 'The preparer sees the reason. A rejected journal never reaches the ledger.'
            : 'A mirror entry is posted today, so the original and the correction both stay on the record.'}
        </p>
        <Field as="textarea" label="Reason" name="reason" rows={3} required minLength={5} />
      </FormModal>
    )
  }

  return (
    <Modal
      title={`${journal.journal_no} — ${journal.status_label}`}
      onClose={onClose}
      wide
      footer={
        <>
          {canPost && journal.status === 'draft' ? (
            <>
              <button
                type="button"
                className="btn primary small"
                disabled={busy}
                onClick={() => onAction('post')}
              >
                Post to the ledger
              </button>
              <button type="button" className="btn small" onClick={() => setAsking('reject')}>
                Reject
              </button>
            </>
          ) : null}
          {canWithdraw && journal.status === 'draft' ? (
            <button
              type="button"
              className="btn small"
              disabled={busy}
              onClick={() => {
                if (window.confirm(`Withdraw ${journal.journal_no}?`)) onAction('withdraw')
              }}
            >
              Withdraw
            </button>
          ) : null}
          {canPost && journal.status === 'posted' ? (
            <button type="button" className="btn danger small" onClick={() => setAsking('reverse')}>
              Reverse
            </button>
          ) : null}
        </>
      }
    >
      <KeyValues
        items={[
          ['Date', journal.entry_date],
          ['What it is for', journal.narration],
          ['Reference', journal.reference || '-'],
          ['Prepared by', `${journal.prepared_by_name || '-'} on ${dateTime(journal.prepared_at)}`],
          ...(journal.posted_at
            ? [
                [
                  journal.status === 'rejected' ? 'Rejected by' : 'Posted by',
                  `${journal.posted_by_name || '-'} on ${dateTime(journal.posted_at)}`,
                ],
              ]
            : []),
          ...(journal.rejected_reason ? [['Why rejected', journal.rejected_reason]] : []),
          ...(journal.entry_no ? [['Ledger entry', journal.entry_no]] : []),
          ...(journal.reversal_entry_no
            ? [
                ['Reversed by', `${journal.reversed_by_name || '-'} on ${dateOnly(journal.reversed_at)}`],
                ['Why reversed', journal.reversal_reason],
                ['Reversal entry', journal.reversal_entry_no],
              ]
            : []),
        ]}
      />
      <DataTable
        caption={`Lines of ${journal.journal_no}`}
        rows={journal.lines}
        columns={[
          { key: 'account', header: 'Account', render: (l) => `${l.account_code} ${l.account_name}` },
          { key: 'debit', header: 'Debit', num: true, render: (l) => (num(l.debit) ? fmt(l.debit) : '') },
          {
            key: 'credit',
            header: 'Credit',
            num: true,
            render: (l) => (num(l.credit) ? fmt(l.credit) : ''),
          },
          { key: 'description', header: 'Description', render: (l) => l.description || '' },
        ]}
      />
    </Modal>
  )
}

/** Operating expenses, other income, assets and opening balances, with four eyes. */
export default function Journals() {
  const { user, can } = useAuth()
  const { toast, toastError } = useToast()
  const [tab, setTab] = useState('draft')
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [form, setForm] = useState(null) // 'journal' | 'expense'
  const [openId, setOpenId] = useState(null)
  const [busy, setBusy] = useState(false)

  const path = `/api/journals${qs({ status: tab, page, q: search })}`
  const list = useApi(path)
  const accounts = useJournalAccounts()

  const canPrepare = can('admin', 'loan_officer', 'teller')
  const isAdmin = can('admin')
  const open = (list.data?.results || []).find((j) => j.id === openId)

  async function prepare(body) {
    setBusy(true)
    try {
      const journal = await post('/api/journals', body)
      toast(`${journal.journal_no} sent for approval`)
      setForm(null)
      setTab('draft')
      list.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  /** Run an action on the open journal. Resolves true when it succeeded. */
  async function act(kind, values) {
    setBusy(true)
    try {
      if (kind === 'post') await post(`/api/journals/${openId}/post`)
      if (kind === 'reject') await post(`/api/journals/${openId}/reject`, values)
      if (kind === 'reverse') await post(`/api/journals/${openId}/reverse`, values)
      if (kind === 'withdraw') {
        await del(`/api/journals/${openId}`)
        setOpenId(null)
      }
      toast(
        {
          post: 'Posted to the ledger',
          reject: 'Journal rejected',
          reverse: 'Journal reversed',
          withdraw: 'Journal withdrawn',
        }[kind],
      )
      list.reload()
      return true
    } catch (err) {
      toastError(err)
      return false
    } finally {
      setBusy(false)
    }
  }

  const waiting = list.data?.awaiting_approval ?? 0

  return (
    <>
      <PageHeader
        title="Journals and expenses"
        meta="What the loan book does not post by itself: salaries, rent, airtime, assets, opening balances"
      >
        {canPrepare ? (
          <>
            <button type="button" className="btn primary" onClick={() => setForm('expense')}>
              Record an expense
            </button>
            <button type="button" className="btn" onClick={() => setForm('journal')}>
              New journal
            </button>
          </>
        ) : null}
        <button
          type="button"
          className="btn"
          onClick={() => downloadCsv(`/api/journals${qs({ status: tab })}`, 'journals').catch(toastError)}
        >
          Export CSV
        </button>
      </PageHeader>

      {isAdmin && waiting > 0 ? (
        <div className="banner" role="status">
          <strong>{waiting} awaiting approval. </strong>
          Nothing reaches the ledger until an administrator posts it.
        </div>
      ) : null}

      <div className="row between" style={{ marginBottom: 12 }}>
        <div className="tabs" role="tablist" style={{ marginBottom: 0 }}>
          {TABS.map((t) => (
            <button
              key={t.key || 'all'}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => {
                setTab(t.key)
                setPage(1)
              }}
            >
              {t.label}
              {t.key === 'draft' && waiting ? ` (${waiting})` : ''}
            </button>
          ))}
        </div>
        <input
          type="search"
          aria-label="Search journals"
          placeholder="Search number, narration, reference"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            setPage(1)
          }}
          style={{ maxWidth: 280 }}
        />
      </div>

      <ErrorBanner error={list.error || accounts.error} onRetry={list.reload} />
      {list.loading && !list.data ? (
        <Loading what="Loading journals" />
      ) : (
        <>
          <DataTable
            caption="Manual journals"
            rows={list.data?.results || []}
            onRowClick={(row) => setOpenId(row.id)}
            empty={
              tab === 'draft'
                ? 'Nothing awaiting approval'
                : 'No journals yet. Record an expense to get started.'
            }
            columns={[
              { key: 'no', header: 'Journal', render: (j) => j.journal_no },
              { key: 'date', header: 'Date', render: (j) => j.entry_date },
              { key: 'narration', header: 'What it is for', render: (j) => j.narration },
              { key: 'reference', header: 'Reference', render: (j) => j.reference || '-' },
              { key: 'total', header: 'Amount', num: true, render: (j) => money(j.total) },
              { key: 'status', header: 'Status', render: (j) => <Badge value={j.status} /> },
              { key: 'prepared', header: 'Prepared by', render: (j) => j.prepared_by_name || '-' },
              { key: 'entry', header: 'Entry', render: (j) => j.entry_no || '-' },
            ]}
          />
          <Pager meta={list.data} onPage={setPage} noun="journals" />
        </>
      )}

      {form === 'journal' ? (
        <JournalForm
          accounts={accounts.usable}
          busy={busy}
          onClose={() => setForm(null)}
          onSubmit={prepare}
        />
      ) : null}
      {form === 'expense' ? (
        <ExpenseForm
          accounts={accounts.usable}
          busy={busy}
          onClose={() => setForm(null)}
          onSubmit={prepare}
        />
      ) : null}
      {open ? (
        <JournalDetail
          journal={open}
          canPost={isAdmin}
          canWithdraw={isAdmin || open.prepared_by_id === user?.id}
          busy={busy}
          onClose={() => setOpenId(null)}
          onAction={act}
        />
      ) : null}
    </>
  )
}
