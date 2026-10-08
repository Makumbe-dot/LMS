import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as api from '../lib/api.js'
import { renderPage, stubApi } from '../test/harness.jsx'
import Users from './Users.jsx'

/**
 * There are no roles below administrator: the Users page is where an
 * administrator ticks what each person may do. What a bug would quietly break is
 * the save, sending the ticks it showed, and the presets, which tick several at once.
 */

const CATALOGUE = {
  rights: [
    { code: 'borrowers', label: 'Borrowers and groups', description: 'Add and edit borrowers.' },
    { code: 'approve', label: 'Approve and reject loans', description: 'Approve applications.' },
    { code: 'cash', label: 'Cash and repayments', description: 'Take repayments.' },
  ],
  presets: [
    { code: 'loan_officer', label: 'Loan officer', rights: ['borrowers', 'approve', 'cash'] },
    { code: 'teller', label: 'Teller', rights: ['cash'] },
  ],
  default_approval_limit: '2000.00',
}

const USERS = [
  { id: 1, username: 'admin', full_name: 'Admin', role: 'admin', rights: ['borrowers', 'approve', 'cash'], is_active: true },
  { id: 2, username: 'tendai', full_name: 'Tendai Moyo', role: 'user', rights: ['cash'], is_active: true },
  { id: 3, username: 'board', full_name: 'Board', role: 'user', rights: [], is_active: true },
]

function setUp() {
  stubApi({ get: { '/api/users/rights': CATALOGUE, '/api/users': USERS } })
  const patchSpy = vi.spyOn(api, 'patch').mockResolvedValue({})
  renderPage(<Users />)
  return { patchSpy }
}

describe('the Users page', () => {
  it('lists what each user may do instead of a role', async () => {
    setUp()
    const row = (await screen.findByText('tendai')).closest('tr')
    expect(row).toHaveTextContent('Cash and repayments')
    expect(screen.getByText('board').closest('tr')).toHaveTextContent('Read only')
    expect(screen.getByText('admin').closest('tr')).toHaveTextContent('Administrator')
  })

  it('saves the rights ticked, with no role below administrator', async () => {
    const { patchSpy } = setUp()
    await userEvent.click(await screen.findByText('tendai'))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByRole('checkbox', { name: /cash and repayments/i })).toBeChecked()
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /borrowers and groups/i }))
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /cash and repayments/i }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save user' }))

    await waitFor(() => expect(patchSpy).toHaveBeenCalled())
    const [path, body] = patchSpy.mock.calls[0]
    expect(path).toBe('/api/users/2')
    expect(body.role).toBe('user')
    expect(body.rights).toEqual(['borrowers'])
  })

  it('ticks a whole preset at once, and asks for a limit once approving is ticked', async () => {
    const { patchSpy } = setUp()
    await userEvent.click(await screen.findByText('board'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText(/approval limit/i)).toBeNull()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Loan officer' }))
    const limit = within(dialog).getByLabelText(/approval limit/i)
    await userEvent.type(limit, '750')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save user' }))

    await waitFor(() => expect(patchSpy).toHaveBeenCalled())
    const body = patchSpy.mock.calls[0][1]
    expect(body.rights).toEqual(['borrowers', 'approve', 'cash'])
    expect(body.approval_limit).toBe('750')
  })

  it('hides the ticks for an administrator, who holds every right', async () => {
    setUp()
    await userEvent.click(await screen.findByText('board'))
    const dialog = await screen.findByRole('dialog')
    await userEvent.selectOptions(within(dialog).getByLabelText('Access'), 'admin')
    expect(within(dialog).queryByRole('checkbox', { name: /cash and repayments/i })).toBeNull()
  })
})
