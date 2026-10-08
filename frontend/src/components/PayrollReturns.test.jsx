import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TELLER, VIEWER, renderPage, stubApi } from '../test/harness.jsx'
import PayrollReturns from './PayrollReturns.jsx'

const RUN = {
  id: 3,
  employer: 'Ministry of Health',
  period_start: '2026-03-01',
  period_end: '2026-03-31',
  received_on: '2026-03-28',
  status: 'draft',
  status_label: 'Checked, not posted',
  expected_total: '591.06',
  deducted_total: '297.02',
  posted_total: '0.00',
}

const DETAIL = {
  ...RUN,
  shortfall: '294.04',
  counts: { full: 1, short: 1, missed: 1 },
  lines: [
    {
      id: 1,
      loan_id: 9,
      loan_no: 'LN-000009',
      name: 'A',
      expected: '197.02',
      deducted: '100.00',
      shortfall: '97.02',
      status: 'short',
      status_label: 'Deducted short',
      note: '97.02 short',
    },
  ],
}

const RETURNS = <PayrollReturns employer="Ministry of Health" start="2026-03-01" end="2026-03-31" />

describe('payroll returns', () => {
  it('shows a checked return line by line and posts it', async () => {
    const { postSpy } = stubApi({
      get: { '/api/payroll/runs/3': DETAIL, '/api/payroll/runs': [RUN] },
      post: { '/post': { ...DETAIL, status: 'posted' } },
    })
    renderPage(RETURNS, { user: TELLER })
    await userEvent.click(await screen.findByText('Ministry of Health'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Deducted short')).toBeInTheDocument()
    expect(within(dialog).getByText('294.04', { exact: false })).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Post deductions' }))
    await waitFor(() => expect(postSpy).toHaveBeenCalled())
    expect(postSpy.mock.calls[0][0]).toBe('/api/payroll/runs/3/post')
  })

  it('offers no checking or posting without the cash right', async () => {
    stubApi({ get: { '/api/payroll/runs': [RUN] } })
    renderPage(RETURNS, { user: VIEWER })
    await screen.findByText('Ministry of Health')
    expect(screen.queryByRole('button', { name: /check a return/i })).toBeNull()
  })
})
