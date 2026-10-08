/* The borrower portal's own line to Django. It carries a portal token, never the
   staff tokens, and keeps it in sessionStorage so closing the tab signs out. */

import { ApiError } from '../lib/api.js'

const BASE = import.meta.env.VITE_API_BASE || ''
const KEY = 'lms_portal_token'

const read = () => {
  try {
    return sessionStorage.getItem(KEY)
  } catch {
    return null
  }
}

export const getPortalToken = read

export function setPortalToken(token) {
  try {
    if (token) sessionStorage.setItem(KEY, token)
    else sessionStorage.removeItem(KEY)
  } catch {
    /* private mode: the session lasts as long as the page */
  }
}

let onSignedOut = () => {}
export const setPortalSignedOutHandler = (fn) => (onSignedOut = fn)

async function call(path, { method = 'GET', body } = {}) {
  const headers = {}
  const token = read()
  if (token) headers.Authorization = `Portal ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('Cannot reach the server. Please try again shortly.', 0)
  }
  if (res.status === 401 && token) {
    setPortalToken(null)
    onSignedOut()
  }
  if (res.status === 204) return null
  const isJson = (res.headers.get('content-type') || '').includes('json')
  const data = isJson ? await res.json() : await res.text()
  if (!res.ok) {
    throw new ApiError((data && data.detail) || 'Something went wrong', res.status)
  }
  return data
}

export const portalGet = (path) => call(path)
export const portalPost = (path, body) => call(path, { method: 'POST', body: body ?? {} })

/** Fetch a document with the portal token and hand it to the browser. */
export async function portalOpen(path, { download, title } = {}) {
  const res = await fetch(BASE + path, { headers: { Authorization: `Portal ${read()}` } })
  if (!res.ok) {
    const isJson = (res.headers.get('content-type') || '').includes('json')
    const data = isJson ? await res.json() : null
    throw new ApiError(data?.detail || 'Could not open the document', res.status)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  if (download) {
    const link = document.createElement('a')
    link.href = url
    link.download = download
    document.body.appendChild(link)
    link.click()
    link.remove()
  } else {
    const opened = window.open(url, '_blank')
    if (opened && title) opened.document.title = title
  }
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}
