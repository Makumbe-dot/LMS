/* The credit scorecard: a score, a grade and the reason for every factor.

   Advisory only — the hard rules (affordability, KYC, blacklist, one open loan
   at a time) are enforced by the API, not by this number. */

const GRADE_CLASS = { A: 'tag-ok', B: 'tag-ok', C: 'tag-warn', D: 'tag-warn', E: 'tag-danger' }

export default function Scorecard({ card, compact = false }) {
  if (!card) return null

  return (
    <div className="scorecard">
      <div className="score-head">
        <div className="score-value">
          {card.score}
          <span className="score-max">/100</span>
        </div>
        <div>
          <div className={`score-grade ${GRADE_CLASS[card.grade] || ''}`}>{card.grade_label}</div>
          <div className="muted" style={{ fontSize: 12 }}>{card.summary}</div>
        </div>
      </div>

      {compact ? null : (
        <div className="score-factors">
          {card.factors.map((factor) => (
            <div className="score-factor" key={factor.factor}>
              <div className="score-factor-head">
                <span>{factor.factor}</span>
                <span className="score-points">
                  {factor.points}
                  {factor.max ? ` / ${factor.max}` : ''}
                </span>
              </div>
              {factor.max ? (
                <div className="score-track">
                  <div
                    className="score-fill"
                    style={{ width: `${Math.round((factor.points / factor.max) * 100)}%` }}
                  />
                </div>
              ) : null}
              <div className="score-reason">{factor.reason}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
