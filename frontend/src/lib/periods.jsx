import { useOrg } from './org.jsx'
import { dateOnly } from './format.js'

/**
 * The earliest date a posting may carry, for a date input's `min` attribute.
 *
 * Correct because months close in order and only forwards, so the closed months
 * are always one contiguous run ending at one date. Returns undefined when
 * nothing is closed, which leaves the input unconstrained.
 */
export function useMinPostingDate() {
  return useOrg().earliestPostableDate || undefined
}

/**
 * An inline warning that the chosen date cannot be posted to.
 *
 * Shown beside the date field rather than only on submit, so the operator is told
 * before they have typed an amount. The server refuses it either way; this is the
 * courtesy, not the control.
 */
export function PeriodNotice({ date }) {
  const { closedThrough, earliestPostableDate } = useOrg()
  if (!closedThrough) return null

  const blocked = date && date <= closedThrough
  return (
    <div className={blocked ? 'banner' : 'hint'} role={blocked ? 'alert' : undefined}>
      {blocked ? (
        <>
          <strong>{dateOnly(date)} falls in a closed accounting period. </strong>
          The books are closed through {dateOnly(closedThrough)}. Post it on{' '}
          {dateOnly(earliestPostableDate)} or later, or ask an administrator to reopen the
          period.
        </>
      ) : (
        <>The books are closed through {dateOnly(closedThrough)}.</>
      )}
    </div>
  )
}
