import { useEffect, useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Check, ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { patch, post } from '../lib/api.js'
import { getCurrency } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

const FIELDS = [
  'name', 'currency', 'address', 'phone', 'email',
  'ecl_stage1_pct', 'ecl_stage2_pct', 'ecl_stage3_pct',
  'ecl_stage2_days', 'ecl_stage3_days', 'reminder_days_before',
  'officer_approval_limit', 'min_credit_score', 'group_arrears_block_days',
  'require_open_till',
]

export default function Settings() {
  const { toast, toastError } = useToast()
  const org = useOrg()
  const { data, error, loading, reload } = useApi('/api/settings')
  const branches = useApi('/api/branches?include_inactive=true')

  const [values, setValues] = useState(null)
  const [busy, setBusy] = useState(false)
  const [editingBranch, setEditingBranch] = useState(null)

  useEffect(() => {
    if (data) setValues(Object.fromEntries(FIELDS.map((f) => [f, data[f] ?? ''])))
  }, [data])

  const set = (key) => (e) => setValues((v) => ({ ...v, [key]: e.target.value }))

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    try {
      await patch('/api/settings', values)
      toast('Settings saved')
      reload()
      org.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function saveBranch(payload) {
    setBusy(true)
    try {
      if (editingBranch === 'new') await post('/api/branches', payload)
      else {
        const body = { ...payload }
        delete body.code
        await patch(`/api/branches/${editingBranch.id}`, body)
      }
      setEditingBranch(null)
      branches.reload()
      org.reload()
      toast('Branch saved')
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  if (loading && !values) return <Loading what="Loading settings" />
  if (error) return <ErrorBanner error={error} onRetry={reload} />
  if (!values) return null

  const branch = editingBranch === 'new' ? null : editingBranch

  return (
    <>
      <PageHeader
        title="Settings"
        meta="Institution details, impairment rates and the branch register"
      />

      <form className="card" onSubmit={save}>
        <h3>Institution</h3>
        <div className="grid cols-3">
          <Field label="Name" value={values.name} onChange={set('name')} required />
          <Field
            label="Currency"
            value={values.currency}
            onChange={set('currency')}
            required
            maxLength={8}
            hint="Shown against every amount in the app"
          />
          <Field label="Phone" value={values.phone || ''} onChange={set('phone')} />
          <Field label="Email" value={values.email || ''} onChange={set('email')} />
        </div>
        <Field
          as="textarea"
          label="Address"
          rows={2}
          value={values.address || ''}
          onChange={set('address')}
        />

        <h3 style={{ marginTop: 20 }}>Credit approval</h3>
        <div className="grid cols-3">
          <Field
            label={`Officer approval limit (${getCurrency()})`}
            type="number"
            step="0.01"
            min="0"
            value={values.officer_approval_limit}
            onChange={set('officer_approval_limit')}
            hint="Above this, an application needs an administrator"
          />
          <Field
            label="Minimum credit score"
            type="number"
            min="0"
            max="100"
            value={values.min_credit_score}
            onChange={set('min_credit_score')}
            hint="Advisory; applications below this are flagged, never blocked"
          />
        </div>

        <h3 style={{ marginTop: 20 }}>Impairment (IFRS 9)</h3>
        <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
          Loans are staged on days past due, and provisioned at the rate for their stage. These
          drive the Provisioning report.
        </p>
        <div className="grid cols-3">
          <Field
            label="Stage 1 provision %"
            type="number"
            step="0.01"
            min="0"
            max="100"
            value={values.ecl_stage1_pct}
            onChange={set('ecl_stage1_pct')}
            hint="Performing"
          />
          <Field
            label="Stage 2 provision %"
            type="number"
            step="0.01"
            min="0"
            max="100"
            value={values.ecl_stage2_pct}
            onChange={set('ecl_stage2_pct')}
            hint="Significant increase in credit risk"
          />
          <Field
            label="Stage 3 provision %"
            type="number"
            step="0.01"
            min="0"
            max="100"
            value={values.ecl_stage3_pct}
            onChange={set('ecl_stage3_pct')}
            hint="Credit impaired"
          />
          <Field
            label="Stage 2 begins after (days past due)"
            type="number"
            min="0"
            value={values.ecl_stage2_days}
            onChange={set('ecl_stage2_days')}
          />
          <Field
            label="Stage 3 begins after (days past due)"
            type="number"
            min="0"
            value={values.ecl_stage3_days}
            onChange={set('ecl_stage3_days')}
          />
          <Field
            label="Reminder window (days before due)"
            type="number"
            min="0"
            value={values.reminder_days_before}
            onChange={set('reminder_days_before')}
            hint="How far ahead instalment reminders are queued"
          />
          <Field
            label="Group borrowing blocked after (days in arrears)"
            type="number"
            min="0"
            value={values.group_arrears_block_days}
            onChange={set('group_arrears_block_days')}
            hint="While any member is this far behind, the group takes on no new debt. 0 turns it off."
          />
        </div>

        <h3>Cash</h3>
        <Check
          label="Cash postings need an open till"
          checked={Boolean(values.require_open_till)}
          onChange={(e) => setValues((v) => ({ ...v, require_open_till: e.target.checked }))}
        />
        <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
          When on, nobody can take or pay out cash without a till open on the Teller till page, so
          every note that crosses a counter lands in a count. Bank, mobile-money and payroll
          postings are never affected.
        </p>

        <div className="row">
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save settings'}
          </button>
        </div>
      </form>

      <div className="card">
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Branches</h3>
          <button type="button" className="btn small" onClick={() => setEditingBranch('new')}>
            New branch
          </button>
        </div>
        <DataTable
          caption="Branches"
          rows={branches.data || []}
          onRowClick={(row) => setEditingBranch(row)}
          empty="No branches yet"
          columns={[
            { key: 'code', header: 'Code', render: (r) => r.code },
            { key: 'name', header: 'Name', render: (r) => r.name },
            { key: 'phone', header: 'Phone', render: (r) => r.phone || '-' },
            { key: 'address', header: 'Address', render: (r) => r.address || '-' },
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
          ]}
        />
      </div>

      {editingBranch ? (
        <FormModal
          title={branch ? `Edit ${branch.code}` : 'New branch'}
          submitLabel="Save branch"
          busy={busy}
          onClose={() => setEditingBranch(null)}
          onSubmit={saveBranch}
        >
          <div className="grid cols-2">
            <Field
              label="Code"
              name="code"
              required={!branch}
              disabled={Boolean(branch)}
              defaultValue={branch?.code || ''}
            />
            <Field label="Name" name="name" required defaultValue={branch?.name || ''} />
            <Field label="Phone" name="phone" defaultValue={branch?.phone || ''} />
          </div>
          <Field
            as="textarea"
            label="Address"
            name="address"
            rows={2}
            defaultValue={branch?.address || ''}
          />
          <Check
            label="Active"
            name="is_active"
            defaultChecked={branch ? branch.is_active : true}
          />
        </FormModal>
      ) : null}
    </>
  )
}
