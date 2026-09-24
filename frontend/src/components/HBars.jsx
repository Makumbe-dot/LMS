/* One series of ordered categories - the arrears ageing buckets.

   A single series, so no legend is needed: the card title names the measure and
   every bar carries its own label and value, which keeps the reading independent
   of colour. */
import { money, num } from '../lib/format.js'

export default function HBars({ rows, valueLabel = money }) {
  const max = Math.max(1, ...rows.map((r) => num(r.value) ?? 0))
  return (
    <div className="hbars">
      {rows.map((row) => {
        const value = num(row.value) ?? 0
        return (
          <div className="hbar-row" key={row.label}>
            <span className="hbar-label">{row.label}</span>
            <div
              className="hbar-track"
              role="img"
              aria-label={`${row.label}: ${valueLabel(value)}`}
            >
              <div className="hbar-fill" style={{ width: `${(value / max) * 100}%` }} />
            </div>
            <span className="hbar-value">{valueLabel(value)}</span>
          </div>
        )
      })}
    </div>
  )
}
