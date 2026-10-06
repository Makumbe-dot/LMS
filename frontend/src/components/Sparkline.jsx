/* A trend with no axes, for a stat tile.

   One series and no legend: the tile's label names the measure and its value is
   the reading, so the line only has to show direction. It takes the tile's tone
   through currentColor, and its accessible name spells out the first and last
   points so the shape is not the only carrier of the trend. With `fluid` it
   stretches to the width of its box (the stroke keeps its weight), and a faint
   wash under the line gives it body without competing with the figure above. */
import { useId } from 'react'

const W = 96
const H = 28
const PAD = 3

export default function Sparkline({ values, label, fluid = false, height = H }) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '')
  const points = values.map((v) => (v === null || v === undefined ? null : Number(v)))
  const present = points.filter((v) => Number.isFinite(v))
  if (present.length < 2) return null

  const w = fluid ? 240 : W
  const h = height
  const min = Math.min(...present)
  const max = Math.max(...present)
  const span = max - min || 1
  const x = (i) => PAD + (i / Math.max(points.length - 1, 1)) * (w - PAD * 2)
  const y = (v) => h - PAD - ((v - min) / span) * (h - PAD * 2)

  let d = ''
  let pen = false
  points.forEach((v, i) => {
    if (!Number.isFinite(v)) {
      pen = false
      return
    }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
    pen = true
  })
  const firstIndex = points.findIndex((v) => Number.isFinite(v))
  const lastIndex = points.findLastIndex((v) => Number.isFinite(v))
  const unbroken = points.slice(firstIndex, lastIndex + 1).every((v) => Number.isFinite(v))
  const area = unbroken
    ? `${d}L${x(lastIndex).toFixed(1)},${h}L${x(firstIndex).toFixed(1)},${h}Z`
    : ''

  return (
    <svg
      className={fluid ? 'sparkline fluid' : 'sparkline'}
      viewBox={`0 0 ${w} ${h}`}
      width={fluid ? '100%' : w}
      height={h}
      preserveAspectRatio={fluid ? 'none' : undefined}
      role="img"
      aria-label={label}
    >
      <defs>
        <linearGradient id={`${uid}-wash`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="currentColor" stopOpacity="0.18" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      {area ? <path className="spark-area" d={area} fill={`url(#${uid}-wash)`} /> : null}
      <path
        className="draw-in"
        d={d}
        pathLength={1}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {fluid ? null : (
        <circle cx={x(lastIndex)} cy={y(points[lastIndex])} r="3" fill="currentColor" className="spark-end" />
      )}
    </svg>
  )
}
