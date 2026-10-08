import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TELLER, VIEWER, renderPage, stubApi } from '../test/harness.jsx'
import Payments from './Payments.jsx'

/**
 * Incoming payments: what matters is that a waiting payment shows why it waits,
 * that assigning sends the loan number typed, and that only the cash right acts.
 */

const WAITING = {
  id: 7,
  provider: 'ecocash',
  external_id: 'MP42',
  method: 'mobile_money',
  amount: '150.00',
  currency: null,
  paid_on: '2026-03-10',
  payer_phone: '263771234567',
  payer_name: 'T Moyo',
  account_ref: 'my loan',
  status: 'unmatched',
  status_label: 'Waiting to be matched',
  reason: "No loan or borrower matches 'my loan'",
  loan_id: null,
  loan_no: null,
  received_at: '2026-03-10T09:00:00Z',
}

const PAGE = {
  count: 1,
  page: 1,
  page_size: 25,
  num_pages: 1,
  results: [WAITING],
  totals: { unmatched: { count: 1, amount: '150.00' } },
}

describe('the Incoming payments page', () => {
  it('shows why a payment waits, and assigns it to the loan typed', async () => {
    const { postSpy } = stubApi({
      get: { '/api/payments': PAGE },
      post: { '/assign': { ...WAITING, status: 'posted', loan_no: 'LN-000003' } },
    })
    renderPage(<Payments />, { user: TELLER })

    expect(await screen.findByText(/no loan or borrower matches/i)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /waiting \(1\)/i })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Assign' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText(/loan number/i), 'LN-000003')
    await userEvent.click(within(dialog).getByRole('button', { name: /post to this loan/i }))

    await waitFor(() =>
      expect(postSpy).toHaveBeenCalledWith('/api/payments/7/assign', { loan_no: 'LN-000003' }),
    )
  })

  it('offers nothing to act on without the cash right', async () => {
    stubApi({ get: { '/api/payments': PAGE } })
    renderPage(<Payments />, { user: VIEWER })
    await screen.findByText(/no loan or borrower matches/i)
    expect(screen.queryByRole('button', { name: 'Assign' })).toBeNull()
    expect(screen.queryByRole('button', { name: /upload a statement/i })).toBeNull()
  })
})
