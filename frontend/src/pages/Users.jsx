import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Check, ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { patch, post } from '../lib/api.js'
import { rightsLabel } from '../lib/auth.jsx'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'


export default function Users() {
  const { toast, toastError } = useToast()
  const { activeBranches } = useOrg()
  const { data, error, loading, reload } = useApi('/api/users')
  const catalogue = useApi('/api/users/rights').data
  const [editing, setEditing] = useState(null) // user object or 'new'
  const [busy, setBusy] = useState(false)
  // The ticks and the role are held here rather than read off the form, so a
  // preset can tick several boxes at once and the boxes hide for an administrator.
  const [role, setRole] = useState('user')
  const [rights, setRights] = useState([])

  function open(target) {
    setRole(target === 'new' ? 'user' : target.role)
    setRights(target === 'new' ? [] : target.role === 'admin' ? [] : target.rights || [])
    setEditing(target)
  }

  function toggle(code, on) {
    setRights((held) => (on ? [...held, code] : held.filter((c) => c !== code)))
  }

  async function save(values) {
    setBusy(true)
    try {
      values = { ...values, role, rights: role === 'admin' ? [] : rights }
      if (values.approval_limit === undefined) delete values.approval_limit
      if (editing === 'new') {
        const body = { ...values }
        if (body.branch_id) body.branch = Number(body.branch_id)
        delete body.branch_id
        delete body.unlock
        delete body.reset_mfa
        await post('/api/users', body)
      } else {
        const body = { ...values }
        delete body.username
        if (!body.password) delete body.password
        body.branch_id = body.branch_id ? Number(body.branch_id) : null
        await patch(`/api/users/${editing.id}`, body)
      }
      setEditing(null)
      reload()
      toast('User saved')
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  const user = editing === 'new' ? null : editing

  return (
    <>
      <PageHeader
        title="Users"
        meta="An administrator can do everything. Everyone else can do what you tick for them, and with nothing ticked can only read."
      >
        <button type="button" className="btn primary" onClick={() => open('new')}>
          New user
        </button>
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading users" />
      ) : (
        <DataTable
          caption="Users"
          rows={data || []}
          onRowClick={(row) => open(row)}
          empty="No users"
          columns={[
            { key: 'username', header: 'Username', render: (r) => r.username },
            { key: 'name', header: 'Full name', render: (r) => r.full_name },
            {
              key: 'role',
              header: 'Access',
              render: (r) => (
                <span className={r.role === 'admin' ? 'tag-warn' : undefined}>
                  {r.role === 'admin' ? 'Administrator' : rightsLabel(r, catalogue)}
                </span>
              ),
            },
            { key: 'branch', header: 'Branch', render: (r) => r.branch_name || '-' },
            { key: 'phone', header: 'Phone', render: (r) => r.phone || '-' },
            { key: 'email', header: 'Email', render: (r) => r.email || '-' },
            {
              key: 'active',
              header: 'Active',
              render: (r) =>
                r.is_active ? (
                  <span className="tag-ok">Yes</span>
                ) : (
                  <span className="tag-danger">No</span>
                ),
            },
            {
              key: 'mfa',
              header: 'Two-factor',
              render: (r) =>
                r.mfa_enabled ? (
                  <span className="tag-ok">On</span>
                ) : r.role !== 'admin' && !r.rights?.length ? (
                  <span className="muted">Off</span>
                ) : (
                  <span className="tag-warn">Off</span>
                ),
            },
          ]}
        />
      )}

      {editing ? (
        <FormModal
          title={user ? `Edit ${user.username}` : 'New user'}
          submitLabel="Save user"
          busy={busy}
          onClose={() => setEditing(null)}
          onSubmit={save}
        >
          <div className="grid cols-2">
            <Field
              label="Username"
              name="username"
              required={!user}
              disabled={Boolean(user)}
              defaultValue={user?.username || ''}
            />
            <Field label="Full name" name="full_name" required defaultValue={user?.full_name || ''} />
            <Field
              as="select"
              label="Access"
              value={role}
              onChange={(event) => setRole(event.target.value)}
              hint={role === 'admin' ? 'Every right, users, settings and the audit log' : undefined}
            >
              <option value="user">User: the rights ticked below</option>
              <option value="admin">Administrator: everything</option>
            </Field>
            <Field
              label={user ? 'New password' : 'Password'}
              name="password"
              type="password"
              minLength={6}
              required={!user}
              autoComplete="new-password"
              hint={user ? 'Leave blank to keep the current password' : 'At least 6 characters'}
            />
            <Field
              as="select"
              label="Branch"
              name="branch_id"
              defaultValue={user?.branch_id || ''}
            >
              <option value="">Not assigned</option>
              {activeBranches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </Field>
            <Field label="Phone" name="phone" defaultValue={user?.phone || ''} />
            <Field label="Email" name="email" defaultValue={user?.email || ''} />
          </div>
          {role === 'admin' ? null : (
            <fieldset className="rights">
              <legend>Access rights</legend>
              {catalogue?.presets?.length ? (
                <div className="row rights-presets">
                  <span className="muted">Start from:</span>
                  {catalogue.presets.map((preset) => (
                    <button
                      key={preset.code}
                      type="button"
                      className="btn small"
                      onClick={() => setRights(preset.rights)}
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
              ) : null}
              {catalogue ? (
                <div className="rights-grid">
                  {catalogue.rights.map((right) => (
                    <label key={right.code} className="check right">
                      <input
                        type="checkbox"
                        checked={rights.includes(right.code)}
                        onChange={(event) => toggle(right.code, event.target.checked)}
                      />
                      <span>
                        <strong>{right.label}</strong>
                        <small className="muted">{right.description}</small>
                      </span>
                    </label>
                  ))}
                </div>
              ) : (
                <Loading what="Loading access rights" />
              )}
              {rights.includes('approve') ? (
                <Field
                  label="Approval limit"
                  name="approval_limit"
                  type="number"
                  min="0"
                  step="0.01"
                  defaultValue={user?.approval_limit ?? ''}
                  hint={`The largest loan they may approve. Blank: the organisation's limit${
                    catalogue ? ` of ${catalogue.default_approval_limit}` : ''
                  }.`}
                />
              ) : null}
            </fieldset>
          )}
          {user ? (
            <>
              <Check label="Active" name="is_active" defaultChecked={user.is_active} />
              <Check
                label="Clear any sign-in lockout"
                name="unlock"
                defaultChecked={false}
              />
              {user.mfa_enabled ? (
                <Check
                  label="Turn two-factor sign-in off (a lost phone): they sign in with the password alone until they set it up again"
                  name="reset_mfa"
                  defaultChecked={false}
                />
              ) : null}
            </>
          ) : null}
        </FormModal>
      ) : null}
    </>
  )
}
