import { useState } from 'react'
import { Link } from 'react-router-dom'

import LoanTable from '../components/LoanTable.jsx'
import { ErrorBanner, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { humanise } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi, useDebounced } from '../lib/useApi.js'

const STATUSES = ['pending', 'approved', 'active', 'closed', 'rejected', 'written_off']

export default function Loans() {
  const { can } = useAuth()
  const { activeBranches } = useOrg()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [branchId, setBranchId] = useState('')
  const [inArrears, setInArrears] = useState(false)
  const [page, setPage] = useState(1)
  const debounced = useDebounced(search)

  const { data, error, loading, reload } = useApi(
    `/api/loans${qs({
      q: debounced,
      status,
      branch_id: branchId,
      in_arrears: inArrears ? '1' : '',
      page,
      page_size: 50,
    })}`,
  )

  const onFilter = (setter) => (event) => {
    setter(event.target.value)
    setPage(1)
  }

  return (
    <>
      <PageHeader title="Loans" meta={data ? `${data.count} loans` : undefined}>
        <input
          type="search"
          placeholder="Search loan no. or borrower"
          value={search}
          onChange={onFilter(setSearch)}
          aria-label="Search loans"
          style={{ minWidth: 230 }}
        />
        <select value={status} onChange={onFilter(setStatus)} aria-label="Filter by status">
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {humanise(s)}
            </option>
          ))}
        </select>
        {activeBranches.length > 1 ? (
          <select value={branchId} onChange={onFilter(setBranchId)} aria-label="Filter by branch">
            <option value="">All branches</option>
            {activeBranches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        ) : null}
        <label className="check">
          <input
            type="checkbox"
            checked={inArrears}
            onChange={(e) => {
              setInArrears(e.target.checked)
              setPage(1)
            }}
          />
          In arrears only
        </label>
        {can('admin', 'loan_officer') ? (
          <Link className="btn primary" to="/loans/new">
            New application
          </Link>
        ) : null}
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading loans" />
      ) : (
        <>
          <Pager meta={data} onPage={setPage} noun="loans" />
          <LoanTable loans={data?.results || []} empty="No loans match these filters" />
        </>
      )}
    </>
  )
}
