import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import Modal, { FormModal } from '../components/Modal.jsx'
import Scorecard from '../components/Scorecard.jsx'
import { useToast } from '../components/Toast.jsx'
import { Badge, ErrorBanner, Field, KeyValues, Loading, PageHeader } from '../components/ui.jsx'
import { del, get, openHtml, patch, post } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import {
  dateOnly,
  dateTime,
  fmt,
  getCurrency,
  humanise,
  money,
  num,
  pct,
  rateMethodLabel,
  today,
} from '../lib/format.js'
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
          label="New term (months)"
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
              ['Monthly instalment', <strong key="i">{money(quote.new_instalment)}</strong>],
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

export default function LoanDetail() {
  const { id } = useParams()
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const { data: loan, error, loading, reload } = useApi(`/api/loans/${id}`)

  const [tab, setTab] = useState('schedule')
  const [action, setAction] = useState(null) // { kind, txnId? }
  const [statement, setStatement] = useState(null)
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
      setStatement(await get(`/api/loans/${id}/statement`))
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

  const isOfficer = can('admin', 'loan_officer')
  const isTeller = can('admin', 'loan_officer', 'teller')
  const isAdmin = can('admin')
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
        {isTeller && loan.status === 'active' ? (
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
        {isOfficer && loan.status === 'pending' ? (
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
        {isOfficer && loan.status === 'approved' ? (
          <>
            <button
              type="button"
              className="btn primary"
              onClick={() => setAction({ kind: 'disburse' })}
            >
              Disburse
            </button>
            <button type="button" className="btn danger" onClick={() => setAction({ kind: 'reject' })}>
              Reject
            </button>
          </>
        ) : null}
        {isOfficer && loan.status === 'active' ? (
          <button
            type="button"
            className="btn"
            onClick={() => run(post(`/api/loans/${id}/accrue-penalties`), 'Penalties accrued')}
            disabled={busy}
          >
            Accrue penalties
          </button>
        ) : null}
        {isAdmin && loan.status === 'active' ? (
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
        {isTeller && loan.status === 'written_off' ? (
          <button
            type="button"
            className="btn primary"
            onClick={() => setAction({ kind: 'recovery' })}
          >
            Record a recovery
          </button>
        ) : null}
        {isOfficer && loan.status === 'active' ? (
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
              ['Principal', money(loan.principal)],
              [
                'Rate',
                `${pct(loan.interest_rate_pct)} per month, ${rateMethodLabel(loan.rate_method).toLowerCase()}`,
              ],
              ['Term', `${loan.term_months} months`],
              ['Instalment', money(loan.instalment_amount)],
              ['Total interest', money(loan.total_interest)],
              [
                'Upfront fees',
                money((num(loan.admin_fee) ?? 0) + (num(loan.insurance_fee) ?? 0)),
              ],
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
              ['Principal outstanding', money(loan.principal_outstanding)],
              ['Interest outstanding', money(loan.interest_outstanding)],
              ['Penalties outstanding', money(loan.penalties_outstanding)],
              ['Total outstanding', <strong key="t">{money(loan.total_outstanding)}</strong>],
              ['Total paid', money(loan.total_paid)],
              [
                'Arrears',
                arrears > 0 ? (
                  <span className="tag-danger">
                    {money(loan.arrears_amount)} ({loan.days_in_arrears} days)
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
      ) : tab === 'security' ? (
        <div className="card">
          <div className="row between" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Security pledged</h3>
            {isOfficer ? (
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
                  isOfficer ? (
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
              {money(
                (loan.collateral || [])
                  .filter((c) => c.status === 'pledged')
                  .reduce((sum, c) => sum + (num(c.estimated_value) ?? 0), 0),
              )}{' '}
              against {money(loan.total_outstanding)} outstanding.
            </p>
          ) : null}
        </div>
      ) : tab === 'notes' ? (
        <div className="card">
          <div className="row between" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Collections follow-ups</h3>
            {isTeller ? (
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
                {isTeller ? (
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
            { key: 'method', header: 'Method', render: (t) => t.method || '-' },
            { key: 'reference', header: 'Reference', render: (t) => t.reference || '-' },
            { key: 'narration', header: 'Narration', render: (t) => t.narration || '' },
            {
              key: 'actions',
              header: '',
              render: (t) =>
                t.reversed ? (
                  <span className="tag-danger">Reversed</span>
                ) : t.txn_type === 'repayment' && isOfficer && loan.status !== 'written_off' ? (
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
          <div className="grid cols-2">
            <Field label="Disbursement date" type="date" name="disbursement_date" defaultValue={today()} />
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
            <Field label="Date" type="date" name="txn_date" defaultValue={today()} />
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
            <Field label="Date" type="date" name="txn_date" defaultValue={today()} />
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
            Total outstanding is {money(loan.total_outstanding)}; more than that is refused.
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
            This writes off <strong>{money(loan.total_outstanding)}</strong> and closes the loan. It
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
              label="New term (months)"
              type="number"
              min="1"
              name="new_term_months"
              required
              defaultValue={loan.term_months}
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
              ['Principal outstanding', money(settlement.principal_outstanding)],
              ['Interest accrued to date', money(settlement.interest_accrued)],
              ['Penalties outstanding', money(settlement.penalties_outstanding)],
              [
                'Interest rebate',
                <span key="r" className="tag-ok">
                  −{money(settlement.interest_rebate)}
                </span>,
              ],
              [
                'Settlement figure',
                <strong key="s" style={{ fontSize: 16 }}>
                  {money(settlement.settlement_amount)}
                </strong>,
              ],
              ['If run to term', money(settlement.total_outstanding)],
              [
                'Borrower saves',
                <span key="v" className="tag-ok">
                  {money(settlement.saving_vs_running_to_term)}
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
                Take {money(settlement.settlement_amount)} and close
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
            <button type="button" className="btn small" onClick={() => window.print()}>
              Print
            </button>
          }
        >
          <KeyValues
            items={[
              ['Borrower', `${statement.borrower} (${statement.national_id})`],
              ['Product', statement.product],
              [
                'Principal',
                `${money(statement.principal)} at ${pct(statement.rate_pct)}/month over ${statement.term} months`,
              ],
              [
                'Disbursed',
                `${statement.disbursement_date || '-'} (maturity ${statement.maturity_date || '-'})`,
              ],
              [
                'Total outstanding',
                <strong key="o">
                  {money(statement.total_outstanding)} (principal{' '}
                  {fmt(statement.principal_outstanding)}, interest{' '}
                  {fmt(statement.interest_outstanding)}, penalties{' '}
                  {fmt(statement.penalties_outstanding)})
                </strong>,
              ],
            ]}
          />
          <p className="muted" style={{ fontSize: 12 }}>
            Interest is recognised instalment by instalment, so the running balance below is
            principal plus penalties less payments.
          </p>
          <DataTable
            caption="Statement lines"
            rows={statement.lines}
            rowKey={(row, index) => index}
            empty="No postings yet"
            columns={[
              { key: 'date', header: 'Date', render: (r) => r.date },
              { key: 'type', header: 'Type', render: (r) => <Badge value={r.type} /> },
              { key: 'narration', header: 'Narration', render: (r) => r.narration || '' },
              { key: 'ref', header: 'Ref', render: (r) => r.reference || '' },
              { key: 'debit', header: 'Debit', num: true, render: (r) => fmt(r.debit) },
              { key: 'credit', header: 'Credit', num: true, render: (r) => fmt(r.credit) },
              { key: 'balance', header: 'Balance', num: true, render: (r) => fmt(r.balance) },
            ]}
          />
        </Modal>
      ) : null}
    </>
  )
}
