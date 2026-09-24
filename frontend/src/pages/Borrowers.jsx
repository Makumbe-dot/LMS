import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import { ErrorBanner, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { fmt } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi, useDebounced } from '../lib/useApi.js'

export default function Borrowers() {
  const { can } = useAuth()
  const { activeBranches } = useOrg()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [branchId, setBranchId] = useState('')
  const [kyc, setKyc] = useState('')
  const [page, setPage] = useState(1)
  const debounced = useDebounced(search)

  const { data, error, loading, reload } = useApi(
    `/api/borrowers${qs({ q: debounced, branch_id: branchId, kyc, page, page_size: 50 })}`,
  )

  const onFilter = (setter) => (event) => {
    setter(event.target.value)
    setPage(1)
  }

  const columns = [
    { key: 'no', header: 'No.', render: (r) => r.borrower_no },
    { key: 'name', header: 'Name', render: (r) => `${r.first_name} ${r.last_name}` },
    { key: 'nid', header: 'National ID', render: (r) => r.national_id },
    { key: 'phone', header: 'Phone', render: (r) => r.phone },
    { key: 'employer', header: 'Employer', render: (r) => r.employer || '-' },
    { key: 'branch', header: 'Branch', render: (r) => r.branch_name || '-' },
    { key: 'salary', header: 'Net salary', num: true, render: (r) => fmt(r.net_salary) },
    {
      key: 'kyc',
      header: 'KYC',
      render: (r) =>
        r.kyc_verified ? (
          <span className="tag-ok">Verified</span>
        ) : (
          <span className="tag-danger">Pending</span>
        ),
    },
    { key: 'docs', header: 'Docs', num: true, render: (r) => r.documents?.length ?? 0 },
    { key: 'active', header: 'Active loans', num: true, render: (r) => r.active_loans },
    {
      key: 'outstanding',
      header: 'Outstanding',
      num: true,
      render: (r) => fmt(r.total_outstanding),
    },
    {
      key: 'flags',
      header: 'Flags',
      render: (r) => (r.is_blacklisted ? <span className="tag-danger">Blacklisted</span> : ''),
    },
  ]

  return (
    <>
      <PageHeader title="Borrowers" meta={data ? `${data.count} on the register` : undefined}>
        <input
          type="search"
          placeholder="Search name, ID, phone, employer"
          value={search}
          onChange={onFilter(setSearch)}
          aria-label="Search borrowers"
          style={{ minWidth: 250 }}
        />
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
        <select value={kyc} onChange={onFilter(setKyc)} aria-label="Filter by KYC status">
          <option value="">Any KYC status</option>
          <option value="1">KYC verified</option>
          <option value="0">KYC pending</option>
        </select>
        {can('admin', 'loan_officer') ? (
          <Link className="btn primary" to="/borrowers/new">
            New borrower
          </Link>
        ) : null}
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading borrowers" />
      ) : (
        <>
          <Pager meta={data} onPage={setPage} noun="borrowers" />
          <DataTable
            caption="Borrower register"
            columns={columns}
            rows={data?.results || []}
            onRowClick={(row) => navigate(`/borrowers/${row.id}`)}
            empty={search ? 'No borrowers match that search' : 'No borrowers yet'}
          />
        </>
      )}
    </>
  )
}
