import { getCurrency } from './format.js'

/*
 * Currencies on savings, facilities and tills. A product, a facility and a till
 * store the organisation's currency as blank and any other by its code; an
 * account or a loan may store the organisation's by its code. These read both.
 */

/** The code a balance is in: `code`, or the organisation's when it is blank. */
export const currencyOf = (code) => (code ? String(code).toUpperCase() : getCurrency())

/** Whether `code` names another currency than the organisation's. */
export const isForeign = (code) => Boolean(code) && currencyOf(code) !== getCurrency()

/**
 * The choices for a currency select, from the /api/currencies payload: the
 * organisation's own first (stored as blank), then every other currency that has
 * a rate, because the server refuses one without. `current` stays in the list
 * even without a rate, so editing never silently changes it.
 */
export function currencyOptions(listing, current = '') {
  const base = listing?.base_currency || getCurrency()
  const options = [{ value: '', label: `${base} (the organisation's currency)` }]
  const seen = new Set([base])
  for (const c of listing?.currencies || []) {
    if (seen.has(c.code) || (!c.rate && c.code !== current)) continue
    seen.add(c.code)
    options.push({ value: c.code, label: c.rate ? `${c.code} at ${c.rate} ${base}` : c.code })
  }
  const kept = current && current !== base ? String(current).toUpperCase() : ''
  if (kept && !seen.has(kept)) options.push({ value: kept, label: kept })
  return options
}

/**
 * The drawers a teller has open, from /api/tills/current: the list when the
 * server sends one, else the single organisation-currency till it always sends.
 * The organisation's currency first, then the others by code.
 */
export function openDrawers(payload) {
  const list = payload?.tills ?? (payload?.till ? [payload.till] : [])
  return [...list].sort((a, b) => (a.currency || '').localeCompare(b.currency || ''))
}

const KINDS = { loan: 'Loan', savings: 'Savings', facility: 'Facility' }

/** What a revaluation line restates, by name; a line from before savings and
 * facilities joined the run is a loan. */
export const revaluedKind = (line) => KINDS[line.kind || 'loan']

/**
 * The balance a revaluation line restates, in its own currency: what a loan
 * has outstanding, what a member holds, or what a funder is owed with the
 * interest accrued on it.
 */
export function revaluedBalance(line) {
  const n = (v) => Number(v) || 0
  const total =
    line.kind === 'savings'
      ? n(line.savings_balance)
      : line.kind === 'facility'
        ? n(line.borrowings) + n(line.borrowing_interest)
        : n(line.principal_outstanding) + n(line.penalties_outstanding) + n(line.charges_outstanding)
  return Math.round(total * 100) / 100
}

/** The currencies with no drawer open yet, of those a teller may open one in. */
export function closedDrawerOptions(options, drawers) {
  const open = new Set(drawers.map((d) => d.currency || ''))
  return options.filter((o) => !open.has(o.value))
}
