import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { OFFICER, TELLER, renderPage, stubApi } from '../test/harness.jsx'
import Till from './Till.jsx'

/**
 * The till page has two things a bug would quietly undo: a short count must ask
 * why before it closes, and nobody may be offered the verification of a till they
 * counted themselves.
 */

const OPEN = {
  id: 3,
  session_no: 'TILL-000003',
  teller_id: TELLER.id,
  teller_name: 'Teller',
  opened_at: '2026-10-03T07:30:00Z',
  opening_float: '500.00',
  status: 'open',
  position: {
    cash_in: '150.00',
    cash_out: '0.00',
    expected_cash: '650.00',
    movements: [
      { at: '2026-10-03T09:00:00Z', kind: 'Repayment', reference: 'LN-000001', detail: '', amount: '150.00' },
    ],
  },
}

const counted = (id, tellerId, variance) => ({
  id,
  session_no: `TILL-00000${id}`,
  teller_id: tellerId,
  teller_name: tellerId === TELLER.id ? 'Teller' : 'Officer',
  business_date: '2026-10-02',
  opening_float: '500.00',
  expected_cash: '650.00',
  counted_cash: String(650 + variance),
  variance: String(variance),
  close_note: 'Recounted',
  status: 'counted',
})

const page = (...rows) => ({ count: rows.length, page: 1, page_size: 25, num_pages: 1, results: rows })

function stub(current, waiting = []) {
  return stubApi({
    get: {
      '/api/tills/current': { till: current },
      'status=counted': page(...waiting),
      '/api/tills': page(...waiting),
    },
  })
}

describe('Till', () => {
  it('asks a teller with no till to open one', async () => {
    stub(null)
    renderPage(<Till />, { user: TELLER })
    expect(await screen.findByRole('button', { name: 'Open my till' })).toBeInTheDocument()
  })

  it('shows what the drawer should hold and asks why when the count is short', async () => {
    stub(OPEN)
    renderPage(<Till />, { user: TELLER })
    expect(await screen.findByText('USD 650.00')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Count and close' }))
    await userEvent.type(screen.getByLabelText(/Cash counted/), '630')
    expect(screen.getByText('Short 20.00')).toBeInTheDocument()
    expect(screen.getByLabelText(/^Note/)).toBeRequired()
  })

  it('shows each open drawer in its own currency', async () => {
    const zwg = {
      ...OPEN,
      id: 4,
      session_no: 'TILL-000004',
      currency: 'ZWG',
      currency_label: 'ZWG',
      opening_float: '1000.00',
      position: { cash_in: '300.00', cash_out: '0.00', expected_cash: '1300.00', movements: [] },
    }
    stubApi({
      get: {
        '/api/tills/current': { till: OPEN, tills: [zwg, { ...OPEN, currency: '', currency_label: 'USD' }] },
        '/api/tills': page(),
      },
    })
    renderPage(<Till />, { user: TELLER })
    expect(await screen.findByText('ZWG 1,300.00')).toBeInTheDocument()
    expect(screen.getByText('USD 650.00')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Count and close' })).toHaveLength(2)
  })

  it('opens a drawer only in a currency not already open, and sends it', async () => {
    const { postSpy } = stubApi({
      get: {
        '/api/tills/current': { till: { ...OPEN, currency: '' }, tills: [{ ...OPEN, currency: '' }] },
        '/api/currencies': {
          base_currency: 'USD',
          currencies: [{ code: 'ZWG', rate: '0.040000', rate_date: '2026-01-01' }],
        },
        '/api/tills': page(),
      },
      post: { '/api/tills': { id: 9 } },
    })
    renderPage(<Till />, { user: TELLER })
    await userEvent.click(await screen.findByRole('button', { name: 'Open a drawer in another currency' }))
    const select = await screen.findByLabelText(/^Drawer currency/)
    expect(await screen.findByLabelText(/^Opening float \(ZWG\)/)).toBeInTheDocument()
    expect([...select.options].map((o) => o.value)).toEqual(['ZWG'])
    await userEvent.type(screen.getByLabelText(/^Opening float \(ZWG\)/), '1000')
    await userEvent.click(screen.getByRole('button', { name: 'Open the till' }))
    expect(postSpy).toHaveBeenCalledWith('/api/tills', { currency: 'ZWG', opening_float: '1000' })
  })

  it('never offers someone the verification of their own count', async () => {
    stub(null, [counted(5, OFFICER.id, -20), counted(6, TELLER.id, 0)])
    renderPage(<Till />, { user: OFFICER })
    await screen.findByText('Awaiting verification')
    const verifyButtons = await screen.findAllByRole('button', { name: 'Verify' })
    expect(verifyButtons).toHaveLength(1)
  })
})
