import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import Scorecard from '../components/Scorecard.jsx'
import { useToast } from '../components/Toast.jsx'
import { Check, ErrorBanner, Field, KeyValues, Loading, PageHeader } from '../components/ui.jsx'
import { post } from '../lib/api.js'
import { fmt, getCurrency, money, pct, rateMethodLabel } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

export default function LoanNew() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { toast, toastError } = useToast()

  const borrowerPage = useApi('/api/borrowers?page_size=1000')
  const products = useApi('/api/products')
  const borrowers = { ...borrowerPage, data: borrowerPage.data?.results }

  const [form, setForm] = useState({
    borrower_id: params.get('borrower') || '',
    product_id: '',
    principal: '',
    term_months: '',
    purpose: '',
  })
  const [quote, setQuote] = useState(null)
  const [quoteError, setQuoteError] = useState('')
  const [busy, setBusy] = useState(false)
  // Which of the borrower's guarantors stand behind this loan. null until the
  // officer touches a box, meaning "all of them" - the server's default too.
  const [guarantorIds, setGuarantorIds] = useState(null)

  // Default to the first active product once the list arrives.
  useEffect(() => {
    if (products.data?.length && !form.product_id) {
      setForm((f) => ({ ...f, product_id: String(products.data[0].id) }))
    }
  }, [products.data, form.product_id])

  const product = products.data?.find((p) => String(p.id) === String(form.product_id))
  const borrower = borrowers.data?.find((b) => String(b.id) === String(form.borrower_id))
  const heldGuarantors = borrower?.guarantors || []
  const chosen = guarantorIds ?? heldGuarantors.map((g) => g.id)
  const set = (key) => (event) => {
    setForm((f) => ({ ...f, [key]: event.target.value }))
    if (key === 'borrower_id') setGuarantorIds(null)
    setQuote(null)
    setQuoteError('')
  }
  const toggleGuarantor = (guarantorId) => (event) =>
    setGuarantorIds(
      event.target.checked
        ? [...chosen, guarantorId]
        : chosen.filter((existing) => existing !== guarantorId),
    )

  async function previewQuote() {
    setQuoteError('')
    if (!form.product_id || !form.principal || !form.term_months) {
      setQuoteError('Choose a product, then enter an amount and a term.')
      return
    }
    try {
      const result = await post('/api/loans/quote', {
        product_id: Number(form.product_id),
        principal: form.principal,
        term_months: Number(form.term_months),
        borrower_id: form.borrower_id ? Number(form.borrower_id) : null,
      })
      setQuote(result)
    } catch (err) {
      setQuote(null)
      setQuoteError(err.message)
    }
  }

  async function onSubmit(event) {
    event.preventDefault()
    setBusy(true)
    try {
      const loan = await post('/api/loans', {
        borrower_id: Number(form.borrower_id),
        product_id: Number(form.product_id),
        principal: form.principal,
        term_months: Number(form.term_months),
        purpose: form.purpose || null,
        guarantor_ids: chosen,
      })
      toast(`Application ${loan.loan_no} captured`)
      navigate(`/loans/${loan.id}`)
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  if (borrowers.loading || products.loading) return <Loading what="Loading borrowers and products" />

  return (
    <>
      <PageHeader title="New loan application" />
      <ErrorBanner error={borrowers.error || products.error} />

      <div className="grid cols-2" style={{ alignItems: 'start' }}>
        <form className="card" onSubmit={onSubmit}>
          <Field
            as="select"
            label="Borrower"
            required
            value={form.borrower_id}
            onChange={set('borrower_id')}
          >
            <option value="">Select a borrower</option>
            {(borrowers.data || []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.borrower_no} - {b.first_name} {b.last_name}
                {b.kyc_verified ? '' : ' (KYC pending)'}
                {b.is_blacklisted ? ' (blacklisted)' : ''}
              </option>
            ))}
          </Field>

          <Field
            as="select"
            label="Product"
            required
            value={form.product_id}
            onChange={set('product_id')}
          >
            {(products.data || []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} - {p.interest_rate_pct}%/month {p.rate_method === 'flat' ? 'flat' : ''},{' '}
                {fmt(p.min_amount)} to {fmt(p.max_amount)}, {p.min_term_months}-
                {p.max_term_months}m
              </option>
            ))}
          </Field>
          {product ? (
            <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
              {rateMethodLabel(product.rate_method)}
              {product.rate_method === 'flat'
                ? ' — interest is charged on the original principal for the whole term.'
                : ' — interest is charged on the balance still outstanding.'}
            </p>
          ) : null}

          <div className="grid cols-2">
            <Field
              label={`Principal (${getCurrency()})`}
              type="number"
              step="0.01"
              min={product?.min_amount || 1}
              max={product?.max_amount || undefined}
              required
              value={form.principal}
              onChange={set('principal')}
              hint={product ? `${fmt(product.min_amount)} to ${fmt(product.max_amount)}` : undefined}
            />
            <Field
              label="Term (months)"
              type="number"
              min={product?.min_term_months || 1}
              max={product?.max_term_months || undefined}
              required
              value={form.term_months}
              onChange={set('term_months')}
              hint={
                product
                  ? `${product.min_term_months} to ${product.max_term_months} months`
                  : undefined
              }
            />
          </div>

          <Field label="Purpose" value={form.purpose} onChange={set('purpose')} />

          {borrower ? (
            <fieldset>
              <legend>Guarantors of this loan</legend>
              {heldGuarantors.length === 0 ? (
                <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
                  {borrower.first_name} {borrower.last_name} has no guarantors on file.
                </p>
              ) : (
                heldGuarantors.map((g) => (
                  <Check
                    key={g.id}
                    label={`${g.full_name} (${g.national_id})`}
                    checked={chosen.includes(g.id)}
                    onChange={toggleGuarantor(g.id)}
                  />
                ))
              )}
            </fieldset>
          ) : null}

          <div className="row">
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? 'Submitting…' : 'Submit application'}
            </button>
            <button type="button" className="btn" onClick={previewQuote}>
              Preview quote
            </button>
            <Link className="btn" to="/loans">
              Cancel
            </Link>
          </div>
        </form>

        <div className="card">
          <h3>Quote</h3>
          {quoteError ? <p className="error">{quoteError}</p> : null}
          {!quote && !quoteError ? (
            <p className="muted">
              Enter an amount and a term, then preview to see the instalment, the fees deducted at
              disbursement and the affordability check.
            </p>
          ) : null}
          {quote ? (
            <>
              <KeyValues
                items={[
                  ['Monthly instalment', <strong key="i">{money(quote.instalment_amount)}</strong>],
                  ['Method', `${pct(quote.interest_rate_pct)} / month, ${rateMethodLabel(quote.rate_method).toLowerCase()}`],
                  ['Total interest', money(quote.total_interest)],
                  ['Total repayable', money(quote.total_repayable)],
                  ['Admin fee', money(quote.admin_fee)],
                  ['Credit life fee', money(quote.insurance_fee)],
                  ...(quote.charges || []).map((c) => [c.name, money(c.amount)]),
                  ['Net disbursed', <strong key="n">{money(quote.net_disbursed)}</strong>],
                  ['Total cost of credit', money(quote.total_cost_of_credit)],
                  [
                    'APR, fees included',
                    quote.apr_pct === null ? '-' : <strong key="apr">{pct(quote.apr_pct)} a year</strong>,
                  ],
                  ...(quote.affordability_pct !== null
                    ? [
                        [
                          'Instalment / salary',
                          <span key="a" className={quote.affordable ? 'tag-ok' : 'tag-danger'}>
                            {pct(quote.affordability_pct)}{' '}
                            {quote.affordable ? '(affordable)' : '(exceeds the product limit)'}
                          </span>,
                        ],
                      ]
                    : []),
                ]}
              />
              {quote.scorecard ? (
                <>
                  <h3 style={{ marginTop: 20 }}>Credit assessment</h3>
                  <Scorecard card={quote.scorecard} />
                </>
              ) : null}

              <h3 style={{ marginTop: 20 }}>Indicative schedule</h3>
              <DataTable
                caption="Indicative repayment schedule"
                rows={quote.schedule}
                rowKey={(row) => row.number}
                columns={[
                  { key: 'n', header: '#', num: true, render: (r) => r.number },
                  { key: 'due', header: 'Due', render: (r) => r.due_date },
                  {
                    key: 'principal',
                    header: 'Principal',
                    num: true,
                    render: (r) => fmt(r.principal_due),
                  },
                  {
                    key: 'interest',
                    header: 'Interest',
                    num: true,
                    render: (r) => fmt(r.interest_due),
                  },
                  {
                    key: 'instalment',
                    header: 'Instalment',
                    num: true,
                    render: (r) => fmt(r.instalment),
                  },
                  {
                    key: 'balance',
                    header: 'Balance',
                    num: true,
                    render: (r) => fmt(r.closing_balance),
                  },
                ]}
              />
            </>
          ) : null}
        </div>
      </div>
    </>
  )
}
