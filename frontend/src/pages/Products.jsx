import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Check, ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { patch, post } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { fmt, frequencyLabel, getCurrency, pct, rateMethodLabel, termUnit } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const NUMERIC = [
  'interest_rate_pct', 'min_amount', 'max_amount', 'min_term_months', 'max_term_months',
  'admin_fee_pct', 'insurance_fee_pct', 'penalty_rate_pct_per_day', 'grace_days',
  'max_instalment_to_salary_pct',
]

export default function Products() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const { data, error, loading, reload } = useApi('/api/products?include_inactive=true')
  const [editing, setEditing] = useState(null) // product object, or 'new'
  const [busy, setBusy] = useState(false)

  const canSetup = can('setup')

  async function save(values) {
    setBusy(true)
    try {
      const body = { ...values }
      for (const key of NUMERIC) if (body[key] === null) delete body[key]
      if (editing === 'new') {
        await post('/api/products', body)
      } else {
        delete body.code
        await patch(`/api/products/${editing.id}`, body)
      }
      setEditing(null)
      reload()
      toast('Product saved')
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  const product = editing === 'new' ? null : editing

  return (
    <>
      <PageHeader
        title="Loan products"
        meta="Pricing, limits, fees and the affordability cap applied to every application"
      >
        {canSetup ? (
          <button type="button" className="btn primary" onClick={() => setEditing('new')}>
            New product
          </button>
        ) : null}
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading products" />
      ) : (
        <DataTable
          caption="Loan products"
          rows={data || []}
          onRowClick={canSetup ? (row) => setEditing(row) : undefined}
          empty="No products defined"
          columns={[
            { key: 'code', header: 'Code', render: (r) => r.code },
            { key: 'name', header: 'Name', render: (r) => r.name },
            { key: 'currency', header: 'Currency', render: (r) => r.currency || getCurrency() },
            {
              key: 'rate',
              header: 'Rate/month',
              num: true,
              render: (r) => pct(r.interest_rate_pct),
            },
            { key: 'method', header: 'Method', render: (r) => rateMethodLabel(r.rate_method) },
            {
              key: 'range',
              header: 'Amount range',
              render: (r) => `${fmt(r.min_amount)} - ${fmt(r.max_amount)}`,
            },
            {
              key: 'frequency',
              header: 'Repaid',
              render: (r) => frequencyLabel(r.repayment_frequency),
            },
            {
              key: 'term',
              header: 'Term',
              render: (r) =>
                `${r.min_term_months} - ${r.max_term_months} ${termUnit(r.repayment_frequency)}`,
            },
            { key: 'admin', header: 'Admin fee', num: true, render: (r) => pct(r.admin_fee_pct) },
            {
              key: 'life',
              header: 'Credit life',
              num: true,
              render: (r) => pct(r.insurance_fee_pct),
            },
            {
              key: 'penalty',
              header: 'Penalty/day',
              num: true,
              render: (r) => pct(r.penalty_rate_pct_per_day),
            },
            { key: 'grace', header: 'Grace', num: true, render: (r) => `${r.grace_days}d` },
            {
              key: 'cap',
              header: 'Max inst/salary',
              num: true,
              render: (r) => pct(r.max_instalment_to_salary_pct),
            },
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
      )}

      {editing ? (
        <FormModal
          title={product ? `Edit ${product.code}` : 'New product'}
          submitLabel="Save product"
          busy={busy}
          onClose={() => setEditing(null)}
          onSubmit={save}
        >
          <div className="grid cols-3">
            <Field
              label="Code"
              name="code"
              required={!product}
              disabled={Boolean(product)}
              defaultValue={product?.code || ''}
              hint={product ? 'The code identifies the product in the loan book' : undefined}
            />
            <Field label="Name" name="name" required defaultValue={product?.name || ''} />
            <Field
              label="Currency"
              name="currency"
              maxLength={8}
              defaultValue={product?.currency || ''}
              placeholder={getCurrency()}
              hint={`Leave blank for ${getCurrency()}. Another currency needs a rate on the Currencies page first.`}
            />
            <Field
              label="Interest %/month"
              name="interest_rate_pct"
              type="number"
              step="0.001"
              required
              defaultValue={product?.interest_rate_pct ?? 5}
            />
            <Field
              as="select"
              label="Interest method"
              name="rate_method"
              defaultValue={product?.rate_method || 'reducing'}
              hint="Flat charges interest on the original principal throughout"
            >
              <option value="reducing">Reducing balance</option>
              <option value="flat">Flat rate</option>
            </Field>
            <Field
              as="select"
              label="Repaid"
              name="repayment_frequency"
              defaultValue={product?.repayment_frequency || 'monthly'}
              hint="The rate stays per month; a week carries 12/52 of it. Loans keep the frequency they were sold with."
            >
              <option value="monthly">Monthly</option>
              <option value="fortnightly">Fortnightly</option>
              <option value="weekly">Weekly</option>
            </Field>
            <Field
              label="Min amount"
              name="min_amount"
              type="number"
              step="0.01"
              required
              defaultValue={product?.min_amount ?? 100}
            />
            <Field
              label="Max amount"
              name="max_amount"
              type="number"
              step="0.01"
              required
              defaultValue={product?.max_amount ?? 5000}
            />
            <Field
              label="Min term (instalments)"
              name="min_term_months"
              type="number"
              min="1"
              required
              defaultValue={product?.min_term_months ?? 1}
              hint="Months, fortnights or weeks, as repaid"
            />
            <Field
              label="Max term (instalments)"
              name="max_term_months"
              type="number"
              min="1"
              required
              defaultValue={product?.max_term_months ?? 12}
            />
            <Field
              label="Admin fee %"
              name="admin_fee_pct"
              type="number"
              step="0.001"
              defaultValue={product?.admin_fee_pct ?? 3}
              hint="Deducted at disbursement"
            />
            <Field
              label="Credit life fee %"
              name="insurance_fee_pct"
              type="number"
              step="0.001"
              defaultValue={product?.insurance_fee_pct ?? 1}
            />
            <Field
              label="Penalty %/day"
              name="penalty_rate_pct_per_day"
              type="number"
              step="0.001"
              defaultValue={product?.penalty_rate_pct_per_day ?? 0.5}
            />
            <Field
              label="Grace days"
              name="grace_days"
              type="number"
              min="0"
              defaultValue={product?.grace_days ?? 3}
            />
            <Field
              label="Max instalment / salary %"
              name="max_instalment_to_salary_pct"
              type="number"
              step="0.01"
              defaultValue={product?.max_instalment_to_salary_pct ?? 40}
            />
          </div>
          <Field
            as="textarea"
            label="Description"
            name="description"
            rows={2}
            defaultValue={product?.description || ''}
          />
          <Check label="Active" name="is_active" defaultChecked={product ? product.is_active : true} />
        </FormModal>
      ) : null}
    </>
  )
}
