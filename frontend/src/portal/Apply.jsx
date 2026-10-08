/* The public Apply page: a new client asks for a loan. No sign-in.

   What it sends is an application for staff to review, not a loan: they call the
   applicant, collect KYC documents, and create the loan the usual way. The hidden
   "website" field is a trap for form-filling robots; people never see it. */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { LogoMark } from '../brand/Brand.jsx'
import { Check, Field } from '../components/ui.jsx'
import { fmt } from '../lib/format.js'

const BASE = import.meta.env.VITE_API_BASE || ''

const BLANK = {
  first_name: '',
  last_name: '',
  national_id: '',
  phone: '',
  email: '',
  address: '',
  income_source: 'employed',
  employer: '',
  business_name: '',
  net_salary: '',
  payday: '',
  product_id: '',
  amount: '',
  term_months: '',
  purpose: '',
  consent: false,
  website: '',
}

export default function Apply() {
  const [products, setProducts] = useState([])
  const [values, setValues] = useState(BLANK)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)

  useEffect(() => {
    fetch(`${BASE}/api/public/products`)
      .then((res) => (res.ok ? res.json() : []))
      .then((list) => {
        setProducts(list)
        if (list.length) setValues((v) => ({ ...v, product_id: String(list[0].id) }))
      })
      .catch(() => setProducts([]))
  }, [])

  const product = useMemo(
    () => products.find((p) => String(p.id) === String(values.product_id)),
    [products, values.product_id],
  )
  const set = (key) => (e) =>
    setValues((v) => ({ ...v, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const res = await fetch(`${BASE}/api/public/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...values, product_id: Number(values.product_id) }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(body.detail || 'Your application could not be sent. Please try again.')
      setDone(body)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="portal">
      <header className="portal-head">
        <LogoMark height={28} />
        <span className="portal-title">Apply for a loan</span>
        <Link className="btn small" to="/portal">
          Already a client? Sign in
        </Link>
      </header>
      <main className="portal-main">
        {done ? (
          <div className="card apply-done">
            <h2>Thank you</h2>
            <p>
              We have your application. Your reference is <strong>{done.reference}</strong>.
            </p>
            <p className="muted">
              One of our loan officers will contact you on the number you gave, to talk it through and
              to collect copies of your ID, payslip and proof of address.
            </p>
          </div>
        ) : (
          <form className="card apply-form" onSubmit={submit}>
            <h2 style={{ marginTop: 0 }}>Apply for a loan</h2>
            <p className="muted">
              It takes a few minutes. We will call you to talk it through before anything is agreed.
            </p>

            <h3>The loan</h3>
            {products.length === 0 ? (
              <p className="muted">Loads the loans we offer…</p>
            ) : (
              <div className="grid cols-3">
                <Field as="select" label="Loan" value={values.product_id} onChange={set('product_id')} required>
                  {products.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </Field>
                <Field
                  label={`Amount (${product?.currency || ''})`}
                  type="number"
                  min={product?.min_amount}
                  max={product?.max_amount}
                  step="0.01"
                  required
                  value={values.amount}
                  onChange={set('amount')}
                  hint={product ? `From ${fmt(product.min_amount)} to ${fmt(product.max_amount)}` : undefined}
                />
                <Field
                  label="Over how many months"
                  type="number"
                  min={product?.min_term}
                  max={product?.max_term}
                  required
                  value={values.term_months}
                  onChange={set('term_months')}
                  hint={product ? `${product.min_term} to ${product.max_term}` : undefined}
                />
              </div>
            )}
            <Field label="What it is for" value={values.purpose} onChange={set('purpose')} maxLength={200} />

            <h3>About you</h3>
            <div className="grid cols-3">
              <Field label="First name" required value={values.first_name} onChange={set('first_name')} />
              <Field label="Surname" required value={values.last_name} onChange={set('last_name')} />
              <Field label="National ID" required value={values.national_id} onChange={set('national_id')} />
              <Field label="Mobile number" required value={values.phone} onChange={set('phone')} placeholder="0771 234 567" />
              <Field label="Email (optional)" type="email" value={values.email} onChange={set('email')} />
              <Field as="select" label="I earn my income" value={values.income_source} onChange={set('income_source')}>
                <option value="employed">From a job (salary)</option>
                <option value="self_employed">From my own business</option>
                <option value="informal">As an informal trader</option>
                <option value="farmer">From farming</option>
              </Field>
              {values.income_source === 'employed' ? (
                <Field label="Employer" value={values.employer} onChange={set('employer')} />
              ) : (
                <Field label="Business or trade" required value={values.business_name} onChange={set('business_name')}
                       placeholder="e.g. hardware shop, Mbare" />
              )}
              <Field
                label={values.income_source === 'employed' ? 'Net monthly salary' : 'What the business leaves you a month'}
                type="number" min="0" step="0.01" value={values.net_salary} onChange={set('net_salary')}
                hint={values.income_source === 'employed' ? undefined : 'Sales less stock and running costs'}
              />
              <Field
                label={values.income_source === 'employed' ? 'Payday (day of the month)' : 'Best day of the month to pay'}
                type="number" min="1" max="31" value={values.payday} onChange={set('payday')}
              />
            </div>
            <Field as="textarea" label="Home address" rows={2} value={values.address} onChange={set('address')} />

            {/* A trap for robots: hidden from people, filled in by scripts. */}
            <div className="apply-trap" aria-hidden="true">
              <label>
                Website <input tabIndex={-1} autoComplete="off" value={values.website} onChange={set('website')} />
              </label>
            </div>

            <Check
              label="I agree to be contacted about this application, and to my details being checked with my employer (if I have one) and a credit bureau."
              checked={values.consent}
              onChange={set('consent')}
              required
            />
            {error ? <p className="signin-err" role="alert">{error}</p> : null}
            <button className="btn primary" type="submit" disabled={busy || !products.length}>
              {busy ? 'Sending…' : 'Send my application'}
            </button>
          </form>
        )}
      </main>
    </div>
  )
}
