import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { OFFICER, TELLER, renderPage, stubApi } from '../test/harness.jsx'
import CollectionsWork from './CollectionsWork.jsx'

const ROW = {
  loan_id: 4,
  loan_no: 'LN-000004',
  borrower: 'Tendai Moyo',
  phone: '0771234567',
  arrears_amount: '197.02',
  days_in_arrears: 16,
  next_action: '2026-04-09',
  action_due: true,
  last_contact: null,
  collector: null,
  promise: { amount: '150.00', date: '2026-04-17', state: 'pending', paid: '0.00' },
}

describe('the collections work queue', () => {
  it('shows each overdue loan with its follow-up and promise', async () => {
    stubApi({ get: { '/api/collections/queue': [ROW] } })
    renderPage(<CollectionsWork />, { user: TELLER })
    expect(await screen.findByText('LN-000004')).toBeInTheDocument()
    expect(screen.getByText(/150.00 by 2026-04-17 · pending/)).toBeInTheDocument()
    expect(screen.getByText(/1 with a follow-up due/)).toBeInTheDocument()
    // A teller works loans but does not hand them out.
    expect(screen.queryByRole('checkbox', { name: /select ln-000004/i })).toBeNull()
  })

  it('lets a supervisor give selected loans to a collector', async () => {
    const { postSpy } = stubApi({
      get: {
        '/api/collections/collectors': [{ id: 9, full_name: 'Rudo' }],
        '/api/collections/queue': [ROW],
      },
      post: { '/api/collections/assign': { changed: 1 } },
    })
    renderPage(<CollectionsWork />, { user: OFFICER })
    await userEvent.click(await screen.findByRole('checkbox', { name: /select ln-000004/i }))
    await userEvent.selectOptions(screen.getByLabelText(/assign selected loans to/i), '9')
    await waitFor(() =>
      expect(postSpy).toHaveBeenCalledWith('/api/collections/assign', {
        loan_ids: [4],
        collector_id: 9,
      }),
    )
  })
})
