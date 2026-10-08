import { useState } from 'react'
import { Link } from 'react-router-dom'

import { del, get, post, postForm, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateOnly, fmt, today } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'
import DataTable from './DataTable.jsx'
import Modal, { FormModal } from './Modal.jsx'
import { useToast } from './Toast.jsx'
import { ExportButtons, ErrorBanner, Field, Loading } from './ui.jsx'

const TONE = {
  full: 'tag-ok',
  short: 'tag-warn',
  missed: 'tag-danger',
  over: 'tag-warn',
  unknown: 'tag-danger',
}

/**
 * What the employer sent back: its file of what was actually deducted, checked
 * against the schedule above, then posted as salary-deduction repayments.
 */
export default function PayrollReturns({ employer, start, end }) {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const runs = useApi(`/api/payroll/runs${qs({ employer })}`)
  const [checking, setChecking] = useState(false)
  const [open, setOpen] = useState(null) // the run shown, with its lines
  const [busy, setBusy] = useState(false)
  const mayAct = can('cash')

  async function show(id) {
    try {
      setOpen(await get(`/api/payroll/runs/${id}`))
    } catch (err) {
      toastError(err)
    }
  }

  async function check(values, file) {
    const body = new FormData()
    body.append('file', file)
    body.append('employer', employer)
    body.append('start', start)
    body.append('end', end)
    body.append('received_on', values.received_on)
    if (values.reference) body.append('reference', values.reference)
    setBusy(true)
    try {
      const run = await postForm('/api/payroll/runs', body)
      setChecking(false)
      setOpen(run)
      runs.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function act(promise, message) {
    setBusy(true)
    try {
      const result = await promise
      toast(message)
      setOpen(result && result.id ? result : null)
      runs.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <div className="row between" style={{ marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>Returns from the employer</h3>
        {mayAct ? (
          <button
            type="button"
            className="btn primary"
            disabled={!employer}
            title={employer ? undefined : 'Choose the employer first'}
            onClick={() => setChecking(true)}
          >
            Check a return
          </button>
        ) : null}
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Upload what {employer || 'the employer'} actually deducted for this period. Each loan
        on the schedule is set against it; nothing is posted until you post the return.
      </p>
      <ErrorBanner error={runs.error} onRetry={runs.reload} />
      {runs.loading && !runs.data ? (
        <Loading what="Loading returns" />
      ) : (
        <DataTable
          caption="Payroll returns"
          rows={runs.data || []}
          onRowClick={(r) => show(r.id)}
          empty="No returns checked yet"
          columns={[
            { key: 'employer', header: 'Employer', render: (r) => r.employer },
            {
              key: 'period',
              header: 'Period',
              render: (r) => `${dateOnly(r.period_start)} to ${dateOnly(r.period_end)}`,
            },
            { key: 'received', header: 'Money arrived', render: (r) => dateOnly(r.received_on) },
            {
              key: 'expected',
              header: 'Asked for',
              num: true,
              render: (r) => fmt(r.expected_total),
            },
            {
              key: 'deducted',
              header: 'Deducted',
              num: true,
              render: (r) => fmt(r.deducted_total),
            },
            {
              key: 'status',
              header: 'Status',
              render: (r) => (
                <span className={r.status === 'posted' ? 'tag-ok' : 'tag-warn'}>
                  {r.status_label}
                </span>
              ),
            },
          ]}
        />
      )}

      {checking ? (
        <CheckReturn
          employer={employer}
          start={start}
          end={end}
          busy={busy}
          onClose={() => setChecking(false)}
          onSubmit={check}
        />
      ) : null}

      {open ? (
        <Modal
          title={`${open.employer}: ${dateOnly(open.period_start)} to ${dateOnly(open.period_end)}`}
          wide
          onClose={() => setOpen(null)}
        >
          <div className="row" style={{ gap: 16, flexWrap: 'wrap', marginBottom: 12 }}>
            <span>
              Asked for <strong>{fmt(open.expected_total)}</strong>
            </span>
            <span>
              Deducted <strong>{fmt(open.deducted_total)}</strong>
            </span>
            <span>
              Short <strong>{fmt(open.shortfall)}</strong>
            </span>
            {open.status === 'posted' ? (
              <span className="tag-ok">Posted {fmt(open.posted_total)}</span>
            ) : null}
          </div>
          <DataTable
            caption="Return lines"
            rows={open.lines}
            empty="No lines"
            columns={[
              {
                key: 'status',
                header: 'Status',
                render: (l) => <span className={TONE[l.status]}>{l.status_label}</span>,
              },
              {
                key: 'loan',
                header: 'Loan',
                render: (l) =>
                  l.loan_id ? <Link to={`/loans/${l.loan_id}`}>{l.loan_no}</Link> : '-',
              },
              { key: 'emp', header: 'Employee no.', render: (l) => l.employee_no || '-' },
              { key: 'name', header: 'Name', render: (l) => l.name || '-' },
              { key: 'expected', header: 'Asked for', num: true, render: (l) => fmt(l.expected) },
              { key: 'deducted', header: 'Deducted', num: true, render: (l) => fmt(l.deducted) },
              { key: 'short', header: 'Short', num: true, render: (l) => fmt(l.shortfall) },
              {
                key: 'note',
                header: '',
                wrap: true,
                render: (l) => <small className="muted">{l.note || ''}</small>,
              },
            ]}
          />
          <div className="row" style={{ justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
            <ExportButtons
              path={`/api/payroll/runs/${open.id}`}
              name={`payroll_return_${open.id}`}
            />
            {mayAct && open.status === 'draft' ? (
              <>
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => act(del(`/api/payroll/runs/${open.id}`), 'Return discarded')}
                >
                  Discard
                </button>
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy}
                  onClick={() =>
                    act(post(`/api/payroll/runs/${open.id}/post`), 'Deductions posted')
                  }
                >
                  Post deductions
                </button>
              </>
            ) : null}
          </div>
        </Modal>
      ) : null}
    </div>
  )
}

function CheckReturn({ employer, start, end, busy, onClose, onSubmit }) {
  const [file, setFile] = useState(null)
  return (
    <FormModal
      title={`Check ${employer}'s return`}
      submitLabel="Check against the schedule"
      busy={busy}
      submitDisabled={!file}
      onClose={onClose}
      onSubmit={(values) => onSubmit(values, file)}
    >
      <p className="muted" style={{ marginTop: 0 }}>
        For the period {start} to {end}.
      </p>
      <div className="grid cols-2">
        <Field
          label="Money arrived on"
          name="received_on"
          type="date"
          required
          defaultValue={today()}
        />
        <Field label="Reference" name="reference" hint="The employer's payment reference, if any" />
      </div>
      <label className="field">
        <span className="label-text">The employer's file (CSV)</span>
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        <span className="hint">
          An amount column, and a loan number, employee number or national ID for each line.
        </span>
      </label>
    </FormModal>
  )
}
