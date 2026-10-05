import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'

import { ADMIN, TELLER, renderPage, stubApi } from '../test/harness.jsx'
import Layout, { findPage } from './Layout.jsx'

function renderShell(route, user = ADMIN) {
  stubApi({ get: { '/api/search': { borrowers: [], loans: [] } } })
  return renderPage(
    <Routes>
      <Route element={<Layout />}>
        <Route path="*" element={<p>page body</p>} />
      </Route>
    </Routes>,
    { route, user },
  )
}

describe('the sidebar', () => {
  it('opens only the section the current page belongs to', () => {
    renderShell('/ledger')
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('button', { name: /finance/i })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(within(nav).getByRole('button', { name: /portfolio/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(within(nav).getByRole('link', { name: /general ledger/i })).toHaveClass('active')
    expect(within(nav).queryByRole('link', { name: /borrowers/i })).toBeNull()
  })

  it('opens another section on a click and keeps the current one open', async () => {
    renderShell('/loans')
    const nav = screen.getByRole('navigation', { name: 'Main' })
    await userEvent.click(within(nav).getByRole('button', { name: /setup/i }))
    expect(within(nav).getByRole('link', { name: /products/i })).toBeVisible()
    expect(within(nav).getByRole('link', { name: /^loans$/i })).toBeVisible()
  })

  it('hides pages a teller cannot open, and whole sections with nothing left', () => {
    renderShell('/collections', TELLER)
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).queryByRole('link', { name: /users/i })).toBeNull()
    expect(within(nav).getByRole('button', { name: /setup/i })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: /bulk repayments/i })).toBeInTheDocument()
  })

  it('names the section and page in the top bar', () => {
    renderShell('/bank-reconciliation')
    const crumbs = screen.getByRole('navigation', { name: 'You are here' })
    expect(crumbs).toHaveTextContent('Finance')
    expect(crumbs).toHaveTextContent('Bank reconciliation')
  })

  it('keeps sign out and my account in the avatar menu', async () => {
    const { auth } = renderShell('/dashboard')
    await userEvent.click(screen.getByRole('button', { name: /account menu/i }))
    expect(screen.getByRole('menuitem', { name: /my account/i })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('menuitem', { name: /sign out/i }))
    expect(auth.signOut).toHaveBeenCalled()
  })
})

describe('findPage', () => {
  it('prefers the longest matching address', () => {
    expect(findPage('/imports/loan-book').item.label).toBe('Loan book migration')
    expect(findPage('/imports').item.label).toBe('Bulk repayments')
    expect(findPage('/loans/12').item.label).toBe('Loans')
    expect(findPage('/dashboard').group).toBeNull()
  })
})
