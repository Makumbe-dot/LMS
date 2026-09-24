import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import DataTable from '../components/DataTable.jsx'
import LoanTable from '../components/LoanTable.jsx'
import Modal, { FormModal } from '../components/Modal.jsx'
import { useToast } from '../components/Toast.jsx'
import {
  Badge,
  ErrorBanner,
  Field,
  KeyValues,
  Kpi,
  Loading,
  PageHeader,
  Pager,
} from '../components/ui.jsx'
import { del, downloadCsv, get, post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { fmt, humanise, money, today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi, useDebounced } from '../lib/useApi.js'

const ROLES = [
  ['leader', 'Chairperson'],
  ['treasurer', 'Treasurer'],
  ['secretary', 'Secretary'],
  ['member', 'Member'],
]

/** Joint-liability groups: the register and each group's standing. */
export default function Groups() {
  const { can } = useAuth()
  const navigate = useNavigate()
  const { activeBranches } = useOrg()
  const { toast, toastError } = useToast()

  const [tab, setTab] = useState('register')
  const [search, setSearch] = useState('')
  const [branchId, setBranchId] = useState('')
  const [page, setPage] = useState(1)
  const [action, setAction] = useState(null)
  const [detail, setDetail] = useState(null)
  const [busy, setBusy] = useState(false)
  const debounced = useDebounced(search)

  const listPath = `/api/groups${qs({ q: debounced, branch_id: branchId, page, page_size: 50 })}`
  const perfPath = `/api/groups/performance${qs({ branch_id: branchId })}`
  const list = useApi(tab === 'register' ? listPath : null)
  const performance = useApi(tab === 'performance' ? perfPath : null)
  const borrowers = useApi(action?.kind === 'member' ? '/api/borrowers?page_size=1000' : null)

  const mayEdit = can('admin', 'loan_officer')

  async function run(promise, message) {
    setBusy(true)
    try {
      await promise
      setAction(null)
      toast(message)
      list.reload()
      if (detail) openDetail(detail.group.id)
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function openDetail(groupId) {
    try {
      const [group, standing, loans] = await Promise.all([
        get(`/api/groups/${groupId}`),
        get(`/api/groups/${groupId}/standing`),
        get(`/api/groups/${groupId}/loans`),
      ])
      setDetail({ group, standing, loans })
    } catch (err) {
      toastError(err)
    }
  }

  return (
    <>
      <PageHeader
        title="Groups"
        meta="Joint liability: members stand behind each other's borrowing"
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
        {mayEdit ? (
          <button type="button" className="btn primary" onClick={() => setAction({ kind: 'group' })}>
            New group
          </button>
        ) : null}
        {tab === 'performance' ? (
          <button
            type="button"
            className="btn"
            onClick={() => downloadCsv(perfPath, 'group_performance').catch(toastError)}
          >
            Export CSV
          </button>
        ) : null}
      </PageHeader>

      <div className="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'register'}
          onClick={() => setTab('register')}
        >
          Register
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'performance'}
          onClick={() => setTab('performance')}
        >
          Standing
        </button>
      </div>

      {tab === 'register' ? (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <input
              type="search"
              placeholder="Search group name or number"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value)
                setPage(1)
              }}
              aria-label="Search groups"
              style={{ minWidth: 260 }}
            />
          </div>
          <ErrorBanner error={list.error} onRetry={list.reload} />
          {list.loading && !list.data ? (
            <Loading what="Loading groups" />
          ) : (
            <>
              <Pager meta={list.data} onPage={setPage} noun="groups" />
              <DataTable
                caption="Groups"
                rows={list.data?.results || []}
                onRowClick={(row) => openDetail(row.id)}
                empty="No groups yet"
                columns={[
                  { key: 'no', header: 'No.', render: (r) => r.group_no },
                  { key: 'name', header: 'Name', render: (r) => r.name },
                  { key: 'branch', header: 'Branch', render: (r) => r.branch_name || '-' },
                  { key: 'officer', header: 'Officer', render: (r) => r.officer_name || '-' },
                  { key: 'members', header: 'Members', num: true, render: (r) => r.member_count },
                  { key: 'day', header: 'Meets', render: (r) => r.meeting_day || '-' },
                  { key: 'formed', header: 'Formed', render: (r) => r.formed_on || '-' },
                  { key: 'status', header: 'Status', render: (r) => <Badge value={r.status} /> },
                ]}
              />
            </>
          )}
        </>
      ) : (
        <>
          <ErrorBanner error={performance.error} onRetry={performance.reload} />
          {performance.loading && !performance.data ? (
            <Loading what="Loading group standing" />
          ) : (
            <DataTable
              caption="Group standing"
              rows={performance.data || []}
              rowKey={(r) => r.group_id}
              onRowClick={(r) => openDetail(r.group_id)}
              empty="No groups to report on"
              columns={[
                { key: 'no', header: 'No.', render: (r) => r.group_no },
                { key: 'name', header: 'Group', render: (r) => r.name },
                { key: 'branch', header: 'Branch', render: (r) => r.branch },
                { key: 'officer', header: 'Officer', render: (r) => r.officer },
                { key: 'members', header: 'Members', num: true, render: (r) => r.members },
                { key: 'loans', header: 'Active loans', num: true, render: (r) => r.active_loans },
                {
                  key: 'outstanding',
                  header: 'Outstanding',
                  num: true,
                  render: (r) => fmt(r.total_outstanding),
                },
                {
                  key: 'arrears',
                  header: 'Arrears',
                  num: true,
                  render: (r) =>
                    Number(r.arrears_amount) > 0 ? (
                      <span className="tag-danger">{fmt(r.arrears_amount)}</span>
                    ) : (
                      '-'
                    ),
                },
                {
                  key: 'days',
                  header: 'Worst days behind',
                  num: true,
                  render: (r) => (
                    <span className={r.worst_days_in_arrears > 30 ? 'tag-danger' : undefined}>
                      {r.worst_days_in_arrears}
                    </span>
                  ),
                },
              ]}
            />
          )}
        </>
      )}

      {action?.kind === 'group' ? (
        <FormModal
          title="New group"
          submitLabel="Create group"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post('/api/groups', {
                ...v,
                branch: v.branch ? Number(v.branch) : null,
                status: v.status || 'forming',
              }),
              'Group created',
            )
          }
        >
          <div className="grid cols-2">
            <Field label="Name" name="name" required />
            <Field as="select" label="Branch" name="branch">
              <option value="">Not assigned</option>
              {activeBranches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </Field>
            <Field label="Meeting day" name="meeting_day" />
            <Field label="Meeting place" name="meeting_place" />
            <Field label="Formed on" type="date" name="formed_on" defaultValue={today()} />
            <Field as="select" label="Status" name="status" defaultValue="active">
              <option value="forming">Forming</option>
              <option value="active">Active</option>
              <option value="dormant">Dormant</option>
            </Field>
          </div>
          <Field as="textarea" label="Notes" name="notes" rows={2} />
        </FormModal>
      ) : null}

      {action?.kind === 'member' ? (
        <FormModal
          title={`Add a member to ${action.group.name}`}
          submitLabel="Add member"
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(`/api/groups/${action.group.id}/members`, {
                borrower_id: Number(v.borrower_id),
                role: v.role,
                joined_on: v.joined_on,
              }),
              'Member added',
            )
          }
        >
          <Field as="select" label="Borrower" name="borrower_id" required>
            <option value="">Select a borrower</option>
            {(borrowers.data?.results || []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.borrower_no} - {b.first_name} {b.last_name}
              </option>
            ))}
          </Field>
          <div className="grid cols-2">
            <Field as="select" label="Role" name="role" defaultValue="member">
              {ROLES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Field>
            <Field label="Joined on" type="date" name="joined_on" defaultValue={today()} />
          </div>
        </FormModal>
      ) : null}

      {detail ? (
        <Modal
          title={`${detail.group.group_no} — ${detail.group.name}`}
          wide
          onClose={() => setDetail(null)}
          footer={
            mayEdit ? (
              <button
                type="button"
                className="btn small primary"
                onClick={() => setAction({ kind: 'member', group: detail.group })}
              >
                Add member
              </button>
            ) : null
          }
        >
          <div className="grid cols-4">
            <Kpi label="Members" value={detail.standing.members} />
            <Kpi label="Active loans" value={detail.standing.active_loans} />
            <Kpi
              label="Total outstanding"
              value={money(detail.standing.total_outstanding)}
              sub={`Average ${money(detail.standing.average_exposure)}`}
            />
            <Kpi
              label="Arrears"
              value={money(detail.standing.arrears_amount)}
              sub={
                detail.standing.worst_days_in_arrears
                  ? `worst ${detail.standing.worst_days_in_arrears} days behind`
                  : 'group is current'
              }
            />
          </div>

          <KeyValues
            items={[
              ['Branch', detail.group.branch_name || '-'],
              ['Officer', detail.group.officer_name || '-'],
              [
                'Meets',
                `${detail.group.meeting_day || '-'}${
                  detail.group.meeting_place ? ` at ${detail.group.meeting_place}` : ''
                }`,
              ],
              ['Formed', detail.group.formed_on || '-'],
              ['Status', <Badge key="s" value={detail.group.status} />],
            ]}
          />

          {detail.standing.members_behind.length ? (
            <div className="banner" style={{ marginTop: 14 }}>
              <strong>Joint liability is engaged. </strong>
              {detail.standing.members_behind
                .map((m) => `${m.borrower} is ${m.days_in_arrears} days behind on ${m.loan_no}`)
                .join('; ')}
              . While the group is behind, it cannot take on new debt.
            </div>
          ) : null}

          <h3 style={{ marginTop: 16 }}>Members</h3>
          <DataTable
            caption="Group members"
            rows={detail.group.members.filter((m) => m.is_active)}
            empty="No members yet"
            columns={[
              { key: 'no', header: 'No.', render: (m) => m.borrower_no },
              { key: 'name', header: 'Member', render: (m) => m.borrower_name },
              { key: 'phone', header: 'Phone', render: (m) => m.phone },
              { key: 'role', header: 'Role', render: (m) => humanise(m.role) },
              { key: 'joined', header: 'Joined', render: (m) => m.joined_on },
              {
                key: 'actions',
                header: '',
                render: (m) =>
                  mayEdit ? (
                    <div className="row" style={{ gap: 6 }}>
                      <button
                        type="button"
                        className="btn small"
                        onClick={() => {
                          setDetail(null)
                          navigate(`/borrowers/${m.borrower_id}`)
                        }}
                      >
                        Open
                      </button>
                      <button
                        type="button"
                        className="btn small"
                        onClick={() => {
                          if (window.confirm(`Remove ${m.borrower_name} from the group?`)) {
                            run(
                              del(`/api/groups/${detail.group.id}/members/${m.id}`),
                              'Member removed',
                            )
                          }
                        }}
                      >
                        Remove
                      </button>
                    </div>
                  ) : null,
              },
            ]}
          />

          <h3 style={{ marginTop: 16 }}>Members' loans</h3>
          <LoanTable loans={detail.loans} empty="No member has borrowed yet" />
        </Modal>
      ) : null}
    </>
  )
}
