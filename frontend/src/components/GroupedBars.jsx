/* Grouped bar chart: two series over 12 months.

   Two series, so a legend is always present and identity never rests on colour
   alone; every group also carries a hover/focus tooltip with both values, and a
   table view is one click away for screen readers, print and forced colours. */
import { useMemo, useState } from 'react'

import { compact, fmt, money } from '../lib/format.js'
import DataTable from './DataTable.jsx'

const W = 760
const H = 230
const M = { top: 10, right: 10, bottom: 28, left: 52 }
const PLOT_W = W - M.left - M.right
const PLOT_H = H - M.top - M.bottom
const BAR_GAP = 2 // surface gap between the two bars of a pair
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

/** Round a maximum up to a readable axis top. */
function niceMax(value) {
  if (value <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  for (const step of [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10]) {
    if (value <= step * magnitude) return step * magnitude
  }
  return 10 * magnitude
}

export default function GroupedBars({
  data,
  title,
  subtitle,
  series = [
    { key: 'disbursed', label: 'Disbursed', color: 'var(--series-1)' },
    { key: 'collected', label: 'Collected', color: 'var(--series-2)' },
  ],
  xKey = 'month',
  xLabel = (row) => String(row[xKey]).slice(2), // 2026-04 -> 26-04
}) {
  const [hover, setHover] = useState(null)
  const [view, setView] = useState('chart')

  const max = useMemo(
    () => niceMax(Math.max(0, ...data.flatMap((row) => series.map((s) => Number(row[s.key]) || 0)))),
    [data, series],
  )
  const ticks = useMemo(() => [0, 0.25, 0.5, 0.75, 1].map((f) => max * f), [max])

  const groupWidth = PLOT_W / Math.max(data.length, 1)
  const barWidth = Math.min(16, (groupWidth * 0.62 - BAR_GAP) / series.length)
  const y = (value) => M.top + PLOT_H - (Math.max(0, Number(value) || 0) / max) * PLOT_H

  return (
    <div>
      <div className="chart-head">
        <h3 style={{ margin: 0 }}>{title}</h3>
        <div className="row" style={{ gap: 8 }}>
          <div className="legend">
            {series.map((s) => (
              <span key={s.key}>
                <span className="swatch" style={{ background: s.color }} aria-hidden="true" />
                {s.label}
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
            { key: 'month', header: 'Month', render: (row) => row[xKey] },
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
            className="chart-svg"
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={`${title}. ${series.map((s) => s.label).join(' and ')} per month.`}
            onMouseLeave={() => setHover(null)}
          >
            {/* recessive gridlines and value axis */}
            {ticks.map((tick) => (
              <g key={tick}>
                <line
                  className="tick-line"
                  x1={M.left}
                  x2={W - M.right}
                  y1={y(tick)}
                  y2={y(tick)}
                />
                <text x={M.left - 8} y={y(tick) + 3} textAnchor="end">
                  {compact(tick)}
                </text>
              </g>
            ))}
            <line
              className="baseline"
              x1={M.left}
              x2={W - M.right}
              y1={M.top + PLOT_H}
              y2={M.top + PLOT_H}
            />

            {data.map((row, index) => {
              const centre = M.left + groupWidth * (index + 0.5)
              const totalWidth = barWidth * series.length + BAR_GAP * (series.length - 1)
              const startX = centre - totalWidth / 2
              return (
                <g key={row[xKey]} className={hover === index ? 'group hovered' : 'group'}>
                  <rect
                    className="hit"
                    x={M.left + groupWidth * index}
                    y={M.top}
                    width={groupWidth}
                    height={PLOT_H}
                    tabIndex={0}
                    role="button"
                    aria-label={`${row[xKey]}: ${series
                      .map((s) => `${s.label} ${money(row[s.key])}`)
                      .join(', ')}`}
                    onMouseEnter={() => setHover(index)}
                    onFocus={() => setHover(index)}
                    onBlur={() => setHover(null)}
                  />
                  {series.map((s, si) => {
                    const value = Number(row[s.key]) || 0
                    const top = y(value)
                    return (
                      <path
                        key={s.key}
                        className="bar"
                        d={barPath(
                          startX + si * (barWidth + BAR_GAP),
                          top,
                          barWidth,
                          M.top + PLOT_H - top,
                        )}
                        fill={s.color}
                      />
                    )
                  })}
                  <text x={centre} y={H - 8} textAnchor="middle">
                    {xLabel(row)}
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
              <div className="tt-title">{data[hover][xKey]}</div>
              {series.map((s) => (
                <div className="tt-row" key={s.key}>
                  <span>
                    <span className="swatch" style={{ background: s.color }} aria-hidden="true" />
                    {s.label}
                  </span>
                  <span className="tt-val">{fmt(data[hover][s.key])}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
