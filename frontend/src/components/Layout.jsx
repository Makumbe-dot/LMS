import { NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '../lib/auth.jsx'
import { humanise } from '../lib/format.js'
import { useTheme } from '../lib/theme.jsx'
import GlobalSearch from './GlobalSearch.jsx'
import { HexMark } from './ui.jsx'

const NAV = [
  {
    heading: 'Portfolio',
    items: [
      { to: '/dashboard', label: 'Dashboard' },
      { to: '/borrowers', label: 'Borrowers' },
      { to: '/groups', label: 'Groups' },
      { to: '/loans', label: 'Loans' },
      { to: '/savings', label: 'Savings' },
    ],
  },
  {
    heading: 'Collections',
    items: [
      { to: '/collections', label: 'Collections due' },
      { to: '/till', label: 'Teller till', roles: ['admin', 'loan_officer', 'teller'] },
      { to: '/arrears', label: 'Arrears / PAR' },
      { to: '/payroll', label: 'Payroll deductions' },
      { to: '/imports', label: 'Bulk repayments', roles: ['admin', 'loan_officer', 'teller'] },
      {
        to: '/payments/incoming',
        label: 'Incoming payments',
        roles: ['admin', 'loan_officer', 'teller'],
      },
      { to: '/notifications', label: 'Messages' },
    ],
  },
  {
    heading: 'Reporting',
    items: [
      { to: '/transactions', label: 'Transactions' },
      { to: '/ledger', label: 'General ledger' },
      { to: '/journals', label: 'Journals & expenses' },
      { to: '/bank-reconciliation', label: 'Bank reconciliation' },
      { to: '/funding', label: 'Funding & capital' },
      { to: '/spreadsheets', label: 'Spreadsheets' },
      { to: '/performance', label: 'Performance' },
      { to: '/provisioning', label: 'Provisioning' },
      { to: '/periods', label: 'Period close' },
    ],
  },
  {
    heading: 'Administration',
    items: [
      { to: '/products', label: 'Products' },
      { to: '/charges', label: 'Charges' },
      { to: '/users', label: 'Users', roles: ['admin'] },
      { to: '/settings', label: 'Settings', roles: ['admin'] },
      { to: '/imports/loan-book', label: 'Loan book migration', roles: ['admin'] },
      { to: '/audit', label: 'Audit log', roles: ['admin'] },
      { to: '/account', label: 'My account' },
    ],
  },
]

const THEME_LABEL = { system: 'Theme: auto', light: 'Theme: light', dark: 'Theme: dark' }

export default function Layout() {
  const { user, signOut, can } = useAuth()
  const { theme, cycle } = useTheme()

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <HexMark />
          <div className="brand-text">
            <span className="brand-name">Loan Management System</span>
          </div>
        </div>
        <nav aria-label="Main">
          {NAV.map((group) => {
            const visible = group.items.filter((item) => !item.roles || can(...item.roles))
            if (!visible.length) return null
            return (
              <div className="nav-group" key={group.heading}>
                <div className="nav-heading">{group.heading}</div>
                {visible.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) => (isActive ? 'active' : undefined)}
                  >
                    {item.label}
                  </NavLink>
                ))}
              </div>
            )
          })}
        </nav>
        <div className="userbox">
          <div>{user?.full_name}</div>
          <div className="muted">
            {humanise(user?.role)}
            {user?.branch_name ? ` · ${user.branch_name}` : ''}
          </div>
          <div className="row">
            <button type="button" className="btn small" onClick={cycle}>
              {THEME_LABEL[theme]}
            </button>
            <button type="button" className="btn small" onClick={signOut}>
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <GlobalSearch />
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
