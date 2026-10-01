import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { renderPage, stubApi } from '../test/harness.jsx'
import Arrears from './Arrears.jsx'

/**
 * The arrears page is paginated, and the figure in its header is the one number
 * the screen exists to show. Summing the rows on the current page would under-report
 * it silently — so the total comes from the response, and these tests pin that.
 */

const row = (n, days, arrears) => ({
  loan_id: n,
  loan_no: `LN-${String(n).padStart(6, '0')}`,
  borrower: `Borrower ${n}`,
  phone: '0771234567',
  employer: 'Acme',
  product: 'Salary loan',
  days_in_arrears: days,
  bucket: days > 90 ? '91-180' : days > 30 ? '31-60' : '1-30',
  arrears_amount: arrears,
  penalties_outstanding: '10.00',
  principal_outstanding: '1000.00',
  total_outstanding: '1200.00',
})

const PAGE_ONE = {
  count: 120,
  page: 1,
  page_size: 50,
  num_pages: 3,
  // Deliberately only two rows against a count of 120: the point is that the
  // header must not be derived from what is on screen.
  results: [row(1, 95, '500.00'), row(2, 12, '100.00')],
  arrears_total: '41250.00',
  principal_total: '120000.00',
}

describe('Arrears', () => {
  it('takes the overdue total from the response, not from the rows on screen', async () => {
    stubApi({ get: { '/api/reports/par': PAGE_ONE } })
    renderPage(<Arrears />)

    // 120 loans and 41,250.00 overdue, although only two rows totalling 600 are shown.
    expect(await screen.findByText(/120 loans in arrears/)).toBeInTheDocument()
    expect(screen.getByText(/41,250\.00 overdue/)).toBeInTheDocument()
    expect(screen.queryByText(/600\.00 overdue/)).not.toBeInTheDocument()
  })

  it('lists the loans it was given, worst first', async () => {
    stubApi({ get: { '/api/reports/par': PAGE_ONE } })
    renderPage(<Arrears />)
    await screen.findByText('LN-000001')
    const cells = screen.getAllByText(/^LN-/).map((el) => el.textContent)
    expect(cells).toEqual(['LN-000001', 'LN-000002'])
  })

  it('asks the server for the next page rather than slicing locally', async () => {
    const { getSpy } = stubApi({ get: { '/api/reports/par': PAGE_ONE } })
    renderPage(<Arrears />)
    await screen.findByText('LN-000001')

    const next = screen.getByRole('button', { name: /next/i })
    await userEvent.click(next)

    expect(getSpy.mock.calls.some(([path]) => path.includes('page=2'))).toBe(true)
  })

  it('says so plainly when nothing is overdue', async () => {
    stubApi({
      get: {
        '/api/reports/par': {
          count: 0, page: 1, page_size: 50, num_pages: 1, results: [],
          arrears_total: '0.00', principal_total: '0.00',
        },
      },
    })
    renderPage(<Arrears />)
    expect(await screen.findByText(/whole book is current/i)).toBeInTheDocument()
  })

  it('marks a loan more than 30 days down as the worse case', async () => {
    stubApi({ get: { '/api/reports/par': PAGE_ONE } })
    renderPage(<Arrears />)
    await screen.findByText('LN-000001')
    // 95 days reads danger, 12 days reads warn — the distinction a collections
    // officer scans the column for.
    const badges = screen.getAllByText(/days$/)
    expect(badges[0].className).toContain('tag-danger')
    expect(badges[1].className).toContain('tag-warn')
  })
})
