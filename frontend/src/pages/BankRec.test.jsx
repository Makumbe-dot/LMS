import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { ADMIN, VIEWER, renderPage, stubApi } from '../test/harness.jsx'
import BankRec from './BankRec.jsx'

/**
 * The statement view has to say plainly what is still open, and offer each line
 * only the actions that make sense for it: an unmatched line can be matched,
 * booked or set aside; a matched one can only be released.
 */

const SUMMARY = {
  lines: 2,
  matched: 1,
  unmatched: 1,
  ignored: 0,
  money_in: '197.02',
  money_out: '2.50',
  unmatched_amount: '-2.50',
  adds_up: false,
  reconciled: false,
}

const STATEMENT = {
  id: 9,
  statement_no: 'STM-000009',
  account_name: 'CBZ current account',
  channel: 'bank_transfer',
  channel_label: 'Bank transfer',
  period_start: '2026-03-01',
  period_end: '2026-03-31',
  uploaded_at: '2026-04-02T08:00:00Z',
  uploaded_by_name: 'Admin',
  summary: SUMMARY,
  lines: [
    {
      id: 1,
      line_no: 2,
      txn_date: '2026-03-26',
      description: 'Payroll credit',
      reference: 'PAY-0325',
      amount: '197.02',
      status: 'matched',
      entry_no: 'JE-00000042',
      entry_narration: 'Repayment on LN-000001',
      auto_matched: true,
    },
    {
      id: 2,
      line_no: 3,
      txn_date: '2026-03-28',
      description: 'Monthly service fee',
      reference: null,
      amount: '-2.50',
      status: 'unmatched',
      entry_no: null,
    },
  ],
}

function stub() {
  return stubApi({
    get: {
      '/outstanding': [],
      '/api/bank-statements/9': STATEMENT,
      '/api/bank-statements': { count: 1, page: 1, page_size: 25, num_pages: 1, results: [STATEMENT] },
      '/api/ledger/accounts': [],
    },
  })
}

describe('BankRec', () => {
  it('offers each line only the actions that fit it', async () => {
    stub()
    renderPage(<BankRec />, { user: ADMIN })
    await userEvent.click(await screen.findByText('STM-000009'))

    const fee = (await screen.findByText('Monthly service fee')).closest('tr')
    expect(within(fee).getByRole('button', { name: 'Find a match' })).toBeInTheDocument()
    expect(within(fee).getByRole('button', { name: 'Book it' })).toBeInTheDocument()

    const payroll = screen.getByText('Payroll credit').closest('tr')
    expect(within(payroll).getByRole('button', { name: 'Unmatch' })).toBeInTheDocument()
    expect(within(payroll).queryByRole('button', { name: 'Find a match' })).not.toBeInTheDocument()
  })

  it('says when the file does not add up to its own closing balance', async () => {
    stub()
    renderPage(<BankRec />, { user: ADMIN })
    await userEvent.click(await screen.findByText('STM-000009'))
    expect(await screen.findByText(/does NOT add up/)).toBeInTheDocument()
    expect(screen.getByText('Open items')).toBeInTheDocument()
  })

  it('lets a viewer read the reconciliation but not change it', async () => {
    stub()
    renderPage(<BankRec />, { user: VIEWER })
    expect(screen.queryByRole('button', { name: 'Upload a statement' })).not.toBeInTheDocument()
    await userEvent.click(await screen.findByText('STM-000009'))
    await screen.findByText('Monthly service fee')
    expect(screen.queryByRole('button', { name: 'Find a match' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Match again' })).not.toBeInTheDocument()
  })
})
