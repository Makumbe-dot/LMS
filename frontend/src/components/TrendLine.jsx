/* One rate over twelve months, against a target.

   A single series, so the title names it and there is no legend box; instead the
   header carries the figures a reader wants first - the latest complete month,
   the twelve-month average and how many months met the target - and the target
   is a dashed reference line with its own label.

   The scale tops out at 100%, or 150% when a month went over: a month that
   collected arrears from earlier ones can reach 200%+, and letting it set the
   scale flattened every other month into a line along the target. Anything above
   the top is drawn on the top edge with a caret and its real value.

   The month in progress is measured against what has fallen due so far (the
   server does that), drawn hollow and joined by a dashed segment; before
   anything has fallen due it has no point at all, rather than a fall to 0%.
   Hover or focus a month for a crosshair and the exact figures; the table view
   has the same numbers for screen readers and print. */
import { useId, useState } from 'react'

import { fmt, money, monthLabel, monthName, num, pct } from '../lib/format.js'
import DataTable from './DataTable.jsx'

const W = 760
const H = 240
const M = { top: 26, right: 74, bottom: 30, left: 46 }
const PLOT_W = W - M.left - M.right
const PLOT_H = H - M.top - M.bottom

/**
 * A smooth path through the points that never overshoots them (monotone cubic,
 * Fritsch-Carlson), so a smoothed line still peaks and dips exactly where the
 * data does.
 */
function smoothPath(points) {
  if (points.length < 2) return points.length ? `M${points[0][0]},${points[0][1]}` : ''
  const n = points.length
  const dx = []
  const slope = []
  for (let i = 0; i < n - 1; i += 1) {
    dx.push(points[i + 1][0] - points[i][0])
    slope.push((points[i + 1][1] - points[i][1]) / dx[i])
  }
  const tangent = [slope[0]]
  for (let i = 1; i < n - 1; i += 1) {
    tangent.push(slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2)
  }
  tangent.push(slope[n - 2])
  for (let i = 0; i < n - 1; i += 1) {
    if (slope[i] === 0) {
      tangent[i] = 0
      tangent[i + 1] = 0
      continue
    }
    const a = tangent[i] / slope[i]
    const b = tangent[i + 1] / slope[i]
    const h = Math.hypot(a, b)
    if (h > 3) {
      tangent[i] = (3 / h) * a * slope[i]
      tangent[i + 1] = (3 / h) * b * slope[i]
    }
  }
  let d = `M${points[0][0]},${points[0][1]}`
  for (let i = 0; i < n - 1; i += 1) {
    const [x0, y0] = points[i]
    const [x1, y1] = points[i + 1]
    const third = dx[i] / 3
    d += `C${x0 + third},${y0 + tangent[i] * third} ${x1 - third},${y1 - tangent[i + 1] * third} ${x1},${y1}`
  }
  return d
}

/** Runs of consecutive non-null indexes: a month with nothing due breaks the line. */
function runs(values, upTo) {
  const out = []
  let current = []
  for (let i = 0; i < upTo; i += 1) {
    if (values[i] === null) {
      if (current.length) out.push(current)
      current = []
    } else current.push(i)
  }
  if (current.length) out.push(current)
  return out
}

export default function TrendLine({
  data,
  title,
  subtitle,
  valueKey = 'collection_rate_pct',
  target,
  partialLast = false,
}) {
  const [hover, setHover] = useState(null)
  const [view, setView] = useState('chart')
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '')

  const values = data.map((row) => num(row[valueKey]))
  const partialIndex = partialLast ? values.length - 1 : -1
  const settledCount = partialLast ? values.length - 1 : values.length
  const settledValues = values.slice(0, settledCount).filter((v) => v !== null)
  const average = settledValues.length
    ? settledValues.reduce((sum, v) => sum + v, 0) / settledValues.length
    : null
  const onTarget = target ? settledValues.filter((v) => v >= target).length : null
  const lastSettled = values.slice(0, settledCount).findLastIndex((v) => v !== null)

  const highest = Math.max(0, ...values.filter((v) => v !== null))
  const top = highest <= 100 ? 100 : 150
  const ticks = top === 100 ? [0, 25, 50, 75, 100] : [0, 50, 100, 150]
  const step = PLOT_W / Math.max(data.length - 1, 1)
  const x = (i) => M.left + i * step
  const y = (v) => M.top + PLOT_H - (Math.min(Math.max(0, v), top) / top) * PLOT_H
  const baseY = M.top + PLOT_H

  const segments = runs(values, settledCount).map((idx) => idx.map((i) => [x(i), y(values[i])]))
  const showPartial = partialIndex >= 0 && values[partialIndex] !== null
  const partialFrom = showPartial ? values.slice(0, partialIndex).findLastIndex((v) => v !== null) : -1
  const latestLabelLeft = lastSettled > data.length * 0.8

  return (
    <div>
      <div className="chart-head">
        <h3 style={{ margin: 0 }}>{title}</h3>
        <div className="row" style={{ gap: 8 }}>
          <div className="legend legend-chips">
            {lastSettled >= 0 ? (
              <span className="legend-chip">
                {monthLabel(data[lastSettled].month, { first: true })}
                <b>{pct(values[lastSettled])}</b>
              </span>
            ) : null}
            {average !== null ? (
              <span className="legend-chip">
                12-month average
                <b>{pct(average)}</b>
              </span>
            ) : null}
            {onTarget !== null && settledValues.length ? (
              <span className="legend-chip" title={`Months at or above the ${target}% target`}>
                On target
                <b>
                  {onTarget} of {settledValues.length}
                </b>
              </span>
            ) : null}
          </div>
          <button
            type="button"
            className="btn small"
            onClick={() => setView(view === 'chart' ? 'table' : 'chart')}
            aria-pressed={view === 'table'}
          >
            {view === 'chart' ? 'Table' : 'Chart'}
          </button>
        </div>
      </div>
      {subtitle ? <p className="chart-sub">{subtitle}</p> : null}

      {view === 'table' ? (
        <DataTable
          caption={title}
          rows={data}
          rowKey={(row) => row.month}
          columns={[
            {
              key: 'month',
              header: 'Month',
              render: (row) => `${monthName(row.month)}${row.partial ? ' (so far)' : ''}`,
            },
            { key: 'collected', header: 'Collected', num: true, render: (row) => fmt(row.collected) },
            {
              key: 'due',
              header: 'Due',
              num: true,
              render: (row) => fmt(row.partial ? row.due_to_date : row.due),
            },
            { key: valueKey, header: 'Rate', num: true, render: (row) => pct(row[valueKey]) },
          ]}
        />
      ) : (
        <div className="chart-frame">
          <svg
            className={`chart-svg trend-svg${hover !== null ? ' has-hover' : ''}`}
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={`${title}. ${data
              .map((row) => `${monthName(row.month)} ${pct(row[valueKey])}`)
              .join(', ')}.`}
            onMouseLeave={() => setHover(null)}
          >
            <defs>
              <linearGradient id={`${uid}-area`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" className="area-stop-top" />
                <stop offset="1" className="area-stop-bottom" />
              </linearGradient>
            </defs>

            {ticks.map((tick) => (
              <g key={tick}>
                <line
                  className={tick === 0 ? 'baseline' : 'tick-line'}
                  x1={M.left}
                  x2={W - M.right}
                  y1={y(tick)}
                  y2={y(tick)}
                />
                <text className="tick-text" x={M.left - 10} y={y(tick) + 3} textAnchor="end">
                  {tick}%
                </text>
              </g>
            ))}

            {/* the target, named in the right margin, where no month's point can sit on it */}
            {target ? (
              <g className="target">
                <line x1={M.left} x2={W - M.right} y1={y(target)} y2={y(target)} />
                <text x={W - M.right + 8} y={y(target) + 3.5}>
                  Target {target}%
                </text>
              </g>
            ) : null}

            {segments.map((points) =>
              points.length > 1 ? (
                <path
                  key={`a${points[0][0]}`}
                  className="trend-area"
                  d={`${smoothPath(points)}L${points[points.length - 1][0]},${baseY}L${points[0][0]},${baseY}Z`}
                  fill={`url(#${uid}-area)`}
                />
              ) : null,
            )}
            {segments.map((points) => (
              <path key={`l${points[0][0]}`} className="trend-line draw-in" d={smoothPath(points)} pathLength={1} />
            ))}
            {showPartial && partialFrom >= 0 ? (
              <path
                className="trend-line partial"
                d={`M${x(partialFrom)},${y(values[partialFrom])}L${x(partialIndex)},${y(values[partialIndex])}`}
              />
            ) : null}

            {hover !== null ? (
              <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={M.top - 6} y2={baseY} />
            ) : null}

            {values.map((v, i) => {
              if (v === null) return null
              const clipped = v > top
              return (
                <g key={data[i].month}>
                  <circle
                    className={`trend-dot${i === partialIndex ? ' hollow' : ''}${hover === i ? ' hovered' : ''}`}
                    cx={x(i)}
                    cy={y(v)}
                    r={hover === i ? 6 : 4.5}
                  />
                  {/* off the scale: a caret on the top edge and the real figure beside it */}
                  {clipped ? (
                    <g className="over-top">
                      <path d={`M${x(i) - 4},${M.top - 9}L${x(i)},${M.top - 14}L${x(i) + 4},${M.top - 9}Z`} />
                      <text x={x(i) + 8} y={M.top - 8}>
                        {pct(v)}
                      </text>
                    </g>
                  ) : null}
                </g>
              )
            })}

            {/* the latest complete month, named: the one value worth writing on the chart */}
            {lastSettled >= 0 && hover === null && values[lastSettled] <= top ? (
              <g className="latest">
                <rect
                  x={latestLabelLeft ? x(lastSettled) - 64 : x(lastSettled) + 10}
                  y={y(values[lastSettled]) - 30}
                  width={54}
                  height={19}
                  rx={9.5}
                />
                <text
                  x={latestLabelLeft ? x(lastSettled) - 37 : x(lastSettled) + 37}
                  y={y(values[lastSettled]) - 17}
                  textAnchor="middle"
                >
                  {pct(values[lastSettled])}
                </text>
              </g>
            ) : null}

            {data.map((row, i) => (
              <g key={row.month}>
                <rect
                  className="hit"
                  x={x(i) - step / 2}
                  y={M.top - 16}
                  width={step}
                  height={PLOT_H + 16}
                  tabIndex={0}
                  role="button"
                  aria-label={`${monthName(row.month)}: ${pct(row[valueKey])}`}
                  onMouseEnter={() => setHover(i)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                />
                <text
                  className={i === partialIndex ? 'axis-now' : undefined}
                  x={x(i)}
                  y={H - 9}
                  textAnchor="middle"
                >
                  {monthLabel(row.month, { first: i === 0 })}
                </text>
              </g>
            ))}
          </svg>

          {hover !== null && data[hover] ? (
            <div
              className="chart-tooltip"
              style={{
                left: `calc(${(x(hover) / W) * 100}% + 12px)`,
                top: 0,
                transform: hover > data.length / 2 ? 'translateX(calc(-100% - 24px))' : 'none',
              }}
            >
              <div className="tt-title">
                {monthName(data[hover].month)}
                {hover === partialIndex ? ' · so far' : ''}
              </div>
              {values[hover] === null ? (
                <div className="tt-note">Nothing had fallen due{hover === partialIndex ? ' yet' : ''}.</div>
              ) : (
                <>
                  <div className="tt-row">
                    <span>Rate</span>
                    <span className="tt-val">{pct(values[hover])}</span>
                  </div>
                  {target ? (
                    <div className="tt-row">
                      <span>Against target</span>
                      <span className="tt-val">
                        {values[hover] >= target ? '+' : '−'}
                        {fmt(Math.abs(values[hover] - target))} pts
                      </span>
                    </div>
                  ) : null}
                </>
              )}
              <div className="tt-row">
                <span>Collected</span>
                <span className="tt-val">{money(data[hover].collected)}</span>
              </div>
              <div className="tt-row">
                <span>{hover === partialIndex ? 'Due so far' : 'Due'}</span>
                <span className="tt-val">
                  {money(hover === partialIndex ? data[hover].due_to_date ?? data[hover].due : data[hover].due)}
                </span>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
