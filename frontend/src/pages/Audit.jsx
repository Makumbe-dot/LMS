import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import { useToast } from '../components/Toast.jsx'
import { ErrorBanner, Loading, PageHeader, Pager } from '../components/ui.jsx'
import { downloadCsv, qs } from '../lib/api.js'
import { dateTime, firstOfMonth, today } from '../lib/format.js'
import { useApi, useDebounced } from '../lib/useApi.js'

const ENTITIES = ['loan', 'borrower', 'product', 'user', 'branch', 'settings', 'system']

export default function Audit() {
  const { toastError } = useToast()
  const [search, setSearch] = useState('')
  const [entity, setEntity] = useState('')
  const [start, setStart] = useState(firstOfMonth())
  const [end, setEnd] = useState(today())
  const [page, setPage] = useState(1)
  const debounced = useDebounced(search)

  const path = `/api/reports/audit${qs({
    q: debounced,
    entity,
    start,
    end,
    page,
    page_size: 100,
  })}`
  const { data, error, loading, reload } = useApi(path)

  const onFilter = (setter) => (event) => {
    setter(event.target.value)
    setPage(1)
  }

  return (
    <>
      <PageHeader
        title="Audit log"
        meta="Every posting and every decision, newest first"
      >
        <input
          type="search"
          placeholder="Search action or detail"
          value={search}
          onChange={onFilter(setSearch)}
          aria-label="Search the audit log"
          style={{ minWidth: 220 }}
        />
        <select value={entity} onChange={onFilter(setEntity)} aria-label="Filter by entity">
          <option value="">All entities</option>
          {ENTITIES.map((e) => (
            <option key={e} value={e}>
              {e}
            </option>
          ))}
        </select>
        <label className="check">
          From&nbsp;
          <input type="date" value={start} onChange={onFilter(setStart)} />
        </label>
        <label className="check">
          To&nbsp;
          <input type="date" value={end} onChange={onFilter(setEnd)} />
        </label>
        <button type="button" className="btn" onClick={reload}>
          Refresh
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => downloadCsv(path, 'audit_log').catch(toastError)}
        >
          Export CSV
        </button>
      </PageHeader>

      <ErrorBanner error={error} onRetry={reload} />
      {loading && !data ? (
        <Loading what="Loading the audit log" />
      ) : (
        <>
          <Pager meta={data} onPage={setPage} noun="entries" />
          <DataTable
            caption="Audit log"
            rows={data?.results || []}
            empty="Nothing recorded in this window"
            columns={[
              { key: 'when', header: 'When', render: (r) => dateTime(r.created_at) },
              { key: 'user', header: 'User', render: (r) => r.username },
              { key: 'action', header: 'Action', render: (r) => r.action },
              {
                key: 'entity',
                header: 'Entity',
                render: (r) => `${r.entity}${r.entity_id ? ` #${r.entity_id}` : ''}`,
              },
              { key: 'detail', header: 'Detail', render: (r) => r.detail || '' },
            ]}
          />
        </>
      )}
    </>
  )
}
