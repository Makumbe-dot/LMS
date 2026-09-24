/* The single place that talks to Django. Everything else calls get/post/patch/del. */

const BASE = import.meta.env.VITE_API_BASE || ''
const TOKEN_KEY = 'lms_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY))

/** Called when the API reports the session is no longer valid. AuthProvider sets this. */
let onUnauthorized = () => {}
export const setUnauthorizedHandler = (fn) => (onUnauthorized = fn)

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request(path, { method = 'GET', body, signal } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let res
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (err) {
    if (err.name === 'AbortError') throw err
    throw new ApiError('Cannot reach the server. Is the Django backend running?', 0)
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

/** Download a report as CSV, reusing the bearer token. */
export async function downloadCsv(path, fallbackName = 'export') {
  const url = BASE + path + (path.includes('?') ? '&' : '?') + 'fmt=csv'
  const res = await fetch(url, { headers: { Authorization: `Bearer ${getToken()}` } })
  if (!res.ok) throw new ApiError('Export failed', res.status)
  const blob = await res.blob()
  const disposition = res.headers.get('content-disposition') || ''
  const match = disposition.match(/filename="?([^";]+)"?/)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = match ? match[1] : `${fallbackName}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(link.href)
}

/** Login posts JSON and returns { access_token, user }. */
export async function login(username, password) {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const isJson = (res.headers.get('content-type') || '').includes('json')
  const data = isJson ? await res.json() : {}
  if (!res.ok) throw new ApiError(data.detail || 'Sign in failed', res.status)
  return data
}
