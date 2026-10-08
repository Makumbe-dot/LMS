/* The single place that talks to Django. Everything else calls get/post/patch/del. */

const BASE = import.meta.env.VITE_API_BASE || ''
const TOKEN_KEY = 'lms_token'
const REFRESH_KEY = 'lms_refresh'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY))
export const getRefreshToken = () => localStorage.getItem(REFRESH_KEY)
export const setRefreshToken = (t) =>
  t ? localStorage.setItem(REFRESH_KEY, t) : localStorage.removeItem(REFRESH_KEY)

/** Store both halves of a token pair, or clear them. */
export function setTokens(pair) {
  setToken(pair?.access_token || null)
  setRefreshToken(pair?.refresh_token || null)
}

/** Called when the session is over for good. AuthProvider sets this. */
let onUnauthorized = () => {}
export const setUnauthorizedHandler = (fn) => (onUnauthorized = fn)

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * The in-flight refresh, if any.
 *
 * Single-flight on purpose. A dashboard fires six requests at once, so when the
 * access token expires all six come back 401 together. Without this they would
 * each POST /api/auth/refresh, and because a refresh token is good for exactly one
 * use — the server rotates and revokes it — the first would succeed and the other
 * five would be rejected as replays, signing the user out. They all await the same
 * promise instead.
 */
let refreshing = null

async function refreshSession() {
  const token = getRefreshToken()
  if (!token) return null
  if (refreshing) return refreshing

  refreshing = (async () => {
    try {
      const res = await fetch(`${BASE}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: token }),
      })
      if (!res.ok) return null
      const pair = await res.json()
      setTokens(pair)
      return pair
    } catch {
      return null
    } finally {
      // Cleared synchronously. JavaScript is single-threaded, so nothing can
      // observe `refreshing` between this promise settling and the clear: a
      // request whose 401 arrives after this point SHOULD start a new refresh,
      // because by then the new token is already stored and the old one spent.
      refreshing = null
    }
  })()
  return refreshing
}

async function send(path, { method, headers, body, signal }) {
  try {
    return await fetch(BASE + path, { method, headers, signal, body })
  } catch (err) {
    if (err.name === 'AbortError') throw err
    throw new ApiError('Cannot reach the server. Is the Django backend running?', 0)
  }
}

async function request(path, { method = 'GET', body, signal, retry = true } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const payload = body === undefined ? undefined : JSON.stringify(body)

  let res = await send(path, { method, headers, body: payload, signal })

  if (res.status === 401 && retry && getRefreshToken()) {
    // The access token has probably just expired. Renew once and replay; a second
    // 401 means the session is genuinely over.
    const pair = await refreshSession()
    if (pair?.access_token) {
      res = await send(path, {
        method,
        headers: { ...headers, Authorization: `Bearer ${pair.access_token}` },
        body: payload,
        signal,
      })
    }
  }

  if (res.status === 401) {
    onUnauthorized()
    throw new ApiError('Your session has expired. Please sign in again.', 401)
  }
  if (res.status === 204) return null

  const isJson = (res.headers.get('content-type') || '').includes('json')
  const data = isJson ? await res.json() : await res.text()
  if (!res.ok) {
    const detail = data && typeof data === 'object' ? data.detail : null
    throw new ApiError(detail || res.statusText || 'Request failed', res.status)
  }
  return data
}

export const get = (path, opts) => request(path, opts)
export const post = (path, body) => request(path, { method: 'POST', body: body ?? {} })
export const patch = (path, body) => request(path, { method: 'PATCH', body })
export const put = (path, body) => request(path, { method: 'PUT', body })
export const del = (path) => request(path, { method: 'DELETE' })

/**
 * Fetch an HTML document with the bearer token and open it in a new tab.
 * Used for the printable loan agreement, which a plain link could not
 * authenticate.
 */
export async function openHtml(path, title = 'Document') {
  const res = await fetch(BASE + path, { headers: { Authorization: `Bearer ${getToken()}` } })
  if (!res.ok) {
    const isJson = (res.headers.get('content-type') || '').includes('json')
    const data = isJson ? await res.json() : null
    throw new ApiError(data?.detail || 'Could not open the document', res.status)
  }
  const html = await res.text()
  const tab = window.open('', '_blank')
  if (!tab) throw new ApiError('Your browser blocked the popup. Allow popups for this site.', 0)
  tab.document.open()
  tab.document.write(html)
  tab.document.close()
  tab.document.title = title
  return tab
}

/** POST multipart/form-data — file uploads and the CSV import. */
export async function postForm(path, formData) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let res
  try {
    res = await fetch(BASE + path, { method: 'POST', headers, body: formData })
  } catch {
    throw new ApiError('Cannot reach the server. Is the Django backend running?', 0)
  }
  if (res.status === 401 && getRefreshToken()) {
    // Worth retrying here especially: an upload is the request a user least wants
    // to lose to an expired token, and the FormData is still in hand.
    const pair = await refreshSession()
    if (pair?.access_token) {
      res = await fetch(BASE + path, {
        method: 'POST',
        headers: { Authorization: `Bearer ${pair.access_token}` },
        body: formData,
      })
    }
  }
  if (res.status === 401) {
    onUnauthorized()
    throw new ApiError('Your session has expired. Please sign in again.', 401)
  }
  const isJson = (res.headers.get('content-type') || '').includes('json')
  const data = isJson ? await res.json() : await res.text()
  if (!res.ok) {
    const detail = data && typeof data === 'object' ? data.detail : null
    throw new ApiError(detail || res.statusText || 'Upload failed', res.status)
  }
  return data
}

/** Paginated endpoints answer with an envelope; plain lists answer with an array. */
export const rowsOf = (data) => (Array.isArray(data) ? data : data?.results ?? [])

/** Build a query string, dropping empty values. */
export function qs(params) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const str = search.toString()
  return str ? `?${str}` : ''
}

/**
 * Download a report or statement as a file: fmt is 'csv', 'xlsx' or 'pdf'. Reuses
 * the bearer token (renewing it if it has expired), and names the file as the
 * server does.
 */
export async function downloadFile(path, fmt = 'csv', fallbackName = 'export') {
  const url = BASE + path + (path.includes('?') ? '&' : '?') + `fmt=${fmt}`
  const fetchIt = () => fetch(url, { headers: { Authorization: `Bearer ${getToken()}` } })
  let res = await fetchIt()
  if (res.status === 401 && getRefreshToken() && (await refreshSession())) res = await fetchIt()
  if (!res.ok) {
    const isJson = (res.headers.get('content-type') || '').includes('json')
    const data = isJson ? await res.json() : null
    throw new ApiError(data?.detail || 'Download failed', res.status)
  }
  const blob = await res.blob()
  const disposition = res.headers.get('content-disposition') || ''
  const match = disposition.match(/filename="?([^";]+)"?/)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = match ? match[1] : `${fallbackName}.${fmt}`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(link.href)
}

/** Download a report as CSV. */
export const downloadCsv = (path, fallbackName = 'export') =>
  downloadFile(path, 'csv', fallbackName)

/**
 * POST and save the reply as a file: for an action that both produces a file and
 * changes something (a bank payment file marks its payments sent).
 */
export async function postDownload(path, body = {}, fallbackName = 'download') {
  const fetchIt = () =>
    fetch(BASE + path, {
      method: 'POST',
      headers: { Authorization: `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  let res = await fetchIt()
  if (res.status === 401 && getRefreshToken() && (await refreshSession())) res = await fetchIt()
  if (!res.ok) {
    const isJson = (res.headers.get('content-type') || '').includes('json')
    const data = isJson ? await res.json() : null
    throw new ApiError(data?.detail || 'Download failed', res.status)
  }
  const blob = await res.blob()
  const match = (res.headers.get('content-disposition') || '').match(/filename="?([^";]+)"?/)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = match ? match[1] : fallbackName
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(link.href)
}

async function signInRequest(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const isJson = (res.headers.get('content-type') || '').includes('json')
  const data = isJson ? await res.json() : {}
  if (!res.ok) throw new ApiError(data.detail || 'Sign in failed', res.status)
  return data
}

/**
 * Login posts JSON and returns { access_token, refresh_token, user } - or, for an
 * account with two-factor sign-in on, { mfa_required: true, mfa_token } and no
 * session until loginVerify is called with a code.
 */
export const login = (username, password) =>
  signInRequest('/api/auth/login', { username, password })

/** The second step: the token from login and the code from the authenticator app. */
export const loginVerify = (mfaToken, code) =>
  signInRequest('/api/auth/login/verify', { mfa_token: mfaToken, code })
