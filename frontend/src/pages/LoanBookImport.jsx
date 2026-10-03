import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { Field, Kpi, PageHeader } from '../components/ui.jsx'
import { downloadCsv, postForm } from '../lib/api.js'
import { bytes, fmt, money, termShort, today } from '../lib/format.js'
import { PeriodNotice, useMinPostingDate } from '../lib/periods.jsx'

/**
 * Bring running loans over from another system, as they stand at a cut-over date.
 * Checked line by line first; imported all or nothing.
 */
export default function LoanBookImport() {
  const { toast, toastError } = useToast()
  const minDate = useMinPostingDate()
  const inputRef = useRef(null)
  const [file, setFile] = useState(null)
  const [cutover, setCutover] = useState(today())
  const [preview, setPreview] = useState(null)
  const [result, setResult] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)

  function choose(next) {
    setFile(next)
    setPreview(null)
    setResult(null)
  }

  async function send(commit) {
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    form.append('cutover_date', cutover)
    form.append('commit', commit ? 'true' : 'false')
    setBusy(true)
    try {
      const data = await postForm('/api/imports/loan-book', form)
      if (commit) {
        setResult(data)
        setPreview(null)
        toast(`Brought over ${data.imported_rows} loan(s)`)
      } else {
        setPreview(data)
      }
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Loan book migration"
        meta="Bring loans already running in another system over as they stand at a cut-over date"
      >
        <button
          type="button"
          className="btn"
          onClick={() => downloadCsv('/api/imports/loan-book', 'loan_book_template').catch(toastError)}
        >
          Download template
        </button>
      </PageHeader>

      <div className="card">
        <h3>How it works</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          One row is one running loan: who borrowed, on which product, how much, when it was
          disbursed and how much has been paid so far. The original schedule is rebuilt from the
          contract and the payments laid over it oldest first, so arrears come out exactly as they
          would for a loan that had always been here. A borrower not yet on the register is
          created from the row.
        </p>
        <p className="muted">
          Each loan posts one opening balance — principal and penalties outstanding against{' '}
          <strong>3900 Opening balances</strong>, with no cash moving. Bring over cash, assets and
          other balances with a <Link to="/journals">journal</Link> against the same account, then
          clear it to retained earnings once the opening book is agreed. Penalties come over as a
          figure and are only charged from the cut-over onwards. A row with an{' '}
          <code>external_ref</code> is never imported twice, and payroll returns quoting that old
          number still find the loan.
        </p>
      </div>

      <div className="card">
        <h3>1. The cut-over date and the file</h3>
        <div className="grid cols-3">
          <Field
            label="Cut-over date"
            type="date"
            min={minDate}
            value={cutover}
            onChange={(e) => {
              setCutover(e.target.value)
              setPreview(null)
            }}
            hint="The day the balances in the file are true at"
          />
        </div>
        <PeriodNotice date={cutover} />
        <div
          className={`dropzone ${dragging ? 'dragging' : ''}`}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            if (e.dataTransfer.files?.[0]) choose(e.dataTransfer.files[0])
          }}
        >
          <p style={{ margin: '0 0 12px' }}>Drop a CSV here, or</p>
          <button type="button" className="btn" onClick={() => inputRef.current?.click()}>
            Browse…
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            hidden
            onChange={(e) => e.target.files?.[0] && choose(e.target.files[0])}
          />
          <p className="muted" style={{ fontSize: 12, marginBottom: 0, marginTop: 12 }}>
            Required: <code>national_id</code>, <code>product_code</code>, <code>principal</code>,{' '}
            <code>term</code>, <code>disbursement_date</code>, <code>amount_paid</code>. The
            template shows the optional columns.
          </p>
        </div>

        {file ? (
          <div className="file-row" style={{ marginTop: 12 }}>
            <div>
              <strong>{file.name}</strong>
              <div className="file-meta">{bytes(file.size)}</div>
            </div>
            <div className="row">
              <button
                type="button"
                className="btn primary"
                disabled={busy}
                onClick={() => send(false)}
              >
                {busy ? 'Checking…' : 'Check the file'}
              </button>
              <button type="button" className="btn" onClick={() => choose(null)}>
                Remove
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {preview ? (
        <>
          <div className="grid cols-4">
            <Kpi
              label="Loans ready"
              value={`${preview.valid_rows} of ${preview.total_rows}`}
              sub={`${preview.new_borrowers} new borrower(s)`}
            />
            <Kpi label="Principal outstanding" value={money(preview.principal_outstanding)} />
            <Kpi label="In arrears at cut-over" value={money(preview.arrears)} />
            <div className="kpi">
              <div className="label">2. Bring them over</div>
              <div className="row" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || preview.valid_rows === 0 || preview.invalid_rows > 0}
                  onClick={() => send(true)}
                >
                  {busy ? 'Importing…' : `Import ${preview.valid_rows} loan(s)`}
                </button>
              </div>
              <div className="sub">
                {preview.invalid_rows
                  ? `${preview.invalid_rows} row(s) need fixing first: it is all or nothing`
                  : 'Every row checked'}
              </div>
            </div>
          </div>

          <DataTable
            caption="Migration preview"
            rows={preview.rows}
            rowKey={(r) => r.line}
            columns={[
              { key: 'line', header: 'Line', num: true, render: (r) => r.line },
              {
                key: 'ok',
                header: '',
                render: (r) =>
                  r.ok ? <span className="tag-ok">OK</span> : <span className="tag-danger">Problem</span>,
              },
              { key: 'ref', header: 'Old number', render: (r) => r.external_ref || '-' },
              {
                key: 'borrower',
                header: 'Borrower',
                render: (r) =>
                  `${r.borrower || r.national_id}${r.ok && r.new_borrower ? ' (new)' : ''}`,
              },
              { key: 'product', header: 'Product', render: (r) => r.product_code },
              { key: 'principal', header: 'Principal', num: true, render: (r) => (r.ok ? fmt(r.principal) : '') },
              {
                key: 'term',
                header: 'Term',
                num: true,
                render: (r) => (r.ok ? termShort(r.term, r.repayment_frequency) : ''),
              },
              { key: 'paid', header: 'Paid so far', num: true, render: (r) => (r.ok ? fmt(r.amount_paid) : '') },
              {
                key: 'outstanding',
                header: 'Principal out',
                num: true,
                render: (r) => (r.ok ? fmt(r.principal_outstanding) : ''),
              },
              {
                key: 'arrears',
                header: 'Arrears',
                num: true,
                render: (r) =>
                  r.ok && Number(r.arrears) > 0 ? <span className="tag-danger">{fmt(r.arrears)}</span> : '',
              },
              {
                key: 'error',
                header: 'Problem',
                render: (r) => (r.error ? <span className="tag-danger">{r.error}</span> : ''),
              },
            ]}
          />
        </>
      ) : null}

      {result ? (
        <>
          <div className="grid cols-3">
            <Kpi label="Brought over" value={result.imported_rows} />
            <Kpi label="Principal outstanding" value={money(result.principal_outstanding)} />
            <div className="kpi">
              <div className="label">Done</div>
              <div className="row" style={{ marginTop: 8 }}>
                <Link className="btn" to="/ledger">
                  Check the reconciliation
                </Link>
              </div>
            </div>
          </div>
          <DataTable
            caption="Imported loans"
            rows={result.loans}
            rowKey={(r) => r.loan_id}
            columns={[
              { key: 'line', header: 'Line', num: true, render: (r) => r.line },
              {
                key: 'loan',
                header: 'Loan',
                render: (r) => <Link to={`/loans/${r.loan_id}`}>{r.loan_no}</Link>,
              },
              { key: 'ref', header: 'Old number', render: (r) => r.external_ref || '-' },
              { key: 'borrower', header: 'Borrower', render: (r) => r.borrower },
              {
                key: 'outstanding',
                header: 'Principal out',
                num: true,
                render: (r) => fmt(r.principal_outstanding),
              },
              {
                key: 'status',
                header: 'At cut-over',
                render: (r) =>
                  r.status === 'overdue' ? (
                    <span className="tag-danger">In arrears</span>
                  ) : (
                    <span className="tag-ok">Current</span>
                  ),
              },
            ]}
          />
        </>
      ) : null}
    </>
  )
}
