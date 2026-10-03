import { useState } from 'react'

import { Field, HexMark } from '../components/ui.jsx'
import { useAuth } from '../lib/auth.jsx'

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
      <form className="card login-card" onSubmit={onSubmit}>
        <div className="login-brand">
          <HexMark size={36} />
          <h1 style={{ margin: 0 }}>Loan Management System</h1>
        </div>

        {mfaToken ? (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              Enter the six-digit code your authenticator app shows for this account.
            </p>
            <Field
              label="Authenticator code"
              name="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9 ]{6,7}"
              maxLength={7}
              required
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <button className="btn primary" type="submit" disabled={busy} style={{ width: '100%' }}>
              {busy ? 'Checking…' : 'Verify and sign in'}
            </button>
            <button
              type="button"
              className="btn-link"
              style={{ marginTop: 10 }}
              onClick={() => {
                setMfaToken(null)
                setCode('')
                setError('')
              }}
            >
              Use a different account
            </button>
            <p className="muted" style={{ fontSize: 12 }}>
              Lost your phone? An administrator can turn two-factor sign-in off for you on the
              Users page.
            </p>
          </>
        ) : (
          <>
            <p className="muted" style={{ marginTop: 0 }}>Sign in to continue</p>
            <Field
              label="Username"
              name="username"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <Field
              label="Password"
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />

            <button className="btn primary" type="submit" disabled={busy} style={{ width: '100%' }}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </>
        )}

        {error ? (
          <p className="error" role="alert" style={{ marginBottom: 0 }}>
            {error}
          </p>
        ) : null}

        {!mfaToken ? (
          <p className="demo">
            Demo logins after <code>manage.py seed</code>:<br />
            admin / admin123 &nbsp;·&nbsp; officer / officer123<br />
            teller / teller123 &nbsp;·&nbsp; viewer / viewer123
          </p>
        ) : null}
      </form>
    </div>
  )
}
