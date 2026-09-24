import { useState } from 'react'

import { Field } from '../components/ui.jsx'
import { useAuth } from '../lib/auth.jsx'

export default function Login() {
  const { signIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function onSubmit(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      await signIn(username, password)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="card login-card" onSubmit={onSubmit}>
        <h1>Loan Management System</h1>
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

        {error ? (
          <p className="error" role="alert" style={{ marginBottom: 0 }}>
            {error}
          </p>
        ) : null}

        <p className="demo">
          Demo logins after <code>manage.py seed</code>:<br />
          admin / admin123 &nbsp;·&nbsp; officer / officer123<br />
          teller / teller123 &nbsp;·&nbsp; viewer / viewer123
        </p>
      </form>
    </div>
  )
}
