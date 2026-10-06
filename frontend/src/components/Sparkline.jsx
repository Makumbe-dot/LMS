/* A trend with no axes, for a stat tile.

   One series and no legend: the tile's label names the measure and its value is
   the reading, so the line only has to show direction. It takes the tile's tone
   through currentColor, and its accessible name spells out the first and last
   points so the shape is not the only carrier of the trend. */
const W = 96
const H = 28
const PAD = 3

export default function Sparkline({ values, label }) {
  const points = values.map((v) => (v === null || v === undefined ? null : Number(v)))
  const present = points.filter((v) => Number.isFinite(v))
  if (present.length < 2) return null

  const min = Math.min(...present)
  const max = Math.max(...present)
  const span = max - min || 1
  const x = (i) => PAD + (i / Math.max(points.length - 1, 1)) * (W - PAD * 2)
  const y = (v) => H - PAD - ((v - min) / span) * (H - PAD * 2)

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
  const lastIndex = points.findLastIndex((v) => Number.isFinite(v))

  return (
    <svg className="sparkline" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={label}>
      <path
        className="draw-in"
        d={d}
        pathLength={1}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <circle cx={x(lastIndex)} cy={y(points[lastIndex])} r="3" fill="currentColor" className="spark-end" />
    </svg>
  )
}
