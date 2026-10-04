import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, ExportButtons, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { downloadFile, qs } from '../lib/api.js'
import { fmt, humanise } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi } from '../lib/useApi.js'

export const SHEETS = [
  {
    kind: 'members',
    title: 'Member register',
    file: 'member_register',
    about:
      'One row per member: contact and employer details, group, savings balance, loans taken, and what each member owes and has overdue.',
  },
  {
    kind: 'loans-outstanding',
    title: 'Loans outstanding',
    file: 'loans_outstanding',
    about: 'Every running loan with principal, interest, penalties and charges still owed.',
  },
  {
    kind: 'overdue',
    title: 'Overdue loans',
    file: 'overdue_loans',
    about: 'Loans in arrears, with the amount overdue, days past due and arrears bucket.',
  },
  {
    kind: 'savings-balances',
    title: 'Savings balances',
    file: 'savings_balances',
    about: 'Every savings account and its balance, available amount and product.',
  },
  {
    kind: 'group-membership',
    title: 'Group membership',
    file: 'group_membership',
    about: 'Who belongs to which group, their role, and the group’s loans and arrears.',
  },
]

/** Columns for whatever the sheet holds; the server names its money columns. */
export function previewColumns(rows, moneyColumns = []) {
  if (!rows.length) return []
  return Object.keys(rows[0])
    .filter((key) => key !== 'id' && !key.endsWith('_id'))
    .map((key) => {
      const isMoney = moneyColumns.includes(key)
      return {
        key,
        header: humanise(key),
        num: isMoney,
        render: (r) => {
          const value = r[key]
          if (value === null || value === undefined || value === '') return '-'
          if (typeof value === 'boolean') return value ? 'Yes' : 'No'
          return isMoney ? fmt(value) : String(value)
        },
      }
    })
}

/** The keepable listings, each downloadable as Excel or CSV, or all of them in one workbook. */
export default function Spreadsheets() {
  const { toastError } = useToast()
  const { activeBranches } = useOrg()
  const [branchId, setBranchId] = useState('')
  const [kind, setKind] = useState('members')
  const [page, setPage] = useState(1)
  const [busy, setBusy] = useState(false)

  const sheet = SHEETS.find((s) => s.kind === kind)
  const filter = qs({ branch_id: branchId })
  const preview = useApi(`/api/reports/spreadsheets/${kind}${qs({ branch_id: branchId, page })}`)
  const rows = preview.data?.results || []

  async function downloadAll() {
    setBusy(true)
    try {
      await downloadFile(`/api/reports/workbook${filter}`, 'xlsx', 'portfolio')
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Spreadsheets"
        meta="The member register, balances, outstanding and overdue loans, ready for Excel"
      >
        {activeBranches.length > 1 ? (
          <select
            value={branchId}
            onChange={(e) => {
              setBranchId(e.target.value)
              setPage(1)
            }}
            aria-label="Filter by branch"
          >
            <option value="">All branches</option>
            {activeBranches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        ) : null}
        <button type="button" className="btn primary" disabled={busy} onClick={downloadAll}>
          {busy ? 'Preparing…' : 'Download everything (Excel workbook)'}
        </button>
      </PageHeader>

      <p className="muted" style={{ marginTop: 0 }}>
        The workbook has a summary sheet and one sheet for each listing below. Every sheet has
        filters on its headings and totals that follow the filter.
      </p>

      <div className="grid cols-3">
        {SHEETS.map((s) => (
          <div key={s.kind} className={`card sheet-card${s.kind === kind ? ' selected' : ''}`}>
            <h3>{s.title}</h3>
            <p className="muted" style={{ fontSize: 13 }}>
              {s.about}
            </p>
            <div className="row">
              <ExportButtons
                small
                path={`/api/reports/spreadsheets/${s.kind}${filter}`}
                name={s.file}
              />
              <button
                type="button"
                className="btn small"
                aria-pressed={s.kind === kind}
                onClick={() => {
                  setKind(s.kind)
                  setPage(1)
                }}
              >
                Preview
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>{sheet.title}</h3>
        <ErrorBanner error={preview.error} onRetry={preview.reload} />
        {preview.loading && !preview.data ? (
          <Loading what={`Loading the ${sheet.title.toLowerCase()}`} />
        ) : (
          <DataTable
            caption={sheet.title}
            rows={rows}
            rowKey={(r, index) => `${page}-${index}`}
            empty="Nothing to show"
            columns={previewColumns(rows, preview.data?.money_columns)}
          />
        )}
        <Pager meta={preview.data} onPage={setPage} noun="rows" />
      </div>
    </>
  )
}
