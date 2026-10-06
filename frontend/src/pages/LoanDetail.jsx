import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import Modal, { FormModal } from '../components/Modal.jsx'
import Scorecard from '../components/Scorecard.jsx'
import { useToast } from '../components/Toast.jsx'
import {
  Badge,
  Check,
  ErrorBanner,
  ExportButtons,
  Field,
  KeyValues,
  Loading,
  PageHeader,
} from '../components/ui.jsx'
import { del, get, openHtml, patch, post, put, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import {
  dateOnly,
  dateTime,
  fmt,
  frequencyLabel,
  getCurrency,
  humanise,
  money,
  num,
  pct,
  rateMethodLabel,
  termUnit,
  today,
} from '../lib/format.js'
import { PeriodNotice, useMinPostingDate } from '../lib/periods.jsx'
import { useApi } from '../lib/useApi.js'

/**
 * Top-up: a new loan that settles this one out of its own proceeds. The quote
 * is refreshed as the amount and term change, because the borrower only ever
 * cares about one number - what actually reaches them.
 */
function TopUpModal({ loanId, loan, busy, onClose, onApply }) {
  const products = useApi('/api/products')
  const [form, setForm] = useState({
    product_id: '',
    principal: '',
    term_months: String(loan.term_months),
    purpose: '',
  })
  const [quote, setQuote] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (products.data?.length && !form.product_id) {
      const same = products.data.find((p) => p.id === loan.product_id)
      setForm((f) => ({ ...f, product_id: String((same || products.data[0]).id) }))
    }
  }, [products.data, form.product_id, loan.product_id])

  const set = (key) => (event) => {
    setForm((f) => ({ ...f, [key]: event.target.value }))
    setQuote(null)
    setError('')
  }
  const product = products.data?.find((p) => String(p.id) === String(form.product_id))

  async function preview() {
    setError('')
    if (!form.product_id || !form.principal || !form.term_months) {
      setError('Choose a product, then enter an amount and a term.')
      return
    }
    try {
      setQuote(
        await post(`/api/loans/${loanId}/top-up-quote`, {
          product_id: Number(form.product_id),
          principal: form.principal,
          term_months: Number(form.term_months),
        }),
      )
    } catch (err) {
      setQuote(null)
      setError(err.message)
    }
  }

  return (
    <Modal title={`Top up ${loan.loan_no}`} onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>
        A top-up is a new loan that settles this one out of its own proceeds. Nothing happens to{' '}
        {loan.loan_no} until the new loan is disbursed, so an application that is never approved
        leaves the borrower where they are.
      </p>

      <Field as="select" label="Product" value={form.product_id} onChange={set('product_id')}>
        {(products.data || []).map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} — {p.interest_rate_pct}%/month, {fmt(p.min_amount)} to {fmt(p.max_amount)}
          </option>
        ))}
      </Field>
      <div className="grid cols-2">
        <Field
          label={`New principal (${getCurrency()})`}
          type="number"
          step="0.01"
          min="1"
          value={form.principal}
          onChange={set('principal')}
          required
        />
        <Field
          label={`New term (${termUnit(product?.repayment_frequency)})`}
          type="number"
          min="1"
          value={form.term_months}
          onChange={set('term_months')}
          required
        />
      </div>
      <Field label="Purpose" value={form.purpose} onChange={set('purpose')} />

      {error ? <p className="error">{error}</p> : null}

      {quote ? (
        <div className="card" style={{ marginTop: 4 }}>
          <KeyValues
            items={[
              ['New principal', money(quote.new_principal)],
              [
                `${frequencyLabel(product?.repayment_frequency)} instalment`,
                <strong key="i">{money(quote.new_instalment)}</strong>,
              ],
              ['Fees and charges', `−${money(quote.fees)}`],
              ['Net advanced', money(quote.net_disbursed)],
              [
                `Settles ${quote.existing_loan_no}`,
                <span key="s" className="tag-warn">
                  −{money(quote.settlement_amount)}
                </span>,
              ],
              [
                'Interest rebated on the old loan',
                <span key="r" className="tag-ok">
                  {money(quote.interest_rebate)}
                </span>,
              ],
              [
                'Cash to the borrower',
                <strong key="c" style={{ fontSize: 16 }}>
                  {money(quote.cash_to_borrower)}
                </strong>,
              ],
            ]}
          />
        </div>
      ) : null}

      <div className="row" style={{ marginTop: 12 }}>
        <button type="button" className="btn" onClick={preview}>
          Preview
        </button>
        <button
          type="button"
          className="btn primary"
          disabled={busy || !quote || !quote.sufficient}
          onClick={() =>
            onApply({
              product_id: Number(form.product_id),
              principal: form.principal,
              term_months: Number(form.term_months),
              purpose: form.purpose || null,
            })
          }
        >
          {busy ? 'Submitting…' : 'Submit the application'}
        </button>
        <button type="button" className="btn" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Modal>
  )
}

/** Pick which of the borrower's guarantors stand behind this loan. */
function GuarantorsModal({ loan, busy, onClose, onSave }) {
  const borrower = useApi(`/api/borrowers/${loan.borrower_id}`)
  const linked = new Set((loan.guarantors || []).map((g) => g.id))
  const held = borrower.data?.guarantors || []

  return (
    <FormModal
      title={`Guarantors of ${loan.loan_no}`}
      submitLabel="Save guarantors"
      busy={busy}
      submitDisabled={borrower.loading}
      onClose={onClose}
      onSubmit={(values) =>
        onSave(held.filter((g) => values[`guarantor_${g.id}`]).map((g) => g.id))
      }
    >
      <p className="muted" style={{ marginTop: 0 }}>
        Tick the guarantors who have agreed to stand behind this loan. Only they are printed on its
        agreement. To add someone new, add them to the borrower first.
      </p>
      {borrower.loading ? <Loading what="Loading the borrower's guarantors" /> : null}
      <ErrorBanner error={borrower.error} />
      {!borrower.loading && held.length === 0 ? (
        <p className="muted">{loan.borrower_name} has no guarantors on file.</p>
      ) : null}
      {held.map((g) => (
        <Check
          key={g.id}
          name={`guarantor_${g.id}`}
          label={`${g.full_name} (${g.national_id})`}
          defaultChecked={linked.has(g.id)}
        />
      ))}
    </FormModal>
  )
}

export default function LoanDetail() {
  const { id } = useParams()
  const { can } = useAuth()
  const minPostingDate = useMinPostingDate()
  const { toast, toastError } = useToast()
  const { data: loan, error, loading, reload } = useApi(`/api/loans/${id}`)
  // Everything on this page is in the loan's own currency.
  const lmoney = (value) => money(value, loan?.currency)

  const [tab, setTab] = useState('schedule')
  const [action, setAction] = useState(null) // { kind, txnId? }
  const [statement, setStatement] = useState(null)
  const [statementPeriod, setStatementPeriod] = useState({ start: '', end: today() })
  const [settlement, setSettlement] = useState(null)
  const [busy, setBusy] = useState(false)

  /** Run a lifecycle action, then refresh the loan. */
  async function run(promise, message) {
    setBusy(true)
    try {
      await promise
      setAction(null)
      toast(message)
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function openStatement() {
    try {
      setStatement(await get(`/api/loans/${id}/statement${qs(statementPeriod)}`))
    } catch (err) {
      toastError(err)
    }
  }

  /** A new period: re-read the statement for it, so the screen and the downloads agree. */
  async function changePeriod(period) {
    setStatementPeriod(period)
    try {
      setStatement(await get(`/api/loans/${id}/statement${qs(period)}`))
    } catch (err) {
      toastError(err)
    }
  }

  async function openSettlement() {
    try {
      setSettlement(await get(`/api/loans/${id}/settlement-quote`))
    } catch (err) {
      toastError(err)
    }
  }

  if (loading && !loan) return <Loading what="Loading loan" />
  if (error) return <ErrorBanner error={error} onRetry={reload} />
  if (!loan) return null

  const canLoans = can('loans')
  const canApprove = can('approve')
  const canDisburse = can('disburse')
  const canCash = can('cash')
  const canReverse = can('reverse')
  const canSupervise = can('supervise')
  const canRestructure = can('restructure')
  const arrears = num(loan.arrears_amount) ?? 0

  return (
    <>
      <PageHeader
        title={
          <>
            {loan.loan_no} <Badge value={loan.status} />
          </>
        }
        meta={`${loan.borrower_name} · ${loan.product_name}`}
      >
        {canCash && loan.status === 'active' ? (
          <>
            <button
              type="button"
              className="btn primary"
              onClick={() => setAction({ kind: 'repay' })}
            >
              Post repayment
            </button>
            <button type="button" className="btn" onClick={openSettlement}>
              Settle early
            </button>
          </>
        ) : null}
        {canApprove && loan.status === 'pending' ? (
          <>
            <button
              type="button"
              className="btn primary"
              onClick={() => run(post(`/api/loans/${id}/approve`), 'Loan approved')}
              disabled={busy}
            >
              Approve
            </button>
            <button type="button" className="btn danger" onClick={() => setAction({ kind: 'reject' })}>
              Reject
            </button>
          </>
        ) : null}
        {canDisburse && loan.status === 'approved' ? (
          <button
            type="button"
            className="btn primary"
            onClick={() => setAction({ kind: 'disburse' })}
          >
            Disburse
          </button>
        ) : null}
        {canApprove && loan.status === 'approved' ? (
          <button type="button" className="btn danger" onClick={() => setAction({ kind: 'reject' })}>
            Reject
          </button>
        ) : null}
        {canSupervise && loan.status === 'active' ? (
          <button
            type="button"
            className="btn"
            onClick={() => run(post(`/api/loans/${id}/accrue-penalties`), 'Penalties accrued')}
            disabled={busy}
          >
            Accrue penalties
          </button>
        ) : null}
        {canRestructure && loan.status === 'active' ? (
          <>
            <button type="button" className="btn" onClick={() => setAction({ kind: 'waive' })}>
              Waive penalties
            </button>
            <button type="button" className="btn" onClick={() => setAction({ kind: 'reschedule' })}>
              Reschedule
            </button>
            <button type="button" className="btn danger" onClick={() => setAction({ kind: 'writeoff' })}>
              Write off
            </button>
          </>
        ) : null}
        {canCash && loan.status === 'written_off' ? (
          <button
            type="button"
            className="btn primary"
            onClick={() => setAction({ kind: 'recovery' })}
          >
            Record a recovery
          </button>
        ) : null}
        {canLoans && loan.status === 'active' ? (
          <button type="button" className="btn" onClick={() => setAction({ kind: 'topup' })}>
            Top up
          </button>
        ) : null}
        <button
          type="button"
          className="btn"
          onClick={() =>
            openHtml(`/api/loans/${id}/agreement`, `Agreement ${loan.loan_no}`).catch(toastError)
          }
        >
          Agreement
        </button>
        <button type="button" className="btn" onClick={openStatement}>
          Statement
        </button>
      </PageHeader>

      <div className="grid cols-3">
        <div className="card">
          <h3>Loan</h3>
          <KeyValues
            items={[
              [
                'Borrower',
                <Link key="b" to={`/borrowers/${loan.borrower_id}`}>
                  {loan.borrower_name}
                </Link>,
              ],
              ['Product', loan.product_name],
              ['Principal', lmoney(loan.principal)],
              [
                'Rate',
                `${pct(loan.interest_rate_pct)} per month, ${rateMethodLabel(loan.rate_method).toLowerCase()}`,
              ],
              ['Term', `${loan.term_months} ${termUnit(loan.repayment_frequency)}`],
              [
                `${frequencyLabel(loan.repayment_frequency)} instalment`,
                lmoney(loan.instalment_amount),
              ],
              ['Total interest', lmoney(loan.total_interest)],
              [
                'Upfront fees',
                lmoney(
                  (num(loan.admin_fee) ?? 0) +
                    (num(loan.insurance_fee) ?? 0) +
                    (num(loan.other_charges) ?? 0),
                ),
              ],
              ['Total cost of credit', lmoney(loan.total_cost_of_credit)],
              [
                'APR, fees included',
                loan.apr_pct === null || loan.apr_pct === undefined ? '-' : `${pct(loan.apr_pct)} a year`,
              ],
              ...(loan.external_ref ? [['Previous system number', loan.external_ref]] : []),
              ['Officer', loan.officer_name || '-'],
              ['Branch', loan.branch_name || '-'],
              ...(loan.group_name ? [['Group', loan.group_name]] : []),
              ...(loan.refinanced_from_no
                ? [
                    [
                      'Tops up',
                      <Link key="r" to={`/loans/${loan.refinanced_from_id}`}>
                        {loan.refinanced_from_no}
                      </Link>,
                    ],
                  ]
                : []),
              ['Purpose', loan.purpose || '-'],
            ]}
          />
        </div>
        <div className="card">
          <h3>Dates</h3>
          <KeyValues
            items={[
              ['Applied', loan.application_date],
              ['Approved', dateOnly(loan.approved_at)],
              ['Disbursed', loan.disbursement_date || '-'],
              ['First instalment', loan.first_instalment_date || '-'],
              ['Maturity', loan.maturity_date || '-'],
              ['Closed', dateOnly(loan.closed_at)],
              ['Rejection reason', loan.rejection_reason || '-'],
            ]}
          />
        </div>
        <div className="card">
          <h3>Balances</h3>
          <KeyValues
            items={[
              ['Principal outstanding', lmoney(loan.principal_outstanding)],
              ['Interest outstanding', lmoney(loan.interest_outstanding)],
              ['Penalties outstanding', lmoney(loan.penalties_outstanding)],
              ...((num(loan.charges_outstanding) ?? 0) > 0
                ? [['Charges outstanding', lmoney(loan.charges_outstanding)]]
                : []),
              ['Total outstanding', <strong key="t">{lmoney(loan.total_outstanding)}</strong>],
              ['Total paid', lmoney(loan.total_paid)],
              [
                'Arrears',
                arrears > 0 ? (
                  <span className="tag-danger">
                    {lmoney(loan.arrears_amount)} ({loan.days_in_arrears} days)
                  </span>
                ) : (
                  <span className="tag-ok">None</span>
                ),
              ],
            ]}
          />
        </div>
      </div>

      <div className="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'schedule'}
          onClick={() => setTab('schedule')}
        >
          Repayment schedule
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'transactions'}
          onClick={() => setTab('transactions')}
        >
          Transactions ({loan.transactions.length})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'notes'}
          onClick={() => setTab('notes')}
        >
          Follow-ups ({loan.notes?.length ?? 0})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'security'}
          onClick={() => setTab('security')}
        >
          Security ({loan.collateral?.length ?? 0})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'charges'}
          onClick={() => setTab('charges')}
        >
          Charges ({loan.charges?.length ?? 0})
        </button>
        {loan.scorecard ? (
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'score'}
            onClick={() => setTab('score')}
          >
            Credit score ({loan.scorecard.score})
          </button>
        ) : null}
      </div>

      {tab === 'schedule' ? (
        loan.schedule.length === 0 ? (
          <p className="muted">The schedule is generated on disbursement.</p>
        ) : (
          <DataTable
            caption="Repayment schedule"
            rows={loan.schedule}
            columns={[
              { key: 'n', header: '#', num: true, render: (r) => r.number },
              { key: 'due', header: 'Due date', render: (r) => r.due_date },
              { key: 'opening', header: 'Opening', num: true, render: (r) => fmt(r.opening_balance) },
              { key: 'principal', header: 'Principal', num: true, render: (r) => fmt(r.principal_due) },
              { key: 'interest', header: 'Interest', num: true, render: (r) => fmt(r.interest_due) },
              { key: 'penalty', header: 'Penalty', num: true, render: (r) => fmt(r.penalty_due) },
              { key: 'charge', header: 'Charges', num: true, render: (r) => fmt(r.charge_due) },
              { key: 'total', header: 'Total due', num: true, render: (r) => fmt(r.total_due) },
              { key: 'paid', header: 'Paid', num: true, render: (r) => fmt(r.total_paid) },
              {
                key: 'balance',
                header: 'Balance',
                num: true,
                render: (r) =>
                  (num(r.balance) ?? 0) > 0 && r.status === 'overdue' ? (
                    <span className="tag-danger">{fmt(r.balance)}</span>
                  ) : (
                    fmt(r.balance)
                  ),
              },
              { key: 'closing', header: 'Closing', num: true, render: (r) => fmt(r.closing_balance) },
              { key: 'status', header: 'Status', render: (r) => <Badge value={r.status} /> },
              { key: 'paid_on', header: 'Paid on', render: (r) => r.paid_date || '-' },
            ]}
          />
        )
      ) : tab === 'score' ? (
        <div className="card">
          <h3>Credit assessment at application</h3>
          <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>
            Advisory. The hard rules — affordability, KYC, blacklist, one open loan at a time —
            are enforced separately and cannot be scored around.
          </p>
          <Scorecard card={loan.scorecard} />
        </div>
      ) : tab === 'charges' ? (
        <div className="card">
          <div className="row between" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Fees and charges</h3>
            {canLoans ? (
              <button
                type="button"
                className="btn small"
                onClick={() => setAction({ kind: 'charge' })}
              >
                Raise a charge
              </button>
            ) : null}
          </div>
          <DataTable
            caption="Loan charges"
            rows={loan.charges || []}
            empty="Nothing beyond the fees deducted at disbursement"
            columns={[
              { key: 'date', header: 'Raised', render: (c) => c.applied_on },
              { key: 'name', header: 'Charge', render: (c) => c.name },
              { key: 'amount', header: 'Amount', num: true, render: (c) => fmt(c.amount) },
              {
                key: 'collection',
                header: 'Recovered',
                render: (c) =>
                  c.collection === 'balance' ? (
                    <span className="tag-warn">
                      Added to instalment {c.instalment_number ?? '-'}
                    </span>
                  ) : (
                    <span className="tag-ok">Collected at the counter</span>
                  ),
              },
            ]}
          />
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            A charge collected at the counter is paid there and then and never touches the loan
            balance. One added to the balance rides on the next unpaid instalment, and the
            repayment waterfall takes it after penalties and before interest.
          </p>
        </div>
      ) : tab === 'security' ? (
        <div className="card">
          <div className="row between" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Security pledged</h3>
            {canLoans ? (
              <button
                type="button"
                className="btn small"
                onClick={() => setAction({ kind: 'collateral' })}
              >
                Pledge security
              </button>
            ) : null}
          </div>
          <DataTable
            caption="Collateral"
            rows={loan.collateral || []}
            empty="Nothing pledged against this loan"
            columns={[
              { key: 'type', header: 'Type', render: (c) => humanise(c.type) },
              { key: 'description', header: 'Description', render: (c) => c.description },
              { key: 'reference', header: 'Reference', render: (c) => c.reference || '-' },
              {
                key: 'value',
                header: 'Valuation',
                num: true,
                render: (c) => fmt(c.estimated_value),
              },
              { key: 'valued', header: 'Valued on', render: (c) => c.valuation_date || '-' },
              {
                key: 'status',
                header: 'Status',
                render: (c) => (
                  <span className={c.status === 'pledged' ? 'tag-ok' : 'muted'}>
                    {humanise(c.status)}
                  </span>
                ),
              },
              { key: 'by', header: 'Recorded by', render: (c) => c.recorded_by_name || '-' },
              {
                key: 'actions',
                header: '',
                render: (c) =>
                  canLoans ? (
                    <div className="row" style={{ gap: 6 }}>
                      {c.status === 'pledged' ? (
                        <button
                          type="button"
                          className="btn small"
                          onClick={() =>
                            run(
                              patch(`/api/loans/${id}/collateral/${c.id}`, { status: 'released' }),
                              'Security released',
                            )
                          }
                        >
                          Release
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="btn small"
                        onClick={() => {
                          if (window.confirm(`Remove ${c.description}?`)) {
                            run(del(`/api/loans/${id}/collateral/${c.id}`), 'Security removed')
                          }
                        }}
                      >
                        Remove
                      </button>
                    </div>
                  ) : null,
              },
            ]}
          />
          {(loan.collateral || []).length > 0 ? (
            <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
              Total valuation{' '}
              {lmoney(
                (loan.collateral || [])
                  .filter((c) => c.status === 'pledged')
                  .reduce((sum, c) => sum + (num(c.estimated_value) ?? 0), 0),
              )}{' '}
              against {lmoney(loan.total_outstanding)} outstanding.
            </p>
          ) : null}

          <div className="row between" style={{ margin: '20px 0 12px' }}>
            <h3 style={{ margin: 0 }}>Guarantors of this loan</h3>
            {canLoans && ['pending', 'approved'].includes(loan.status) ? (
              <button
                type="button"
                className="btn small"
                onClick={() => setAction({ kind: 'guarantors' })}
              >
                Change guarantors
              </button>
            ) : null}
          </div>
          <DataTable
            caption="Guarantors of this loan"
            rows={loan.guarantors || []}
            empty="Nobody guarantees this loan"
            columns={[
              { key: 'name', header: 'Name', render: (g) => g.full_name },
              { key: 'id', header: 'National ID', render: (g) => g.national_id },
              { key: 'phone', header: 'Phone', render: (g) => g.phone },
              {
                key: 'relationship',
                header: 'Relationship',
                render: (g) => g.relationship_to_borrower || '-',
              },
            ]}
          />
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            These are the guarantors printed on the agreement. They can be changed until the loan is
            disbursed; a guarantor added to the borrower later does not stand behind this loan.
          </p>
        </div>
      ) : tab === 'notes' ? (
        <div className="card">
          <div className="row between" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Collections follow-ups</h3>
            {canCash ? (
              <button
                type="button"
                className="btn small"
                onClick={() => setAction({ kind: 'note' })}
              >
                Add follow-up
              </button>
            ) : null}
          </div>
          {(loan.notes || []).length === 0 ? (
            <p className="muted">
              Nothing recorded. Log what was tried and what the borrower promised, so the next
              person picking this up is not starting from scratch.
            </p>
          ) : (
            loan.notes.map((note) => (
              <div className={`note-card ${note.resolved ? 'resolved' : ''}`} key={note.id}>
                <div className="note-meta">
                  {dateTime(note.created_at)} · {note.author_name || 'system'}
                  {note.next_action_date ? ` · next action ${note.next_action_date}` : ''}
                  {note.promised_amount
                    ? ` · promised ${money(note.promised_amount)}${
                        note.promised_date ? ` by ${note.promised_date}` : ''
                      }`
                    : ''}
                  {note.resolved ? ' · resolved' : ''}
                </div>
                <div className="note-body">{note.body}</div>
                {canCash ? (
                  <div className="row" style={{ marginTop: 8 }}>
                    <button
                      type="button"
                      className="btn small"
                      onClick={() =>
                        run(
                          patch(`/api/loans/${id}/notes/${note.id}`, { resolved: !note.resolved }),
                          note.resolved ? 'Follow-up reopened' : 'Follow-up resolved',
                        )
                      }
                    >
                      {note.resolved ? 'Reopen' : 'Mark resolved'}
                    </button>
                    <button
                      type="button"
                      className="btn small"
                      onClick={() => {
                        if (window.confirm('Delete this follow-up?')) {
                          run(del(`/api/loans/${id}/notes/${note.id}`), 'Follow-up deleted')
                        }
                      }}
                    >
                      Delete
                    </button>
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>
      ) : (
        <DataTable
          caption="Transactions"
          rows={loan.transactions}
          empty="No transactions yet"
          columns={[
            { key: 'id', header: 'ID', num: true, render: (t) => t.id },
            { key: 'date', header: 'Date', render: (t) => t.txn_date },
            { key: 'type', header: 'Type', render: (t) => <Badge value={t.txn_type} /> },
            { key: 'amount', header: 'Amount', num: true, render: (t) => fmt(t.amount) },
            {
              key: 'principal',
              header: 'Principal',
              num: true,
              render: (t) => fmt(t.principal_component),
            },
            {
              key: 'interest',
              header: 'Interest',
              num: true,
              render: (t) => fmt(t.interest_component),
            },
            {
              key: 'penalty',
              header: 'Penalty',
              num: true,
              render: (t) => fmt(t.penalty_component),
            },
            {
              key: 'charge',
              header: 'Charge',
              num: true,
              render: (t) => fmt(t.charge_component),
            },
            { key: 'method', header: 'Method', render: (t) => t.method || '-' },
            { key: 'reference', header: 'Reference', render: (t) => t.reference || '-' },
            { key: 'narration', header: 'Narration', render: (t) => t.narration || '' },
            {
              key: 'actions',
              header: '',
              render: (t) =>
                t.reversed ? (
                  <span className="tag-danger">Reversed</span>
                ) : t.txn_type === 'repayment' && canReverse && loan.status !== 'written_off' ? (
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => setAction({ kind: 'reverse', txnId: t.id })}
                  >
                    Reverse
                  </button>
                ) : null,
            },
          ]}
        />
      )}

      {/* ----------------------------------------------------------- actions */}
      {action?.kind === 'reject' ? (
        <FormModal
          title="Reject loan"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/reject`, v), 'Loan rejected')}
        >
          <Field as="textarea" label="Reason" name="reason" rows={3} required />
        </FormModal>
      ) : null}

      {action?.kind === 'disburse' ? (
        <FormModal
          title="Disburse loan"
          submitLabel="Disburse"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/disburse`, v), 'Loan disbursed')}
        >
          <p className="muted">
            Disbursing generates the amortisation schedule and posts the advance net of the{' '}
            {money((num(loan.admin_fee) ?? 0) + (num(loan.insurance_fee) ?? 0))} upfront fees.
          </p>
          <PeriodNotice date={today()} />
          <div className="grid cols-2">
            <Field
              label="Disbursement date"
              type="date"
              name="disbursement_date"
              min={minPostingDate}
              defaultValue={today()}
            />
            <Field
              label="First instalment date"
              type="date"
              name="first_instalment_date"
              hint="Leave blank for the borrower's next payday"
            />
            <Field as="select" label="Method" name="method" defaultValue="bank_transfer">
              <option value="bank_transfer">Bank transfer</option>
              <option value="mobile_money">Mobile money</option>
              <option value="cash">Cash</option>
            </Field>
            <Field label="Reference" name="reference" />
          </div>
        </FormModal>
      ) : null}

      {action?.kind === 'topup' ? (
        <TopUpModal
          loanId={id}
          loan={loan}
          busy={busy}
          onClose={() => setAction(null)}
          onApply={(body) =>
            run(post(`/api/loans/${id}/top-up`, body), 'Top-up application captured')
          }
        />
      ) : null}

      {action?.kind === 'charge' ? (
        <FormModal
          title="Raise a charge"
          submitLabel="Raise charge"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/charges`, v), 'Charge raised')}
        >
          <div className="grid cols-2">
            <Field label="What it is for" name="name" required />
            <Field
              label={`Amount (${getCurrency()})`}
              type="number"
              step="0.01"
              min="0.01"
              name="amount"
              required
            />
            <Field
              label="Raised on"
              type="date"
              name="applied_on"
              min={minPostingDate}
              defaultValue={today()}
            />
            <Field
              as="select"
              label="How it is recovered"
              name="collection"
              defaultValue="counter"
              hint={
                loan.status === 'active'
                  ? undefined
                  : 'Only an active loan can carry a charge on its balance'
              }
            >
              <option value="counter">Collected at the counter now</option>
              {loan.status === 'active' ? (
                <option value="balance">Added to the loan balance</option>
              ) : null}
            </Field>
          </div>
          <p className="muted" style={{ marginBottom: 0 }}>
            Added to the balance, the charge rides on the next unpaid instalment and is taken
            after penalties and before interest. Collected at the counter, it is cash in today and
            the loan is untouched.
          </p>
        </FormModal>
      ) : null}

      {action?.kind === 'guarantors' ? (
        <GuarantorsModal
          loan={loan}
          busy={busy}
          onClose={() => setAction(null)}
          onSave={(ids) =>
            run(put(`/api/loans/${id}/guarantors`, { guarantor_ids: ids }), 'Guarantors updated')
          }
        />
      ) : null}

      {action?.kind === 'collateral' ? (
        <FormModal
          title="Pledge security against this loan"
          submitLabel="Record security"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/collateral`, v), 'Security recorded')}
        >
          <div className="grid cols-2">
            <Field as="select" label="Type" name="type" defaultValue="vehicle">
              <option value="vehicle">Vehicle</option>
              <option value="property">Property</option>
              <option value="equipment">Equipment</option>
              <option value="livestock">Livestock</option>
              <option value="cash_deposit">Cash deposit</option>
              <option value="guarantee">Third-party guarantee</option>
              <option value="other">Other</option>
            </Field>
            <Field
              label="Reference"
              name="reference"
              hint="Registration number, title deed, serial number"
            />
          </div>
          <Field label="Description" name="description" required />
          <div className="grid cols-2">
            <Field
              label={`Estimated value (${getCurrency()})`}
              type="number"
              step="0.01"
              min="0"
              name="estimated_value"
              required
            />
            <Field label="Valued on" type="date" name="valuation_date" defaultValue={today()} />
          </div>
          <Field as="textarea" label="Notes" name="notes" rows={2} />
        </FormModal>
      ) : null}

      {action?.kind === 'recovery' ? (
        <FormModal
          title="Record a recovery"
          submitLabel="Record recovery"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/recovery`, v), 'Recovery recorded')}
        >
          <p className="muted" style={{ marginTop: 0 }}>
            Money collected after this loan was written off. The loan's balances stay at zero —
            they left the book at write-off — and the receipt goes to recovery income.
          </p>
          <div className="grid cols-2">
            <Field
              label={`Amount (${getCurrency()})`}
              type="number"
              step="0.01"
              min="0.01"
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
              <option value="salary_deduction">Salary deduction</option>
            </Field>
            <Field label="Reference" name="reference" />
          </div>
          <Field label="Narration" name="narration" />
        </FormModal>
      ) : null}

      {action?.kind === 'note' ? (
        <FormModal
          title="Add a collections follow-up"
          submitLabel="Save follow-up"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/notes`, v), 'Follow-up saved')}
        >
          <Field as="textarea" label="What happened" name="body" rows={3} required />
          <div className="grid cols-3">
            <Field label="Next action date" type="date" name="next_action_date" />
            <Field
              label={`Amount promised (${getCurrency()})`}
              type="number"
              step="0.01"
              min="0"
              name="promised_amount"
            />
            <Field label="Promised by" type="date" name="promised_date" />
          </div>
        </FormModal>
      ) : null}

      {action?.kind === 'repay' ? (
        <FormModal
          title="Post repayment"
          submitLabel="Post repayment"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/repayments`, v), 'Repayment posted')}
        >
          <PeriodNotice date={today()} />
          <div className="grid cols-2">
            <Field
              label={`Amount (${getCurrency()})`}
              type="number"
              step="0.01"
              min="0.01"
              max={loan.total_outstanding}
              name="amount"
              required
              defaultValue={arrears > 0 ? loan.arrears_amount : loan.instalment_amount}
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
              <option value="salary_deduction">Salary deduction</option>
              <option value="bank_transfer">Bank transfer</option>
              <option value="mobile_money">Mobile money</option>
            </Field>
            <Field label="Reference" name="reference" />
          </div>
          <Field label="Narration" name="narration" />
          <p className="muted">
            Allocation order is penalties, then interest, then principal, oldest instalment first.
            Total outstanding is {lmoney(loan.total_outstanding)}; more than that is refused.
          </p>
        </FormModal>
      ) : null}

      {action?.kind === 'reverse' ? (
        <FormModal
          title={`Reverse transaction ${action.txnId}`}
          submitLabel="Reverse"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(`/api/loans/${id}/transactions/${action.txnId}/reverse`, v),
              'Transaction reversed',
            )
          }
        >
          <Field label="Reason" name="narration" required />
        </FormModal>
      ) : null}

      {action?.kind === 'waive' ? (
        <FormModal
          title="Waive penalties"
          submitLabel="Waive"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/waive-penalties`, v), 'Penalties waived')}
        >
          <Field
            label={`Amount (maximum ${fmt(loan.penalties_outstanding)})`}
            type="number"
            step="0.01"
            min="0.01"
            max={loan.penalties_outstanding}
            name="amount"
            required
          />
          <Field label="Reason" name="narration" required />
        </FormModal>
      ) : null}

      {action?.kind === 'writeoff' ? (
        <FormModal
          title="Write off loan"
          submitLabel="Write off"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/write-off`, v), 'Loan written off')}
        >
          <p>
            This writes off <strong>{lmoney(loan.total_outstanding)}</strong> and closes the loan. It
            cannot be undone.
          </p>
          <Field as="textarea" label="Reason" name="narration" rows={3} required />
        </FormModal>
      ) : null}

      {action?.kind === 'reschedule' ? (
        <FormModal
          title="Reschedule loan"
          submitLabel="Reschedule"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/loans/${id}/reschedule`, v), 'Loan rescheduled')}
        >
          <p className="muted">
            Outstanding principal, overdue interest and penalties are capitalised into a new
            schedule. Unearned future interest on the old schedule is dropped.
          </p>
          <div className="grid cols-2">
            <Field
              label={`New term (${termUnit(loan.repayment_frequency)})`}
              type="number"
              min="1"
              name="new_term_months"
              required
              defaultValue={loan.term_months}
              hint={`${frequencyLabel(loan.repayment_frequency)} instalments, as before`}
            />
            <Field
              label="New rate (%/month)"
              type="number"
              step="0.001"
              name="new_interest_rate_pct"
              hint={`Blank keeps ${loan.interest_rate_pct}`}
            />
            <Field label="First instalment date" type="date" name="first_instalment_date" />
            <Field label="Narration" name="narration" defaultValue="Loan rescheduled" />
          </div>
        </FormModal>
      ) : null}

      {settlement ? (
        <Modal
          title={`Early settlement - ${settlement.loan_no}`}
          onClose={() => setSettlement(null)}
        >
          <p className="muted" style={{ marginTop: 0 }}>
            Interest already fallen due is payable. Interest on instalments not yet due has not
            been earned, so it is rebated rather than collected.
          </p>
          <KeyValues
            items={[
              ['Quoted as at', settlement.as_of],
              ['Principal outstanding', lmoney(settlement.principal_outstanding)],
              ['Interest accrued to date', lmoney(settlement.interest_accrued)],
              ['Penalties outstanding', lmoney(settlement.penalties_outstanding)],
              [
                'Interest rebate',
                <span key="r" className="tag-ok">
                  −{lmoney(settlement.interest_rebate)}
                </span>,
              ],
              [
                'Settlement figure',
                <strong key="s" style={{ fontSize: 16 }}>
                  {lmoney(settlement.settlement_amount)}
                </strong>,
              ],
              ['If run to term', lmoney(settlement.total_outstanding)],
              [
                'Borrower saves',
                <span key="v" className="tag-ok">
                  {lmoney(settlement.saving_vs_running_to_term)}
                </span>,
              ],
            ]}
          />
          <form
            style={{ marginTop: 16 }}
            onSubmit={(event) => {
              event.preventDefault()
              const form = event.currentTarget
              const values = {}
              for (const element of form.elements) {
                if (element.name) values[element.name] = element.value || null
              }
              setSettlement(null)
              run(post(`/api/loans/${id}/settle`, values), 'Loan settled and closed')
            }}
          >
            <div className="grid cols-2">
              <Field as="select" label="Method" name="method" defaultValue="bank_transfer">
                <option value="bank_transfer">Bank transfer</option>
                <option value="cash">Cash</option>
                <option value="mobile_money">Mobile money</option>
                <option value="salary_deduction">Salary deduction</option>
              </Field>
              <Field label="Reference" name="reference" />
            </div>
            <Field label="Narration" name="narration" defaultValue="Early settlement" />
            <div className="row">
              <button className="btn primary" type="submit" disabled={busy}>
                Take {lmoney(settlement.settlement_amount)} and close
              </button>
              <button type="button" className="btn" onClick={() => setSettlement(null)}>
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {statement ? (
        <Modal
          title={`Loan statement - ${statement.loan_no}`}
          wide
          onClose={() => setStatement(null)}
          footer={
            <ExportButtons
              small
              path={`/api/loans/${id}/statement${qs(statementPeriod)}`}
              name={`statement_${statement.loan_no}`}
              formats={['pdf', 'xlsx']}
            />
          }
        >
          <div className="row" style={{ alignItems: 'flex-end', marginBottom: 4 }}>
            <Field
              label="From"
              type="date"
              value={statementPeriod.start}
              onChange={(e) => changePeriod({ ...statementPeriod, start: e.target.value })}
            />
            <Field
              label="To"
              type="date"
              value={statementPeriod.end}
              onChange={(e) => changePeriod({ ...statementPeriod, end: e.target.value })}
            />
            <p className="muted" style={{ fontSize: 12, marginBottom: 14 }}>
              Leave From empty for the whole loan. The downloads cover the same period.
            </p>
          </div>
          <KeyValues
            items={[
              ['Borrower', `${statement.borrower} (${statement.national_id})`],
              ['Product', statement.product],
              [
                'Principal',
                `${lmoney(statement.principal)} at ${pct(statement.rate_pct)}/month over ${statement.term} months`,
              ],
              [
                'Disbursed',
                `${statement.disbursement_date || '-'} (maturity ${statement.maturity_date || '-'})`,
              ],
              [
                'Total outstanding',
                <strong key="o">
                  {lmoney(statement.total_outstanding)} (principal{' '}
                  {fmt(statement.principal_outstanding)}, interest{' '}
                  {fmt(statement.interest_outstanding)}, penalties{' '}
                  {fmt(statement.penalties_outstanding)})
                </strong>,
              ],
            ]}
          />
          <p className="muted" style={{ fontSize: 12 }}>
            The balance is the principal, penalties and charges owed. Interest is charged on each
            instalment as it falls due, so it has its own column when paid, and the interest still
            to fall due is in the total outstanding above.
          </p>
          <DataTable
            caption="Statement lines"
            rows={[
              ...(statement.period_start
                ? [{ description: 'Opening balance', balance: statement.opening_balance }]
                : []),
              ...statement.lines,
            ]}
            rowKey={(row, index) => index}
            empty="No postings in this period"
            columns={[
              { key: 'date', header: 'Date', render: (r) => r.date || '' },
              { key: 'description', header: 'Description', wrap: true, render: (r) => r.description },
              { key: 'ref', header: 'Ref', render: (r) => r.reference || '' },
              {
                key: 'debit',
                header: 'Charged',
                num: true,
                render: (r) => (num(r.debit) ? fmt(r.debit) : ''),
              },
              {
                key: 'credit',
                header: 'Paid',
                num: true,
                render: (r) => (num(r.credit) ? fmt(r.credit) : ''),
              },
              {
                key: 'interest',
                header: 'Interest',
                num: true,
                render: (r) => (num(r.interest) ? fmt(r.interest) : ''),
              },
              { key: 'balance', header: 'Balance', num: true, render: (r) => fmt(r.balance) },
            ]}
          />
        </Modal>
      ) : null}
    </>
  )
}
