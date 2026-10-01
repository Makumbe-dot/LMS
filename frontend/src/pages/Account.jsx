import { useState } from 'react'

import { useToast } from '../components/Toast.jsx'
import { Field, KeyValues, PageHeader } from '../components/ui.jsx'
import { post, setTokens } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { humanise } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'

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
              ['Role', humanise(user?.role)],
              ['Branch', user?.branch_name || 'Not assigned'],
              ['Phone', user?.phone || '-'],
              ['Email', user?.email || '-'],
              ['Institution', orgName],
              ['Currency', settings?.currency || 'USD'],
            ]}
          />
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            Your name, role and branch are maintained by an administrator under Users.
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
