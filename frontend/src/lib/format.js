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

/** Money in the organisation's currency, or in `code` when a loan is kept in another. */
export const money = (value, code) => `${code || currency} ${fmt(value)}`

/** 12.5 -> "12.50%"; null/blank -> "-", not "-%". */
export const pct = (value) => (num(value) === null ? '-' : `${fmt(value)}%`)

/** "reducing" -> "Reducing balance" */
export const rateMethodLabel = (value) =>
  value === 'flat' ? 'Flat rate' : value === 'reducing' ? 'Reducing balance' : '-'

const TERM_UNITS = { monthly: 'months', fortnightly: 'fortnights', weekly: 'weeks' }
const TERM_SHORT = { monthly: 'm', fortnightly: 'fn', weekly: 'w' }

/**
 * The unit a term is counted in. A loan's term is a number of instalments, so a
 * weekly loan's "16" is sixteen weeks; anything missing is an older monthly loan.
 */
export const termUnit = (frequency) => TERM_UNITS[frequency] || 'months'

/** 16, "weekly" -> "16w"; 6, "monthly" -> "6m" */
export const termShort = (term, frequency) => `${term}${TERM_SHORT[frequency] || 'm'}`

/** "weekly" -> "Weekly" */
export const frequencyLabel = (frequency) =>
  ({ monthly: 'Monthly', fortnightly: 'Fortnightly', weekly: 'Weekly' })[frequency] || 'Monthly'

export const bytes = (value) => {
  const n = Number(value) || 0
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  if (n >= 1024) return `${Math.round(n / 1024)} KB`
  return `${n} B`
}

/** Compact money for axis ticks and bar labels: 625, 1.3k, 12k, 1.2m */
export function compact(value) {
  const n = num(value) ?? 0
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}m`
  if (abs >= 10_000) return `${Math.round(n / 1_000)}k`
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, '')}k`
  return String(Math.round(n))
}

/**
 * Money short enough for a headline: "USD 2.86M", "USD 94.3K", "USD 845".
 * The full figure belongs beside it (a title, a tooltip), never instead of it.
 */
export function moneyShort(value, code) {
  const n = num(value) ?? 0
  const abs = Math.abs(n)
  const trim = (s) => s.replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1')
  let text
  if (abs >= 1_000_000_000) text = `${trim((n / 1_000_000_000).toFixed(2))}B`
  else if (abs >= 1_000_000) text = `${trim((n / 1_000_000).toFixed(2))}M`
  else if (abs >= 10_000) text = `${trim((n / 1_000).toFixed(1))}K`
  else text = Math.round(n).toLocaleString('en-US')
  return `${code || currency} ${text}`
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

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * "2026-04" -> "Apr", for a month axis. The year is added where it changes:
 * on the first label and on each January, so a twelve-month run reads
 * "Nov 25 · Dec · Jan 26 · Feb …" rather than twelve "26-04"s.
 */
export function monthLabel(yearMonth, { first = false } = {}) {
  const [year, month] = String(yearMonth).split('-').map(Number)
  const name = MONTHS[month - 1] || String(yearMonth)
  return first || month === 1 ? `${name} ${String(year).slice(2)}` : name
}

/** "2026-04" -> "April 2026", for a tooltip or a sentence. */
export function monthName(yearMonth) {
  const [year, month] = String(yearMonth).split('-').map(Number)
  const date = new Date(year, (month || 1) - 1, 1)
  return date.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
}

/** Add months to an ISO date string, clamping to month end. */
export function addMonthsIso(iso, months) {
  const [y, m, d] = iso.split('-').map(Number)
  const target = new Date(Date.UTC(y, m - 1 + months, 1))
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate()
  target.setUTCDate(Math.min(d, lastDay))
  return target.toISOString().slice(0, 10)
}
