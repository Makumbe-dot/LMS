/* The 5 x 5 risk grid, and the rating chip used wherever a score is shown.

   Bands mirror core/services/risks.py: 1-4 low, 5-9 medium, 10-16 high,
   20-25 critical. The band is always written out as well as coloured, so the
   grid and the chips read the same without colour. */

export const LIKELIHOOD = ['Rare', 'Unlikely', 'Possible', 'Likely', 'Almost certain']
export const IMPACT = ['Insignificant', 'Minor', 'Moderate', 'Major', 'Severe']

export function band(score) {
  if (score >= 20) return 'critical'
  if (score >= 10) return 'high'
  if (score >= 5) return 'medium'
  return 'low'
}

const BAND_LABEL = { low: 'Low', medium: 'Medium', high: 'High', critical: 'Critical' }

export function RatingChip({ likelihood, impact }) {
  if (!likelihood || !impact) return <span>-</span>
  const score = likelihood * impact
  const rating = band(score)
  return (
    <span
      className={`rating rating-${rating}`}
      title={`Likelihood ${likelihood} (${LIKELIHOOD[likelihood - 1]}) × impact ${impact} (${IMPACT[impact - 1]})`}
    >
      {score} · {BAND_LABEL[rating]}
    </span>
  )
}

/**
 * cells: [{ likelihood, impact, count }] — only the non-empty ones, as the API sends them.
 * selected: { likelihood, impact } or null. onSelect(cell | null) toggles a cell.
 */
export default function RiskHeatMap({ cells = [], selected, onSelect, caption }) {
  const counts = new Map(cells.map((c) => [`${c.likelihood}-${c.impact}`, c.count]))
  const rows = [5, 4, 3, 2, 1]
  const cols = [1, 2, 3, 4, 5]

  return (
    <div className="heatmap" role="group" aria-label={caption}>
      <div className="heatmap-y-title">Likelihood</div>
      <div className="heatmap-grid">
        {rows.map((likelihood) => (
          <div className="heatmap-row" key={likelihood}>
            <div className="heatmap-y" title={LIKELIHOOD[likelihood - 1]}>
              {likelihood}
            </div>
            {cols.map((impact) => {
              const count = counts.get(`${likelihood}-${impact}`) || 0
              const rating = band(likelihood * impact)
              const isSelected =
                selected?.likelihood === likelihood && selected?.impact === impact
              return (
                <button
                  type="button"
                  key={impact}
                  className={`heatmap-cell rating-${rating}${count ? '' : ' empty'}${
                    isSelected ? ' selected' : ''
                  }`}
                  aria-pressed={isSelected}
                  aria-label={`Likelihood ${likelihood} (${LIKELIHOOD[likelihood - 1]}), impact ${impact} (${IMPACT[impact - 1]}): ${count} ${count === 1 ? 'risk' : 'risks'}, ${BAND_LABEL[rating].toLowerCase()}`}
                  disabled={!count && !isSelected}
                  onClick={() => onSelect(isSelected ? null : { likelihood, impact })}
                >
                  {count || ''}
                </button>
              )
            })}
          </div>
        ))}
        <div className="heatmap-row">
          <div className="heatmap-y" />
          {cols.map((impact) => (
            <div className="heatmap-x" key={impact} title={IMPACT[impact - 1]}>
              {impact}
            </div>
          ))}
        </div>
      </div>
      <div className="heatmap-x-title">Impact</div>
    </div>
  )
}
