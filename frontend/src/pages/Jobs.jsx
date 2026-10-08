import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import Modal from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { post, qs } from '../lib/api.js'
import { dateOnly, dateTime } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const TONE = { ok: 'tag-ok', failed: 'tag-danger', running: 'tag-warn' }

/**
 * The nightly and monthly jobs: whether each ran, how it went, and its history.
 * The server's scheduler calls `manage.py run_jobs`; this page is how anyone
 * finds out that it did, or that it stopped.
 */
export default function Jobs() {
  const { toast, toastError } = useToast()
  const { data, error, loading, reload } = useApi('/api/jobs')
  const [job, setJob] = useState('')
  const [page, setPage] = useState(1)
  const history = useApi(`/api/jobs/runs${qs({ job, page })}`)
  const [busy, setBusy] = useState(null)
  const [shown, setShown] = useState(null)

  async function runNow(row) {
    setBusy(row.key)
    try {
      const run = await post(`/api/jobs/${row.key}/run`)
      if (run.status === 'ok') toast(`${row.label}: done`)
      else toastError(new Error(`${row.label} failed: ${run.error}`))
      reload()
      history.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(null)
    }
  }

  const attention = (data || []).filter((row) => row.needs_attention)

  return (
    <>
      <PageHeader
        title="Scheduled jobs"
        meta="Run by the server every night through manage.py run_jobs; a job already done for the day is not run twice."
      />
      {attention.length ? (
        <div className="banner" role="status">
          <strong>{attention.length} job(s) need attention. </strong>
          {attention.some((row) => row.stale)
            ? 'A daily job has not succeeded for over a day: check that the scheduled task ' +
              'on the server is still running. '
            : ''}
          A failed job is tried again on the next run; “Run now” tries it at once.
        </div>
      ) : null}
      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading jobs" />
      ) : (
        <DataTable
          caption="Jobs"
          rows={data || []}
          rowKey={(r) => r.key}
          columns={[
            {
              key: 'job',
              header: 'Job',
              wrap: true,
              render: (r) => (
                <>
                  <strong>{r.label}</strong>
                  <div>
                    <small className="muted">{r.about}</small>
                  </div>
                </>
              ),
            },
            {
              key: 'when',
              header: 'Runs',
              render: (r) => (r.schedule === 'daily' ? 'Nightly' : 'On the 1st'),
            },
            {
              key: 'last',
              header: 'Last run',
              render: (r) =>
                r.last_status ? (
                  <>
                    <span className={TONE[r.last_status]}>{r.last_status}</span>{' '}
                    <small className="muted">{dateTime(r.last_run_at)}</small>
                    {r.last_error ? (
                      <div>
                        <small className="tag-danger">{r.last_error}</small>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <span className="muted">never</span>
                ),
            },
            {
              key: 'ok',
              header: 'Last success',
              render: (r) =>
                r.last_success_at ? (
                  <span className={r.stale ? 'tag-warn' : undefined}>
                    {dateTime(r.last_success_at)}
                  </span>
                ) : (
                  <span className={r.stale ? 'tag-warn' : 'muted'}>never</span>
                ),
            },
            {
              key: 'act',
              header: '',
              render: (r) => (
                <button
                  type="button"
                  className="btn small"
                  disabled={busy !== null}
                  onClick={() => runNow(r)}
                >
                  {busy === r.key ? 'Running…' : 'Run now'}
                </button>
              ),
            },
          ]}
        />
      )}

      <div className="card">
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>History</h3>
          <select
            value={job}
            aria-label="Filter history by job"
            onChange={(e) => {
              setJob(e.target.value)
              setPage(1)
            }}
          >
            <option value="">All jobs</option>
            {(data || []).map((row) => (
              <option key={row.key} value={row.key}>
                {row.label}
              </option>
            ))}
          </select>
        </div>
        <Pager meta={history.data} onPage={setPage} noun="runs" />
        <DataTable
          caption="Job history"
          rows={history.data?.results || []}
          onRowClick={(r) => setShown(r)}
          empty="Nothing has run yet."
          columns={[
            { key: 'started', header: 'Started', render: (r) => dateTime(r.started_at) },
            {
              key: 'job',
              header: 'Job',
              render: (r) => (data || []).find((row) => row.key === r.job)?.label || r.job,
            },
            { key: 'for', header: 'For', render: (r) => dateOnly(r.as_of) },
            {
              key: 'status',
              header: 'Outcome',
              render: (r) => <span className={TONE[r.status]}>{r.status_label}</span>,
            },
            { key: 'by', header: 'By', render: (r) => r.triggered_by_name || 'Scheduler' },
          ]}
        />
      </div>

      {shown ? (
        <Modal
          title={`${shown.job} for ${dateOnly(shown.as_of)}`}
          wide
          onClose={() => setShown(null)}
        >
          {shown.error ? <p className="tag-danger">{shown.error}</p> : null}
          <pre className="job-output">{shown.output || 'No output.'}</pre>
        </Modal>
      ) : null}
    </>
  )
}
