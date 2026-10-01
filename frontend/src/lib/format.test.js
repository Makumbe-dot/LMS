import { describe, expect, it } from 'vitest'

import { dateOnly, fmt, humanise, money, num, pct, setCurrency, today } from './format.js'

describe('num', () => {
  it('reads the decimal STRINGS the API sends', () => {
    // The API renders money as a string so cents survive JSON. Anything that
    // parses those has to handle the string form, which is the normal case.
    expect(num('1234.56')).toBe(1234.56)
    expect(num('0.00')).toBe(0)
    expect(num('-500.25')).toBe(-500.25)
  })

  it('returns null for absent values rather than NaN or 0', () => {
    // 0 would be wrong: a missing figure and a zero figure must render
    // differently, or an empty report looks like a balanced one.
    expect(num(null)).toBeNull()
    expect(num(undefined)).toBeNull()
    expect(num('')).toBeNull()
    expect(num('not a number')).toBeNull()
  })

  it('passes numbers through', () => {
    expect(num(42)).toBe(42)
    expect(num(0)).toBe(0)
  })
})

describe('money and fmt', () => {
  it('renders two decimal places with thousands separators', () => {
    setCurrency('USD')
    expect(fmt('1234.5')).toBe('1,234.50')
    expect(fmt('0')).toBe('0.00')
    expect(fmt('1000000')).toBe('1,000,000.00')
  })

  it('shows a dash for a missing figure, not a zero', () => {
    expect(fmt(null)).toBe('-')
    expect(fmt(undefined)).toBe('-')
    expect(fmt('')).toBe('-')
  })

  it('keeps the sign on a negative, which a contra-asset relies on', () => {
    // Account 1900 Provision and 3200 Distributions both carry a balance that
    // reads negative, and hiding the sign would misstate the balance sheet.
    expect(fmt('-623.46')).toBe('-623.46')
    expect(money('-623.46')).toContain('623.46')
  })

  it('prefixes money with the configured currency', () => {
    setCurrency('ZWL')
    expect(money('10')).toMatch(/ZWL/)
    setCurrency('USD')
    expect(money('10')).toMatch(/USD/)
  })

  it('does not lose cents on a value too large for a float to hold exactly', () => {
    // Not a realistic loan, but it pins that the formatter does not round-trip
    // through something lossier than the string it was given.
    expect(fmt('12345678901.99')).toBe('12,345,678,901.99')
  })
})

describe('pct', () => {
  it('renders a percentage with its sign', () => {
    expect(pct('12.5')).toBe('12.50%')
    expect(pct('0')).toBe('0.00%')
  })

  it('shows a dash when there is no rate', () => {
    expect(pct(null)).toBe('-')
  })
})

describe('dateOnly', () => {
  it('passes an ISO date through unchanged', () => {
    expect(dateOnly('2026-09-30')).toBe('2026-09-30')
  })

  it('takes the date half of a timestamp', () => {
    expect(dateOnly('2026-09-30T14:22:01Z')).toBe('2026-09-30')
  })

  it('shows a dash for nothing', () => {
    expect(dateOnly(null)).toBe('-')
    expect(dateOnly('')).toBe('-')
  })
})

describe('today', () => {
  it('is an ISO date a date input accepts', () => {
    expect(today()).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('is the LOCAL date, not UTC', () => {
    // A teller east of Greenwich posting in the morning must not see yesterday.
    const now = new Date()
    const expected = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, '0'),
      String(now.getDate()).padStart(2, '0'),
    ].join('-')
    expect(today()).toBe(expected)
  })
})

describe('humanise', () => {
  it('turns a snake_case enum into words, leaving the casing to CSS', () => {
    // Deliberately not capitalised here: these land in .badge and th, both of
    // which are text-transform: uppercase. Capitalising in JS as well would
    // fight the stylesheet.
    expect(humanise('written_off')).toBe('written off')
    expect(humanise('salary_deduction')).toBe('salary deduction')
    expect(humanise('active')).toBe('active')
  })

  it('leaves nothing as nothing', () => {
    expect(humanise(null)).toBe('')
    expect(humanise('')).toBe('')
  })
})
