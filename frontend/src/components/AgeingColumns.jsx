/* Arrears ageing: how much of the book is current, then how the rest is spread
   across the days-overdue buckets.

   "Current" is usually most of the book, so drawn on one scale with the overdue
   buckets it flattens them to slivers. It is taken out and stated as a share in
   a meter of its own; the overdue buckets are then drawn against each other, as
   columns on an ordered ramp that deepens with the days. Each column carries its
   amount on its cap and its loan count under its label, so nothing is read from
   colour alone; hover or focus a column for the exact figure. */
import { useState } from 'react'

import { compact, money, num } from '../lib/format.js'

const W = 340
const H = 168
const M = { top: 20, right: 4, bottom: 34, left: 4 }
const PLOT_H = H - M.top - M.bottom
const BAR_MAX = 30

function capPath(x, y, width, height, r = 4) {
  if (height <= 0) return ''
  const rr = Math.min(r, width / 2, height)
  return `M${x},${y + height}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + width - rr}Q${x + width},${y} ${
    x + width
  },${y + rr}V${y + height}Z`
}

export default function AgeingColumns({ current, buckets, total }) {
  const [hover, setHover] = useState(null)
  const whole = num(total) ?? 0
  const currentValue = num(current?.value) ?? 0
  const overdue = buckets.reduce((sum, b) => sum + (num(b.value) ?? 0), 0)
  const currentShare = whole > 0 ? (currentValue / whole) * 100 : 0
  const max = Math.max(1, ...buckets.map((b) => num(b.value) ?? 0))

  const col = (W - M.left - M.right) / Math.max(buckets.length, 1)
  const barW = Math.min(BAR_MAX, col * 0.56)
  const y = (v) => M.top + PLOT_H - ((num(v) ?? 0) / max) * PLOT_H
  const floor = M.top + PLOT_H

  return (
    <div className="ageing">
      <div className="ageing-meter">
        <div className="ageing-meter-head">
          <span className="ageing-big">{currentShare.toFixed(currentShare >= 99.95 ? 0 : 1)}%</span>
          <span className="ageing-caption">
            of principal is current
            <small>
              {money(currentValue)} on {current?.loans ?? 0} loan{current?.loans === 1 ? '' : 's'}
            </small>
          </span>
        </div>
        <div
          className="meter"
          role="img"
          aria-label={`${currentShare.toFixed(1)}% of principal is current, ${(100 - currentShare).toFixed(1)}% overdue`}
        >
          <span className="meter-fill" style={{ width: `${currentShare}%` }} />
        </div>
      </div>

      <div className="ageing-cols-head">
        <span>Overdue by days</span>
        <b>{money(overdue)}</b>
      </div>
      {overdue > 0 ? (
        <div className="chart-frame">
          <svg
            className="chart-svg ageing-svg"
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={`Overdue principal by days: ${buckets
              .map((b) => `${b.label} ${money(b.value)}, ${b.loans} loans`)
              .join('; ')}.`}
            onMouseLeave={() => setHover(null)}
          >
            <line className="baseline" x1={M.left} x2={W - M.right} y1={floor} y2={floor} />
            {buckets.map((b, i) => {
              const cx = M.left + col * (i + 0.5)
              const top = y(b.value)
              const value = num(b.value) ?? 0
              return (
                <g key={b.label} className={hover === i ? 'col hovered' : 'col'}>
                  {hover === i ? (
                    <rect className="hover-band" x={cx - col / 2 + 2} y={M.top - 16} width={col - 4} height={PLOT_H + 16} rx={8} />
                  ) : null}
                  <path
                    className="bar"
                    d={capPath(cx - barW / 2, top, barW, floor - top)}
                    style={{ fill: b.color, animationDelay: `${i * 70 + 150}ms` }}
                  />
                  {value > 0 ? (
                    <text className="col-value" x={cx} y={top - 6} textAnchor="middle">
                      {compact(value)}
                    </text>
                  ) : null}
                  <text className="col-label" x={cx} y={floor + 14} textAnchor="middle">
                    {b.short}
                  </text>
                  <text className="col-note" x={cx} y={floor + 27} textAnchor="middle">
                    {b.loans} loan{b.loans === 1 ? '' : 's'}
                  </text>
                  <rect
                    className="hit"
                    x={cx - col / 2}
                    y={M.top - 16}
                    width={col}
                    height={H - M.top + 16}
                    tabIndex={0}
                    role="button"
                    aria-label={`${b.label}: ${money(b.value)} on ${b.loans} loans`}
                    onMouseEnter={() => setHover(i)}
                    onFocus={() => setHover(i)}
                    onBlur={() => setHover(null)}
                  />
                </g>
              )
            })}
          </svg>
          {hover !== null ? (
            <div
              className="chart-tooltip"
              style={
                hover >= buckets.length / 2
                  ? { left: `calc(${((M.left + col * hover) / W) * 100}% - 4px)`, top: 0, transform: 'translateX(-100%)' }
                  : { left: `calc(${((M.left + col * (hover + 1)) / W) * 100}% + 4px)`, top: 0 }
              }
            >
              <div className="tt-title">{buckets[hover].label} overdue</div>
              <div className="tt-row">
                <span>Principal</span>
                <span className="tt-val">{money(buckets[hover].value)}</span>
              </div>
              <div className="tt-row">
                <span>Loans</span>
                <span className="tt-val">{buckets[hover].loans}</span>
              </div>
              <div className="tt-foot">{buckets[hover].share} of principal outstanding</div>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="dash-empty">Nothing is overdue.</p>
      )}
    </div>
  )
}
