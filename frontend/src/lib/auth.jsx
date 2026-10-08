import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import * as api from './api'

// Exported so a test can render a page as a given role without the provider
// fetching anything. Application code should use useAuth().
export const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [status, setStatus] = useState(api.getToken() ? 'loading' : 'anonymous')

  /** Drop the local session. Used when the server has already refused us. */
  const forget = useCallback(() => {
    api.setTokens(null)
    setUser(null)
    setStatus('anonymous')
  }, [])

  /**
   * Sign out properly: tell the server to retire the refresh token, then forget it.
   *
   * The call is awaited but its failure is ignored — a network error must not
   * leave someone stuck signed in at a shared counter machine. The server-side
   * revocation is what makes the refresh token unusable afterwards; clearing
   * localStorage alone would leave a working token on the device.
   */
  const signOut = useCallback(async () => {
    const refreshToken = api.getRefreshToken()
    if (refreshToken) {
      try {
        await api.post('/api/auth/logout', { refresh_token: refreshToken })
      } catch {
        // Already expired, or the server is unreachable. Sign out regardless.
      }
    }
    forget()
  }, [forget])

  /** End every session on every device. */
  const signOutEverywhere = useCallback(async () => {
    try {
      await api.post('/api/auth/sign-out-everywhere')
    } finally {
      forget()
    }
  }, [forget])

  // A 401 that survived a token refresh means the session is over for good.
  useEffect(() => {
    api.setUnauthorizedHandler(forget)
  }, [forget])

  // Resume an existing session on a page reload. The access token has very likely
  // expired by now — it only lasts half an hour — so this relies on api.js
  // renewing it transparently rather than bouncing to the sign-in screen.
  useEffect(() => {
    if (!api.getToken() && !api.getRefreshToken()) return
    let live = true
    api
      .get('/api/auth/me')
      .then((me) => {
        if (!live) return
        setUser(me)
        setStatus('signed-in')
      })
      .catch(() => {
        if (live) forget()
      })
    return () => {
      live = false
    }
  }, [forget])

  const accept = useCallback((data) => {
    api.setTokens(data)
    setUser(data.user)
    setStatus('signed-in')
    return data.user
  }, [])

  /**
   * Resolves to the user, or - when the account has two-factor sign-in on - to
   * { mfaToken } with nobody signed in yet. The caller then asks for the code and
   * finishes with completeSignIn.
   */
  const signIn = useCallback(
    async (username, password) => {
      const data = await api.login(username, password)
      if (data.mfa_required) return { mfaToken: data.mfa_token }
      return accept(data)
    },
    [accept],
  )

  const completeSignIn = useCallback(
    async (mfaToken, code) => accept(await api.loginVerify(mfaToken, code)),
    [accept],
  )

  const value = useMemo(
    () => ({
      user,
      status,
      signIn,
      completeSignIn,
      signOut,
      signOutEverywhere,
      /** Replace the signed-in user's details, e.g. after turning two-factor on. */
      updateUser: setUser,
      /** can('approve', 'disburse') - true when the signed-in user holds any of these
          access rights. An administrator holds them all; can('admin') is true for an
          administrator alone. */
      can: (...rights) => userCan(user, rights),
    }),
    [user, status, signIn, completeSignIn, signOut, signOutEverywhere],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Whether a user holds any of the named access rights ('admin' meaning an
    administrator). Exported so tests answer can() exactly as the app does. */
export function userCan(user, rights) {
  if (!user) return false
  if (user.role === 'admin') return true
  return rights.some((right) => right !== 'admin' && (user.rights || []).includes(right))
}

/** "Administrator" or "User", for the account menu and My account. */
export function roleLabel(user) {
  if (!user) return ''
  return user.role === 'admin' ? 'Administrator' : 'User'
}

/** A user's access rights by name, from the /api/users/rights catalogue. */
export function rightsLabel(user, catalogue) {
  if (!user?.rights?.length) return 'Read only'
  const names = Object.fromEntries((catalogue?.rights || []).map((r) => [r.code, r.label]))
  return user.rights.map((code) => names[code] || code).join(', ')
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
