import { useEffect, useState } from 'react'

import Icon from '../components/Icons.jsx'
import { HexMark } from '../components/ui.jsx'
import { useAuth } from '../lib/auth.jsx'

/**
 * The sign-in page: a panel that turns through what the system does, and the
 * form beside it. Below 900px the panel becomes a short banner above the form.
 *
 * Each slide has its own animated scene, and looks for a photograph at
 * /login/slide-N.jpg (frontend/public/login/). None ships with the code, because
 * a stock photo needs a licence; until one is dropped in, the slide shows its
 * scene alone, which is a finished look on its own, not a placeholder.
 */
const SLIDES = [
  {
    tag: 'Built for microfinance',
    title: 'Lending that keeps its own books.',
    body: 'Quote, approve, disburse and collect, while the ledger, arrears and provisions keep up on their own.',
    scene: 'dawn',
    points: [
      { icon: 'loans', title: 'Salary and group loans', sub: 'Monthly, fortnightly or weekly' },
      { icon: 'book', title: 'A ledger that keeps itself', sub: 'Every movement posted, every month reconciled' },
      { icon: 'shield', title: 'IFRS 9 staging and provisions', sub: 'With a trial balance that always balances' },
    ],
  },
  {
    tag: 'Collections',
    title: 'Every payment, placed.',
    body: 'Cash at the till, payroll deductions, bank transfers and mobile money all land on the right instalment, oldest first.',
    scene: 'tide',
    points: [
      { icon: 'till', title: 'Teller tills', sub: 'Counted against what the system recorded' },
      { icon: 'scale', title: 'Bank reconciliation', sub: 'Statement lines matched to the ledger' },
      { icon: 'message', title: 'Reminders and receipts', sub: 'SMS and email from the outbox' },
    ],
  },
  {
    tag: 'Control',
    title: 'Four eyes on every dollar.',
    body: 'Maker-checker approvals, journals that pass through a second pair of hands, and months that close in order.',
    scene: 'ember',
    points: [
      { icon: 'userCog', title: 'Maker-checker', sub: 'Nobody approves their own loan' },
      { icon: 'lock', title: 'Period close', sub: 'A signed-off month stays signed off' },
      { icon: 'history', title: 'An audit trail', sub: 'Who did what, and when' },
    ],
  },
  {
    tag: 'Fair to borrowers',
    title: 'Clear costs, humane rules.',
    body: 'The APR and every fee on the agreement, due dates that step over holidays, and a credit bureau check on the file.',
    scene: 'meadow',
    points: [
      { icon: 'percent', title: 'The APR, fees included', sub: 'On the quote and the agreement' },
      { icon: 'calendar', title: 'A holiday calendar', sub: 'Nothing falls due on a closed day' },
      { icon: 'coins', title: 'More than one currency', sub: 'Loans in local currency, books in USD' },
    ],
  },
]

const SLIDE_MS = 7000
const photo = (n) => `${import.meta.env.BASE_URL}login/slide-${n}.jpg`

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false,
  )
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!query) return undefined
    const onChange = () => setReduced(query.matches)
    query.addEventListener?.('change', onChange)
    return () => query.removeEventListener?.('change', onChange)
  }, [])
  return reduced
}

/** The slideshow panel: one slide showing, the next fading in every few seconds. */
function Slideshow() {
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const [photos, setPhotos] = useState({})
  const reduced = usePrefersReducedMotion()

  useEffect(() => {
    if (paused || reduced) return undefined
    const timer = setInterval(() => setIndex((i) => (i + 1) % SLIDES.length), SLIDE_MS)
    return () => clearInterval(timer)
  }, [paused, reduced])

  const slide = SLIDES[index]

  return (
    <aside
      className={`login-panel${reduced ? ' still' : ''}`}
      aria-label="About the system"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {SLIDES.map((s, i) => (
        <div
          key={s.tag}
          className={`login-scene scene-${s.scene}${i === index ? ' is-active' : ''}`}
          aria-hidden="true"
        >
          {photos[i] !== false ? (
            <img
              className="login-photo"
              src={photo(i + 1)}
              alt=""
              onLoad={() => setPhotos((p) => ({ ...p, [i]: true }))}
              onError={() => setPhotos((p) => ({ ...p, [i]: false }))}
            />
          ) : null}
          <span className="login-orb orb-1" />
          <span className="login-orb orb-2" />
          <span className="login-hex hex-1">
            <HexMark size={120} />
          </span>
          <span className="login-hex hex-2">
            <HexMark size={64} />
          </span>
          <span className="login-hex hex-3">
            <HexMark size={40} />
          </span>
        </div>
      ))}

      <div className="login-brand">
        <HexMark size={34} />
        <span>Loan Management System</span>
      </div>

      <div className="login-story" key={slide.tag}>
        <p className="login-tag">{slide.tag}</p>
        <h1>{slide.title}</h1>
        <p className="login-lead">{slide.body}</p>
        <ul className="login-points">
          {slide.points.map((point) => (
            <li key={point.title}>
              <span className="login-point-icon">
                <Icon name={point.icon} />
              </span>
              <span>
                <strong>{point.title}</strong>
                <small>{point.sub}</small>
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="login-dots" role="tablist" aria-label="Slides">
        {SLIDES.map((s, i) => (
          <button
            key={s.tag}
            type="button"
            role="tab"
            aria-selected={i === index}
            aria-label={`${s.tag}, slide ${i + 1} of ${SLIDES.length}`}
            className={i === index ? 'is-active' : undefined}
            onClick={() => setIndex(i)}
          >
            <span className="login-dot-fill" />
          </button>
        ))}
      </div>
    </aside>
  )
}

export default function Login() {
  const { signIn, completeSignIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  // Set once the password is right on an account with two-factor sign-in on.
  const [mfaToken, setMfaToken] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function onSubmit(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (mfaToken) {
        await completeSignIn(mfaToken, code)
      } else {
        const result = await signIn(username, password)
        if (result?.mfaToken) {
          setMfaToken(result.mfaToken)
          setPassword('')
        }
      }
    } catch (err) {
      setError(err.message)
      // An expired half-finished sign-in starts again from the password.
      if (mfaToken && err.status === 401 && /expired/i.test(err.message)) setMfaToken(null)
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <Slideshow />

      <main className="login-side">
        <form className="signin-card" onSubmit={onSubmit} aria-labelledby="signin-title">
          <div className="signin-mark">
            <HexMark size={40} />
          </div>
          <h2 className="signin-wordmark" id="signin-title">
            {mfaToken ? 'One more step' : 'Welcome back'}
          </h2>
          <p className="signin-welcome">
            {mfaToken
              ? 'Enter the six-digit code your authenticator app shows for this account.'
              : 'Sign in to your account to continue.'}
          </p>

          {error ? (
            <p className="signin-err" role="alert">
              {error}
            </p>
          ) : null}

          {mfaToken ? (
            <>
              <label className="signin-label" htmlFor="signin-code">
                Authenticator code
              </label>
              <input
                id="signin-code"
                className="signin-input signin-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9 ]{6,7}"
                maxLength={7}
                required
                autoFocus
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
              <button className="btn primary signin-btn" type="submit" disabled={busy}>
                {busy ? 'Checking…' : 'Verify and sign in'}
              </button>
              <div className="signin-hint">
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => {
                    setMfaToken(null)
                    setCode('')
                    setError('')
                  }}
                >
                  Use a different account
                </button>
                <br />
                Lost your phone? An administrator can turn two-factor sign-in off for you on the
                Users page.
              </div>
            </>
          ) : (
            <>
              <label className="signin-label" htmlFor="signin-username">
                Username
              </label>
              <input
                id="signin-username"
                className="signin-input"
                name="username"
                autoComplete="username"
                required
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
              <label className="signin-label" htmlFor="signin-password">
                Password
              </label>
              <input
                id="signin-password"
                className="signin-input"
                type="password"
                name="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button className="btn primary signin-btn" type="submit" disabled={busy}>
                {busy ? 'Signing in…' : 'Sign in'}
              </button>
              <div className="signin-hint">
                Forgot your password? Ask your administrator to reset it.
                <span className="demo">
                  Demo logins after <code>manage.py seed</code>: admin / admin123 · officer /
                  officer123 · teller / teller123 · viewer / viewer123
                </span>
              </div>
            </>
          )}
        </form>
      </main>
    </div>
  )
}
