/* A ring of proportions with the total in the middle and a legend beside it.

   The legend carries every label, count and share, so nothing is read from
   colour alone; the ring is the shape of the book at a glance. Slices are parted
   by a hairline of surface so neighbours never merge. Hovering a slice or its
   legend row lifts that slice and puts its figure in the centre. */
import { useState } from 'react'

import { fmt } from '../lib/format.js'

const R = 40
const STROKE = 13
const CIRC = 2 * Math.PI * R
const GAP = 2.2 // surface between slices, along the ring

export default function Donut({ slices, total, centreLabel, 'aria-label': ariaLabel }) {
  const [active, setActive] = useState(null)
  const sum = slices.reduce((acc, s) => acc + (Number(s.value) || 0), 0) || 1
  let offset = 0
  const shown = active !== null ? slices[active] : null

  return (
    <div className="donut" onMouseLeave={() => setActive(null)}>
      <svg viewBox="0 0 100 100" className="donut-ring" role="img" aria-label={ariaLabel}>
        <circle cx="50" cy="50" r={R} className="donut-track" strokeWidth={STROKE} fill="none" />
        {slices.map((s, i) => {
          const share = (Number(s.value) || 0) / sum
          const length = share * CIRC
          const gap = slices.length > 1 ? Math.min(GAP, length / 2) : 0
          const el = (
            <circle
              key={s.key}
              cx="50"
              cy="50"
              r={R}
              fill="none"
              strokeWidth={active === i ? STROKE + 4 : STROKE}
              className={`donut-slice tone-${s.tone}${active !== null && active !== i ? ' dim' : ''}`}
              strokeDasharray={`${Math.max(0, length - gap)} ${CIRC - Math.max(0, length - gap)}`}
              strokeDashoffset={-(offset + gap / 2)}
              transform="rotate(-90 50 50)"
              onMouseEnter={() => setActive(i)}
              style={{ animationDelay: `${i * 90 + 200}ms` }}
            />
          )
          offset += length
          return el
        })}
        <text x="50" y="47" textAnchor="middle" className="donut-total">
          {shown ? fmt(shown.value).replace('.00', '') : fmt(total).replace('.00', '')}
        </text>
        <text x="50" y="60" textAnchor="middle" className="donut-caption">
          {shown ? shown.label : centreLabel}
        </text>
      </svg>
      <ul className="donut-legend">
        {slices.map((s, i) => (
          <li
            key={s.key}
            className={active === i ? 'is-active' : undefined}
            onMouseEnter={() => setActive(i)}
          >
            <span className={`dot tone-${s.tone}`} aria-hidden="true" />
            <span className="donut-label">{s.label}</span>
            <small>{Math.round(((Number(s.value) || 0) / sum) * 100)}%</small>
            <b>{s.value}</b>
          </li>
        ))}
        {slices.length === 0 ? <li className="muted">No loans yet</li> : null}
      </ul>
    </div>
  )
}
