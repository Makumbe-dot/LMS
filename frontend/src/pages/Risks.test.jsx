import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as api from '../lib/api.js'
import { ADMIN, OFFICER, TELLER, VIEWER, renderPage, stubApi } from '../test/harness.jsx'
import Risks from './Risks.jsx'

/**
 * The register is maintained by risk owners, so the page has two jobs a bug would
 * hide: offer Review and Edit exactly where the server allows them, and draw the
 * heat map from the whole register rather than from the page of rows on screen.
 */

const risk = (id, overrides = {}) => ({
  id,
  risk_no: `RSK-${String(id).padStart(5, '0')}`,
  title: `Risk ${id}`,
  description: null,
  category: 'operational',
  branch_id: null,
  branch_name: null,
  owner_id: TELLER.id,
  owner_name: 'Teller',
  owner_active: true,
  raised_by_name: 'Admin',
  inherent_likelihood: 4,
  inherent_impact: 4,
  controls: 'Dual count at close',
  residual_likelihood: 2,
  residual_impact: 3,
  treatment: 'treat',
  action_plan: null,
  action_due: null,
  action_overdue: false,
  review_every_months: 3,
  last_reviewed_on: '2026-09-01',
  next_review_on: '2026-12-01',
  review_overdue: false,
  status: 'open',
  closed_on: null,
  closed_reason: null,
  can_edit: false,
  created_at: '2026-09-01T08:00:00Z',
  reviews: [
    {
      id: id * 10,
      reviewed_on: '2026-09-01',
      reviewed_by_name: 'Admin',
      residual_likelihood: 2,
      residual_impact: 3,
      note: 'Raised: initial assessment',
      next_review_on: '2026-12-01',
    },
  ],
  ...overrides,
})

const page = (...rows) => ({ count: rows.length, page: 1, page_size: 50, num_pages: 1, results: rows })

const SUMMARY = {
  open: 9,
  closed: 1,
  review_overdue: 2,
  action_overdue: 0,
  unowned: 0,
  mine: 1,
  mine_overdue: 0,
  by_rating: { critical: 0, high: 7, medium: 2, low: 0 },
  heatmap: {
    residual: [
      { likelihood: 4, impact: 4, count: 7 },
      { likelihood: 2, impact: 3, count: 2 },
    ],
    inherent: [{ likelihood: 5, impact: 5, count: 9 }],
  },
}

// Order matters: the first fragment a path contains wins, and the list is the
// only path with a query string.
function stubRegister({ rows = [risk(1)], summary = SUMMARY, details = {} } = {}) {
  return stubApi({
    get: {
      '/api/risks/summary': summary,
      ...Object.fromEntries(Object.entries(details).map(([id, d]) => [`/api/risks/${id}`, d])),
      '/api/users': [
        { ...ADMIN, is_active: true },
        { ...TELLER, is_active: true },
        { ...OFFICER, is_active: true },
      ],
      '/api/risks?': page(...rows),
    },
  })
}

describe('Risks', () => {
  it('draws the heat map from the whole register, not from the rows on screen', async () => {
    // One row on screen, but the summary says seven risks sit at 4 x 4.
    stubRegister({ rows: [risk(1)] })
    renderPage(<Risks />)
    expect(
      await screen.findByRole('button', { name: /likelihood 4 \(likely\), impact 4 \(major\): 7 risks/i }),
    ).toBeInTheDocument()
  })

  it('shows the inherent map when asked, with its own counts', async () => {
    stubRegister()
    renderPage(<Risks />)
    await userEvent.click(await screen.findByRole('tab', { name: 'Inherent' }))
    expect(
      screen.getByRole('button', { name: /likelihood 5 \(almost certain\), impact 5 \(severe\): 9 risks/i }),
    ).toBeInTheDocument()
  })

  it('asks the server for the risks in a cell rather than filtering the page', async () => {
    const { getSpy } = stubRegister()
    renderPage(<Risks />)
    await userEvent.click(await screen.findByRole('button', { name: /impact 4 \(major\): 7 risks/i }))
    const asked = getSpy.mock.calls.map(([path]) => path)
    expect(
      asked.some(
        (path) => path.startsWith('/api/risks?') && path.includes('likelihood=4') &&
          path.includes('impact=4') && path.includes('basis=residual'),
      ),
    ).toBe(true)
  })

  it("offers an owner Record review and Edit on their own risk", async () => {
    const mine = risk(1, { can_edit: true })
    stubRegister({ rows: [mine], details: { 1: mine } })
    renderPage(<Risks />, { user: TELLER })
    await userEvent.click(await screen.findByText('Risk 1'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Record review' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    // Closing and reopening is an administrator's call, even for the owner.
    expect(within(dialog).queryByRole('button', { name: 'Close risk' })).not.toBeInTheDocument()
  })

  it("does not offer to change a risk someone else owns, and says who can", async () => {
    const theirs = risk(2, { owner_id: OFFICER.id, owner_name: 'Officer', can_edit: false })
    stubRegister({ rows: [theirs], details: { 2: theirs } })
    renderPage(<Risks />, { user: TELLER })
    await userEvent.click(await screen.findByText('Risk 2'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('button', { name: 'Record review' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    expect(within(dialog).getByText(/only officer or an administrator/i)).toBeInTheDocument()
  })

  it('lets anyone with a working role raise a risk, but not a viewer', async () => {
    stubRegister()
    renderPage(<Risks />, { user: VIEWER })
    await screen.findByText('Risk 1')
    expect(screen.queryByRole('button', { name: 'Raise a risk' })).not.toBeInTheDocument()
  })

  it('tells an owner their reviews are overdue and takes them straight there', async () => {
    const { getSpy } = stubRegister({ summary: { ...SUMMARY, mine_overdue: 2 } })
    renderPage(<Risks />, { user: TELLER })
    expect(await screen.findByText(/2 of your risks are overdue for review/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show them' }))
    expect(
      getSpy.mock.calls.some(([path]) => path.includes('owner=me') && path.includes('overdue=1')),
    ).toBe(true)
  })

  it('marks a risk whose review is overdue', async () => {
    stubRegister({ rows: [risk(1, { review_overdue: true, next_review_on: '2026-08-01' })] })
    renderPage(<Risks />)
    expect(await screen.findByText('2026-08-01 · overdue')).toBeInTheDocument()
  })

  it('an admin editing an unowned risk does not hand it to themselves', async () => {
    // The owner select is uncontrolled; defaulting it to the admin would reassign
    // the risk to whoever happened to press Save.
    const orphan = risk(3, { owner_id: null, owner_name: null, owner_active: false, can_edit: true })
    stubRegister({ rows: [orphan], details: { 3: orphan } })
    const patchSpy = vi.spyOn(api, 'patch').mockResolvedValue(orphan)
    renderPage(<Risks />, { user: ADMIN })

    await userEvent.click(await screen.findByText('Risk 3'))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Edit' }))
    await screen.findByRole('option', { name: 'Teller' })
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(patchSpy).toHaveBeenCalledTimes(1)
    const [path, body] = patchSpy.mock.calls[0]
    expect(path).toBe('/api/risks/3')
    expect(body).not.toHaveProperty('owner')
    // An edit never carries the residual rating: that changes only by review.
    expect(body).not.toHaveProperty('residual_likelihood')
  })
})
