/* One series of ordered categories, as horizontal bars.

   A single series, so no legend is needed: the card title names the measure and
   every bar carries its own label and value, which keeps the reading independent
   of colour. A row may carry a `color` (a CSS value; the arrears buckets take a
   severity ramp, the products a categorical palette), a `note` under its label
   (how many loans) and a `share` (of the whole) beside its value. With `overview`
   the rows are first summed into one strip, so the proportions are read at a
   glance before the detail. */
import { money, num } from '../lib/format.js'

export default function HBars({ rows, valueLabel = money, wide = false, overview = false }) {
  const values = rows.map((r) => num(r.value) ?? 0)
  const max = Math.max(1, ...values)
  const total = values.reduce((sum, v) => sum + v, 0)

  return (
    <div className={wide ? 'hbars wide' : 'hbars'}>
      {overview && total > 0 ? (
        <div className="hbar-overview" role="img" aria-label={rows
          .map((row, i) => `${row.label} ${Math.round((values[i] / total) * 100)}%`)
          .join(', ')}>
          {rows.map((row, i) =>
            values[i] > 0 ? (
              <span
                key={row.label}
                className="hbar-overview-seg"
                style={{
                  width: `${(values[i] / total) * 100}%`,
                  background: row.color || 'var(--series-single)',
                  animationDelay: `${i * 60}ms`,
                }}
                title={`${row.label}: ${valueLabel(values[i])}`}
              />
            ) : null,
          )}
        </div>
      ) : null}
      {rows.map((row, i) => {
        const value = values[i]
        return (
          <div className="hbar-row" key={row.label}>
            <span className="hbar-label">
              {row.color ? (
                <span className="hbar-dot" style={{ background: row.color }} aria-hidden="true" />
              ) : null}
              <span className="hbar-text">
                {row.label}
                {row.note ? <small>{row.note}</small> : null}
              </span>
            </span>
            <div className="hbar-track" role="img" aria-label={`${row.label}: ${valueLabel(value)}`}>
              <div
                className="hbar-fill"
                style={{
                  width: `${(value / max) * 100}%`,
                  background: row.color || undefined,
                  animationDelay: `${i * 50 + 150}ms`,
                }}
              />
            </div>
            <span className="hbar-value">
              {valueLabel(value)}
              {row.share !== undefined ? <small>{row.share}</small> : null}
            </span>
          </div>
        )
      })}
    </div>
  )
}
