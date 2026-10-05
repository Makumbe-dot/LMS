import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from '../lib/auth.jsx'
import { humanise } from '../lib/format.js'
import { useOrg } from '../lib/org.jsx'
import { useTheme } from '../lib/theme.jsx'
import GlobalSearch from './GlobalSearch.jsx'
import Icon from './Icons.jsx'
import { HexMark } from './ui.jsx'

/**
 * The menu, in sections that open one at a time.
 *
 * Thirty pages in one long list is what made the old sidebar feel crowded. Here
 * only the section the current page belongs to is open; the others show as a
 * single row each, and open on a click. Which sections someone has opened is
 * remembered per browser. "My account" and "Sign out" live in the avatar menu,
 * so the sidebar carries pages and nothing else.
 */
export const NAV = [
  {
    key: 'portfolio',
    heading: 'Portfolio',
    icon: 'loans',
    items: [
      { to: '/borrowers', label: 'Borrowers', icon: 'user' },
      { to: '/groups', label: 'Groups', icon: 'users' },
      { to: '/loans', label: 'Loans', icon: 'loans' },
      { to: '/savings', label: 'Savings', icon: 'savings' },
    ],
  },
  {
    key: 'collections',
    heading: 'Collections',
    icon: 'calendar',
    items: [
      { to: '/collections', label: 'Collections due', icon: 'calendar' },
      { to: '/arrears', label: 'Arrears / PAR', icon: 'trending' },
      { to: '/payroll', label: 'Payroll deductions', icon: 'briefcase' },
      {
        to: '/imports',
        label: 'Bulk repayments',
        icon: 'upload',
        roles: ['admin', 'loan_officer', 'teller'],
        end: true,
      },
      { to: '/notifications', label: 'Messages', icon: 'message' },
    ],
  },
  {
    key: 'finance',
    heading: 'Finance',
    icon: 'book',
    items: [
      { to: '/till', label: 'Teller till', icon: 'till', roles: ['admin', 'loan_officer', 'teller'] },
      { to: '/transactions', label: 'Transactions', icon: 'list' },
      { to: '/ledger', label: 'General ledger', icon: 'book' },
      { to: '/journals', label: 'Journals & expenses', icon: 'pen' },
      { to: '/bank-reconciliation', label: 'Bank reconciliation', icon: 'scale' },
      { to: '/funding', label: 'Funding & capital', icon: 'landmark' },
      { to: '/provisioning', label: 'Provisioning', icon: 'umbrella' },
      { to: '/periods', label: 'Period close', icon: 'lock' },
    ],
  },
  {
    key: 'reports',
    heading: 'Reports',
    icon: 'chart',
    items: [
      { to: '/performance', label: 'Performance', icon: 'chart' },
      { to: '/spreadsheets', label: 'Spreadsheets', icon: 'sheet' },
    ],
  },
  {
    key: 'setup',
    heading: 'Setup',
    icon: 'settings',
    items: [
      { to: '/products', label: 'Products', icon: 'package' },
      { to: '/charges', label: 'Charges', icon: 'tag' },
      { to: '/users', label: 'Users', icon: 'userCog', roles: ['admin'] },
      { to: '/settings', label: 'Settings', icon: 'settings', roles: ['admin'] },
      { to: '/imports/loan-book', label: 'Loan book migration', icon: 'database', roles: ['admin'] },
      { to: '/audit', label: 'Audit log', icon: 'history', roles: ['admin'] },
    ],
  },
]

const HOME = { to: '/dashboard', label: 'Dashboard', icon: 'dashboard', end: true }
const ACCOUNT = { to: '/account', label: 'My account', icon: 'user' }

const THEMES = [
  { value: 'light', icon: 'sun', label: 'Light' },
  { value: 'system', icon: 'monitor', label: 'Match the device' },
  { value: 'dark', icon: 'moon', label: 'Dark' },
]
const NEXT_THEME = { light: 'dark', dark: 'system', system: 'light' }

const RAIL_KEY = 'lms_nav_rail'
const OPEN_KEY = 'lms_nav_open'

function readStored(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? fallback : JSON.parse(raw)
  } catch {
    return fallback
  }
}

function store(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* private window or storage off: the preference just does not persist */
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

function matches(item, pathname) {
  if (item.end) return pathname === item.to
  return pathname === item.to || pathname.startsWith(`${item.to}/`)
}

/** The section and page the current address belongs to. */
export function findPage(pathname) {
  let best = null
  const consider = (group, item) => {
    if (matches(item, pathname) && (!best || item.to.length > best.item.to.length)) {
      best = { group, item }
    }
  }
  consider(null, HOME)
  consider(null, ACCOUNT)
  for (const group of NAV) for (const item of group.items) consider(group, item)
  return best
}

function useOutsideClose(open, close) {
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => ref.current && !ref.current.contains(e.target) && close()
    const onKey = (e) => e.key === 'Escape' && close()
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, close])
  return ref
}

function UserMenu({ user, onSignOut }) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const ref = useOutsideClose(open, () => setOpen(false))

  return (
    <div className="user-menu" ref={ref}>
      <button
        type="button"
        className="avatar-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="avatar">{initials(user?.full_name)}</span>
        <span className="avatar-text">
          <strong>{user?.full_name}</strong>
          <small>{humanise(user?.role)}</small>
        </span>
        <Icon name="chevron" size={14} className="avatar-caret" />
      </button>
      {open ? (
        <div className="menu-pop" role="menu">
          <div className="menu-head">
            <strong>{user?.full_name}</strong>
            <small>
              {humanise(user?.role)}
              {user?.branch_name ? ` · ${user.branch_name}` : ''}
            </small>
          </div>
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              setOpen(false)
              navigate(ACCOUNT.to)
            }}
          >
            <Icon name="user" size={16} /> My account
          </button>
          <button type="button" role="menuitem" className="menu-item" onClick={onSignOut}>
            <Icon name="logout" size={16} /> Sign out
          </button>
        </div>
      ) : null}
    </div>
  )
}

function NavItem({ item, rail, onNavigate }) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      title={rail ? item.label : undefined}
      className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
      onClick={onNavigate}
    >
      <Icon name={item.icon} />
      <span className="nav-label">{item.label}</span>
    </NavLink>
  )
}

export default function Layout() {
  const { user, signOut, can } = useAuth()
  const { orgName } = useOrg()
  const { theme, setTheme } = useTheme()
  const { pathname } = useLocation()

  const [rail, setRail] = useState(() => readStored(RAIL_KEY, false))
  const [drawer, setDrawer] = useState(false)
  const [opened, setOpened] = useState(() => readStored(OPEN_KEY, []))

  const current = useMemo(() => findPage(pathname), [pathname])
  const currentGroup = current?.group?.key || null

  useEffect(() => store(RAIL_KEY, rail), [rail])
  useEffect(() => store(OPEN_KEY, opened), [opened])
  // Changing page closes the phone drawer; nothing else should.
  useEffect(() => setDrawer(false), [pathname])

  const isOpen = (group) => group.key === currentGroup || opened.includes(group.key)
  const toggle = (group) => {
    if (rail) setRail(false)
    setOpened((list) =>
      list.includes(group.key) ? list.filter((k) => k !== group.key) : [...list, group.key],
    )
  }

  const visibleGroups = NAV.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.roles || can(...item.roles)),
  })).filter((group) => group.items.length)

  const themeNow = THEMES.find((t) => t.value === theme) || THEMES[1]
  const closeDrawer = () => setDrawer(false)

  return (
    <div className={`app-shell${rail ? ' rail' : ''}${drawer ? ' drawer-open' : ''}`}>
      <button
        type="button"
        className="drawer-backdrop"
        aria-label="Close the menu"
        tabIndex={-1}
        onClick={closeDrawer}
      />
      <aside className="sidebar" aria-label="Sidebar">
        <div className="brand">
          <HexMark size={28} />
          <span className="brand-text">
            <span className="brand-name">{orgName}</span>
            <span className="brand-sub">Loan management</span>
          </span>
          <button
            type="button"
            className="icon-btn sidebar-close"
            aria-label="Close the menu"
            onClick={closeDrawer}
          >
            <Icon name="close" />
          </button>
        </div>

        <nav aria-label="Main">
          <NavItem item={HOME} rail={rail} onNavigate={closeDrawer} />
          {visibleGroups.map((group) => {
            const open = isOpen(group)
            const holdsCurrent = group.key === currentGroup
            return (
              <div
                className={`nav-group${open ? ' open' : ''}${holdsCurrent ? ' current' : ''}`}
                key={group.key}
              >
                <button
                  type="button"
                  className="nav-section"
                  aria-expanded={open}
                  aria-controls={`nav-${group.key}`}
                  title={rail ? group.heading : undefined}
                  onClick={() => toggle(group)}
                >
                  <Icon name={group.icon} />
                  <span className="nav-label">{group.heading}</span>
                  <Icon name="chevron" size={14} className="nav-caret" />
                </button>
                <div className="nav-items" id={`nav-${group.key}`} hidden={!open}>
                  {group.items.map((item) => (
                    <NavItem key={item.to} item={item} rail={rail} onNavigate={closeDrawer} />
                  ))}
                </div>
              </div>
            )
          })}
        </nav>

        <div className="sidebar-foot">
          <button
            type="button"
            className="nav-link rail-toggle"
            aria-pressed={rail}
            title={rail ? 'Expand the menu' : 'Collapse the menu'}
            onClick={() => setRail((v) => !v)}
          >
            <Icon name="collapse" className="rail-icon" />
            <span className="nav-label">Collapse menu</span>
          </button>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <button
            type="button"
            className="icon-btn menu-btn"
            aria-label="Open the menu"
            onClick={() => setDrawer(true)}
          >
            <Icon name="menu" />
          </button>
          <nav className="crumbs" aria-label="You are here">
            {current?.group ? (
              <>
                <span className="crumb-section">{current.group.heading}</span>
                <span className="crumb-sep" aria-hidden="true">
                  /
                </span>
              </>
            ) : null}
            <span className="crumb-page">{current?.item.label || 'Loan Management System'}</span>
          </nav>
          <div className="topbar-search">
            <GlobalSearch />
          </div>
          <div className="topbar-tools">
            <button
              type="button"
              className="icon-btn"
              title={`Theme: ${themeNow.label.toLowerCase()}. Click to change.`}
              aria-label={`Theme: ${themeNow.label}. Click to change.`}
              onClick={() => setTheme(NEXT_THEME[theme] || 'light')}
            >
              <Icon name={themeNow.icon} />
            </button>
            <UserMenu user={user} onSignOut={signOut} />
          </div>
        </header>
        <main className="content" key={pathname}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
