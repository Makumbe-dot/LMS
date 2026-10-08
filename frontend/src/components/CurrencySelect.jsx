import { useEffect } from 'react'

import { currencyOptions } from '../lib/currency.js'
import { useApi } from '../lib/useApi.js'
import { Field } from './ui.jsx'

/**
 * A currency select for a savings product, a facility or a till: the
 * organisation's currency (sent as blank) and every other one with a rate on the
 * Currencies page. `only` narrows the choices, `onChange` reports the code.
 */
export default function CurrencySelect({
  label = 'Currency',
  name = 'currency',
  value,
  defaultValue = '',
  onChange,
  only,
  hint,
  disabled,
}) {
  const listing = useApi('/api/currencies')
  let options = currencyOptions(listing.data, value ?? defaultValue)
  if (only) options = only(options)
  const controlled = value !== undefined
  // A controlled value the choices no longer offer (a drawer already open in it)
  // moves to the first that is, so the label beside it never names another.
  const offered = options.some((o) => o.value === value)
  const first = options[0]?.value
  useEffect(() => {
    if (controlled && onChange && listing.data && !offered && first !== undefined) onChange(first)
  }, [controlled, onChange, listing.data, offered, first])
  return (
    <Field
      // remounted once the list arrives, so an uncontrolled default finds its option
      key={listing.data ? 'loaded' : 'loading'}
      as="select"
      label={label}
      name={name}
      disabled={disabled || listing.loading}
      {...(controlled ? { value } : { defaultValue })}
      onChange={onChange ? (e) => onChange(e.target.value) : undefined}
      hint={hint}
    >
      {options.map((o) => (
        <option key={o.value || 'base'} value={o.value}>
          {o.label}
        </option>
      ))}
    </Field>
  )
}
