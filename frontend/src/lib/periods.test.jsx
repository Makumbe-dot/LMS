import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { renderPage } from '../test/harness.jsx'
import { PeriodNotice, useMinPostingDate } from './periods.jsx'

/**
 * The period notice and the `min` attribute are a courtesy, not the control — the
 * server refuses a closed date either way. What matters is that they tell the
 * truth, because a notice that cries wolf gets ignored and a `min` that is wrong
 * stops a legitimate posting the server would accept.
 */

function ShowsMin() {
  const min = useMinPostingDate()
  return <span data-testid="min">{min ?? 'none'}</span>
}

describe('PeriodNotice', () => {
  it('says nothing at all when nothing is closed', () => {
    renderPage(<PeriodNotice date="2026-01-15" />, { org: { closedThrough: null } })
    expect(screen.queryByText(/closed/i)).not.toBeInTheDocument()
  })

  it('warns, as an alert, when the chosen date falls in a closed month', () => {
    renderPage(<PeriodNotice date="2026-08-15" />, {
      org: { closedThrough: '2026-08-31', earliestPostableDate: '2026-09-01' },
    })
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(/2026-08-15 falls in a closed accounting period/)
    expect(alert).toHaveTextContent(/Post it on 2026-09-01 or later/)
  })

  it('states the boundary without alarm when the date is fine', () => {
    renderPage(<PeriodNotice date="2026-09-15" />, {
      org: { closedThrough: '2026-08-31', earliestPostableDate: '2026-09-01' },
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText(/closed through 2026-08-31/)).toBeInTheDocument()
  })

  it('treats the close-through date itself as closed', () => {
    // The guard is `on <= closed_through`, so the last day of the closed month is
    // closed. An off-by-one here would promise a posting the server refuses.
    renderPage(<PeriodNotice date="2026-08-31" />, {
      org: { closedThrough: '2026-08-31', earliestPostableDate: '2026-09-01' },
    })
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('treats the day after as open', () => {
    renderPage(<PeriodNotice date="2026-09-01" />, {
      org: { closedThrough: '2026-08-31', earliestPostableDate: '2026-09-01' },
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('useMinPostingDate', () => {
  it('is undefined when nothing is closed, leaving the input unconstrained', () => {
    renderPage(<ShowsMin />, { org: { earliestPostableDate: null } })
    expect(screen.getByTestId('min')).toHaveTextContent('none')
  })

  it('is the earliest postable date when a month is closed', () => {
    renderPage(<ShowsMin />, { org: { earliestPostableDate: '2026-09-01' } })
    expect(screen.getByTestId('min')).toHaveTextContent('2026-09-01')
  })
})
