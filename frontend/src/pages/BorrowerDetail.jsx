import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import LoanTable from '../components/LoanTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Field, KeyValues, Loading, PageHeader } from '../components/ui.jsx'
import { del, post, postForm } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { bytes, dateOnly, dateTime, humanise, money } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

/**
 * What the credit bureau says about this borrower. Hidden behind a sentence when
 * no bureau is configured, so the button only appears when it would do something.
 */
function BureauCard({ borrowerId, mayEdit }) {
  const { toast, toastError } = useToast()
  const enquiries = useApi(`/api/borrowers/${borrowerId}/bureau`)
  const [running, setRunning] = useState(false)
  const data = enquiries.data

  async function runCheck() {
    setRunning(true)
    try {
      const row = await post(`/api/borrowers/${borrowerId}/bureau`)
      enquiries.reload()
      if (row.status === 'ok') toast('Bureau report received')
      else toastError(row.error || 'The bureau did not answer')
    } catch (err) {
      toastError(err)
    } finally {
      setRunning(false)
    }
  }

  if (!data) return null
  const latest = data.rows.find((r) => r.id === data.latest_id)
  const rows = data.rows || []

  return (
    <div className="card">
      <div className="row between" style={{ marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>Credit bureau</h3>
        {data.configured && mayEdit ? (
          <button type="button" className="btn small" onClick={runCheck} disabled={running}>
            {running ? 'Asking the bureau…' : 'Run bureau check'}
          </button>
        ) : null}
      </div>
      {!data.configured ? (
        <p className="muted" style={{ marginBottom: 0 }}>
          No credit bureau is connected. The scorecard reads this book only; set BUREAU_BACKEND in
          the backend settings once the institution has a bureau contract.
        </p>
      ) : (
        <>
          {data.demo ? (
            <p className="banner" style={{ borderLeftColor: 'var(--warn)' }}>
              The bureau is in demo mode: reports are made up from the national ID and leave this
              machine never.
            </p>
          ) : null}
          {latest ? (
            <KeyValues
              items={[
                ['Report of', dateTime(latest.enquired_at)],
                ['Bureau score', latest.score === null ? '-' : `${latest.score} / ${latest.score_max}`],
                ['Accounts elsewhere', latest.open_accounts],
                [
                  'In arrears elsewhere',
                  latest.accounts_in_arrears ? (
                    <span className="tag-danger">
                      {latest.accounts_in_arrears} (worst {latest.worst_days_in_arrears} days)
                    </span>
                  ) : (
                    <span className="tag-ok">None</span>
                  ),
                ],
                [
                  'Defaults on record',
                  latest.defaults ? <span className="tag-danger">{latest.defaults}</span> : 'None',
                ],
                ['Owed to other lenders', money(latest.total_exposure)],
                ['Reference', latest.reference || '-'],
              ]}
            />
          ) : (
            <p className="muted">
              No report inside the last {data.valid_days} days. The scorecard will use one once it
              is run.
            </p>
          )}
          {rows.length > 1 || (rows.length === 1 && !latest) ? (
            <DataTable
              caption="Earlier enquiries"
              rows={rows}
              columns={[
                { key: 'when', header: 'When', render: (r) => dateTime(r.enquired_at) },
                { key: 'status', header: 'Status', render: (r) => humanise(r.status) },
                {
                  key: 'score',
                  header: 'Score',
                  num: true,
                  render: (r) => (r.score === null ? '-' : `${r.score}/${r.score_max}`),
                },
                { key: 'summary', header: 'Summary', render: (r) => r.summary || r.error || '-' },
                { key: 'by', header: 'By', render: (r) => r.enquired_by_name || '-' },
              ]}
            />
          ) : null}
        </>
      )}
    </div>
  )
}

const DOC_TYPES = [
  ['id', 'Identity document'],
  ['payslip', 'Payslip'],
  ['contract', 'Employment contract'],
  ['bank_statement', 'Bank statement'],
  ['agreement', 'Signed loan agreement'],
  ['other', 'Other'],
]

export default function BorrowerDetail() {
  const { id } = useParams()
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const borrower = useApi(`/api/borrowers/${id}`)
  const loans = useApi(`/api/borrowers/${id}/loans`)
  const [adding, setAdding] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [busy, setBusy] = useState(false)

  const b = borrower.data
  const mayEdit = can('borrowers')

  async function addGuarantor(values) {
    setBusy(true)
    try {
      await post(`/api/borrowers/${id}/guarantors`, values)
      setAdding(false)
      borrower.reload()
      toast('Guarantor added')
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function uploadDocument(values) {
    const input = document.getElementById('doc-file')
    if (!input?.files?.[0]) {
      toastError('Choose a file first')
      return
    }
    const form = new FormData()
    form.append('file', input.files[0])
    form.append('doc_type', values.doc_type || 'other')
    if (values.note) form.append('note', values.note)
    setBusy(true)
    try {
      await postForm(`/api/borrowers/${id}/documents`, form)
      setUploading(false)
      borrower.reload()
      toast('Document uploaded')
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function removeDocument(document) {
    if (!window.confirm(`Delete ${document.original_name}?`)) return
    try {
      await del(`/api/borrowers/${id}/documents/${document.id}`)
      borrower.reload()
      toast('Document deleted')
    } catch (err) {
      toastError(err)
    }
  }

  async function removeGuarantor(guarantor) {
    if (!window.confirm(`Remove ${guarantor.full_name} as guarantor?`)) return
    try {
      await del(`/api/borrowers/${id}/guarantors/${guarantor.id}`)
      borrower.reload()
      toast('Guarantor removed')
    } catch (err) {
      toastError(err)
    }
  }

  if (borrower.loading && !b) return <Loading what="Loading borrower" />
  if (borrower.error) return <ErrorBanner error={borrower.error} onRetry={borrower.reload} />
  if (!b) return null

  return (
    <>
      <PageHeader title={`${b.first_name} ${b.last_name}`} meta={b.borrower_no}>
        {mayEdit ? (
          <>
            <Link className="btn" to={`/borrowers/${id}/edit`}>
              Edit
            </Link>
            <Link className="btn primary" to={`/loans/new?borrower=${b.id}`}>
              New loan
            </Link>
          </>
        ) : null}
      </PageHeader>

      <div className="grid cols-2">
        <div className="card">
          <h3>Profile</h3>
          <KeyValues
            items={[
              ['National ID', b.national_id],
              ['Phone', b.phone],
              ['Messages by', { whatsapp: 'WhatsApp', email: 'Email' }[b.preferred_channel] || 'SMS'],
              ['Politically exposed', b.is_pep ? 'Yes: enhanced due diligence' : 'No'],
              ['Paid out to', [b.bank_name, b.bank_account_no].filter(Boolean).join(' · ') || `Mobile money ${b.mobile_wallet || b.phone}`],
              ['Email', b.email || '-'],
              ['Date of birth', b.date_of_birth || '-'],
              ['Gender', b.gender || '-'],
              ['Address', b.address || '-'],
              [
                'KYC',
                b.kyc_verified ? (
                  <span className="tag-ok">Verified</span>
                ) : (
                  <span className="tag-danger">Not verified</span>
                ),
              ],
              [
                'Standing',
                b.is_blacklisted ? (
                  <span className="tag-danger">Blacklisted</span>
                ) : (
                  'Good standing'
                ),
              ],
              ['Registered', dateOnly(b.created_at)],
              ['Notes', b.notes || '-'],
            ]}
          />
        </div>
        <div className="card">
          <h3>Employment and exposure</h3>
          <KeyValues
            items={[
              ['Employer', b.employer || '-'],
              ['Employee no.', b.employee_no || '-'],
              ['Job title', b.job_title || '-'],
              ['Branch', b.branch_name || 'Not assigned'],
              ['Net salary', money(b.net_salary)],
              ['Payday', `Day ${b.payday} of the month`],
              ['Active loans', b.active_loans],
              ['Total outstanding', <strong key="o">{money(b.total_outstanding)}</strong>],
            ]}
          />
        </div>
      </div>

      <div className="card">
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Guarantors</h3>
          {mayEdit ? (
            <button type="button" className="btn small" onClick={() => setAdding(true)}>
              Add guarantor
            </button>
          ) : null}
        </div>
        <DataTable
          caption="Guarantors"
          rows={b.guarantors}
          empty="No guarantors recorded"
          columns={[
            { key: 'name', header: 'Name', render: (g) => g.full_name },
            { key: 'nid', header: 'National ID', render: (g) => g.national_id },
            { key: 'phone', header: 'Phone', render: (g) => g.phone },
            {
              key: 'rel',
              header: 'Relationship',
              render: (g) => g.relationship_to_borrower || '-',
            },
            { key: 'employer', header: 'Employer', render: (g) => g.employer || '-' },
            {
              key: 'actions',
              header: '',
              render: (g) =>
                mayEdit ? (
                  <button
                    type="button"
                    className="btn small"
                    onClick={(event) => {
                      event.stopPropagation()
                      removeGuarantor(g)
                    }}
                  >
                    Remove
                  </button>
                ) : null,
            },
          ]}
        />
      </div>

      <div className="card">
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Documents</h3>
          {mayEdit ? (
            <button type="button" className="btn small" onClick={() => setUploading(true)}>
              Upload document
            </button>
          ) : null}
        </div>
        {(b.documents || []).length === 0 ? (
          <p className="muted" style={{ marginBottom: 0 }}>
            No KYC paperwork on file. Identity documents and payslips belong here.
          </p>
        ) : (
          (b.documents || []).map((document) => (
            <div className="file-row" key={document.id}>
              <div>
                <strong>{document.original_name}</strong>
                <div className="file-meta">
                  {humanise(document.doc_type)} · {bytes(document.size_bytes)} ·{' '}
                  {dateOnly(document.uploaded_at)}
                  {document.uploaded_by_name ? ` · ${document.uploaded_by_name}` : ''}
                  {document.note ? ` · ${document.note}` : ''}
                </div>
              </div>
              <div className="row">
                <a className="btn small" href={document.download_url}>
                  Download
                </a>
                {mayEdit ? (
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => removeDocument(document)}
                  >
                    Delete
                  </button>
                ) : null}
              </div>
            </div>
          ))
        )}
      </div>

      <BureauCard borrowerId={id} mayEdit={mayEdit} />

      <div className="card">
        <h3>Loans</h3>
        {loans.loading && !loans.data ? (
          <Loading what="Loading loans" />
        ) : (
          <LoanTable loans={loans.data || []} empty="This borrower has no loans yet" />
        )}
      </div>

      {adding ? (
        <FormModal
          title="Add guarantor"
          onClose={() => setAdding(false)}
          onSubmit={addGuarantor}
          submitLabel="Add guarantor"
          busy={busy}
        >
          <div className="grid cols-2">
            <Field label="Full name" name="full_name" required />
            <Field label="National ID" name="national_id" required />
            <Field label="Phone" name="phone" required />
            <Field label="Relationship" name="relationship_to_borrower" />
            <Field label="Employer" name="employer" />
            <Field label="Address" name="address" />
          </div>
        </FormModal>
      ) : null}

      {uploading ? (
        <FormModal
          title="Upload a document"
          submitLabel="Upload"
          busy={busy}
          onClose={() => setUploading(false)}
          onSubmit={uploadDocument}
        >
          <label className="field">
            <span className="label-text">File</span>
            <input
              id="doc-file"
              type="file"
              accept=".pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff,.doc,.docx"
              required
            />
            <span className="hint">PDF, image or Word document, up to 10 MB</span>
          </label>
          <div className="grid cols-2">
            <Field as="select" label="Type" name="doc_type" defaultValue="id">
              {DOC_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Field>
            <Field label="Note" name="note" />
          </div>
        </FormModal>
      ) : null}
    </>
  )
}
