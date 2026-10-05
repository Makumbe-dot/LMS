import { useState } from 'react'

import Icon from '../components/Icons.jsx'
import { HexMark } from '../components/ui.jsx'
import { useAuth } from '../lib/auth.jsx'

/**
 * The sign-in page: a navy panel that says what the system is, and the form
 * beside it. Below 900px the panel becomes a short banner above the form.
 */
const POINTS = [
  { icon: 'loans', title: 'Salary and group loans', sub: 'Monthly, fortnightly or weekly' },
  { icon: 'book', title: 'A ledger that keeps itself', sub: 'Every movement posted, every month reconciled' },
  { icon: 'shield', title: 'IFRS 9 staging and provisions', sub: 'With a trial balance that always balances' },
]

export default function Login() {
  const { signIn, completeSignIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  // Set once the password is right on an account with two-factor sign-in on.
  const [mfaToken, setMfaToken] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function onSubmit(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (mfaToken) {
        await completeSignIn(mfaToken, code)
      } else {
        const result = await signIn(username, password)
        if (result?.mfaToken) {
          setMfaToken(result.mfaToken)
          setPassword('')
        }
      }
    } catch (err) {
      setError(err.message)
      // An expired half-finished sign-in starts again from the password.
      if (mfaToken && err.status === 401 && /expired/i.test(err.message)) setMfaToken(null)
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <aside className="login-panel" aria-hidden="true">
        <div className="login-brand">
          <HexMark size={34} />
          <span>Loan Management System</span>
        </div>
        <div className="login-story">
          <p className="login-tag">Built for microfinance</p>
          <h1>Lending that keeps its own books.</h1>
          <p className="login-lead">
            Quote, approve, disburse and collect, while the ledger, arrears and provisions keep
            up on their own.
          </p>
          <ul className="login-points">
            {POINTS.map((point) => (
              <li key={point.title}>
                <span className="login-point-icon">
                  <Icon name={point.icon} />
                </span>
                <span>
                  <strong>{point.title}</strong>
                  <small>{point.sub}</small>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </aside>

      <main className="login-side">
        <form className="signin-card" onSubmit={onSubmit} aria-labelledby="signin-title">
          <div className="signin-mark">
            <HexMark size={40} />
          </div>
          <h2 className="signin-wordmark" id="signin-title">
            {mfaToken ? 'One more step' : 'Welcome back'}
          </h2>
          <p className="signin-welcome">
            {mfaToken
              ? 'Enter the six-digit code your authenticator app shows for this account.'
              : 'Sign in to your account to continue.'}
          </p>

          {error ? (
            <p className="signin-err" role="alert">
              {error}
            </p>
          ) : null}

          {mfaToken ? (
            <>
              <label className="signin-label" htmlFor="signin-code">
                Authenticator code
              </label>
              <input
                id="signin-code"
                className="signin-input signin-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9 ]{6,7}"
                maxLength={7}
                required
                autoFocus
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
              <button className="btn primary signin-btn" type="submit" disabled={busy}>
                {busy ? 'Checking…' : 'Verify and sign in'}
              </button>
              <div className="signin-hint">
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => {
                    setMfaToken(null)
                    setCode('')
                    setError('')
                  }}
                >
                  Use a different account
                </button>
                <br />
                Lost your phone? An administrator can turn two-factor sign-in off for you on the
                Users page.
              </div>
            </>
          ) : (
            <>
              <label className="signin-label" htmlFor="signin-username">
                Username
              </label>
              <input
                id="signin-username"
                className="signin-input"
                name="username"
                autoComplete="username"
                required
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
              <label className="signin-label" htmlFor="signin-password">
                Password
              </label>
              <input
                id="signin-password"
                className="signin-input"
                type="password"
                name="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button className="btn primary signin-btn" type="submit" disabled={busy}>
                {busy ? 'Signing in…' : 'Sign in'}
              </button>
              <div className="signin-hint">
                Forgot your password? Ask your administrator to reset it.
                <span className="demo">
                  Demo logins after <code>manage.py seed</code>: admin / admin123 · officer /
                  officer123 · teller / teller123 · viewer / viewer123
                </span>
              </div>
            </>
          )}
        </form>
      </main>
    </div>
  )
}
