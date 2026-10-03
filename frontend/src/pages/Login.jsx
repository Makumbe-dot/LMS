import { useState } from 'react'

import { GroupLogos, GroupWordmark } from '../brand/brand.jsx'
import { useAuth } from '../lib/auth.jsx'

/**
 * One centred card on the honeycomb page, as on the group's ERM sign-in: the
 * companies' logos, "Welcome to", the wordmark, then the form.
 */
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
      <form className="signin-card" onSubmit={onSubmit} aria-labelledby="signin-title">
        <GroupLogos height={mfaToken ? 64 : 84} className="signin-logos" />
        <p className="signin-welcome">Welcome to</p>
        <GroupWordmark as="h1" className="signin-wordmark" id="signin-title" suffix="LMS" />

        {error ? (
          <p className="signin-err" role="alert">
            {error}
          </p>
        ) : null}

        {mfaToken ? (
          <>
            <p className="signin-note">
              Enter the six-digit code your authenticator app shows for this account.
            </p>
            <label className="signin-label" htmlFor="signin-code">
              Authenticator code
            </label>
            <input
              id="signin-code"
              className="signin-input"
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
    </div>
  )
}
