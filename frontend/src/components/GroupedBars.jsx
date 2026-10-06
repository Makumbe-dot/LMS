/* Grouped bar chart: two series over twelve months.

   Two series, so a legend is always present and identity never rests on colour
   alone; the legend also carries each series' total for the period, which is the
   first thing anyone asks of this chart. The month in progress sits on a faint
   band so it is read as unfinished. Every group has a hover/focus tooltip and,
   while hovered, its two values written above the bars; a table view is one
   click away for screen readers, print and forced colours. */
import { useId, useMemo, useState } from 'react'

import { compact, fmt, money, monthLabel, monthName } from '../lib/format.js'
import DataTable from './DataTable.jsx'

const W = 760
const H = 250
const M = { top: 22, right: 12, bottom: 30, left: 52 }
const PLOT_W = W - M.left - M.right
const PLOT_H = H - M.top - M.bottom
const BAR_GAP = 3 // surface gap between the two bars of a pair
const RADIUS = 4

/** A rectangle with only its top corners rounded, anchored to the baseline. */
function barPath(x, y, width, height, radius = RADIUS) {
  if (height <= 0) return ''
  const r = Math.min(radius, width / 2, height)
  return [
    `M${x},${y + height}`,
    `V${y + r}`,
    `Q${x},${y} ${x + r},${y}`,
    `H${x + width - r}`,
    `Q${x + width},${y} ${x + width},${y + r}`,
    `V${y + height}`,
    'Z',
  ].join(' ')
}

/**
 * An axis whose ticks are round figures: a step of 1, 2, 2.5 or 5 times a power
 * of ten, and a top that is a whole number of steps above the largest value.
 * Quartering the maximum instead gave ticks like 625 and 1,875.
 */
function niceScale(value, ticksWanted = 4) {
  if (value <= 0) return { top: 1, ticks: [0, 1] }
  const raw = value / ticksWanted
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const norm = raw / magnitude
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * magnitude
  const top = step * Math.ceil(value / step)
  const ticks = []
  for (let t = 0; t <= top + step / 2; t += step) ticks.push(t)
  return { top, ticks }
}

export default function GroupedBars({
  data,
  title,
  subtitle,
  series = [
    { key: 'disbursed', label: 'Disbursed', color: 'var(--series-1)', deep: 'var(--series-1-deep)' },
    { key: 'collected', label: 'Collected', color: 'var(--series-2)', deep: 'var(--series-2-deep)' },
  ],
  xKey = 'month',
  // The last group is the month still in progress: banded, and named so.
  currentLast = true,
}) {
  const [hover, setHover] = useState(null)
  const [view, setView] = useState('chart')
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '')

  const { top: max, ticks } = useMemo(
    () => niceScale(Math.max(0, ...data.flatMap((row) => series.map((s) => Number(row[s.key]) || 0)))),
    [data, series],
  )
  const totals = useMemo(
    () => series.map((s) => data.reduce((sum, row) => sum + (Number(row[s.key]) || 0), 0)),
    [data, series],
  )

  const groupWidth = PLOT_W / Math.max(data.length, 1)
  const barWidth = Math.min(18, (groupWidth * 0.64 - BAR_GAP) / series.length)
  const y = (value) => M.top + PLOT_H - (Math.max(0, Number(value) || 0) / max) * PLOT_H
  const lastIndex = data.length - 1

  return (
    <div>
      <div className="chart-head">
        <h3 style={{ margin: 0 }}>{title}</h3>
        <div className="row" style={{ gap: 8 }}>
          <div className="legend legend-chips">
            {series.map((s, i) => (
              <span className="legend-chip" key={s.key}>
                <span className="swatch" style={{ background: s.color }} aria-hidden="true" />
                {s.label}
                <b>{money(totals[i])}</b>
              </span>
            ))}
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
          rowKey={(row) => row[xKey]}
          columns={[
            { key: 'month', header: 'Month', render: (row) => monthName(row[xKey]) },
            ...series.map((s) => ({
              key: s.key,
              header: s.label,
              num: true,
              render: (row) => fmt(row[s.key]),
            })),
          ]}
        />
      ) : (
        <div className="chart-frame">
          <svg
            className="chart-svg bars-svg"
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={`${title}. ${series.map((s) => s.label).join(' and ')} per month.`}
            onMouseLeave={() => setHover(null)}
          >
            <defs>
              {series.map((s) => (
                <linearGradient key={s.key} id={`${uid}-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" style={{ stopColor: s.color }} />
                  <stop offset="1" style={{ stopColor: s.deep || s.color }} />
                </linearGradient>
              ))}
            </defs>

            {/* the month in progress: a faint band behind its bars */}
            {currentLast && data.length ? (
              <rect
                className="now-band"
                x={M.left + groupWidth * lastIndex + 2}
                y={M.top - 8}
                width={groupWidth - 4}
                height={PLOT_H + 8}
                rx={8}
              />
            ) : null}

            {/* recessive gridlines and value axis */}
            {ticks.map((tick) => (
              <g key={tick}>
                <line className="tick-line" x1={M.left} x2={W - M.right} y1={y(tick)} y2={y(tick)} />
                <text x={M.left - 10} y={y(tick) + 3} textAnchor="end">
                  {compact(tick)}
                </text>
              </g>
            ))}
            <line className="baseline" x1={M.left} x2={W - M.right} y1={M.top + PLOT_H} y2={M.top + PLOT_H} />

            {data.map((row, index) => {
              const centre = M.left + groupWidth * (index + 0.5)
              const totalWidth = barWidth * series.length + BAR_GAP * (series.length - 1)
              const startX = centre - totalWidth / 2
              const hovered = hover === index
              return (
                <g key={row[xKey]} className={hovered ? 'group hovered' : 'group'}>
                  {hovered ? (
                    <rect
                      className="hover-band"
                      x={M.left + groupWidth * index + 1}
                      y={M.top - 8}
                      width={groupWidth - 2}
                      height={PLOT_H + 8}
                      rx={8}
                    />
                  ) : null}
                  {series.map((s, si) => {
                    const value = Number(row[s.key]) || 0
                    const top = y(value)
                    const x = startX + si * (barWidth + BAR_GAP)
                    return (
                      <g key={s.key}>
                        <path
                          className="bar"
                          d={barPath(x, top, barWidth, M.top + PLOT_H - top)}
                          fill={`url(#${uid}-${s.key})`}
                          style={{ animationDelay: `${index * 35 + si * 60}ms` }}
                        />
                        {hovered && value > 0 ? (
                          <text className="bar-value" x={x + barWidth / 2} y={top - 5} textAnchor="middle">
                            {compact(value)}
                          </text>
                        ) : null}
                      </g>
                    )
                  })}
                  <rect
                    className="hit"
                    x={M.left + groupWidth * index}
                    y={M.top - 10}
                    width={groupWidth}
                    height={PLOT_H + 10}
                    tabIndex={0}
                    role="button"
                    aria-label={`${monthName(row[xKey])}: ${series
                      .map((s) => `${s.label} ${money(row[s.key])}`)
                      .join(', ')}`}
                    onMouseEnter={() => setHover(index)}
                    onFocus={() => setHover(index)}
                    onBlur={() => setHover(null)}
                  />
                  <text
                    className={currentLast && index === lastIndex ? 'axis-now' : undefined}
                    x={centre}
                    y={H - 9}
                    textAnchor="middle"
                  >
                    {monthLabel(row[xKey], { first: index === 0 })}
                  </text>
                </g>
              )
            })}
          </svg>

          {hover !== null && data[hover] ? (
            <div
              className="chart-tooltip"
              style={{
                left: `calc(${((M.left + groupWidth * (hover + 0.5)) / W) * 100}% + 8px)`,
                top: 0,
                transform: hover > data.length / 2 ? 'translateX(-108%)' : 'none',
              }}
            >
              <div className="tt-title">
                {monthName(data[hover][xKey])}
                {currentLast && hover === lastIndex ? ' · so far' : ''}
              </div>
              {series.map((s) => (
                <div className="tt-row" key={s.key}>
                  <span>
                    <span className="swatch" style={{ background: s.color }} aria-hidden="true" />
                    {s.label}
                  </span>
                  <span className="tt-val">{money(data[hover][s.key])}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
