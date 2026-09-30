import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { get } from './api.js'
import { setCurrency } from './format.js'

const OrgContext = createContext(null)

/**
 * Institution-wide reference data: the settings row, the branch list and how far
 * the books are closed. Loaded once after sign-in; everything else reads it here.
 */
export function OrgProvider({ children }) {
  const [settings, setSettings] = useState(null)
  const [branches, setBranches] = useState([])
  const [posting, setPosting] = useState(null)
  const [ready, setReady] = useState(false)

  const load = useCallback(async () => {
    // Two try blocks, not one: a failure fetching the posting window must not
    // lose the settings as well, or currency formatting silently falls back to
    // USD across the whole app.
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
    }
    try {
      setPosting(await get('/api/periods/status'))
    } catch {
      // Treated as nothing closed, which is what the server says when no month
      // has been closed. A date the server refuses still reports its own error.
    }
    setReady(true)
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
      closedThrough: posting?.closed_through || null,
      earliestPostableDate: posting?.earliest_postable_date || null,
    }),
    [settings, branches, posting, ready, load],
  )

  return <OrgContext.Provider value={value}>{children}</OrgContext.Provider>
}

export function useOrg() {
  return (
    useContext(OrgContext) || {
      settings: null,
      branches: [],
      activeBranches: [],
      ready: true,
      reload: () => {},
      orgName: 'Loan Management System',
      closedThrough: null,
      earliestPostableDate: null,
    }
  )
}
