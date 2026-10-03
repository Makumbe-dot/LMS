import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { ADMIN, TELLER, renderPage, stubApi } from '../test/harness.jsx'
import Journals from './Journals.jsx'

/**
 * A journal is the one place a person writes straight to the ledger, so the page
 * has three jobs a bug would hide: never offer an account the server will refuse,
 * never let an unbalanced journal be sent, and offer Post only to an administrator.
 */

const ACCOUNTS = [
  { id: 1, code: '1000', name: 'Cash and bank', type: 'asset', is_active: true, controlled_by: null },
  {
    id: 2,
    code: '1100',
    name: 'Loans receivable - principal',
    type: 'asset',
    is_active: true,
    controlled_by: 'the loan itself',
  },
  { id: 3, code: '2900', name: 'Accruals and other payables', type: 'liability', is_active: true, controlled_by: null },
  { id: 4, code: '6000', name: 'Staff costs', type: 'expense', is_active: true, controlled_by: null },
  { id: 5, code: '6100', name: 'Rent and premises', type: 'expense', is_active: true, controlled_by: null },
]

const DRAFT = {
  id: 7,
  journal_no: 'MJ-000007',
  entry_date: '2026-09-30',
  narration: 'September rent',
  reference: 'INV-88',
  status: 'draft',
  status_label: 'Awaiting approval',
  prepared_by_id: TELLER.id,
  prepared_by_name: 'Teller',
  prepared_at: '2026-09-30T08:00:00Z',
  total: '400.00',
  entry_no: null,
  lines: [
    { id: 1, account_code: '6100', account_name: 'Rent and premises', debit: '400.00', credit: '0.00' },
    { id: 2, account_code: '1000', account_name: 'Cash and bank', debit: '0.00', credit: '400.00' },
  ],
}

const list = (...rows) => ({
  count: rows.length,
  page: 1,
  page_size: 25,
  num_pages: 1,
  results: rows,
  awaiting_approval: rows.filter((r) => r.status === 'draft').length,
})

function stub(rows = [DRAFT], post = {}) {
  return stubApi({ get: { '/api/journals': list(...rows), '/api/ledger/accounts': ACCOUNTS }, post })
}

describe('Journals', () => {
  it('never offers an account that only its own sub-ledger may post to', async () => {
    stub()
    renderPage(<Journals />, { user: TELLER })
    await userEvent.click(await screen.findByRole('button', { name: 'New journal' }))

    const account = screen.getByLabelText('Account on line 1')
    const options = within(account).getAllByRole('option').map((o) => o.textContent)
    expect(options).toContain('6000 Staff costs')
    expect(options.some((text) => text.startsWith('1100'))).toBe(false)
  })

  it('will not send a journal until its debits equal its credits', async () => {
    stub()
    renderPage(<Journals />, { user: TELLER })
    await userEvent.click(await screen.findByRole('button', { name: 'New journal' }))

    await userEvent.selectOptions(screen.getByLabelText('Account on line 1'), '4')
    await userEvent.type(screen.getByLabelText('Debit on line 1'), '500')
    await userEvent.selectOptions(screen.getByLabelText('Account on line 2'), '1')
    await userEvent.type(screen.getByLabelText('Credit on line 2'), '450')

    expect(screen.getByText('Out by 50.00')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send for approval' })).toBeDisabled()

    await userEvent.clear(screen.getByLabelText('Credit on line 2'))
    await userEvent.type(screen.getByLabelText('Credit on line 2'), '500')
    expect(screen.getByText('Balanced')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send for approval' })).toBeEnabled()
  })

  it('records an expense as a debit to the expense and a credit to the bank', async () => {
    const { postSpy } = stub([], { '/api/journals': { ...DRAFT, journal_no: 'MJ-000008' } })
    renderPage(<Journals />, { user: TELLER })
    await userEvent.click(await screen.findByRole('button', { name: 'Record an expense' }))

    await userEvent.type(screen.getByLabelText(/Amount/), '400')
    await userEvent.selectOptions(screen.getByLabelText('What it was'), '5')
    await userEvent.type(screen.getByLabelText('Paid to / what for'), 'Landlord, October rent')
    await userEvent.click(screen.getByRole('button', { name: 'Send for approval' }))

    const [path, body] = postSpy.mock.calls[0]
    expect(path).toBe('/api/journals')
    expect(body.lines).toEqual([
      { account_id: 5, debit: '400', description: 'Landlord, October rent' },
      { account_id: 1, credit: '400', description: 'Landlord, October rent' },
    ])
  })

  it('shows a journal as posted once it is, rather than closing it or reopening it later', async () => {
    // Posting takes a journal out of the "awaiting" list. The detail used to vanish
    // with it, then pop open again when the Posted tab was chosen.
    stub([DRAFT], {
      '/post': { ...DRAFT, status: 'posted', status_label: 'Posted', entry_no: 'JE-00000280' },
    })
    renderPage(<Journals />, { user: ADMIN })
    await userEvent.click(await screen.findByText('MJ-000007'))
    await userEvent.click(screen.getByRole('button', { name: 'Post to the ledger' }))

    const dialog = await screen.findByRole('dialog', { name: 'MJ-000007 — Posted' })
    expect(within(dialog).getByText('JE-00000280')).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    await userEvent.click(screen.getByRole('tab', { name: 'Posted' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('offers Post to an administrator and not to the teller who prepared it', async () => {
    stub()
    const { unmount } = renderPage(<Journals />, { user: TELLER })
    await userEvent.click(await screen.findByText('MJ-000007'))
    expect(screen.queryByRole('button', { name: 'Post to the ledger' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Withdraw' })).toBeInTheDocument()
    unmount()

    stub()
    renderPage(<Journals />, { user: ADMIN })
    await userEvent.click(await screen.findByText('MJ-000007'))
    expect(screen.getByRole('button', { name: 'Post to the ledger' })).toBeInTheDocument()
  })
})
