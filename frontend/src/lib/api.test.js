import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from './api.js'

/** A fetch stub driven by a list of handlers, one per call, last one repeating. */
function respond(...handlers) {
  const queue = [...handlers]
  const calls = []
  global.fetch = vi.fn(async (url, init = {}) => {
    calls.push({ url, method: init.method || 'GET', headers: init.headers || {}, body: init.body })
    const handler = queue.length > 1 ? queue.shift() : queue[0]
    const { status = 200, body = {} } = typeof handler === 'function'
      ? handler(url, init)
      : handler
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: { get: () => 'application/json' },
      json: async () => body,
      text: async () => JSON.stringify(body),
    }
  })
  return calls
}

const refreshCalls = (calls) => calls.filter((c) => c.url.includes('/api/auth/refresh'))

describe('request', () => {
  beforeEach(() => {
    api.setTokens({ access_token: 'old-access', refresh_token: 'the-refresh' })
    api.setUnauthorizedHandler(() => {})
  })

  it('sends the bearer token', async () => {
    const calls = respond({ body: { ok: 1 } })
    await api.get('/api/thing')
    expect(calls[0].headers.Authorization).toBe('Bearer old-access')
  })

  it('renews an expired access token and replays the request', async () => {
    const calls = respond(
      { status: 401 },
      { body: { access_token: 'new-access', refresh_token: 'new-refresh' } },
      { body: { value: 42 } },
    )

    await expect(api.get('/api/thing')).resolves.toEqual({ value: 42 })

    expect(refreshCalls(calls)).toHaveLength(1)
    // The replay carries the NEW token, not the one that just failed.
    expect(calls[2].headers.Authorization).toBe('Bearer new-access')
    // And the new pair is stored, so the next request does not 401 again.
    expect(api.getToken()).toBe('new-access')
    expect(api.getRefreshToken()).toBe('new-refresh')
  })

  it('refreshes ONCE for a burst of simultaneous 401s', async () => {
    // This is the whole reason the single-flight guard exists. A dashboard fires
    // several requests at once; rotation means the refresh token is good for one
    // use, so six parallel refreshes would see one succeed and five rejected as
    // replays — signing the user out for loading a page.
    let refreshed = false
    const calls = respond((url) => {
      if (url.includes('/api/auth/refresh')) {
        if (refreshed) return { status: 401, body: { detail: 'replayed' } }
        refreshed = true
        return { body: { access_token: 'new-access', refresh_token: 'new-refresh' } }
      }
      return refreshed ? { body: { ok: true } } : { status: 401 }
    })

    const results = await Promise.all([
      api.get('/api/a'),
      api.get('/api/b'),
      api.get('/api/c'),
      api.get('/api/d'),
      api.get('/api/e'),
      api.get('/api/f'),
    ])

    expect(results).toHaveLength(6)
    expect(refreshCalls(calls)).toHaveLength(1)
    results.forEach((r) => expect(r).toEqual({ ok: true }))
  })

  it('gives up and reports the session over when the refresh also fails', async () => {
    const onUnauthorized = vi.fn()
    api.setUnauthorizedHandler(onUnauthorized)
    respond({ status: 401 }, { status: 401, body: { detail: 'signed out' } })

    await expect(api.get('/api/thing')).rejects.toThrow(/session has expired/)
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('does not try to refresh when there is no refresh token', async () => {
    api.setTokens({ access_token: 'only-access' })
    const onUnauthorized = vi.fn()
    api.setUnauthorizedHandler(onUnauthorized)
    const calls = respond({ status: 401 })

    await expect(api.get('/api/thing')).rejects.toThrow(/session has expired/)
    expect(refreshCalls(calls)).toHaveLength(0)
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('does not retry a request twice', async () => {
    // A refresh that succeeds but whose replay still 401s must stop, not loop.
    const calls = respond((url) =>
      url.includes('/api/auth/refresh')
        ? { body: { access_token: 'new-access', refresh_token: 'new-refresh' } }
        : { status: 401 },
    )
    await expect(api.get('/api/thing')).rejects.toThrow(/session has expired/)
    expect(refreshCalls(calls)).toHaveLength(1)
    expect(calls).toHaveLength(3) // original, refresh, one replay
  })

  it('surfaces the server detail on a business-rule failure', async () => {
    respond({ status: 400, body: { detail: 'Amount exceeds the outstanding balance' } })
    await expect(api.post('/api/loans/1/repayments', { amount: '1' }))
      .rejects.toThrow('Amount exceeds the outstanding balance')
  })

  it('passes a 409 through with its status, so a closed period is distinguishable', async () => {
    respond({ status: 409, body: { detail: 'falls in a closed accounting period' } })
    await api.post('/api/x').catch((err) => {
      expect(err.status).toBe(409)
      expect(err.message).toMatch(/closed accounting period/)
    })
  })

  it('reports an unreachable server rather than a cryptic fetch error', async () => {
    global.fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    await expect(api.get('/api/thing')).rejects.toThrow(/Cannot reach the server/)
  })

  it('lets an aborted request through as an abort', async () => {
    global.fetch = vi.fn(async () => {
      const err = new Error('aborted')
      err.name = 'AbortError'
      throw err
    })
    await expect(api.get('/api/thing')).rejects.toThrow('aborted')
  })

  it('returns null for 204 rather than trying to parse a body', async () => {
    respond({ status: 204 })
    await expect(api.del('/api/thing/1')).resolves.toBeNull()
  })
})

describe('setTokens', () => {
  it('stores and clears both halves together', () => {
    api.setTokens({ access_token: 'a', refresh_token: 'r' })
    expect(api.getToken()).toBe('a')
    expect(api.getRefreshToken()).toBe('r')

    api.setTokens(null)
    expect(api.getToken()).toBeNull()
    expect(api.getRefreshToken()).toBeNull()
  })

  it('survives a storage that throws, as a private window can', () => {
    Object.defineProperty(window, 'localStorage', {
      value: {
        getItem: () => null,
        setItem: () => {
          throw new Error('blocked')
        },
        removeItem: () => {},
      },
      writable: true,
      configurable: true,
    })
    // Documents current behaviour: storage failures are NOT swallowed here, so a
    // blocked-storage browser fails loudly at sign-in rather than silently
    // appearing signed in with nothing stored.
    expect(() => api.setTokens({ access_token: 'a', refresh_token: 'r' })).toThrow()
  })
})

describe('qs', () => {
  it('drops empty values so a blank filter is not sent as filter=', () => {
    expect(api.qs({ page: 2, q: '', branch_id: null, status: 'active', zero: 0 }))
      .toBe('?page=2&status=active&zero=0')
  })

  it('returns an empty string when everything is empty', () => {
    expect(api.qs({ a: '', b: null, c: undefined })).toBe('')
  })
})

describe('rowsOf', () => {
  it('reads a paginated envelope and a bare array the same way', () => {
    expect(api.rowsOf({ results: [1, 2] })).toEqual([1, 2])
    expect(api.rowsOf([3, 4])).toEqual([3, 4])
    expect(api.rowsOf(null)).toEqual([])
    expect(api.rowsOf(undefined)).toEqual([])
  })
})
