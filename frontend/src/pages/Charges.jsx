import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Check, ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { del, get, patch, post, put } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { fmt, getCurrency, humanise } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

/** The charges catalogue, and which products carry which charge. */
export default function Charges() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const charges = useApi('/api/charges?include_inactive=true')
  const products = useApi('/api/products?include_inactive=true')

  const [editing, setEditing] = useState(null) // charge object or 'new'
  const [attaching, setAttaching] = useState(null) // { product, attached: [] }
  const [busy, setBusy] = useState(false)

  const canSetup = can('setup')

  async function save(values) {
    setBusy(true)
    try {
      const body = { ...values }
      if (editing === 'new') {
        await post('/api/charges', body)
      } else {
        delete body.code
        await patch(`/api/charges/${editing.id}`, body)
      }
      setEditing(null)
      charges.reload()
      toast('Charge saved')
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function openAttach(product) {
    try {
      const attached = await get(`/api/products/${product.id}/charges`)
      setAttaching({ product, attached: attached.map((c) => c.id) })
    } catch (err) {
      toastError(err)
    }
  }

  async function saveAttach() {
    setBusy(true)
    try {
      await put(`/api/products/${attaching.product.id}/charges`, {
        charge_ids: attaching.attached,
      })
      setAttaching(null)
      products.reload()
      toast('Product charges updated')
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  const charge = editing === 'new' ? null : editing

  return (
    <>
      <PageHeader
        title="Charges"
        meta="Fees beside the core pricing, defined once and attached to the products that carry them"
      >
        {canSetup ? (
          <button type="button" className="btn primary" onClick={() => setEditing('new')}>
            New charge
          </button>
        ) : null}
      </PageHeader>

      <ErrorBanner error={charges.error} onRetry={charges.reload} />
      {charges.loading && !charges.data ? (
        <Loading what="Loading charges" />
      ) : (
        <DataTable
          caption="Charges catalogue"
          rows={charges.data || []}
          onRowClick={canSetup ? (row) => setEditing(row) : undefined}
          empty="No charges defined. The admin and credit-life fees on each product are separate."
          columns={[
            { key: 'code', header: 'Code', render: (r) => r.code },
            { key: 'name', header: 'Name', render: (r) => r.name },
            { key: 'basis', header: 'Basis', render: (r) => humanise(r.basis) },
            {
              key: 'value',
              header: 'Value',
              num: true,
              render: (r) =>
                r.basis === 'percent'
                  ? `${Number(r.value)}%`
                  : `${getCurrency()} ${fmt(r.value)}`,
            },
            { key: 'timing', header: 'Raised', render: (r) => humanise(r.timing) },
            { key: 'description', header: 'What it covers', render: (r) => r.description || '' },
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
              key: 'actions',
              header: '',
              render: (r) =>
                canSetup ? (
                  <button
                    type="button"
                    className="btn small"
                    onClick={(event) => {
                      event.stopPropagation()
                      if (window.confirm(`Delete ${r.code}?`)) {
                        del(`/api/charges/${r.id}`)
                          .then(() => {
                            charges.reload()
                            toast('Charge deleted')
                          })
                          .catch(toastError)
                      }
                    }}
                  >
                    Delete
                  </button>
                ) : null,
            },
          ]}
        />
      )}

      <div className="card">
        <h3>Which products carry which charge</h3>
        <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>
          A charge attached here is quoted and deducted at disbursement, on top of the product's
          own admin and credit-life fees.
        </p>
        <DataTable
          caption="Products"
          rows={products.data || []}
          onRowClick={canSetup ? (row) => openAttach(row) : undefined}
          empty="No products"
          columns={[
            { key: 'code', header: 'Code', render: (r) => r.code },
            { key: 'name', header: 'Product', render: (r) => r.name },
            {
              key: 'fees',
              header: 'Built-in fees',
              render: (r) => `${Number(r.admin_fee_pct)}% admin, ${Number(r.insurance_fee_pct)}% credit life`,
            },
            {
              key: 'actions',
              header: '',
              render: (r) =>
                canSetup ? (
                  <button
                    type="button"
                    className="btn small"
                    onClick={(event) => {
                      event.stopPropagation()
                      openAttach(r)
                    }}
                  >
                    Set charges
                  </button>
                ) : null,
            },
          ]}
        />
      </div>

      {editing ? (
        <FormModal
          title={charge ? `Edit ${charge.code}` : 'New charge'}
          submitLabel="Save charge"
          busy={busy}
          onClose={() => setEditing(null)}
          onSubmit={save}
        >
          <div className="grid cols-2">
            <Field
              label="Code"
              name="code"
              required={!charge}
              disabled={Boolean(charge)}
              defaultValue={charge?.code || ''}
            />
            <Field label="Name" name="name" required defaultValue={charge?.name || ''} />
            <Field
              as="select"
              label="Basis"
              name="basis"
              defaultValue={charge?.basis || 'fixed'}
            >
              <option value="fixed">Fixed amount</option>
              <option value="percent">Percent of principal</option>
            </Field>
            <Field
              label="Value"
              type="number"
              step="0.0001"
              min="0"
              name="value"
              required
              defaultValue={charge?.value ?? ''}
              hint="A percentage, or an amount, depending on the basis"
            />
            <Field
              as="select"
              label="When it is raised"
              name="timing"
              defaultValue={charge?.timing || 'disbursement'}
            >
              <option value="disbursement">Deducted at disbursement</option>
              <option value="manual">Raised manually</option>
            </Field>
          </div>
          <Field
            as="textarea"
            label="Description"
            name="description"
            rows={2}
            defaultValue={charge?.description || ''}
          />
          <Check label="Active" name="is_active" defaultChecked={charge ? charge.is_active : true} />
        </FormModal>
      ) : null}

      {attaching ? (
        <FormModal
          title={`Charges on ${attaching.product.name}`}
          submitLabel="Save"
          busy={busy}
          onClose={() => setAttaching(null)}
          onSubmit={saveAttach}
        >
          <p className="muted" style={{ marginTop: 0 }}>
            Tick the charges this product carries. Loans already disbursed keep the charges they
            were given.
          </p>
          {(charges.data || [])
            .filter((c) => c.is_active)
            .map((c) => (
              <Check
                key={c.id}
                label={`${c.code} — ${c.name} (${
                  c.basis === 'percent' ? `${Number(c.value)}%` : `${getCurrency()} ${fmt(c.value)}`
                })`}
                checked={attaching.attached.includes(c.id)}
                onChange={(e) =>
                  setAttaching((current) => ({
                    ...current,
                    attached: e.target.checked
                      ? [...current.attached, c.id]
                      : current.attached.filter((id) => id !== c.id),
                  }))
                }
              />
            ))}
        </FormModal>
      ) : null}
    </>
  )
}
