import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { useToast } from '../components/Toast.jsx'
import { Check, ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { patch, post } from '../lib/api.js'
import { getCurrency } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

const BLANK = {
  first_name: '',
  last_name: '',
  national_id: '',
  date_of_birth: '',
  gender: '',
  phone: '',
  email: '',
  address: '',
  employer: '',
  employee_no: '',
  job_title: '',
  net_salary: '0',
  payday: '25',
  kyc_verified: false,
  is_pep: false,
  bank_name: '',
  bank_branch: '',
  bank_account_no: '',
  bank_account_name: '',
  mobile_wallet: '',
  preferred_channel: 'sms',
  is_blacklisted: false,
  notes: '',
  branch: '',
}

/** Empty strings become null so the API treats them as "not supplied". */
function clean(values, keys) {
  const out = {}
  for (const key of keys) {
    const value = values[key]
    out[key] = value === '' ? null : value
  }
  return out
}

export default function BorrowerForm() {
  const { id } = useParams()
  const editing = Boolean(id)
  const navigate = useNavigate()
  const { toast, toastError } = useToast()

  const { activeBranches } = useOrg()
  const { data: existing, error, loading } = useApi(editing ? `/api/borrowers/${id}` : null)
  const [values, setValues] = useState(BLANK)
  const [guarantor, setGuarantor] = useState({
    full_name: '',
    national_id: '',
    phone: '',
    relationship_to_borrower: '',
    employer: '',
    address: '',
  })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!existing) return
    const next = { ...BLANK }
    for (const key of Object.keys(BLANK)) {
      next[key] = existing[key] ?? (typeof BLANK[key] === 'boolean' ? false : '')
    }
    setValues(next)
  }, [existing])

  const set = (key) => (event) => {
    const target = event.target
    setValues((current) => ({
      ...current,
      [key]: target.type === 'checkbox' ? target.checked : target.value,
    }))
  }

  async function onSubmit(event) {
    event.preventDefault()
    setBusy(true)
    try {
      let saved
      if (editing) {
        const editable = Object.keys(BLANK).filter((k) => k !== 'national_id')
        saved = await patch(`/api/borrowers/${id}`, clean(values, editable))
      } else {
        const body = clean(values, Object.keys(BLANK))
        body.guarantors = guarantor.full_name
          ? [clean(guarantor, Object.keys(guarantor))]
          : []
        saved = await post('/api/borrowers', body)
      }
      toast('Borrower saved')
      navigate(`/borrowers/${saved.id}`)
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  if (editing && loading) return <Loading what="Loading borrower" />
  if (error) return <ErrorBanner error={error} />

  return (
    <>
      <PageHeader title={editing ? 'Edit borrower' : 'New borrower'} />
      <ErrorBanner error={error} />

      <form className="card" onSubmit={onSubmit}>
        <div className="grid cols-3">
          <Field label="First name" required value={values.first_name} onChange={set('first_name')} />
          <Field label="Last name" required value={values.last_name} onChange={set('last_name')} />
          <Field
            label="National ID"
            required={!editing}
            disabled={editing}
            hint={editing ? 'The national ID identifies the borrower and cannot be changed' : undefined}
            value={values.national_id}
            onChange={set('national_id')}
          />
          <Field
            label="Date of birth"
            type="date"
            value={values.date_of_birth || ''}
            onChange={set('date_of_birth')}
          />
          <Field as="select" label="Gender" value={values.gender || ''} onChange={set('gender')}>
            <option value="">-</option>
            <option value="M">M</option>
            <option value="F">F</option>
          </Field>
          <Field label="Phone" required value={values.phone} onChange={set('phone')} />
          <Field label="Email" type="email" value={values.email || ''} onChange={set('email')} />
          <Field
            as="select"
            label="Send messages by"
            value={values.preferred_channel || 'sms'}
            onChange={set('preferred_channel')}
            hint="Reminders, notices and receipts. WhatsApp and email fall back to SMS by themselves if a message cannot be delivered."
          >
            <option value="sms">SMS</option>
            <option value="whatsapp">WhatsApp</option>
            <option value="email">Email</option>
          </Field>
          <Field label="Employer" value={values.employer || ''} onChange={set('employer')} />
          <Field label="Employee no." value={values.employee_no || ''} onChange={set('employee_no')} />
          <Field label="Bank" value={values.bank_name || ''} onChange={set('bank_name')} hint="Where loans are paid out by bank transfer" />
          <Field label="Bank branch" value={values.bank_branch || ''} onChange={set('bank_branch')} />
          <Field label="Account number" value={values.bank_account_no || ''} onChange={set('bank_account_no')} />
          <Field label="Account name" value={values.bank_account_name || ''} onChange={set('bank_account_name')} hint="If different from the borrower's name" />
          <Field label="Mobile-money wallet" value={values.mobile_wallet || ''} onChange={set('mobile_wallet')} hint="Blank: the phone number above" />
          <Field label="Job title" value={values.job_title || ''} onChange={set('job_title')} />
          <Field
            as="select"
            label="Branch"
            value={values.branch || ''}
            onChange={set('branch')}
            hint="The office that owns this relationship"
          >
            <option value="">Not assigned</option>
            {activeBranches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </Field>
          <Field
            label={`Net monthly salary (${getCurrency()})`}
            type="number"
            step="0.01"
            min="0"
            value={values.net_salary}
            onChange={set('net_salary')}
            hint="Drives the affordability check on every application"
          />
          <Field
            label="Payday (day of month)"
            type="number"
            min="1"
            max="31"
            value={values.payday}
            onChange={set('payday')}
            hint="First instalment lands on the next payday"
          />
        </div>

        <Field as="textarea" label="Address" rows={2} value={values.address || ''} onChange={set('address')} />
        <Field as="textarea" label="Notes" rows={2} value={values.notes || ''} onChange={set('notes')} />

        <div className="row">
          <Check label="KYC verified" checked={values.kyc_verified} onChange={set('kyc_verified')} />
          <Check
            label="Politically exposed person (or a close relative or associate of one)"
            checked={values.is_pep}
            onChange={set('is_pep')}
          />
          <Check label="Blacklisted" checked={values.is_blacklisted} onChange={set('is_blacklisted')} />
        </div>

        {!editing ? (
          <fieldset style={{ marginTop: 8 }}>
            <legend>Guarantor (optional)</legend>
            <div className="grid cols-3">
              {[
                ['full_name', 'Full name'],
                ['national_id', 'National ID'],
                ['phone', 'Phone'],
                ['relationship_to_borrower', 'Relationship'],
                ['employer', 'Employer'],
                ['address', 'Address'],
              ].map(([key, label]) => (
                <Field
                  key={key}
                  label={label}
                  value={guarantor[key]}
                  onChange={(e) => setGuarantor((g) => ({ ...g, [key]: e.target.value }))}
                />
              ))}
            </div>
          </fieldset>
        ) : null}

        <div className="row">
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save borrower'}
          </button>
          <Link className="btn" to={editing ? `/borrowers/${id}` : '/borrowers'}>
            Cancel
          </Link>
        </div>
      </form>
    </>
  )
}
