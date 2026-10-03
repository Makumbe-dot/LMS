import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Check, ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { patch, post } from '../lib/api.js'
import { humanise } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

const ROLES = ['admin', 'loan_officer', 'teller', 'viewer']

export default function Users() {
  const { toast, toastError } = useToast()
  const { activeBranches } = useOrg()
  const { data, error, loading, reload } = useApi('/api/users')
  const [editing, setEditing] = useState(null) // user object or 'new'
  const [busy, setBusy] = useState(false)

  async function save(values) {
    setBusy(true)
    try {
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
        meta="Roles decide what each person can do: officers originate and decide, tellers take money, admins do the irreversible things, viewers only read."
      >
        <button type="button" className="btn primary" onClick={() => setEditing('new')}>
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
          onRowClick={(row) => setEditing(row)}
          empty="No users"
          columns={[
            { key: 'username', header: 'Username', render: (r) => r.username },
            { key: 'name', header: 'Full name', render: (r) => r.full_name },
            { key: 'role', header: 'Role', render: (r) => humanise(r.role) },
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
                ) : r.role === 'viewer' ? (
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
            <Field as="select" label="Role" name="role" defaultValue={user?.role || 'loan_officer'}>
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {humanise(role)}
                </option>
              ))}
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
