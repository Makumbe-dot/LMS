import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

// jsdom has no localStorage that survives between files reliably, and several
// modules read it at import time. A real in-memory implementation is simpler to
// reason about than a mock, and it means a test can assert what was stored.
class MemoryStorage {
  constructor() {
    this.map = new Map()
  }

  getItem(key) {
    return this.map.has(key) ? this.map.get(key) : null
  }

  setItem(key, value) {
    this.map.set(key, String(value))
  }

  removeItem(key) {
    this.map.delete(key)
  }

  clear() {
    this.map.clear()
  }
}

beforeEach(() => {
  Object.defineProperty(window, 'localStorage', {
    value: new MemoryStorage(),
    writable: true,
    configurable: true,
  })
  // Every test says what the network does. An un-stubbed fetch should be loud,
  // not a silent undefined that fails three assertions later.
  global.fetch = vi.fn(() => {
    throw new Error('fetch was called without a stub — set one up in the test')
  })
  // jsdom implements neither, and the charts and modals use them.
  window.matchMedia ||= (query) => ({
    matches: false,
    media: query,
    addEventListener() {},
    removeEventListener() {},
  })
  window.scrollTo ||= () => {}
})

afterEach(() => {
  cleanup()
})

/** A fetch stub: `jsonOnce({ ok: true, body: {...} })`, queued per call. */
export function queueFetch(...responses) {
  const queue = [...responses]
  global.fetch = vi.fn(async (url, init) => {
    const next = queue.length > 1 ? queue.shift() : queue[0]
    if (typeof next === 'function') return next(url, init)
    const { status = 200, body = {}, headers = {} } = next || {}
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: { get: (name) => ({ 'content-type': 'application/json', ...headers })[name.toLowerCase()] },
      json: async () => body,
      text: async () => JSON.stringify(body),
    }
  })
  return global.fetch
}
