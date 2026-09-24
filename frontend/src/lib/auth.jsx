import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import * as api from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [status, setStatus] = useState(api.getToken() ? 'loading' : 'anonymous')

  const signOut = useCallback(() => {
    api.setToken(null)
    setUser(null)
    setStatus('anonymous')
  }, [])

  // A 401 from anywhere in the app drops straight back to the sign-in screen.
  useEffect(() => {
    api.setUnauthorizedHandler(signOut)
  }, [signOut])

  // Resume an existing session on a page reload.
  useEffect(() => {
    if (!api.getToken()) return
    let live = true
    api
      .get('/api/auth/me')
      .then((me) => {
        if (!live) return
        setUser(me)
        setStatus('signed-in')
      })
      .catch(() => {
        if (live) signOut()
      })
    return () => {
      live = false
    }
  }, [signOut])

  const signIn = useCallback(async (username, password) => {
    const data = await api.login(username, password)
    api.setToken(data.access_token)
    setUser(data.user)
    setStatus('signed-in')
    return data.user
  }, [])

  const value = useMemo(
    () => ({
      user,
      status,
      signIn,
      signOut,
      /** can('admin', 'loan_officer') - true when the signed-in role is one of these. */
      can: (...roles) => Boolean(user && roles.includes(user.role)),
    }),
    [user, status, signIn, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
