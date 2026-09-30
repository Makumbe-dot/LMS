import { useRef, useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { Check, Kpi, PageHeader } from '../components/ui.jsx'
import { postForm } from '../lib/api.js'
import { bytes, fmt, money } from '../lib/format.js'
import { PeriodNotice } from '../lib/periods.jsx'

const SAMPLE = `loan_no,amount,date,method,reference
LN-000001,197.02,2026-09-25,salary_deduction,PAY-0925
LN-000002,150.00,2026-09-25,salary_deduction,PAY-0925`

/**
 * Two-step CSV import: preview what would happen, then post the batch.
 * Nothing is written until Post is pressed.
 */
export default function BulkImport() {
  const { toast, toastError } = useToast()
  const inputRef = useRef(null)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [result, setResult] = useState(null)
  const [allowPartial, setAllowPartial] = useState(false)
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
    if (commit) {
      form.append('commit', 'true')
      form.append('allow_partial', allowPartial ? 'true' : 'false')
    }
    setBusy(true)
    try {
      const data = await postForm('/api/imports/repayments', form)
      if (commit) {
        setResult(data)
        setPreview(null)
        toast(`Posted ${data.posted_rows} repayment(s) totalling ${money(data.total_amount)}`)
      } else {
        setPreview(data)
      }
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  function downloadTemplate() {
    const blob = new Blob([SAMPLE], { type: 'text/csv' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = 'repayments_template.csv'
    link.click()
    URL.revokeObjectURL(link.href)
  }

  return (
    <>
      <PageHeader
        title="Bulk repayments"
        meta="Import a payroll or bank return as CSV. It is checked line by line before anything is posted."
      >
        <button type="button" className="btn" onClick={downloadTemplate}>
          Download template
        </button>
      </PageHeader>

      <div className="card">
        <h3>1. Choose a file</h3>
        {/* Before the upload, not after: a payroll return full of last month's
            dates is worth knowing about before it is validated row by row. */}
        <PeriodNotice />
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
          <p style={{ margin: '0 0 12px' }}>
            Drop a CSV here, or
          </p>
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
            Columns: <code>loan_no</code> and <code>amount</code> are required;{' '}
            <code>date</code>, <code>method</code>, <code>reference</code> and{' '}
            <code>narration</code> are optional.
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
            <Kpi label="Rows in the file" value={preview.total_rows} />
            <Kpi label="Ready to post" value={preview.valid_rows} sub={money(preview.total_amount)} />
            <Kpi
              label="With problems"
              value={preview.invalid_rows}
              sub={preview.invalid_rows ? 'these are listed below' : 'none'}
            />
            <div className="kpi">
              <div className="label">2. Post the batch</div>
              <div className="row" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || preview.valid_rows === 0}
                  onClick={() => send(true)}
                >
                  {busy ? 'Posting…' : `Post ${preview.valid_rows} repayment(s)`}
                </button>
              </div>
              {preview.invalid_rows ? (
                <Check
                  label="Post the valid rows only"
                  checked={allowPartial}
                  onChange={(e) => setAllowPartial(e.target.checked)}
                />
              ) : null}
            </div>
          </div>

          <DataTable
            caption="Import preview"
            rows={preview.rows}
            rowKey={(r) => r.line}
            empty="Nothing to show"
            columns={[
              { key: 'line', header: 'Line', num: true, render: (r) => r.line },
              {
                key: 'ok',
                header: '',
                render: (r) =>
                  r.ok ? (
                    <span className="tag-ok">OK</span>
                  ) : (
                    <span className="tag-danger">Problem</span>
                  ),
              },
              { key: 'loan', header: 'Loan', render: (r) => r.loan_no || '-' },
              { key: 'borrower', header: 'Borrower', render: (r) => r.borrower || '-' },
              { key: 'amount', header: 'Amount', num: true, render: (r) => fmt(r.amount) },
              { key: 'date', header: 'Date', render: (r) => r.date },
              { key: 'method', header: 'Method', render: (r) => r.method },
              { key: 'reference', header: 'Reference', render: (r) => r.reference || '-' },
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
            <Kpi label="Posted" value={result.posted_rows} sub={money(result.total_amount)} />
            <Kpi label="Skipped" value={result.skipped_rows} sub="failed validation" />
            <div className="kpi">
              <div className="label">Done</div>
              <div className="row" style={{ marginTop: 8 }}>
                <button type="button" className="btn" onClick={() => choose(null)}>
                  Import another file
                </button>
              </div>
            </div>
          </div>
          <DataTable
            caption="Postings"
            rows={result.postings}
            rowKey={(r) => r.transaction_id}
            columns={[
              { key: 'line', header: 'Line', num: true, render: (r) => r.line },
              { key: 'loan', header: 'Loan', render: (r) => r.loan_no },
              { key: 'txn', header: 'Transaction', num: true, render: (r) => r.transaction_id },
              { key: 'amount', header: 'Amount', num: true, render: (r) => fmt(r.amount) },
            ]}
          />
        </>
      ) : null}
    </>
  )
}
