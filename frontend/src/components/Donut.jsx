/* A ring of proportions with the total in the middle and a legend beside it.

   The legend carries every label and count, so nothing is read from colour
   alone; the ring is the shape of the book at a glance. */
import { fmt } from '../lib/format.js'

const R = 40
const STROKE = 14
const CIRC = 2 * Math.PI * R

export default function Donut({ slices, total, centreLabel, 'aria-label': ariaLabel }) {
  const sum = slices.reduce((acc, s) => acc + (Number(s.value) || 0), 0) || 1
  let offset = 0

  return (
    <div className="donut">
      <svg viewBox="0 0 100 100" className="donut-ring" role="img" aria-label={ariaLabel}>
        <circle cx="50" cy="50" r={R} className="donut-track" strokeWidth={STROKE} fill="none" />
        {slices.map((s) => {
          const share = (Number(s.value) || 0) / sum
          const dash = share * CIRC
          const el = (
            <circle
              key={s.key}
              cx="50"
              cy="50"
              r={R}
              fill="none"
              strokeWidth={STROKE}
              className={`donut-slice tone-${s.tone}`}
              strokeDasharray={`${dash} ${CIRC - dash}`}
              strokeDashoffset={-offset}
              transform="rotate(-90 50 50)"
            />
          )
          offset += dash
          return el
        })}
        <text x="50" y="47" textAnchor="middle" className="donut-total">
          {fmt(total).replace('.00', '')}
        </text>
        <text x="50" y="60" textAnchor="middle" className="donut-caption">
          {centreLabel}
        </text>
      </svg>
      <ul className="donut-legend">
        {slices.map((s) => (
          <li key={s.key}>
            <span className={`dot tone-${s.tone}`} aria-hidden="true" />
            <span className="donut-label">{s.label}</span>
            <b>{s.value}</b>
          </li>
        ))}
        {slices.length === 0 ? <li className="muted">No loans yet</li> : null}
      </ul>
    </div>
  )
}
