import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'

import { LogoMark } from '../brand/Brand.jsx'
import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Field, Loading } from '../components/ui.jsx'
import { dateOnly, fmt } from '../lib/format.js'
import {
  getPortalToken,
  portalGet,
  portalOpen,
  portalPost,
  setPortalSignedOutHandler,
  setPortalToken,
} from './portalApi.js'

/**
 * The borrower portal: a borrower's own loans, statements and agreement, and a
 * way to ask for a top-up. Nothing here uses the staff session, and the staff
 * application is never reachable from it.
 */
export default function Portal() {
  const [token, setToken] = useState(getPortalToken())
  const navigate = useNavigate()

  useEffect(() => {
    setPortalSignedOutHandler(() => {
      setToken(null)
      navigate('/portal')
    })
  }, [navigate])

  async function signOut() {
    try {
      await portalPost('/api/portal/logout')
    } catch {
      /* the session may already be over; either way it is gone here */
    }
    setPortalToken(null)
    setToken(null)
    navigate('/portal')
  }

  return (
    <div className="portal">
      <header className="portal-head">
        <LogoMark height={28} />
        <span className="portal-title">My loans</span>
        {token ? (
          <button type="button" className="btn small" onClick={signOut}>
            Sign out
          </button>
        ) : null}
      </header>
      <main className="portal-main">
        {token ? (
          <Routes>
            <Route index element={<PortalHome />} />
            <Route path="loans/:id" element={<PortalLoan />} />
            <Route path="*" element={<Navigate to="/portal" replace />} />
          </Routes>
        ) : (
          <PortalLogin
            onSignedIn={(value) => {
              setPortalToken(value)
              setToken(value)
            }}
          />
        )}
      </main>
    </div>
  )
}

function PortalLogin({ onSignedIn }) {
  const [step, setStep] = useState('details')
  const [challenge, setChallenge] = useState(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setBusy(true)
    setError(null)
    try {
      if (step === 'details') {
        const result = await portalPost('/api/portal/login', {
          national_id: form.get('national_id'),
          phone: form.get('phone'),
        })
        setChallenge(result.challenge)
        setMessage(result.detail)
        setStep('code')
      } else {
        const result = await portalPost('/api/portal/login/verify', {
          challenge,
          code: form.get('code'),
        })
        onSignedIn(result.token)
      }
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card portal-login" onSubmit={submit}>
      <h2>Sign in</h2>
      {step === 'details' ? (
        <>
          <p className="muted">
            Enter your national ID and the phone number we have for you. We will text you a
            code.
          </p>
          <Field label="National ID" name="national_id" required autoComplete="off" />
          <Field label="Phone number" name="phone" type="tel" required autoComplete="tel" />
        </>
      ) : (
        <>
          <p className="muted">{message}</p>
          <Field
            label="Code from the text message"
            name="code"
            required
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
          />
          <button type="button" className="btn small" onClick={() => setStep('details')}>
            Start again
          </button>
        </>
      )}
      {error ? <p className="tag-danger">{error.message}</p> : null}
      <button type="submit" className="btn primary" disabled={busy}>
        {step === 'details' ? 'Text me a code' : 'Sign in'}
      </button>
    </form>
  )
}

function usePortal(path) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [tick, setTick] = useState(0)
  const reload = useCallback(() => setTick((n) => n + 1), [])
  useEffect(() => {
    let live = true
    portalGet(path)
      .then((value) => live && setData(value))
      .catch((err) => live && setError(err))
    return () => {
      live = false
    }
  }, [path, tick])
  return { data, error, reload }
}

function PortalHome() {
  const { data, error, reload } = usePortal('/api/portal/me')
  const requests = usePortal('/api/portal/requests')
  const [asking, setAsking] = useState(false)
  if (error) return <ErrorBanner error={error} onRetry={reload} />
  if (!data) return <Loading what="Fetching your loans" />
  const running = data.loans.filter((l) => l.status === 'active')

  return (
    <>
      <h2>Hello, {data.first_name}</h2>
      <p className="muted">
        Your loans with {data.institution}
        {data.institution_phone ? ` · questions: ${data.institution_phone}` : ''}
      </p>
      {data.loans.length === 0 ? <p>You have no loans with us.</p> : null}
      <div className="portal-cards">
        {data.loans.map((loan) => (
          <Link key={loan.id} to={`loans/${loan.id}`} className="card portal-loan">
            <div className="row between">
              <strong>{loan.loan_no}</strong>
              <span className={Number(loan.arrears) > 0 ? 'tag-danger' : 'tag-ok'}>
                {Number(loan.arrears) > 0
                  ? `${loan.days_in_arrears} days overdue`
                  : loan.status_label}
              </span>
            </div>
            <div className="muted">{loan.product}</div>
            <dl>
              <dt>Still to pay</dt>
              <dd>
                {loan.currency} {fmt(loan.outstanding)}
              </dd>
              {loan.next_due_date ? (
                <>
                  <dt>Next payment</dt>
                  <dd>
                    {loan.currency} {fmt(loan.next_due_amount)} on {dateOnly(loan.next_due_date)}
                  </dd>
                </>
              ) : null}
              {Number(loan.arrears) > 0 ? (
                <>
                  <dt>Overdue now</dt>
                  <dd>
                    {loan.currency} {fmt(loan.arrears)}
                  </dd>
                </>
              ) : null}
            </dl>
          </Link>
        ))}
      </div>

      <div className="card">
        <div className="row between">
          <h3 style={{ margin: 0 }}>Ask us</h3>
          {!asking ? (
            <button type="button" className="btn small primary" onClick={() => setAsking(true)}>
              Ask for a top-up or a call
            </button>
          ) : null}
        </div>
        {asking ? (
          <RequestForm
            loans={running}
            onDone={() => {
              setAsking(false)
              requests.reload()
            }}
          />
        ) : null}
        {(requests.data || []).length ? (
          <DataTable
            caption="Your requests"
            rows={requests.data}
            columns={[
              { key: 'when', header: 'Asked', render: (r) => dateOnly(r.created_at) },
              { key: 'what', header: 'For', render: (r) => r.kind_label },
              {
                key: 'amount',
                header: 'Amount',
                num: true,
                render: (r) => (r.amount ? fmt(r.amount) : '-'),
              },
              {
                key: 'status',
                header: 'Status',
                render: (r) => (r.status === 'done' ? r.outcome || 'Dealt with' : 'Waiting'),
              },
            ]}
          />
        ) : null}
      </div>
    </>
  )
}

function RequestForm({ loans, onDone }) {
  const { toast } = useToast()
  const [kind, setKind] = useState(loans.length ? 'top_up' : 'call_back')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setBusy(true)
    setError(null)
    try {
      await portalPost('/api/portal/requests', {
        kind,
        loan_id: form.get('loan_id') ? Number(form.get('loan_id')) : null,
        amount: form.get('amount') || null,
        message: form.get('message') || '',
      })
      toast('Thank you. We will be in touch.')
      onDone()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} style={{ marginTop: 12 }}>
      <Field
        as="select"
        label="What would you like?"
        value={kind}
        onChange={(e) => setKind(e.target.value)}
      >
        {loans.length ? <option value="top_up">A top-up on a loan</option> : null}
        <option value="call_back">Please call me</option>
      </Field>
      {kind === 'top_up' ? (
        <div className="grid cols-2">
          <Field as="select" label="Loan" name="loan_id" required>
            {loans.map((l) => (
              <option key={l.id} value={l.id}>
                {l.loan_no}
              </option>
            ))}
          </Field>
          <Field label="How much more" name="amount" type="number" min="1" step="0.01" required />
        </div>
      ) : null}
      <Field as="textarea" label="Anything we should know" name="message" rows={3} />
      {error ? <p className="tag-danger">{error.message}</p> : null}
      <div className="row" style={{ gap: 8 }}>
        <button type="submit" className="btn primary" disabled={busy}>
          Send
        </button>
        <button type="button" className="btn" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function PortalLoan() {
  const { id } = useParams()
  const { toastError, toast } = useToast()
  const { data, error, reload } = usePortal(`/api/portal/loans/${id}`)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  if (error) return <ErrorBanner error={error} onRetry={reload} />
  if (!data) return <Loading what="Fetching the loan" />
  const signature = data.signature
  const canSign = ['pending', 'approved', 'active'].includes(data.status) && !signature.signed

  async function act(promise, message) {
    setBusy(true)
    try {
      await promise
      toast(message)
      setCode('')
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <p>
        <Link to="/portal">← All my loans</Link>
      </p>
      <h2>
        {data.loan_no} <small className="muted">{data.product}</small>
      </h2>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <button
          type="button"
          className="btn"
          onClick={() =>
            portalOpen(`/api/portal/loans/${id}/agreement`, {
              title: `Agreement ${data.loan_no}`,
            }).catch(toastError)
          }
        >
          Read the agreement
        </button>
        {data.disbursement_date ? (
          <button
            type="button"
            className="btn"
            onClick={() =>
              portalOpen(`/api/portal/loans/${id}/statement`, {
                download: `statement_${data.loan_no}.pdf`,
              }).catch(toastError)
            }
          >
            Download my statement
          </button>
        ) : null}
      </div>

      {canSign ? (
        <div className={signature.stale ? 'banner' : 'hint'}>
          <p style={{ marginTop: 0 }}>
            {signature.stale
              ? 'The terms changed after you signed. Please read the agreement and sign again.'
              : 'Please read the agreement, then sign it with a code we text you.'}
          </p>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <button
              type="button"
              className="btn small"
              disabled={busy}
              onClick={() =>
                act(portalPost(`/api/portal/loans/${id}/signature/code`), 'Code sent')
              }
            >
              {signature.pending ? 'Send a new code' : 'Text me a signing code'}
            </button>
            {signature.pending ? (
              <form
                className="row"
                style={{ gap: 6 }}
                onSubmit={(e) => {
                  e.preventDefault()
                  act(
                    portalPost(`/api/portal/loans/${id}/signature/verify`, { code }),
                    'Signed. Thank you.',
                  )
                }}
              >
                <input
                  aria-label="Signing code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  style={{ width: 120 }}
                />
                <button
                  type="submit"
                  className="btn small primary"
                  disabled={busy || code.length < 6}
                >
                  I agree, sign
                </button>
              </form>
            ) : null}
          </div>
        </div>
      ) : signature.signed ? (
        <p className="hint">You signed this agreement on {dateOnly(signature.signed_at)}.</p>
      ) : null}

      <div className="card">
        <h3>Payments due</h3>
        <DataTable
          caption="Schedule"
          rows={data.schedule}
          rowKey={(r) => r.number}
          empty="The schedule starts once the loan is paid out."
          columns={[
            { key: 'n', header: '#', num: true, render: (r) => r.number },
            { key: 'due', header: 'Due', render: (r) => dateOnly(r.due_date) },
            { key: 'amount', header: 'Amount', num: true, render: (r) => fmt(r.amount) },
            { key: 'paid', header: 'Paid', num: true, render: (r) => fmt(r.paid) },
            { key: 'left', header: 'Left to pay', num: true, render: (r) => fmt(r.balance) },
          ]}
        />
      </div>
      <div className="card">
        <h3>What you have paid</h3>
        <DataTable
          caption="Payments"
          rows={data.payments}
          rowKey={(r, i) => i}
          empty="No payments yet."
          columns={[
            { key: 'date', header: 'Date', render: (r) => dateOnly(r.date) },
            { key: 'amount', header: 'Amount', num: true, render: (r) => fmt(r.amount) },
            { key: 'how', header: 'How', render: (r) => r.method },
            { key: 'ref', header: 'Reference', render: (r) => r.reference || '-' },
          ]}
        />
      </div>
    </>
  )
}
