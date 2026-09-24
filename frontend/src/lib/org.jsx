import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { get } from './api.js'
import { setCurrency } from './format.js'

const OrgContext = createContext(null)

/**
 * Institution-wide reference data: the settings row and the branch list.
 * Loaded once after sign-in; everything else reads it from here.
 */
export function OrgProvider({ children }) {
  const [settings, setSettings] = useState(null)
  const [branches, setBranches] = useState([])
  const [ready, setReady] = useState(false)

  const load = useCallback(async () => {
    try {
      const [config, branchList] = await Promise.all([
        get('/api/settings'),
        get('/api/branches?include_inactive=true'),
      ])
      setCurrency(config.currency)
      setSettings(config)
      setBranches(branchList)
    } catch {
      // The app still works without them; money just falls back to USD.
    } finally {
      setReady(true)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const value = useMemo(
    () => ({
      settings,
      branches,
      activeBranches: branches.filter((b) => b.is_active),
      ready,
      reload: load,
      orgName: settings?.name || 'Loan Management System',
    }),
    [settings, branches, ready, load],
  )

  return <OrgContext.Provider value={value}>{children}</OrgContext.Provider>
}

export function useOrg() {
  return useContext(OrgContext) || { settings: null, branches: [], activeBranches: [], ready: true, reload: () => {}, orgName: 'Loan Management System' }
}
