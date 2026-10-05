import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '../lib/auth.jsx'
import { humanise } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useTheme } from '../lib/theme.jsx'
import GlobalSearch from './GlobalSearch.jsx'
import Icon from './Icons.jsx'
import { HexMark } from './ui.jsx'

const NAV = [
  {
    heading: 'Portfolio',
    items: [
      { to: '/dashboard', label: 'Dashboard', icon: 'dashboard' },
      { to: '/borrowers', label: 'Borrowers', icon: 'user' },
      { to: '/groups', label: 'Groups', icon: 'users' },
      { to: '/loans', label: 'Loans', icon: 'loans' },
      { to: '/savings', label: 'Savings', icon: 'savings' },
    ],
  },
  {
    heading: 'Collections',
    items: [
      { to: '/collections', label: 'Collections due', icon: 'calendar' },
      { to: '/till', label: 'Teller till', icon: 'till', roles: ['admin', 'loan_officer', 'teller'] },
      { to: '/arrears', label: 'Arrears / PAR', icon: 'trending' },
      { to: '/payroll', label: 'Payroll deductions', icon: 'briefcase' },
      {
        to: '/imports',
        label: 'Bulk repayments',
        icon: 'upload',
        roles: ['admin', 'loan_officer', 'teller'],
        end: true,
      },
      {
        to: '/payments/incoming',
        label: 'Incoming payments',
        icon: 'inbox',
        roles: ['admin', 'loan_officer', 'teller'],
      },
      { to: '/notifications', label: 'Messages', icon: 'message' },
      { to: '/claims', label: 'Credit-life claims', icon: 'shield' },
    ],
  },
  {
    heading: 'Reporting',
    items: [
      { to: '/transactions', label: 'Transactions', icon: 'list' },
      { to: '/ledger', label: 'General ledger', icon: 'book' },
      { to: '/journals', label: 'Journals & expenses', icon: 'pen' },
      { to: '/bank-reconciliation', label: 'Bank reconciliation', icon: 'scale' },
      { to: '/funding', label: 'Funding & capital', icon: 'landmark' },
      { to: '/spreadsheets', label: 'Spreadsheets', icon: 'sheet' },
      { to: '/performance', label: 'Performance', icon: 'chart' },
      { to: '/provisioning', label: 'Provisioning', icon: 'umbrella' },
      { to: '/periods', label: 'Period close', icon: 'lock' },
    ],
  },
  {
    heading: 'Administration',
    items: [
      { to: '/products', label: 'Products', icon: 'package' },
      { to: '/charges', label: 'Charges', icon: 'tag' },
      { to: '/users', label: 'Users', icon: 'userCog', roles: ['admin'] },
      { to: '/settings', label: 'Settings', icon: 'settings', roles: ['admin'] },
      { to: '/imports/loan-book', label: 'Loan book migration', icon: 'database', roles: ['admin'] },
      { to: '/audit', label: 'Audit log', icon: 'history', roles: ['admin'] },
      { to: '/account', label: 'My account', icon: 'user' },
    ],
  },
]

const THEMES = [
  { value: 'light', icon: 'sun', label: 'Light' },
  { value: 'system', icon: 'monitor', label: 'Match the device' },
  { value: 'dark', icon: 'moon', label: 'Dark' },
]

const COLLAPSE_KEY = 'lms_nav_collapsed'

function readCollapsed() {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

function initials(name) {
  return (name || '?')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join('')
}

/** The section and page the current address belongs to, for the top bar. */
function useCurrentPage() {
  const { pathname } = useLocation()
  return useMemo(() => {
    let best = null
    for (const group of NAV) {
      for (const item of group.items) {
        const hit = pathname === item.to || pathname.startsWith(`${item.to}/`)
        if (hit && (!best || item.to.length > best.item.to.length)) best = { group, item }
      }
    }
    return best
  }, [pathname])
}

function UserMenu({ user, onSignOut }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="user-menu" ref={ref}>
      <button
        type="button"
        className="avatar-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="avatar">{initials(user?.full_name)}</span>
        <span className="avatar-text">
          <strong>{user?.full_name}</strong>
          <small>{humanise(user?.role)}</small>
        </span>
      </button>
      {open ? (
        <div className="menu-pop" role="menu">
          <div className="menu-head">
            <span className="avatar large">{initials(user?.full_name)}</span>
            <span>
              <strong>{user?.full_name}</strong>
              <small>
                {humanise(user?.role)}
                {user?.branch_name ? ` · ${user.branch_name}` : ''}
              </small>
            </span>
          </div>
          <NavLink to="/account" role="menuitem" className="menu-item" onClick={() => setOpen(false)}>
            <Icon name="user" size={16} /> My account
          </NavLink>
          <button type="button" role="menuitem" className="menu-item" onClick={onSignOut}>
            <Icon name="logout" size={16} /> Sign out
          </button>
        </div>
      ) : null}
    </div>
  )
}

export default function Layout() {
  const { user, signOut, can } = useAuth()
  const { theme, setTheme } = useTheme()
  const { pathname } = useLocation()
  const org = useOrg()
  const current = useCurrentPage()
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [drawer, setDrawer] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0')
    } catch {
      /* a private window: the choice lasts for this visit only */
    }
  }, [collapsed])

  // The phone drawer closes once a page is chosen.
  useEffect(() => setDrawer(false), [pathname])

  const orgName = org?.settings?.name

  return (
    <div className={`app-shell${collapsed ? ' nav-collapsed' : ''}${drawer ? ' drawer-open' : ''}`}>
      <aside className="sidebar" aria-label="Sidebar">
        <div className="brand">
          <span className="brand-logo">
            <HexMark size={22} />
          </span>
          <div className="brand-text">
            <span className="brand-name">Loan Management</span>
            <span className="brand-sub">{orgName || 'Microfinance suite'}</span>
          </div>
          <button
            type="button"
            className="drawer-close"
            aria-label="Close the menu"
            onClick={() => setDrawer(false)}
          >
            <Icon name="close" />
          </button>
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
                    end={item.end}
                    title={collapsed ? item.label : undefined}
                    className={({ isActive }) => (isActive ? 'active' : undefined)}
                  >
                    <Icon name={item.icon} size={18} />
                    <span className="nav-label">{item.label}</span>
                  </NavLink>
                ))}
              </div>
            )
          })}
        </nav>

        <div className="sidebar-foot">
          <div className="theme-switch" role="radiogroup" aria-label="Colour theme">
            {THEMES.map((t) => (
              <button
                key={t.value}
                type="button"
                role="radio"
                aria-checked={theme === t.value}
                title={t.label}
                aria-label={t.label}
                className={theme === t.value ? 'on' : undefined}
                onClick={() => setTheme(t.value)}
              >
                <Icon name={t.icon} size={16} />
              </button>
            ))}
          </div>
          <button
            type="button"
            className="collapse-btn"
            aria-label={collapsed ? 'Expand the menu' : 'Collapse the menu'}
            aria-pressed={collapsed}
            onClick={() => setCollapsed((v) => !v)}
          >
            <Icon name="collapse" size={16} />
            <span className="nav-label">Collapse</span>
          </button>
        </div>
      </aside>
      <button
        type="button"
        className="drawer-scrim"
        aria-label="Close the menu"
        tabIndex={drawer ? 0 : -1}
        onClick={() => setDrawer(false)}
      />

      <div className="main-column">
        <header className="topbar">
          <button
            type="button"
            className="icon-btn drawer-open-btn"
            aria-label="Open the menu"
            onClick={() => setDrawer(true)}
          >
            <Icon name="menu" />
          </button>
          <div className="crumbs">
            {current ? (
              <>
                <span className="crumb-section">{current.group.heading}</span>
                <span className="crumb-sep" aria-hidden="true">
                  /
                </span>
                <span className="crumb-page">{current.item.label}</span>
              </>
            ) : (
              <span className="crumb-page">Loan Management</span>
            )}
          </div>
          <GlobalSearch />
          <UserMenu user={user} onSignOut={signOut} />
        </header>
        <main className="content" key={pathname}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
