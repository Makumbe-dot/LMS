import { afterEach, describe, expect, it } from 'vitest'

import {
  closedDrawerOptions,
  currencyOf,
  currencyOptions,
  isForeign,
  openDrawers,
  revaluedBalance,
  revaluedKind,
} from './currency.js'
import { setCurrency } from './format.js'

/**
 * Savings, facilities and tills store the organisation's currency as blank and
 * any other by its code. A slip here labels a ZWG balance in dollars, or offers a
 * currency the server will refuse for want of a rate.
 */

afterEach(() => setCurrency('USD'))

const LISTING = {
  base_currency: 'USD',
  currencies: [
    { code: 'ZWG', rate: '0.040000', rate_date: '2026-01-01' },
    { code: 'ZAR', rate: null, rate_date: null },
  ],
}

describe('currencyOf and isForeign', () => {
  it('reads blank as the organisation currency', () => {
    setCurrency('USD')
    expect(currencyOf('')).toBe('USD')
    expect(currencyOf(null)).toBe('USD')
    expect(currencyOf('zwg')).toBe('ZWG')
    expect(isForeign('')).toBe(false)
    expect(isForeign('USD')).toBe(false)
    expect(isForeign('ZWG')).toBe(true)
  })
})

describe('currencyOptions', () => {
  it('offers the base as blank, then only currencies with a rate', () => {
    const options = currencyOptions(LISTING)
    expect(options.map((o) => o.value)).toEqual(['', 'ZWG'])
    expect(options[0].label).toMatch(/^USD/)
  })

  it('keeps the current currency even without a rate, so an edit never changes it', () => {
    expect(currencyOptions(LISTING, 'ZAR').map((o) => o.value)).toEqual(['', 'ZWG', 'ZAR'])
    expect(currencyOptions(LISTING, 'USD').map((o) => o.value)).toEqual(['', 'ZWG'])
  })

  it('still offers the base before the listing has loaded', () => {
    expect(currencyOptions(null).map((o) => o.value)).toEqual([''])
  })
})

describe('drawers', () => {
  const usd = { id: 1, currency: '' }
  const zwg = { id: 2, currency: 'ZWG' }

  it('lists every open drawer, the base first', () => {
    expect(openDrawers({ till: usd, tills: [zwg, usd] })).toEqual([usd, zwg])
  })

  it('falls back on the single till an older payload sends', () => {
    expect(openDrawers({ till: usd })).toEqual([usd])
    expect(openDrawers({ till: null })).toEqual([])
    expect(openDrawers(null)).toEqual([])
  })

  it('offers only the currencies with no drawer open', () => {
    const options = currencyOptions(LISTING)
    expect(closedDrawerOptions(options, [usd]).map((o) => o.value)).toEqual(['ZWG'])
    expect(closedDrawerOptions(options, [usd, zwg])).toEqual([])
  })
})

describe('revaluation lines', () => {
  it('reads the balance each kind restates, in its own currency', () => {
    expect(
      revaluedBalance({
        kind: 'loan',
        principal_outstanding: '100.10',
        penalties_outstanding: '0.20',
        charges_outstanding: '0.30',
      }),
    ).toBe(100.6)
    expect(revaluedBalance({ kind: 'savings', savings_balance: '1212.00' })).toBe(1212)
    expect(revaluedBalance({ kind: 'facility', borrowings: '50000.00', borrowing_interest: '500.00' })).toBe(
      50500,
    )
  })

  it('treats a line without a kind as a loan', () => {
    expect(revaluedKind({})).toBe('Loan')
    expect(revaluedKind({ kind: 'facility' })).toBe('Facility')
    expect(revaluedBalance({ principal_outstanding: '5' })).toBe(5)
  })
})
