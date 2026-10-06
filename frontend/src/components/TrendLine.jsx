/* One rate over twelve months, against a target.

   A single series, so the title names it and there is no legend box; instead the
   header carries the two figures a reader wants first - the latest complete
   month and the twelve-month average - and the target is a dashed line with its
   own label. The month in progress is drawn hollow and joined by a dashed
   segment, because a rate measured on the 6th is not a fall. The latest settled
   point is labelled; nothing else is, so the labels stay readable. Hover or focus
   a month for a crosshair and the exact figures; the table view has the same
   numbers for screen readers and print. */
import { useId, useState } from 'react'

import { fmt, money, monthLabel, monthName, num, pct } from '../lib/format.js'
import DataTable from './DataTable.jsx'

const W = 760
const H = 230
const M = { top: 22, right: 16, bottom: 30, left: 46 }
const PLOT_W = W - M.left - M.right
const PLOT_H = H - M.top - M.bottom

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
  const settledCount = partialLast ? values.length - 1 : values.length
  const settledValues = values.slice(0, settledCount).filter((v) => v !== null)
  const average = settledValues.length
    ? settledValues.reduce((sum, v) => sum + v, 0) / settledValues.length
    : null
  const lastSettled = values.slice(0, settledCount).findLastIndex((v) => v !== null)

  // A whole hundred, so the quarter ticks land on round figures (75%, 150%...).
  const top = Math.max(100, Math.ceil(Math.max(0, ...values.filter((v) => v !== null)) / 100) * 100)
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => top * f)
  const step = PLOT_W / Math.max(data.length - 1, 1)
  const x = (i) => M.left + i * step
  const y = (v) => M.top + PLOT_H - (Math.max(0, v) / top) * PLOT_H

  let line = ''
  let pen = false
  for (let i = 0; i < settledCount; i += 1) {
    if (values[i] === null) {
      pen = false
      continue
    }
    line += `${pen ? 'L' : 'M'}${x(i)},${y(values[i])}`
    pen = true
  }
  const area =
    lastSettled > 0 && values.slice(0, settledCount).every((v) => v !== null)
      ? `${line}L${x(lastSettled)},${M.top + PLOT_H}L${x(0)},${M.top + PLOT_H}Z`
      : ''
  const partialIndex = values.length - 1
  const showPartial = partialLast && values[partialIndex] !== null && lastSettled >= 0
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
            { key: 'month', header: 'Month', render: (row) => monthName(row.month) },
            { key: 'collected', header: 'Collected', num: true, render: (row) => fmt(row.collected) },
            { key: 'due', header: 'Due', num: true, render: (row) => fmt(row.due) },
            { key: valueKey, header: 'Rate', num: true, render: (row) => pct(row[valueKey]) },
          ]}
        />
      ) : (
        <div className="chart-frame">
          <svg
            className="chart-svg trend-svg"
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
                <line className="tick-line" x1={M.left} x2={W - M.right} y1={y(tick)} y2={y(tick)} />
                <text x={M.left - 10} y={y(tick) + 3} textAnchor="end">
                  {tick}%
                </text>
              </g>
            ))}

            {/* the target, named at the left end: the latest point and its label sit at the right */}
            {target ? (
              <g className="target">
                <line x1={M.left} x2={W - M.right} y1={y(target)} y2={y(target)} />
                <rect x={M.left + 6} y={y(target) - 18} width={64} height={15} rx={7.5} />
                <text x={M.left + 38} y={y(target) - 7} textAnchor="middle">
                  Target {target}%
                </text>
              </g>
            ) : null}

            {area ? <path className="trend-area" d={area} fill={`url(#${uid}-area)`} /> : null}
            <path className="trend-line draw-in" d={line} pathLength={1} />
            {showPartial ? (
              <path
                className="trend-line partial"
                d={`M${x(lastSettled)},${y(values[lastSettled])}L${x(partialIndex)},${y(values[partialIndex])}`}
              />
            ) : null}

            {hover !== null ? (
              <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={M.top} y2={M.top + PLOT_H} />
            ) : null}

            {values.map((v, i) =>
              v === null ? null : (
                <circle
                  key={data[i].month}
                  className={`trend-dot${partialLast && i === partialIndex ? ' hollow' : ''}${
                    hover === i ? ' hovered' : ''
                  }`}
                  cx={x(i)}
                  cy={y(v)}
                  r={4}
                />
              ),
            )}

            {/* the latest complete month, named: the one value worth writing on the chart */}
            {lastSettled >= 0 && hover === null ? (
              <g className="latest">
                <rect
                  x={latestLabelLeft ? x(lastSettled) - 60 : x(lastSettled) + 10}
                  y={y(values[lastSettled]) - 28}
                  width={50}
                  height={18}
                  rx={9}
                />
                <text
                  x={latestLabelLeft ? x(lastSettled) - 35 : x(lastSettled) + 35}
                  y={y(values[lastSettled]) - 16}
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
                  y={M.top - 10}
                  width={step}
                  height={PLOT_H + 10}
                  tabIndex={0}
                  role="button"
                  aria-label={`${monthName(row.month)}: ${pct(row[valueKey])}`}
                  onMouseEnter={() => setHover(i)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                />
                <text
                  className={partialLast && i === partialIndex ? 'axis-now' : undefined}
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
                left: `calc(${(x(hover) / W) * 100}% + 10px)`,
                top: 0,
                transform: hover > data.length / 2 ? 'translateX(-112%)' : 'none',
              }}
            >
              <div className="tt-title">
                {monthName(data[hover].month)}
                {partialLast && hover === partialIndex ? ' · so far' : ''}
              </div>
              <div className="tt-row">
                <span>Rate</span>
                <span className="tt-val">{pct(data[hover][valueKey])}</span>
              </div>
              <div className="tt-row">
                <span>Collected</span>
                <span className="tt-val">{money(data[hover].collected)}</span>
              </div>
              <div className="tt-row">
                <span>Due</span>
                <span className="tt-val">{money(data[hover].due)}</span>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
