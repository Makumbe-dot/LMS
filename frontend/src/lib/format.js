/* Display formatting. Money arrives from the API as a decimal string; it is
   only ever converted to Number for display, never for arithmetic that matters. */

/* The currency comes from organisation settings at sign-in. It is held here
   rather than threaded through every component, because every money format in
   the app wants it and it changes once a year at most. */
let currency = 'USD'

export const setCurrency = (code) => {
  currency = code || 'USD'
}
export const getCurrency = () => currency

export function num(value) {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** 1234.5 -> "1,234.50"; null/blank -> "-" */
export function fmt(value) {
  const n = num(value)
  if (n === null) return '-'
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export const money = (value) => `${currency} ${fmt(value)}`

/** 12.5 -> "12.50%"; null/blank -> "-", not "-%". */
export const pct = (value) => (num(value) === null ? '-' : `${fmt(value)}%`)

/** "reducing" -> "Reducing balance" */
export const rateMethodLabel = (value) =>
  value === 'flat' ? 'Flat rate' : value === 'reducing' ? 'Reducing balance' : '-'

export const bytes = (value) => {
  const n = Number(value) || 0
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  if (n >= 1024) return `${Math.round(n / 1024)} KB`
  return `${n} B`
}

/** Compact money for axis ticks: 12.5k, 1.2m */
export function compact(value) {
  const n = num(value) ?? 0
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`
  if (abs >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(Math.round(n))
}

export const today = () => new Date().toISOString().slice(0, 10)
export const firstOfMonth = () => `${today().slice(0, 8)}01`

/** "2026-03-25T08:30:00Z" -> "2026-03-25" */
export const dateOnly = (value) => (value ? String(value).slice(0, 10) : '-')

/** "2026-03-25T08:30:00Z" -> "2026-03-25 08:30:00" */
export const dateTime = (value) =>
  value ? String(value).replace('T', ' ').slice(0, 19) : '-'

/** "written_off" -> "written off" */
export const humanise = (value) => String(value ?? '').replace(/_/g, ' ')

/** Add months to an ISO date string, clamping to month end. */
export function addMonthsIso(iso, months) {
  const [y, m, d] = iso.split('-').map(Number)
  const target = new Date(Date.UTC(y, m - 1 + months, 1))
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate()
  target.setUTCDate(Math.min(d, lastDay))
  return target.toISOString().slice(0, 10)
}
