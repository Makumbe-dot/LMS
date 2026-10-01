import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { renderPage, stubApi } from '../test/harness.jsx'
import Ledger from './Ledger.jsx'

/**
 * The Ledger page has six tabs answering six different shapes, and `data` holds
 * the PREVIOUS tab's response until the new one lands. It crashed once on exactly
 * that — `Cannot read properties of undefined (reading 'length')` — which is why
 * it renders on the shape of the payload rather than on the name of the tab.
 *
 * These tests switch tabs against a server that is slow or broken, which is the
 * situation the shape guard exists for.
 */

const TRIAL = {
  rows: [{ code: '1000', name: 'Cash and bank', type: 'asset', debit: '100.00',
           credit: '0.00', balance: '100.00', side: 'debit' }],
  total_debit: '100.00', total_credit: '100.00', balanced: true,
}
const BALANCE_SHEET = {
  as_of: '2026-10-01',
  assets: [{ code: '1000', name: 'Cash and bank', type: 'asset', debit: '100.00',
             credit: '0.00', balance: '100.00', side: 'debit' }],
  liabilities: [], equity: [{ code: '3000', name: 'Retained earnings', type: 'equity',
                              debit: '0.00', credit: '100.00', balance: '100.00',
                              side: 'credit' }],
  total_assets: '100.00', total_liabilities: '0.00', total_equity: '100.00',
  total_liabilities_and_equity: '100.00', retained_earnings: '100.00',
  income_to_date: '100.00', expense_to_date: '0.00',
  difference: '0.00', balanced: true, branch_id: null,
}
const TIES = {
  as_of: '2026-10-01',
  rows: [{ code: '1100', name: 'Loans receivable', ledger: '50.00', book: '50.00',
           difference: '0.00', agrees: true, sub_ledger: 'principal outstanding' }],
  agrees: true, breaks: [], trial_balance_balanced: true, balance_sheet_balanced: true,
}

const mount = () => renderPage(<Ledger />)
const routeBy = (get) => stubApi({ get })

describe('Ledger', () => {
  it('shows the trial balance and whether it balances', async () => {
    routeBy({ 'trial-balance': TRIAL })
    mount()
    expect(await screen.findByText('Balanced')).toBeInTheDocument()
    expect(screen.getByText('Cash and bank')).toBeInTheDocument()
  })

  it('does not crash when a tab is switched before its data arrives', async () => {
    // The shape guard. The balance-sheet tab reads data.assets.length; if it
    // rendered while `data` still held the trial balance, it would throw.
    let release
    const pending = new Promise((resolve) => {
      release = resolve
    })
    routeBy({
      'trial-balance': TRIAL,
      'balance-sheet': () => pending.then(() => BALANCE_SHEET),
    })
    mount()
    await screen.findByText('Cash and bank')

    await userEvent.click(screen.getByRole('tab', { name: 'Balance sheet' }))
    // The old payload is still in `data` and the new one has not landed.
    expect(screen.getByText(/Loading the ledger/i)).toBeInTheDocument()

    release()
    expect(await screen.findByText('Total assets')).toBeInTheDocument()
  })

  it('flags a balance sheet that does not add up rather than hiding it', async () => {
    routeBy({ 'balance-sheet': { ...BALANCE_SHEET, balanced: false, difference: '12.34' } })
    mount()
    await userEvent.click(screen.getByRole('tab', { name: 'Balance sheet' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Out by/)
  })

  it('marks retained earnings as derived, so nobody looks for the posting', async () => {
    routeBy({ 'balance-sheet': BALANCE_SHEET })
    mount()
    await userEvent.click(screen.getByRole('tab', { name: 'Balance sheet' }))
    expect(await screen.findByText(/derived, income less expense to date/))
      .toBeInTheDocument()
  })

  it('says a branch slice is a sub-book, not the institution balance sheet', async () => {
    routeBy({ 'balance-sheet': { ...BALANCE_SHEET, branch_id: 3 } })
    mount()
    await userEvent.click(screen.getByRole('tab', { name: 'Balance sheet' }))
    expect(await screen.findByText(/branch sub-book/)).toBeInTheDocument()
  })

  it('shows every sub-ledger tie and its result', async () => {
    routeBy({ reconciliation: TIES })
    mount()
    await userEvent.click(screen.getByRole('tab', { name: 'Reconciliation' }))
    expect(await screen.findByText('All agree')).toBeInTheDocument()
    expect(screen.getByText('principal outstanding')).toBeInTheDocument()
  })

  it('names the break when a tie fails', async () => {
    const broken = {
      ...TIES,
      agrees: false,
      rows: [{ ...TIES.rows[0], book: '44.00', difference: '6.00', agrees: false }],
      breaks: [{ ...TIES.rows[0], book: '44.00', difference: '6.00', agrees: false }],
    }
    routeBy({ reconciliation: broken })
    mount()
    await userEvent.click(screen.getByRole('tab', { name: 'Reconciliation' }))
    expect(await screen.findByText('1 break(s)')).toBeInTheDocument()
    expect(screen.getByText(/Out by 6\.00/)).toBeInTheDocument()
  })

  it('offers a retry when the server is unreachable', async () => {
    routeBy({ 'trial-balance': new Error('Cannot reach the server') })
    mount()
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /Try again/i })).toBeInTheDocument()
  })
})
