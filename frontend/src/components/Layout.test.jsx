import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'

import { ADMIN, TELLER, VIEWER, renderPage, stubApi } from '../test/harness.jsx'
import Layout, { findPage } from './Layout.jsx'

function renderShell(route, user = ADMIN, counts = {}) {
  stubApi({
    get: {
      '/api/search': { borrowers: [], loans: [] },
      '/api/nav-summary': { pending_applications: 0, loans_in_arrears: 0, ...counts },
    },
  })
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
  it('lists entries rather than pages, and marks the one the current page is in', () => {
    renderShell('/ledger')
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: /accounting/i })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(within(nav).getByRole('link', { name: /customers/i })).not.toHaveAttribute('aria-current')
    // The ledger is a tab of Accounting now, not a row of its own.
    expect(within(nav).queryByRole('link', { name: /general ledger/i })).toBeNull()
    expect(within(nav).getAllByRole('link')).toHaveLength(12)
  })

  it('groups the entries under captions', () => {
    renderShell('/dashboard')
    const nav = screen.getByRole('navigation', { name: 'Main' })
    const finance = within(nav).getByRole('group', { name: 'Finance' })
    expect(within(finance).getAllByRole('link').map((a) => a.textContent)).toEqual([
      'Accounting',
      'Treasury',
      'Reports',
    ])
  })

  it('hides pages a teller cannot open, and whole entries with nothing left', () => {
    renderShell('/collections', TELLER)
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).queryByRole('link', { name: /system/i })).toBeNull()
    expect(within(nav).getByRole('link', { name: /products & charges/i })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: /teller till/i })).toBeInTheDocument()
  })

  it('shows how much work is waiting behind an entry', async () => {
    renderShell('/dashboard', ADMIN, { pending_applications: 3, loans_in_arrears: 1 })
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(
      await within(nav).findByRole('link', { name: /loans.*3 applications waiting/i }),
    ).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: /collections.*1 loan in arrears/i })).toBeInTheDocument()
    // Nothing waiting, no badge.
    expect(within(nav).getByRole('link', { name: /savings/i })).toHaveTextContent(/^Savings$/)
  })
})

describe('the tabs of a section', () => {
  it('are the pages of the current entry, with the current one marked', () => {
    renderShell('/journals')
    const tabs = screen.getByRole('navigation', { name: 'Accounting pages' })
    expect(within(tabs).getAllByRole('link').map((a) => a.textContent)).toEqual([
      'General ledger',
      'Journals & expenses',
      'Bank reconciliation',
      'Provisioning',
      'Period close',
    ])
    expect(within(tabs).getByRole('link', { name: /journals/i })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('stay on the right tab for a page underneath it', () => {
    renderShell('/borrowers/12')
    const tabs = screen.getByRole('navigation', { name: 'Customers pages' })
    expect(within(tabs).getByRole('link', { name: /borrowers/i })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('are not shown for an entry with a single page', () => {
    renderShell('/savings')
    expect(screen.queryByRole('navigation', { name: /pages$/ })).toBeNull()
  })

  it('leave out a page the user has no right to open', () => {
    renderShell('/collections', VIEWER)
    const tabs = screen.getByRole('navigation', { name: 'Collections pages' })
    expect(within(tabs).getByRole('link', { name: /arrears/i })).toBeInTheDocument()
    expect(within(tabs).queryByRole('link', { name: /bulk repayments/i })).toBeNull()
  })
})

describe('the top bar', () => {
  it('names the section and page', () => {
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

  it('finds a page by name from the search box', async () => {
    renderShell('/dashboard')
    await userEvent.type(screen.getByRole('searchbox'), 'audit')
    const option = await screen.findByRole('option', { name: /audit log/i })
    expect(option).toHaveTextContent('Page')
    await userEvent.keyboard('{Enter}')
    expect(screen.getByRole('navigation', { name: 'System pages' })).toBeInTheDocument()
  })
})

describe('findPage', () => {
  it('prefers the longest matching address', () => {
    expect(findPage('/imports/loan-book').item.label).toBe('Loan book migration')
    expect(findPage('/imports/loan-book').entry.label).toBe('System')
    expect(findPage('/imports').item.label).toBe('Bulk repayments')
    expect(findPage('/loans/12').item.label).toBe('Loans')
  })

  it('places the dashboard outside every section', () => {
    expect(findPage('/dashboard').section).toBeNull()
    expect(findPage('/dashboard').entry).toBeNull()
  })
})
