import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { get } from '../lib/api.js'
import { roleLabel, useAuth } from '../lib/auth.jsx'
import { ACCOUNT, HOME, findPage, visibleNav } from '../lib/nav.js'
import { useOrg } from '../lib/org.jsx'
import { useTheme } from '../lib/theme.jsx'
import { LogoMark } from '../brand/Brand.jsx'
import GlobalSearch from './GlobalSearch.jsx'
import Icon from './Icons.jsx'

/* The shell: sidebar, top bar, and the tabs of the section you are in.

   The sidebar lists entries, not pages (see lib/nav.js): ten rows under four
   captions. The pages inside the current entry run as tabs under the top bar, so
   moving between the ledger and its journals is one click and the sidebar never
   scrolls. "My account" and "Sign out" live in the avatar menu. */

// Kept exported from here: it is the shell's question, and tests ask it here.
export { findPage }

const THEMES = [
  { value: 'light', icon: 'sun', label: 'Light' },
  { value: 'system', icon: 'monitor', label: 'Match the device' },
  { value: 'dark', icon: 'moon', label: 'Dark' },
]
const NEXT_THEME = { light: 'dark', dark: 'system', system: 'light' }

const RAIL_KEY = 'lms_nav_rail'

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

function shortDate(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/**
 * The counts beside sidebar rows: applications waiting, loans in arrears.
 *
 * Asked for when the page changes (approving a loan should move the number) but
 * not more than once in ten seconds, and once a minute while the tab is showing.
 * A failure leaves the last counts in place; a badge is not worth an error.
 */
function useNavSummary(pathname) {
  const [counts, setCounts] = useState({})
  const asked = useRef(0)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    const load = () => {
      asked.current = Date.now()
      get('/api/nav-summary')
        .then((data) => mounted.current && data && setCounts(data))
        .catch(() => {})
    }
    if (Date.now() - asked.current > 10_000) load()
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') load()
    }, 60_000)
    return () => {
      mounted.current = false
      clearInterval(timer)
    }
  }, [pathname])

  return counts
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
          {roleLabel(user) !== user?.full_name ? <small>{roleLabel(user)}</small> : null}
        </span>
        <Icon name="chevron" size={14} className="avatar-caret" />
      </button>
      {open ? (
        <div className="menu-pop" role="menu">
          <div className="menu-head">
            <strong>{user?.full_name}</strong>
            <small>
              {roleLabel(user)}
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

/** One sidebar row. `badge` is { count, text, tone } when there is work waiting. */
function NavRow({ to, icon, label, active, rail, badge, hint, onNavigate }) {
  const title = rail ? [label, badge?.text].filter(Boolean).join(' · ') : hint
  return (
    <Link
      to={to}
      className={active ? 'nav-link active' : 'nav-link'}
      aria-current={active ? 'page' : undefined}
      title={title || undefined}
      onClick={onNavigate}
    >
      <Icon name={icon} />
      <span className="nav-label">{label}</span>
      {badge ? (
        <>
          <span className={`nav-badge${badge.tone ? ` ${badge.tone}` : ''}`} aria-hidden="true">
            {badge.count > 99 ? '99+' : badge.count}
          </span>
          <span className="sr-only">, {badge.text}</span>
        </>
      ) : null}
    </Link>
  )
}

export default function Layout() {
  const { user, signOut, can } = useAuth()
  const { orgName, closedThrough } = useOrg()
  const { theme, setTheme } = useTheme()
  const { pathname } = useLocation()

  const [rail, setRail] = useState(() => readStored(RAIL_KEY, false))
  const [drawer, setDrawer] = useState(false)
  const counts = useNavSummary(pathname)
  const tabsRef = useRef(null)

  const current = useMemo(() => findPage(pathname), [pathname])
  const sections = visibleNav(can)
  // The entry as this user sees it: its tabs are only the pages they may open.
  const entry =
    sections.flatMap((s) => s.entries).find((e) => e.key === current?.entry?.key) || null
  const tabs = entry && entry.pages.length > 1 ? entry.pages : null

  useEffect(() => store(RAIL_KEY, rail), [rail])
  // Changing page closes the phone drawer; nothing else should.
  useEffect(() => setDrawer(false), [pathname])
  // A tab off the edge of a narrow screen is brought into view when it is the page.
  useEffect(() => {
    tabsRef.current
      ?.querySelector('[aria-current="page"]')
      ?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }, [pathname])

  const badgeFor = (item) => {
    const count = Number(counts[item.badge?.key]) || 0
    if (!count) return null
    return {
      count,
      tone: item.badge.tone,
      text: `${count} ${count === 1 ? item.badge.one : item.badge.many}`,
    }
  }

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
          <LogoMark height={24} />
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
          <NavRow
            to={HOME.to}
            icon={HOME.icon}
            label={HOME.label}
            active={current?.item === HOME}
            rail={rail}
            onNavigate={closeDrawer}
          />
          {sections.map((section) => (
            <div
              className="nav-group"
              role="group"
              aria-labelledby={`nav-${section.key}`}
              key={section.key}
            >
              <div className="nav-caption" id={`nav-${section.key}`}>
                <span>{section.heading}</span>
              </div>
              {section.entries.map((item) => (
                <NavRow
                  key={item.key}
                  to={item.to}
                  icon={item.icon}
                  label={item.label}
                  active={item.key === entry?.key}
                  rail={rail}
                  badge={badgeFor(item)}
                  hint={
                    item.pages.length > 1 ? item.pages.map((p) => p.label).join(' · ') : undefined
                  }
                  onNavigate={closeDrawer}
                />
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <Link
            to="/periods"
            className="nav-status"
            title={
              closedThrough
                ? `Books are closed through ${shortDate(closedThrough)}. Nothing can be posted on or before that date.`
                : 'No month has been closed yet.'
            }
            onClick={closeDrawer}
          >
            <Icon name="lock" size={16} />
            <span className="nav-label">
              <small>Books closed through</small>
              <strong>{closedThrough ? shortDate(closedThrough) : 'Nothing closed yet'}</strong>
            </span>
          </Link>
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
            {current?.section ? (
              <>
                <span className="crumb-section">{current.section.heading}</span>
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

        {tabs ? (
          <nav className="subnav" aria-label={`${entry.label} pages`} ref={tabsRef}>
            <div className="subnav-inner">
              <span className="subnav-title">
                <Icon name={entry.icon} size={15} />
                {entry.label}
              </span>
              {tabs.map((page) => {
                const active = page.to === current.item.to
                return (
                  <Link
                    key={page.to}
                    to={page.to}
                    className={active ? 'subnav-link active' : 'subnav-link'}
                    aria-current={active ? 'page' : undefined}
                  >
                    <Icon name={page.icon} size={15} />
                    {page.label}
                  </Link>
                )
              })}
            </div>
          </nav>
        ) : null}

        <main className="content" key={pathname}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
