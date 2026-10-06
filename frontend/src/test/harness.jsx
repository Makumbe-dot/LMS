import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { ToastProvider } from '../components/Toast.jsx'
import * as api from '../lib/api.js'
import { AuthContext, userCan } from '../lib/auth.jsx'
import { OrgContext } from '../lib/org.jsx'

const ADMIN = { id: 1, username: 'admin', full_name: 'Admin', role: 'admin' }

/**
 * Render a page with the providers it reads from, stubbed.
 *
 * The real AuthProvider and OrgProvider fetch on mount, which would make every
 * page test also a test of those two. Supplying the context values directly keeps
 * each test about the page in front of it, and lets a test say "as a teller" in one
 * argument.
 */
export function renderPage(ui, { user = ADMIN, org = {}, route = '/' } = {}) {
  const auth = {
    user,
    status: user ? 'signed-in' : 'anonymous',
    signIn: vi.fn(),
    signOut: vi.fn(),
    signOutEverywhere: vi.fn(),
    can: (...rights) => userCan(user, rights),
  }
  const orgValue = {
    settings: { name: 'Test Microfinance', currency: 'USD' },
    branches: [],
    activeBranches: [],
    ready: true,
    reload: vi.fn(),
    orgName: 'Test Microfinance',
    closedThrough: null,
    earliestPostableDate: null,
    ...org,
  }

  const result = render(
    <MemoryRouter initialEntries={[route]}>
      <AuthContext.Provider value={auth}>
        <OrgContext.Provider value={orgValue}>
          <ToastProvider>{ui}</ToastProvider>
        </OrgContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
  return { ...result, auth, org: orgValue }
}

/**
 * Stub api.get (and optionally post) by matching a fragment of the path.
 *
 * A map rather than a call-ordered queue, because a page's requests fire in
 * whatever order React schedules them and a queue makes the test depend on that.
 * A value may be a plain payload, a function, or an Error to throw.
 */
export function stubApi({ get = {}, post = {} } = {}) {
  const resolve = (map, path) => {
    for (const [fragment, value] of Object.entries(map)) {
      if (path.includes(fragment)) {
        if (value instanceof Error) throw value
        return typeof value === 'function' ? value() : value
      }
    }
    throw new Error(`no stub for ${path} (stubs: ${Object.keys(map).join(', ') || 'none'})`)
  }

  const getSpy = vi.spyOn(api, 'get').mockImplementation(async (path) => resolve(get, path))
  const postSpy = vi.spyOn(api, 'post').mockImplementation(async (path, body) => {
    for (const [fragment, value] of Object.entries(post)) {
      if (path.includes(fragment)) {
        if (value instanceof Error) throw value
        return typeof value === 'function' ? value(body, path) : value
      }
    }
    return {}
  })
  return { getSpy, postSpy }
}

export { ADMIN }
// Users holding the rights the old loan officer, teller and viewer roles carried.
export const OFFICER = {
  id: 2,
  username: 'officer',
  full_name: 'Officer',
  role: 'user',
  rights: ['borrowers', 'loans', 'approve', 'disburse', 'cash', 'reverse', 'supervise', 'messages'],
}
export const TELLER = { id: 3, username: 'teller', full_name: 'Teller', role: 'user', rights: ['cash'] }
export const VIEWER = { id: 4, username: 'viewer', full_name: 'Viewer', role: 'user', rights: [] }
