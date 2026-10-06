import { useState } from 'react'
import { Link } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import Modal, { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import { Badge, ErrorBanner, Field, Kpi, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { del, post, postForm, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime, fmt, humanise, money, num } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const signed = (value) =>
  num(value) < 0 ? <span className="tag-warn">{fmt(value)}</span> : fmt(value)

function UploadForm({ busy, onClose, onSubmit }) {
  const [file, setFile] = useState(null)
  return (
    <FormModal
      title="Upload a statement"
      submitLabel="Upload and match"
      busy={busy}
      submitDisabled={!file}
      onClose={onClose}
      onSubmit={(v) => {
        const form = new FormData()
        form.append('file', file)
        form.append('account_name', v.account_name)
        form.append('channel', v.channel)
        if (v.opening_balance) form.append('opening_balance', v.opening_balance)
        if (v.closing_balance) form.append('closing_balance', v.closing_balance)
        onSubmit(form)
      }}
    >
      <div className="grid cols-2">
        <Field label="Account" name="account_name" required hint="e.g. CBZ current account" />
        <Field as="select" label="Kind" name="channel" defaultValue="bank_transfer">
          <option value="bank_transfer">Bank account</option>
          <option value="mobile_money">Mobile-money wallet</option>
        </Field>
        <Field
          label="Opening balance"
          type="number"
          step="0.01"
          name="opening_balance"
          hint="Optional: checks the file adds up"
        />
        <Field label="Closing balance" type="number" step="0.01" name="closing_balance" />
      </div>
      <Field
        label="Statement file (CSV)"
        type="file"
        accept=".csv,text/csv"
        onChange={(e) => setFile(e.target.files?.[0] || null)}
        hint="A date column, and either an amount or money in / money out columns. ISO or DD/MM/YYYY dates."
      />
    </FormModal>
  )
}

function CandidatesModal({ line, busy, onClose, onMatch }) {
  const found = useApi(`/api/bank-statements/lines/${line.id}/candidates`)
  return (
    <Modal title={`Match line ${line.line_no}: ${money(line.amount)} on ${line.txn_date}`} onClose={onClose} wide>
      <p className="muted" style={{ marginTop: 0 }}>
        Ledger entries of exactly {money(line.amount)}, within two weeks and not matched to anything
        else, nearest first. Nothing here? The books do not know about this line yet — book it
        instead.
      </p>
      <ErrorBanner error={found.error} />
      {found.loading ? (
        <Loading what="Finding candidates" />
      ) : (
        <DataTable
          caption="Candidate entries"
          rows={found.data || []}
          empty="No entry of that amount"
          columns={[
            { key: 'no', header: 'Entry', render: (e) => e.entry_no },
            { key: 'date', header: 'Date', render: (e) => e.entry_date },
            { key: 'what', header: 'What', render: (e) => e.narration },
            { key: 'ref', header: 'Reference', render: (e) => e.reference || '-' },
            { key: 'method', header: 'Method', render: (e) => (e.method ? humanise(e.method) : '-') },
            {
              key: 'actions',
              header: '',
              render: (e) => (
                <button type="button" className="btn small primary" disabled={busy} onClick={() => onMatch(e.id)}>
                  Match
                </button>
              ),
            },
          ]}
        />
      )}
    </Modal>
  )
}

function StatementView({ id, canAccount, onBack }) {
  const { toast, toastError } = useToast()
  const statement = useApi(`/api/bank-statements/${id}`)
  const outstanding = useApi(`/api/bank-statements/${id}/outstanding`)
  const accounts = useApi('/api/ledger/accounts')
  const [action, setAction] = useState(null) // {kind, line}
  const [busy, setBusy] = useState(false)

  async function run(promise, message, { reloadAll = true } = {}) {
    setBusy(true)
    try {
      const result = await promise
      // Line actions answer with the whole statement; booking a line answers with
      // the journal it prepared, which must not replace the statement on screen.
      if (result?.statement_no) statement.setData(result)
      else statement.reload()
      if (reloadAll) outstanding.reload()
      toast(message)
      setAction(null)
      return result
    } catch (err) {
      toastError(err)
      return null
    } finally {
      setBusy(false)
    }
  }

  if (statement.loading && !statement.data) return <Loading what="Loading the statement" />
  if (statement.error) return <ErrorBanner error={statement.error} onRetry={statement.reload} />
  const s = statement.data
  if (!s) return null
  const sum = s.summary
  const usable = (accounts.data || []).filter((a) => a.is_active && !a.controlled_by && a.code !== '1000')

  return (
    <>
      <PageHeader
        title={`${s.statement_no} — ${s.account_name}`}
        meta={`${s.channel_label}, ${s.period_start} to ${s.period_end}, uploaded ${dateTime(s.uploaded_at)} by ${s.uploaded_by_name || '-'}`}
      >
        <button type="button" className="btn" onClick={onBack}>
          All statements
        </button>
        {canAccount ? (
          <>
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={() => run(post(`/api/bank-statements/${id}/auto-match`), 'Matched what could be')}
            >
              Match again
            </button>
            <button
              type="button"
              className="btn danger"
              onClick={() => {
                if (window.confirm(`Delete ${s.statement_no}? Its matches are released.`)) {
                  del(`/api/bank-statements/${id}`).then(onBack).catch(toastError)
                }
              }}
            >
              Delete
            </button>
          </>
        ) : null}
      </PageHeader>

      <div className="grid cols-4">
        <Kpi label="Lines" value={sum.lines} sub={`${money(sum.money_in)} in, ${money(sum.money_out)} out`} />
        <Kpi label="Matched" value={sum.matched} sub={`${sum.ignored} set aside`} />
        <Kpi
          label="Not in the books"
          value={sum.unmatched}
          sub={sum.unmatched ? `${money(sum.unmatched_amount)} net` : 'none'}
        />
        <Kpi
          label="Status"
          value={sum.reconciled ? <span className="tag-ok">Reconciled</span> : <span className="tag-warn">Open items</span>}
          sub={
            sum.adds_up === null
              ? 'No balances given to check'
              : sum.adds_up
                ? 'The file adds up to its closing balance'
                : 'The file does NOT add up — is it complete?'
          }
        />
      </div>

      <div className="card">
        <h3>On the statement</h3>
        <DataTable
          caption="Statement lines"
          rows={s.lines}
          columns={[
            { key: 'n', header: '#', num: true, render: (l) => l.line_no },
            { key: 'date', header: 'Date', render: (l) => l.txn_date },
            { key: 'description', header: 'Description', render: (l) => l.description || '-' },
            { key: 'reference', header: 'Reference', render: (l) => l.reference || '-' },
            { key: 'amount', header: 'Amount', num: true, render: (l) => signed(l.amount) },
            { key: 'status', header: 'Status', render: (l) => <Badge value={l.status} /> },
            {
              key: 'entry',
              header: 'Matched to',
              render: (l) =>
                l.entry_no
                  ? `${l.entry_no}${l.auto_matched ? ' (auto)' : ''} — ${l.entry_narration}`
                  : l.note || '-',
            },
            {
              key: 'actions',
              header: '',
              render: (l) =>
                !canAccount ? null : l.status === 'unmatched' ? (
                  <div className="row" style={{ gap: 6 }}>
                    <button type="button" className="btn small" onClick={() => setAction({ kind: 'match', line: l })}>
                      Find a match
                    </button>
                    <button type="button" className="btn small" onClick={() => setAction({ kind: 'journal', line: l })}>
                      Book it
                    </button>
                    <button type="button" className="btn small" onClick={() => setAction({ kind: 'ignore', line: l })}>
                      Set aside
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="btn small"
                    disabled={busy}
                    onClick={() => run(post(`/api/bank-statements/lines/${l.id}/unmatch`), 'Line released')}
                  >
                    Unmatch
                  </button>
                ),
            },
          ]}
        />
      </div>

      <div className="card">
        <h3>In the books, not on the statement</h3>
        <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>
          {s.channel === 'mobile_money' ? 'Mobile-money' : 'Bank and payroll'} postings dated in the
          statement period that no line has matched: payments the bank has not shown yet, or
          postings made to the wrong channel. Cash never appears here — it goes through the tills.
        </p>
        <ErrorBanner error={outstanding.error} />
        <DataTable
          caption="Outstanding book entries"
          rows={outstanding.data || []}
          empty="Everything in the books for this period is on the statement"
          columns={[
            { key: 'no', header: 'Entry', render: (e) => e.entry_no },
            { key: 'date', header: 'Date', render: (e) => e.entry_date },
            { key: 'what', header: 'What', render: (e) => e.narration },
            { key: 'ref', header: 'Reference', render: (e) => e.reference || '-' },
            { key: 'loan', header: 'Loan', render: (e) => e.loan_no || '-' },
            { key: 'amount', header: 'Amount', num: true, render: (e) => signed(e.amount) },
          ]}
        />
      </div>

      {action?.kind === 'match' ? (
        <CandidatesModal
          line={action.line}
          busy={busy}
          onClose={() => setAction(null)}
          onMatch={(entryId) =>
            run(
              post(`/api/bank-statements/lines/${action.line.id}/match`, { entry_id: entryId }),
              'Matched',
            )
          }
        />
      ) : null}

      {action?.kind === 'ignore' ? (
        <FormModal
          title={`Set line ${action.line.line_no} aside`}
          submitLabel="Set aside"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) => run(post(`/api/bank-statements/lines/${action.line.id}/ignore`, v), 'Line set aside')}
        >
          <p className="muted" style={{ marginTop: 0 }}>
            For a line that is not a ledger movement at all — a transfer between the institution's
            own accounts, say. The reason stays on the line.
          </p>
          <Field as="textarea" label="Reason" name="reason" rows={2} required minLength={5} />
        </FormModal>
      ) : null}

      {action?.kind === 'journal' ? (
        <FormModal
          title={`Book line ${action.line.line_no}: ${money(action.line.amount)}`}
          submitLabel="Prepare the journal"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={async (v) => {
            const journal = await run(
              post(`/api/bank-statements/lines/${action.line.id}/journal`, v),
              'Journal prepared',
              { reloadAll: false },
            )
            if (journal?.journal_no) {
              toast(`${journal.journal_no} awaits approval; once posted, Match again pairs it`)
            }
          }}
        >
          <p className="muted" style={{ marginTop: 0 }}>
            The books do not know about this line. Choose what it was and a journal is prepared
            against the bank for {money(Math.abs(num(action.line.amount)))}, carrying the bank's
            reference. It goes to the <Link to="/journals">Journals</Link> page for approval like
            any other.
          </p>
          <Field as="select" label="What it was" name="account_code" required defaultValue={num(action.line.amount) < 0 ? '6600' : '4900'}>
            {usable.map((a) => (
              <option key={a.id} value={a.code}>
                {a.code} {a.name}
              </option>
            ))}
          </Field>
        </FormModal>
      ) : null}
    </>
  )
}

/** Bank and mobile-money statements, matched line by line against the ledger. */
export default function BankRec() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const [page, setPage] = useState(1)
  const list = useApi(`/api/bank-statements${qs({ page })}`)
  const [openId, setOpenId] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [busy, setBusy] = useState(false)
  const canAccount = can('accounting')

  if (openId) {
    return (
      <StatementView
        id={openId}
        canAccount={canAccount}
        onBack={() => {
          setOpenId(null)
          list.reload()
        }}
      />
    )
  }

  async function upload(form) {
    setBusy(true)
    try {
      const statement = await postForm('/api/bank-statements', form)
      toast(`${statement.statement_no}: ${statement.summary.matched} of ${statement.summary.lines} matched`)
      setUploading(false)
      setOpenId(statement.id)
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Bank reconciliation"
        meta="The bank's record against the ledger's: what neither a trial balance nor a reconciliation of the books can see"
      >
        {canAccount ? (
          <button type="button" className="btn primary" onClick={() => setUploading(true)}>
            Upload a statement
          </button>
        ) : null}
      </PageHeader>

      <ErrorBanner error={list.error} onRetry={list.reload} />
      {list.loading && !list.data ? (
        <Loading what="Loading statements" />
      ) : (
        <>
          <DataTable
            caption="Statements"
            rows={list.data?.results || []}
            onRowClick={(row) => setOpenId(row.id)}
            empty="No statements yet. Export one from the bank or wallet as CSV and upload it."
            columns={[
              { key: 'no', header: 'Statement', render: (s) => s.statement_no },
              { key: 'account', header: 'Account', render: (s) => s.account_name },
              { key: 'kind', header: 'Kind', render: (s) => s.channel_label },
              { key: 'period', header: 'Period', render: (s) => `${s.period_start} to ${s.period_end}` },
              { key: 'lines', header: 'Lines', num: true, render: (s) => s.summary.lines },
              { key: 'matched', header: 'Matched', num: true, render: (s) => s.summary.matched },
              { key: 'open', header: 'Not in the books', num: true, render: (s) => s.summary.unmatched },
              {
                key: 'status',
                header: 'Status',
                render: (s) =>
                  s.summary.reconciled ? <span className="tag-ok">Reconciled</span> : <span className="tag-warn">Open items</span>,
              },
            ]}
          />
          <Pager meta={list.data} onPage={setPage} noun="statements" />
        </>
      )}

      {uploading ? <UploadForm busy={busy} onClose={() => setUploading(false)} onSubmit={upload} /> : null}
    </>
  )
}
