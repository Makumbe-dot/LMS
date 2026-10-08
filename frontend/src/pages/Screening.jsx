import { useState } from 'react'
import { Link } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Field, Loading, PageHeader } from '../components/ui.jsx'
import { post, postForm, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const STATUS_LABEL = { open: 'To review', cleared: 'Not the same person', confirmed: 'Confirmed match' }

/**
 * Sanctions and watch-list screening. Borrowers are checked against the loaded
 * lists when they are added and whenever a list is loaded; a possible match holds
 * that borrower's loans (no approval, no payout) until someone reviews it here.
 */
export default function Screening() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const [status, setStatus] = useState('open')
  const [reviewing, setReviewing] = useState(null) // { hit, decision }
  const [busy, setBusy] = useState(false)
  const hits = useApi(`/api/screening/hits${qs({ status })}`)
  const lists = useApi('/api/screening/lists')
  const mayReview = can('admin') || can('supervise')
  const isAdmin = can('admin')

  async function upload(event) {
    event.preventDefault()
    const element = event.currentTarget
    const form = new FormData(element)
    setBusy(true)
    try {
      const result = await postForm('/api/screening/lists', form)
      toast(
        `Loaded ${result.loaded} names (${result.lists.join(', ')}). Screened ${result.borrowers} borrowers: ${result.new_hits} new possible match(es).`,
      )
      element.reset()
      lists.reload()
      hits.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function screenAgain() {
    setBusy(true)
    try {
      const result = await post('/api/screening/run')
      toast(`Screened ${result.borrowers} borrowers: ${result.new_hits} new possible match(es).`)
      hits.reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  const loaded = lists.data?.lists || []

  return (
    <>
      <PageHeader
        title="Screening"
        meta="Borrowers checked against sanctions and watch lists. A possible match holds the borrower's loans until it is reviewed."
      >
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Show">
          <option value="open">To review</option>
          <option value="cleared">Cleared</option>
          <option value="confirmed">Confirmed</option>
          <option value="">All</option>
        </select>
        {isAdmin ? (
          <button type="button" className="btn" onClick={screenAgain} disabled={busy || !loaded.length}>
            Screen everyone again
          </button>
        ) : null}
      </PageHeader>

      {lists.data && !loaded.length ? (
        <div className="banner" role="status">
          <strong>No lists loaded, so nobody is being screened. </strong>
          Load the UN Security Council consolidated list (its XML file, from the UN's website) and any local
          list below.
        </div>
      ) : null}

      <ErrorBanner error={hits.error} onRetry={hits.reload} />
      {hits.loading && !hits.data ? (
        <Loading what="Loading matches" />
      ) : (
        <DataTable
          caption="Screening matches"
          rows={hits.data || []}
          empty={status === 'open' ? 'Nothing to review.' : 'None.'}
          columns={[
            {
              key: 'borrower',
              header: 'Borrower',
              render: (h) => (
                <>
                  <Link to={`/borrowers/${h.borrower_id}`}>{h.borrower_name}</Link>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {h.borrower_no} · {h.national_id}
                    {h.date_of_birth ? ` · born ${h.date_of_birth}` : ''}
                  </div>
                </>
              ),
            },
            {
              key: 'listed',
              header: 'Listed as',
              wrap: true,
              render: (h) => (
                <>
                  <strong>{h.listed_name}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {h.source}
                    {h.reference ? ` · ${h.reference}` : ''}
                    {h.listed_dob ? ` · born ${h.listed_dob}` : ''}
                    {h.listed_aliases ? ` · also ${h.listed_aliases.split('\n').join(', ')}` : ''}
                  </div>
                </>
              ),
            },
            { key: 'score', header: 'Likeness', num: true, render: (h) => `${h.score}%` },
            { key: 'why', header: 'Why', wrap: true, render: (h) => h.reason },
            {
              key: 'status',
              header: 'Status',
              wrap: true,
              render: (h) => (
                <>
                  <span className={`badge ${h.status === 'open' ? 'pending' : h.status === 'cleared' ? 'active' : 'failed'}`}>
                    {STATUS_LABEL[h.status]}
                  </span>
                  {h.review_note ? (
                    <div className="muted" style={{ fontSize: 12 }}>
                      {h.review_note} ({h.reviewed_by_name}, {dateTime(h.reviewed_at)})
                    </div>
                  ) : null}
                </>
              ),
            },
            {
              key: 'act',
              header: '',
              render: (h) =>
                h.status === 'open' && mayReview ? (
                  <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                    <button type="button" className="btn small" onClick={() => setReviewing({ hit: h, decision: 'cleared' })}>
                      Not them
                    </button>
                    <button type="button" className="btn small danger" onClick={() => setReviewing({ hit: h, decision: 'confirmed' })}>
                      Confirm
                    </button>
                  </div>
                ) : null,
            },
          ]}
        />
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Lists</h3>
        {loaded.length ? (
          <ul className="screen-lists">
            {loaded.map((l) => (
              <li key={l.source}>
                <strong>{l.source}</strong> · {l.names} names · loaded {dateTime(l.loaded_at)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">None loaded yet.</p>
        )}
        {isAdmin ? (
          <form onSubmit={upload} className="grid cols-3" style={{ alignItems: 'end' }}>
            <Field label="File" name="file" type="file" accept=".xml,.csv" required
                   hint="The UN consolidated list (XML), or a CSV with a 'name' column and optional aliases (split by ;), date_of_birth, id_number, reference" />
            <Field label="List name (for a CSV)" name="source" placeholder="e.g. Local PEP list"
                   hint="Loading a list again replaces that list's names and re-screens everyone" />
            <div>
              <button type="submit" className="btn primary" disabled={busy}>
                {busy ? 'Loading…' : 'Load list and screen'}
              </button>
            </div>
          </form>
        ) : null}
      </div>

      {reviewing ? (
        <FormModal
          title={reviewing.decision === 'cleared' ? 'Not the same person' : 'Confirm the match'}
          submitLabel={reviewing.decision === 'cleared' ? 'Clear' : 'Confirm and blacklist'}
          busy={busy}
          onClose={() => setReviewing(null)}
          onSubmit={async (values) => {
            setBusy(true)
            try {
              await post(`/api/screening/hits/${reviewing.hit.id}/review`, {
                decision: reviewing.decision,
                note: values.note,
              })
              toast(reviewing.decision === 'cleared' ? 'Cleared: the loan can go ahead' : 'Confirmed: the borrower is blacklisted')
              setReviewing(null)
              hits.reload()
            } catch (err) {
              toastError(err)
            } finally {
              setBusy(false)
            }
          }}
        >
          <p>
            <strong>{reviewing.hit.borrower_name}</strong> ({reviewing.hit.national_id}) against{' '}
            <strong>{reviewing.hit.listed_name}</strong> on the {reviewing.hit.source} list.
          </p>
          {reviewing.decision === 'confirmed' ? (
            <p className="tag-danger">
              Confirming blacklists the borrower and stops all their loans being approved or paid out. Report it as
              your AML procedure requires.
            </p>
          ) : null}
          <Field as="textarea" label="What you checked" name="note" rows={3} required
                 placeholder="e.g. Different ID number and date of birth; confirmed with the employer" />
        </FormModal>
      ) : null}
    </>
  )
}
