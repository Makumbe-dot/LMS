import { useCallback, useEffect, useState } from 'react'

import { get } from './api.js'

/**
 * Load a GET endpoint, with loading / error state and a reload().
 * Pass `path = null` to skip the request.
 *
 * `data` is kept while a reload or a new path is loading, so a page can go on
 * showing what it has instead of blanking; `loadedPath` is the path that data
 * came from, for a page that wants to mark it as about to change.
 */
export function useApi(path) {
  const [data, setData] = useState(null)
  const [loadedPath, setLoadedPath] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(Boolean(path))
  const [tick, setTick] = useState(0)

  const reload = useCallback(() => setTick((n) => n + 1), [])

  useEffect(() => {
    if (!path) {
      setData(null)
      setLoadedPath(null)
      setLoading(false)
      return
    }
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    get(path, { signal: controller.signal })
      .then((result) => {
        setData(result)
        setLoadedPath(path)
        setLoading(false)
      })
      .catch((err) => {
        if (err.name === 'AbortError') return
        setError(err)
        setLoading(false)
      })
    return () => controller.abort()
  }, [path, tick])

  return { data, error, loading, reload, setData, loadedPath }
}

/** Debounce a value - used for search boxes so each keystroke is not a request. */
export function useDebounced(value, delay = 300) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}
