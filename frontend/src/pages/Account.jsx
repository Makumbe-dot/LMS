import QRCode from 'qrcode'
import { useEffect, useState } from 'react'

import { useToast } from '../components/Toast.jsx'
import { Field, KeyValues, PageHeader } from '../components/ui.jsx'
import { post, setTokens } from '../lib/api.js'
import { rightsLabel, roleLabel, useAuth } from '../lib/auth.jsx'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

/** "JBSWY3DPEHPK3PXP" -> "JBSW Y3DP EHPK 3PXP", for typing into an app by hand. */
const grouped = (secret) => secret.replace(/(.{4})/g, '$1 ').trim()

/**
 * Two-factor sign-in: set up with an authenticator app, or turn off with both
 * factors. Setting up does nothing until a code from the new secret is confirmed,
 * so walking away halfway cannot lock anyone out.
 */
function TwoFactorCard() {
  const { user, updateUser } = useAuth()
  const catalogue = useApi('/api/users/rights').data
  const { toast, toastError } = useToast()
  const [setup, setSetup] = useState(null) // { secret, otpauth_uri }
  const [qr, setQr] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!setup) return
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 180 })
      .then(setQr)
      .catch(() => setQr('')) // the setup key below still works without the picture
  }, [setup])

  async function act(promise, message, after) {
    setBusy(true)
    try {
      const result = await promise
      after?.(result)
      if (message) toast(message)
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  if (user?.mfa_enabled) {
    return (
      <form
        className="card"
        onSubmit={(e) => {
          e.preventDefault()
          act(post('/api/auth/mfa/disable', { password, code }), 'Two-factor sign-in is off', (u) => {
            updateUser(u)
            setPassword('')
            setCode('')
          })
        }}
      >
        <h3>
          Two-factor sign-in <span className="tag-ok">On</span>
        </h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Signing in takes your password and a code from your authenticator app. Turning it off
          takes both too, so a browser left signed in is not enough to remove it.
        </p>
        <div className="grid cols-2">
          <Field
            label="Password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Field
            label="Current code"
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
        <button type="submit" className="btn danger" disabled={busy}>
          Turn two-factor sign-in off
        </button>
      </form>
    )
  }

  return (
    <div className="card">
      <h3>
        Two-factor sign-in <span className="tag-warn">Off</span>
      </h3>
      {!setup ? (
        <>
          <p className="muted" style={{ marginTop: 0 }}>
            Ask for a code from an authenticator app on your phone as well as your password, so a
            password that leaks is not enough to sign in as you. Strongly advised for anyone who
            approves loans, posts journals or handles cash.
          </p>
          <button
            type="button"
            className="btn primary"
            disabled={busy}
            onClick={() => act(post('/api/auth/mfa/setup'), null, setSetup)}
          >
            Set it up
          </button>
        </>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            act(post('/api/auth/mfa/enable', { code }), 'Two-factor sign-in is on', (u) => {
              updateUser(u)
              setSetup(null)
              setCode('')
            })
          }}
        >
          <ol className="muted" style={{ marginTop: 0, paddingLeft: 18 }}>
            <li>Open Google Authenticator, Microsoft Authenticator or a similar app.</li>
            <li>Scan this code, or type the setup key in by hand.</li>
            <li>Enter the six digits the app shows to finish.</li>
          </ol>
          <div className="row" style={{ alignItems: 'center', gap: 20 }}>
            {qr ? <img src={qr} alt="QR code for your authenticator app" width={180} height={180} /> : null}
            <div>
              <div className="muted" style={{ fontSize: 12 }}>
                Setup key
              </div>
              <code style={{ fontSize: 15, letterSpacing: 1 }}>{grouped(setup.secret)}</code>
            </div>
          </div>
          <Field
            label="Code from the app"
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <div className="row">
            <button type="submit" className="btn primary" disabled={busy}>
              Turn it on
            </button>
            <button type="button" className="btn" onClick={() => setSetup(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  )
}

export default function Account() {
  const { user, signOutEverywhere } = useAuth()
  const { orgName, settings } = useOrg()
  const { toast, toastError } = useToast()
  const [form, setForm] = useState({ current_password: '', new_password: '', confirm: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const set = (key) => (e) => {
    setForm((f) => ({ ...f, [key]: e.target.value }))
    setError('')
  }

  async function submit(event) {
    event.preventDefault()
    if (form.new_password !== form.confirm) {
      setError('The two new passwords do not match.')
      return
    }
    setBusy(true)
    try {
      const result = await post('/api/auth/change-password', {
        current_password: form.current_password,
        new_password: form.new_password,
      })
      // The change ends every session for this user, including this one, so the
      // server hands back a fresh pair. Storing it is what keeps the person who
      // just did the right thing from being bounced to the sign-in screen.
      setTokens(result)
      toast(result.detail)
      setForm({ current_password: '', new_password: '', confirm: '' })
    } catch (err) {
      setError(err.message)
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader title="My account" />

      <div className="grid cols-2" style={{ alignItems: 'start' }}>
        <div className="card">
          <h3>Profile</h3>
          <KeyValues
            items={[
              ['Username', user?.username],
              ['Full name', user?.full_name],
              ['Access', roleLabel(user)],
              ...(user?.role === 'admin'
                ? []
                : [['Access rights', rightsLabel(user, catalogue)]]),
              ['Branch', user?.branch_name || 'Not assigned'],
              ['Phone', user?.phone || '-'],
              ['Email', user?.email || '-'],
              ['Institution', orgName],
              ['Currency', settings?.currency || 'USD'],
            ]}
          />
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            Your name, access rights and branch are maintained by an administrator under Users.
          </p>
        </div>

        <form className="card" onSubmit={submit}>
          <h3>Change password</h3>
          <Field
            label="Current password"
            type="password"
            autoComplete="current-password"
            required
            value={form.current_password}
            onChange={set('current_password')}
          />
          <Field
            label="New password"
            type="password"
            autoComplete="new-password"
            minLength={6}
            required
            value={form.new_password}
            onChange={set('new_password')}
            hint="At least 6 characters, and different from the current one"
          />
          <Field
            label="Confirm new password"
            type="password"
            autoComplete="new-password"
            minLength={6}
            required
            value={form.confirm}
            onChange={set('confirm')}
          />
          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="row">
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? 'Saving…' : 'Change password'}
            </button>
          </div>
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            Changing your password signs you out everywhere else. This device stays
            signed in.
          </p>
        </form>
      </div>

      <TwoFactorCard />

      <div className="card">
        <h3>Sessions</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Signing out on this device leaves your other devices signed in. If you have
          left a session open somewhere you no longer control — a shared counter
          machine, a lost phone — end them all. It takes effect immediately, not
          whenever the other device's token expires.
        </p>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true)
            try {
              await signOutEverywhere()
            } catch (err) {
              toastError(err)
            } finally {
              setBusy(false)
            }
          }}
        >
          Sign out on every device
        </button>
      </div>
    </>
  )
}
