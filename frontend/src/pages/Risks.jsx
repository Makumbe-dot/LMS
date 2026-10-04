import { useState } from 'react'

import DataTable from '../components/DataTable.jsx'
import Modal, { FormModal } from '../components/Modal.jsx'
import RiskHeatMap, { IMPACT, LIKELIHOOD, RatingChip } from '../components/RiskHeatMap.jsx'
import { useToast } from '../components/Toast.jsx'
import {
  Badge,
  ExportButtons,
  ErrorBanner,
  Field,
  KeyValues,
  Kpi,
  Loading,
  PageHeader,
  Pager,
} from '../components/ui.jsx'
import { get, patch, post, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { today } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useApi, useDebounced } from '../lib/useApi.js'

const CATEGORIES = [
  ['credit', 'Credit'],
  ['liquidity', 'Liquidity and funding'],
  ['operational', 'Operational'],
  ['fraud', 'Fraud'],
  ['compliance', 'Compliance and regulatory'],
  ['technology', 'Technology and data'],
  ['people', 'People'],
  ['strategic', 'Strategic'],
  ['reputational', 'Reputational'],
]
const CATEGORY_LABEL = Object.fromEntries(CATEGORIES)

const TREATMENTS = [
  ['treat', 'Treat - reduce it'],
  ['tolerate', 'Tolerate - accept it'],
  ['transfer', 'Transfer - insure or contract it out'],
  ['terminate', 'Terminate - stop the activity'],
]
const TREATMENT_LABEL = Object.fromEntries(TREATMENTS.map(([v, l]) => [v, l.split(' - ')[0]]))

const INTERVALS = [1, 3, 6, 12]

// Each scope is a set of query parameters. The heat map counts open risks, so a
// cell can only narrow a scope made of open risks.
const SCOPES = [
  ['open', 'All open risks', { status: 'open' }],
  ['mine', 'Mine', { owner: 'me' }],
  ['mine-due', 'Mine, overdue for review', { owner: 'me', overdue: 1 }],
  ['overdue', 'Overdue for review', { overdue: 1 }],
  ['unowned', 'No active owner', { owner: 'none' }],
  ['closed', 'Closed', { status: 'closed' }],
  ['all', 'Everything', { status: 'all' }],
]
const SCOPE_PARAMS = Object.fromEntries(SCOPES.map(([key, , params]) => [key, params]))
const OPEN_SCOPES = new Set(['open', 'mine', 'mine-due', 'overdue', 'unowned'])

function RatingSelect({ axis, name, label, defaultValue }) {
  const labels = axis === 'likelihood' ? LIKELIHOOD : IMPACT
  return (
    <Field as="select" label={label} name={name} defaultValue={defaultValue} required>
      {labels.map((text, index) => (
        <option key={text} value={index + 1}>
          {index + 1} - {text}
        </option>
      ))}
    </Field>
  )
}

/** The risk register: every risk has an owner, and the owner keeps it current. */
export default function Risks() {
  const { user, can } = useAuth()
  const { activeBranches } = useOrg()
  const { toast, toastError } = useToast()

  const [scope, setScope] = useState('open')
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [rating, setRating] = useState('')
  const [branchId, setBranchId] = useState('')
  const [basis, setBasis] = useState('residual')
  const [cell, setCell] = useState(null)
  const [page, setPage] = useState(1)
  const [action, setAction] = useState(null)
  const [detail, setDetail] = useState(null)
  const [busy, setBusy] = useState(false)
  const debounced = useDebounced(search)

  const isAdmin = can('admin')
  const mayRaise = can('admin', 'loan_officer', 'teller')

  const filters = {
    ...SCOPE_PARAMS[scope],
    q: debounced,
    category,
    rating,
    branch_id: branchId,
    ...(cell ? { basis, likelihood: cell.likelihood, impact: cell.impact } : {}),
  }
  const listPath = `/api/risks${qs({ ...filters, page, page_size: 50 })}`
  const list = useApi(listPath)
  const summary = useApi(`/api/risks/summary${qs({ branch_id: branchId })}`)
  // Only an administrator assigns owners, so only they need the staff list.
  const staff = useApi(
    isAdmin && (action?.kind === 'raise' || action?.kind === 'edit') ? '/api/users' : null,
  )

  const s = summary.data

  function narrow(update) {
    update()
    setPage(1)
  }

  function pickCell(next) {
    narrow(() => {
      setCell(next)
      if (next && !OPEN_SCOPES.has(scope)) setScope('open')
    })
  }

  async function run(promise, message) {
    setBusy(true)
    try {
      const updated = await promise
      setAction(null)
      toast(message)
      list.reload()
      summary.reload()
      // Every risk endpoint answers with the risk as it now stands.
      if (updated?.id) setDetail(updated)
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  async function openDetail(id) {
    try {
      setDetail(await get(`/api/risks/${id}`))
    } catch (err) {
      toastError(err)
    }
  }

  function riskBody(v, { withResidual }) {
    const body = {
      title: v.title,
      description: v.description,
      category: v.category,
      branch: v.branch ? Number(v.branch) : null,
      inherent_likelihood: Number(v.inherent_likelihood),
      inherent_impact: Number(v.inherent_impact),
      controls: v.controls,
      treatment: v.treatment,
      action_plan: v.action_plan,
      action_due: v.action_due,
      review_every_months: Number(v.review_every_months),
    }
    if (withResidual) {
      body.residual_likelihood = Number(v.residual_likelihood)
      body.residual_impact = Number(v.residual_impact)
    }
    if (isAdmin && v.owner) body.owner = Number(v.owner)
    return body
  }

  const editing = action?.kind === 'edit' ? action.risk : null

  return (
    <>
      <PageHeader title="Risk register" meta="Every risk has an owner, who keeps it current">
        {activeBranches.length > 1 ? (
          <select
            value={branchId}
            onChange={(e) => narrow(() => setBranchId(e.target.value))}
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
        {mayRaise ? (
          <button type="button" className="btn primary" onClick={() => setAction({ kind: 'raise' })}>
            Raise a risk
          </button>
        ) : null}
        <ExportButtons path={`/api/risks${qs(filters)}`} name={'risk_register'} />
      </PageHeader>

      <ErrorBanner error={summary.error} onRetry={summary.reload} />

      {s?.mine_overdue ? (
        <div className="banner" role="status">
          <strong>
            {s.mine_overdue === 1
              ? 'One of your risks is overdue for review. '
              : `${s.mine_overdue} of your risks are overdue for review. `}
          </strong>
          <button type="button" className="btn-link" onClick={() => narrow(() => setScope('mine-due'))}>
            Show them
          </button>
        </div>
      ) : null}

      {s ? (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <Kpi
            label="Open risks"
            value={s.open}
            sub={`${s.by_rating.critical} critical · ${s.by_rating.high} high`}
          />
          <Kpi
            label="Overdue for review"
            value={s.review_overdue}
            sub={s.action_overdue ? `${s.action_overdue} with an action past due` : 'actions on time'}
          />
          <Kpi
            label="Yours"
            value={s.mine}
            sub={
              <button type="button" className="btn-link" onClick={() => narrow(() => setScope('mine'))}>
                Show my risks
              </button>
            }
          />
          <Kpi
            label="No active owner"
            value={s.unowned}
            sub={s.unowned ? 'needs an owner assigned' : 'every risk is owned'}
          />
        </div>
      ) : null}

      {s ? (
        <div className="card">
          <div className="risk-overview">
            <div>
              <div className="row between" style={{ marginBottom: 8 }}>
                <h3 style={{ margin: 0 }}>Heat map</h3>
                <div className="tabs" role="tablist" style={{ margin: 0, border: 0 }}>
                  {[
                    ['residual', 'Residual'],
                    ['inherent', 'Inherent'],
                  ].map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      role="tab"
                      aria-selected={basis === key}
                      onClick={() => narrow(() => setBasis(key))}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <RiskHeatMap
                caption={`Open risks by ${basis} rating`}
                cells={s.heatmap[basis]}
                selected={cell}
                onSelect={pickCell}
              />
            </div>
            <div>
              <p className="muted" style={{ marginTop: 0 }}>
                Each open risk sits in a cell by likelihood and impact.{' '}
                <strong>Inherent</strong> is the risk before controls; <strong>residual</strong> is
                what is left after them, and is what the register is sorted by. Select a cell to
                list the risks in it.
              </p>
              <p className="muted">
                Score is likelihood × impact: <span className="rating rating-low">1-4 Low</span>{' '}
                <span className="rating rating-medium">5-9 Medium</span>{' '}
                <span className="rating rating-high">10-16 High</span>{' '}
                <span className="rating rating-critical">20-25 Critical</span>
              </p>
              <p className="muted" style={{ marginBottom: 0 }}>
                The owner of a risk reviews it on its schedule: re-rates it, says what changed, and
                updates the controls and the plan. Only an administrator can hand a risk to someone
                else or close it.
              </p>
            </div>
          </div>
        </div>
      ) : null}

      <div className="row" style={{ marginBottom: 12 }}>
        <input
          type="search"
          placeholder="Search number, title or description"
          value={search}
          onChange={(e) => narrow(() => setSearch(e.target.value))}
          aria-label="Search risks"
          style={{ minWidth: 260 }}
        />
        <select
          value={scope}
          onChange={(e) =>
            narrow(() => {
              setScope(e.target.value)
              if (!OPEN_SCOPES.has(e.target.value)) setCell(null)
            })
          }
          aria-label="Which risks"
        >
          {SCOPES.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <select
          value={category}
          onChange={(e) => narrow(() => setCategory(e.target.value))}
          aria-label="Filter by category"
        >
          <option value="">Any category</option>
          {CATEGORIES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select
          value={rating}
          onChange={(e) => narrow(() => setRating(e.target.value))}
          aria-label="Filter by residual rating"
        >
          <option value="">Any rating</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        {cell ? (
          <span className="row" style={{ gap: 6 }}>
            <span className="muted">
              {basis === 'residual' ? 'Residual' : 'Inherent'} likelihood {cell.likelihood} ×
              impact {cell.impact}
            </span>
            <button type="button" className="btn small" onClick={() => pickCell(null)}>
              Clear
            </button>
          </span>
        ) : null}
      </div>

      <ErrorBanner error={list.error} onRetry={list.reload} />
      {list.loading && !list.data ? (
        <Loading what="Loading the register" />
      ) : (
        <>
          <Pager meta={list.data} onPage={setPage} noun="risks" />
          <DataTable
            caption="Risk register"
            rows={list.data?.results || []}
            onRowClick={(row) => openDetail(row.id)}
            empty={scope.startsWith('mine') ? 'You own no risks that match' : 'No risks match'}
            columns={[
              { key: 'no', header: 'No.', render: (r) => r.risk_no },
              {
                key: 'title',
                header: 'Risk',
                render: (r) => (
                  <div style={{ whiteSpace: 'normal', minWidth: 220 }}>
                    <div>{r.title}</div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {CATEGORY_LABEL[r.category] || r.category}
                      {r.branch_name ? ` · ${r.branch_name}` : ''}
                    </div>
                  </div>
                ),
              },
              {
                key: 'owner',
                header: 'Owner',
                render: (r) =>
                  !r.owner_id ? (
                    <span className="tag-danger">Unowned</span>
                  ) : !r.owner_active ? (
                    <span className="tag-danger">{r.owner_name} (disabled)</span>
                  ) : r.owner_id === user?.id ? (
                    <strong>{r.owner_name} (you)</strong>
                  ) : (
                    r.owner_name
                  ),
              },
              {
                key: 'inherent',
                header: 'Inherent',
                render: (r) => (
                  <RatingChip likelihood={r.inherent_likelihood} impact={r.inherent_impact} />
                ),
              },
              {
                key: 'residual',
                header: 'Residual',
                render: (r) => (
                  <RatingChip likelihood={r.residual_likelihood} impact={r.residual_impact} />
                ),
              },
              { key: 'treatment', header: 'Treatment', render: (r) => TREATMENT_LABEL[r.treatment] },
              {
                key: 'review',
                header: 'Next review',
                render: (r) =>
                  r.status !== 'open' ? (
                    '-'
                  ) : r.review_overdue ? (
                    <span className="tag-danger">{r.next_review_on} · overdue</span>
                  ) : (
                    r.next_review_on
                  ),
              },
              { key: 'status', header: 'Status', render: (r) => <Badge value={r.status} /> },
            ]}
          />
        </>
      )}

      {action?.kind === 'raise' || editing ? (
        <FormModal
          title={editing ? `Edit ${editing.risk_no}` : 'Raise a risk'}
          submitLabel={editing ? 'Save changes' : 'Raise risk'}
          busy={busy}
          wide
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            editing
              ? run(patch(`/api/risks/${editing.id}`, riskBody(v, { withResidual: false })), 'Risk updated')
              : run(post('/api/risks', riskBody(v, { withResidual: true })), 'Risk raised')
          }
        >
          <Field label="Title" name="title" required defaultValue={editing?.title} maxLength={200} />
          <Field
            as="textarea"
            label="Description"
            name="description"
            rows={2}
            defaultValue={editing?.description || ''}
            hint="The cause, what could happen, and what it would do to us"
          />
          <div className="grid cols-3">
            <Field as="select" label="Category" name="category" defaultValue={editing?.category || 'operational'}>
              {CATEGORIES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Field>
            <Field as="select" label="Branch" name="branch" defaultValue={editing?.branch_id || ''}>
              <option value="">Organisation-wide</option>
              {activeBranches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </Field>
            {isAdmin ? (
              <Field
                // Remounted when the staff list arrives: an uncontrolled select keeps
                // whichever option came first, which would silently reassign the risk.
                key={staff.data ? 'staff' : 'loading'}
                as="select"
                label="Owner"
                name="owner"
                defaultValue={editing ? editing.owner_id || '' : user?.id}
                hint="Who keeps this risk current"
              >
                {editing && !editing.owner_id ? (
                  <option value="">Unowned - choose someone</option>
                ) : null}
                {(staff.data || [])
                  .filter((u) => u.is_active || u.id === editing?.owner_id)
                  .map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.full_name}
                    </option>
                  ))}
                {!staff.data ? <option value={editing?.owner_id || user?.id}>Loading staff…</option> : null}
              </Field>
            ) : (
              <Field
                label="Owner"
                value={editing ? editing.owner_name || 'Unowned' : `${user?.full_name} (you)`}
                disabled
                hint="An administrator can hand it to someone else"
                readOnly
              />
            )}
          </div>

          <fieldset>
            <legend>Inherent rating - before any controls</legend>
            <div className="grid cols-2">
              <RatingSelect
                axis="likelihood"
                name="inherent_likelihood"
                label="Likelihood"
                defaultValue={editing?.inherent_likelihood || 3}
              />
              <RatingSelect
                axis="impact"
                name="inherent_impact"
                label="Impact"
                defaultValue={editing?.inherent_impact || 3}
              />
            </div>
          </fieldset>

          <Field
            as="textarea"
            label="Controls"
            name="controls"
            rows={2}
            defaultValue={editing?.controls || ''}
            hint="What is already in place that makes it less likely or less damaging"
          />

          {editing ? (
            <p className="muted" style={{ marginTop: 0 }}>
              Residual rating: <RatingChip likelihood={editing.residual_likelihood} impact={editing.residual_impact} />.
              To change it, record a review, so the change has a date and a reason.
            </p>
          ) : (
            <fieldset>
              <legend>Residual rating - after the controls</legend>
              <div className="grid cols-2">
                <RatingSelect axis="likelihood" name="residual_likelihood" label="Likelihood" defaultValue={2} />
                <RatingSelect axis="impact" name="residual_impact" label="Impact" defaultValue={2} />
              </div>
              <p className="muted" style={{ margin: '0 0 10px', fontSize: 12 }}>
                Neither can be higher than the inherent rating: controls do not make a risk worse.
              </p>
            </fieldset>
          )}

          <div className="grid cols-3">
            <Field as="select" label="Treatment" name="treatment" defaultValue={editing?.treatment || 'treat'}>
              {TREATMENTS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Field>
            <Field label="Action due" type="date" name="action_due" defaultValue={editing?.action_due || ''} />
            <Field
              as="select"
              label="Review every"
              name="review_every_months"
              defaultValue={editing?.review_every_months || 3}
            >
              {INTERVALS.map((n) => (
                <option key={n} value={n}>
                  {n === 1 ? 'month' : `${n} months`}
                </option>
              ))}
            </Field>
          </div>
          <Field
            as="textarea"
            label="Action plan"
            name="action_plan"
            rows={2}
            defaultValue={editing?.action_plan || ''}
            hint="What is being done to bring it down further, and by whom"
          />
        </FormModal>
      ) : null}

      {action?.kind === 'review' ? (
        <FormModal
          title={`Review ${action.risk.risk_no}`}
          submitLabel="Record review"
          busy={busy}
          wide
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(
              post(`/api/risks/${action.risk.id}/reviews`, {
                residual_likelihood: Number(v.residual_likelihood),
                residual_impact: Number(v.residual_impact),
                note: v.note,
                reviewed_on: v.reviewed_on,
                next_review_on: v.next_review_on,
                controls: v.controls,
                action_plan: v.action_plan,
                action_due: v.action_due,
              }),
              'Review recorded',
            )
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            {action.risk.title}. Inherent rating{' '}
            <RatingChip likelihood={action.risk.inherent_likelihood} impact={action.risk.inherent_impact} />
            ; the residual cannot be higher.
          </p>
          <div className="grid cols-2">
            <RatingSelect
              axis="likelihood"
              name="residual_likelihood"
              label="Residual likelihood now"
              defaultValue={action.risk.residual_likelihood}
            />
            <RatingSelect
              axis="impact"
              name="residual_impact"
              label="Residual impact now"
              defaultValue={action.risk.residual_impact}
            />
          </div>
          <Field
            as="textarea"
            label="What was checked, and what changed"
            name="note"
            rows={3}
            required
            minLength={10}
          />
          <Field as="textarea" label="Controls" name="controls" rows={2} defaultValue={action.risk.controls || ''} />
          <Field as="textarea" label="Action plan" name="action_plan" rows={2} defaultValue={action.risk.action_plan || ''} />
          <div className="grid cols-3">
            <Field label="Action due" type="date" name="action_due" defaultValue={action.risk.action_due || ''} />
            <Field label="Reviewed on" type="date" name="reviewed_on" defaultValue={today()} max={today()} />
            <Field
              label="Next review"
              type="date"
              name="next_review_on"
              hint={`Blank: ${action.risk.review_every_months} months after this review`}
            />
          </div>
        </FormModal>
      ) : null}

      {action?.kind === 'close' || action?.kind === 'reopen' ? (
        <FormModal
          title={`${action.kind === 'close' ? 'Close' : 'Reopen'} ${action.risk.risk_no}`}
          submitLabel={action.kind === 'close' ? 'Close risk' : 'Reopen risk'}
          busy={busy}
          onClose={() => setAction(null)}
          onSubmit={(v) =>
            run(post(`/api/risks/${action.risk.id}/${action.kind}`, { reason: v.reason }),
              action.kind === 'close' ? 'Risk closed' : 'Risk reopened')
          }
        >
          <Field
            as="textarea"
            label={action.kind === 'close' ? 'Why it no longer applies' : 'Why it applies again'}
            name="reason"
            rows={3}
            required
            minLength={10}
            hint={
              action.kind === 'reopen'
                ? 'A reopened risk is due for review straight away.'
                : 'Goes in the audit trail.'
            }
          />
        </FormModal>
      ) : null}

      {detail && !action ? (
        <Modal
          title={`${detail.risk_no} — ${detail.title}`}
          wide
          onClose={() => setDetail(null)}
          footer={
            <>
              {detail.can_edit && detail.status === 'open' ? (
                <>
                  <button
                    type="button"
                    className="btn small primary"
                    onClick={() => setAction({ kind: 'review', risk: detail })}
                  >
                    Record review
                  </button>
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => setAction({ kind: 'edit', risk: detail })}
                  >
                    Edit
                  </button>
                </>
              ) : null}
              {isAdmin ? (
                <button
                  type="button"
                  className="btn small"
                  onClick={() =>
                    setAction({ kind: detail.status === 'open' ? 'close' : 'reopen', risk: detail })
                  }
                >
                  {detail.status === 'open' ? 'Close risk' : 'Reopen'}
                </button>
              ) : null}
            </>
          }
        >
          <div className="grid cols-4">
            <Kpi
              label="Inherent"
              value={<RatingChip likelihood={detail.inherent_likelihood} impact={detail.inherent_impact} />}
              sub={`${LIKELIHOOD[detail.inherent_likelihood - 1]} · ${IMPACT[detail.inherent_impact - 1]}`}
            />
            <Kpi
              label="Residual"
              value={<RatingChip likelihood={detail.residual_likelihood} impact={detail.residual_impact} />}
              sub={`${LIKELIHOOD[detail.residual_likelihood - 1]} · ${IMPACT[detail.residual_impact - 1]}`}
            />
            <Kpi
              label="Next review"
              value={detail.status === 'open' ? detail.next_review_on : '-'}
              sub={
                detail.review_overdue ? (
                  <span className="tag-danger">overdue</span>
                ) : (
                  `every ${detail.review_every_months === 1 ? 'month' : `${detail.review_every_months} months`}`
                )
              }
            />
            <Kpi label="Last reviewed" value={detail.last_reviewed_on || '-'} sub={<Badge value={detail.status} />} />
          </div>

          {!detail.can_edit && detail.status === 'open' ? (
            <p className="muted">
              Only {detail.owner_name || 'its owner'} or an administrator can review or change this
              risk.
            </p>
          ) : null}
          {detail.status === 'closed' ? (
            <div className="banner">
              <strong>Closed on {detail.closed_on}. </strong>
              {detail.closed_reason}
            </div>
          ) : null}

          <KeyValues
            items={[
              ['Category', CATEGORY_LABEL[detail.category] || detail.category],
              ['Branch', detail.branch_name || 'Organisation-wide'],
              [
                'Owner',
                detail.owner_id
                  ? `${detail.owner_name}${detail.owner_active ? '' : ' (disabled - needs a new owner)'}`
                  : 'Unowned',
              ],
              // The date is the first row of the history; created_at can differ for
              // a risk recorded after the fact.
              ['Raised by', detail.raised_by_name || '-'],
              ['Treatment', TREATMENTS.find(([v]) => v === detail.treatment)?.[1] || detail.treatment],
              [
                'Action due',
                detail.action_overdue ? (
                  <span key="due" className="tag-danger">
                    {detail.action_due} · past due
                  </span>
                ) : (
                  detail.action_due || '-'
                ),
              ],
            ]}
          />

          <h3 style={{ marginTop: 16, marginBottom: 0 }}>Description</h3>
          <p className="risk-text">{detail.description || '-'}</p>
          <h3 style={{ marginBottom: 0 }}>Controls</h3>
          <p className="risk-text">{detail.controls || '-'}</p>
          <h3 style={{ marginBottom: 0 }}>Action plan</h3>
          <p className="risk-text">{detail.action_plan || '-'}</p>

          <h3>Assessment history</h3>
          <DataTable
            caption="Assessment history"
            rows={detail.reviews}
            empty="No reviews yet"
            columns={[
              { key: 'on', header: 'Date', render: (r) => r.reviewed_on },
              { key: 'by', header: 'By', render: (r) => r.reviewed_by_name || '-' },
              {
                key: 'rating',
                header: 'Residual',
                render: (r) => <RatingChip likelihood={r.residual_likelihood} impact={r.residual_impact} />,
              },
              {
                key: 'note',
                header: 'Note',
                render: (r) => <div style={{ whiteSpace: 'pre-wrap', minWidth: 260 }}>{r.note}</div>,
              },
              { key: 'next', header: 'Next review set', render: (r) => r.next_review_on },
            ]}
          />
        </Modal>
      ) : null}
    </>
  )
}
