import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import * as api from '../lib/api.js'
import { renderPage, stubApi } from '../test/harness.jsx'
import Spreadsheets from './Spreadsheets.jsx'

const page = (results, money_columns = []) => ({
  count: results.length, page: 1, page_size: 100, num_pages: 1, results, money_columns,
})

const MEMBERS = page(
  [
    { member_no: 'M001', name: 'Rudo Moyo', borrower_id: 7, savings_balance: 1250.5,
      total_outstanding: 300, overdue_amount: 0, kyc_verified: true, group: null },
  ],
  ['overdue_amount', 'savings_balance', 'total_outstanding'],
)
const OVERDUE = page([{ loan_no: 'LN-9', borrower: 'Tendai', overdue_amount: 80, days_overdue: 12 }],
  ['overdue_amount'])

describe('Spreadsheets', () => {
  afterEach(() => vi.restoreAllMocks())

  it('previews the member register, with money formatted and ids left out', async () => {
    stubApi({ get: { 'spreadsheets/members': MEMBERS } })
    renderPage(<Spreadsheets />)
    const table = await screen.findByRole('table', { name: 'Member register' })
    expect(within(table).getByText('Rudo Moyo')).toBeInTheDocument()
    expect(within(table).getByText('1,250.50')).toBeInTheDocument()
    expect(within(table).getByText('Yes')).toBeInTheDocument()
    expect(within(table).queryByText('Borrower id')).not.toBeInTheDocument()
  })

  it('switches the preview to another sheet', async () => {
    stubApi({ get: { 'spreadsheets/members': MEMBERS, 'spreadsheets/overdue': OVERDUE } })
    renderPage(<Spreadsheets />)
    await screen.findByText('Rudo Moyo')
    const card = screen.getByRole('heading', { name: 'Overdue loans' }).closest('.sheet-card')
    await userEvent.click(within(card).getByRole('button', { name: 'Preview' }))
    const table = await screen.findByRole('table', { name: 'Overdue loans' })
    expect(within(table).getByText('LN-9')).toBeInTheDocument()
  })

  it('downloads a sheet as Excel, narrowed to the branch chosen', async () => {
    stubApi({ get: { 'spreadsheets/': MEMBERS } })
    const download = vi.spyOn(api, 'downloadFile').mockResolvedValue()
    renderPage(<Spreadsheets />, {
      org: { activeBranches: [{ id: 1, name: 'Harare' }, { id: 2, name: 'Bulawayo' }] },
    })
    await screen.findByText('Rudo Moyo')
    await userEvent.selectOptions(screen.getByLabelText('Filter by branch'), '2')

    // The heading appears twice: on its card, and over the preview below.
    const card = screen
      .getAllByRole('heading', { name: 'Member register' })
      .map((h) => h.closest('.sheet-card'))
      .find(Boolean)
    await userEvent.click(within(card).getByRole('button', { name: 'Download Excel' }))
    expect(download).toHaveBeenCalledWith(
      '/api/reports/spreadsheets/members?branch_id=2', 'xlsx', 'member_register',
    )
  })

  it('downloads everything as one workbook', async () => {
    stubApi({ get: { 'spreadsheets/': MEMBERS } })
    const download = vi.spyOn(api, 'downloadFile').mockResolvedValue()
    renderPage(<Spreadsheets />)
    await userEvent.click(
      screen.getByRole('button', { name: 'Download everything (Excel workbook)' }),
    )
    expect(download).toHaveBeenCalledWith('/api/reports/workbook', 'xlsx', 'portfolio')
  })

  it('says why a download failed instead of failing silently', async () => {
    stubApi({ get: { 'spreadsheets/': MEMBERS } })
    vi.spyOn(api, 'downloadFile').mockRejectedValue(new api.ApiError('Not allowed', 403))
    renderPage(<Spreadsheets />)
    await userEvent.click(
      screen.getByRole('button', { name: 'Download everything (Excel workbook)' }),
    )
    expect(await screen.findByText(/Not allowed/)).toBeInTheDocument()
  })
})
