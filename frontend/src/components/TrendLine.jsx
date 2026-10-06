/* One rate over twelve months, against a target.

   A single series, so the title names it and there is no legend box; instead the
   chips under the title carry the figures a reader wants first - the latest
   complete month, the average, and how many months met the target. The value
   axis is fitted to the settled months and the target, not pinned at zero: a
   rate that moves between 88% and 97% is a flat line on a 0-100 scale. The band
   under the target is washed, so a month that missed sits visibly in it.

   The month in progress is not joined to the line, because a rate measured on
   the 6th is not a fall: its column is banded and its figure written at the
   foot as "so far". The latest settled point is labelled; nothing else is.
   Hover or focus a month for a crosshair and the exact figures; the table view
   has the same numbers for screen readers and print. */
import { useId, useState } from 'react'

import { fmt, money, monthLabel, monthName, num, pct } from '../lib/format.js'
import DataTable from './DataTable.jsx'

const W = 760
const H = 296
const M = { top: 18, right: 8, bottom: 30, left: 46 }
const PLOT_W = W - M.left - M.right
const PLOT_H = H - M.top - M.bottom

/** A value axis fitted to the data: whole steps of 5 or 10, at most six ticks. */
export function fitAxis(values, target) {
  const seen = values.filter((v) => v !== null)
  if (target) seen.push(target)
  if (!seen.length) return { lo: 0, hi: 100, ticks: [0, 25, 50, 75, 100] }
  const min = Math.min(...seen)
  const max = Math.max(...seen)
  let step = 5
  let lo = Math.max(0, Math.floor((min - 4) / step) * step)
  let hi = Math.max(lo + step * 2, Math.ceil((max + 2) / step) * step)
  while ((hi - lo) / step > 5) {
    step = step === 5 ? 10 : step * 2
    lo = Math.max(0, Math.floor(lo / step) * step)
    hi = Math.ceil(hi / step) * step
  }
  const ticks = []
  for (let t = lo; t <= hi; t += step) ticks.push(t)
  return { lo, hi, ticks }
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
  const settledCount = partialLast ? values.length - 1 : values.length
  const settled = values.slice(0, settledCount)
  const settledValues = settled.filter((v) => v !== null)
  const average = settledValues.length
    ? settledValues.reduce((sum, v) => sum + v, 0) / settledValues.length
    : null
  const onTarget = target ? settledValues.filter((v) => v >= target).length : null
  const lastSettled = settled.findLastIndex((v) => v !== null)
  const partialIndex = values.length - 1
  const partial = partialLast ? values[partialIndex] : null

  const { lo, hi, ticks } = fitAxis(settled, target)
  // each month owns an equal column, its point at the centre, so the first and
  // last months have room for their labels
  const step = PLOT_W / Math.max(data.length, 1)
  const x = (i) => M.left + (i + 0.5) * step
  const clampY = (v) => Math.min(hi, Math.max(lo, v))
  const y = (v) => M.top + PLOT_H - ((clampY(v) - lo) / (hi - lo)) * PLOT_H
  const floor = M.top + PLOT_H

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
  const firstSettled = settled.findIndex((v) => v !== null)
  const area =
    lastSettled > firstSettled && settled.slice(firstSettled, lastSettled + 1).every((v) => v !== null)
      ? `${line}L${x(lastSettled)},${floor}L${x(firstSettled)},${floor}Z`
      : ''
  const latestLabelLeft = lastSettled > data.length * 0.8

  return (
    <div>
      <div className="chart-head">
        <h3 style={{ margin: 0 }}>{title}</h3>
        <button
          type="button"
          className="btn small"
          onClick={() => setView(view === 'chart' ? 'table' : 'chart')}
          aria-pressed={view === 'table'}
        >
          {view === 'chart' ? 'Table' : 'Chart'}
        </button>
      </div>
      {subtitle ? <p className="chart-sub">{subtitle}</p> : null}
      <div className="legend legend-chips chart-legend">
        {lastSettled >= 0 ? (
          <span className="legend-chip">
            {monthLabel(data[lastSettled].month, { first: true })}
            <b>{pct(values[lastSettled])}</b>
          </span>
        ) : null}
        {average !== null ? (
          <span className="legend-chip">
            {settledValues.length}-month average
            <b>{pct(average)}</b>
          </span>
        ) : null}
        {onTarget !== null && settledValues.length ? (
          <span className="legend-chip">
            On target
            <b>
              {onTarget} of {settledValues.length} months
            </b>
          </span>
        ) : null}
      </div>

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

            {/* the month in progress: a faint band, so its column reads as unfinished */}
            {partialLast && data.length ? (
              <rect
                className="now-band"
                x={x(partialIndex) - step / 2 + 3}
                y={M.top - 6}
                width={step - 6}
                height={PLOT_H + 6}
                rx={8}
              />
            ) : null}

            {/* below target: a wash, so a month that missed sits in it */}
            {target && target > lo ? (
              <rect
                className="below-target"
                x={M.left}
                y={y(target)}
                width={PLOT_W}
                height={floor - y(target)}
              />
            ) : null}

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
                <rect x={M.left + 6} y={y(target) + 4} width={64} height={16} rx={8} />
                <text x={M.left + 38} y={y(target) + 15} textAnchor="middle">
                  Target {target}%
                </text>
              </g>
            ) : null}

            {area ? <path className="trend-area" d={area} fill={`url(#${uid}-area)`} /> : null}
            <path className="trend-line draw-in" d={line} pathLength={1} />

            {hover !== null ? (
              <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={M.top} y2={floor} />
            ) : null}

            {settled.map((v, i) =>
              v === null ? null : (
                <circle
                  key={data[i].month}
                  className={`trend-dot${target && v < target ? ' missed' : ''}${hover === i ? ' hovered' : ''}`}
                  cx={x(i)}
                  cy={y(v)}
                  r={hover === i ? 6 : 4}
                />
              ),
            )}

            {/* the month in progress, written rather than plotted */}
            {partialLast && partial !== null ? (
              <g className="so-far">
                <text x={x(partialIndex)} y={floor - 22} textAnchor="middle" className="so-far-label">
                  so far
                </text>
                <text x={x(partialIndex)} y={floor - 8} textAnchor="middle" className="so-far-value">
                  {Math.round(partial)}%
                </text>
              </g>
            ) : null}

            {/* the latest complete month, named: the one value worth writing on the chart */}
            {lastSettled >= 0 && hover === null ? (
              <g className="latest">
                <rect
                  x={latestLabelLeft ? x(lastSettled) - 62 : x(lastSettled) + 10}
                  y={y(values[lastSettled]) - 30}
                  width={52}
                  height={20}
                  rx={10}
                />
                <text
                  x={latestLabelLeft ? x(lastSettled) - 36 : x(lastSettled) + 36}
                  y={y(values[lastSettled]) - 16.5}
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
                  aria-label={`${monthName(row.month)}${partialLast && i === partialIndex ? ' so far' : ''}: ${pct(
                    row[valueKey],
                  )}`}
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
              style={
                hover >= data.length / 2
                  ? { left: `calc(${(x(hover) / W) * 100}% - 12px)`, top: 0, transform: 'translateX(-100%)' }
                  : { left: `calc(${(x(hover) / W) * 100}% + 12px)`, top: 0 }
              }
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
              {target && num(data[hover][valueKey]) !== null && !(partialLast && hover === partialIndex) ? (
                <div className="tt-foot">
                  {num(data[hover][valueKey]) >= target
                    ? `Met the ${target}% target`
                    : `${fmt(target - num(data[hover][valueKey]))} points under target`}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
