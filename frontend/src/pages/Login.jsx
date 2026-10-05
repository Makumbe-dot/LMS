import { useEffect, useState } from 'react'

import { HexMark } from '../components/ui.jsx'
import { useAuth } from '../lib/auth.jsx'

/**
 * The sign-in page: a full-height story panel on the left that turns through what
 * the system does, and the form on the right. Below 960px the story panel shrinks
 * to a banner above the form.
 *
 * Each slide looks for a photograph at /login/slide-N.jpg (frontend/public/login/).
 * None ships with the code, because a stock photo needs a licence; until one is
 * dropped in, the slide shows the brand gradient instead, which is a finished
 * look on its own, not a placeholder.
 */
const SLIDES = [
  {
    tag: 'Built for microfinance',
    title: 'Lending that keeps its own books',
    body: 'Quote, approve, disburse and collect salary and group loans, while the ledger, arrears and provisions keep up on their own.',
  },
  {
    tag: 'Collections',
    title: 'Every payment, placed',
    body: 'EcoCash and bank payments post themselves to the right loan. Anything uncertain waits for a person, never a guess.',
  },
  {
    tag: 'Control',
    title: 'Four eyes on every dollar',
    body: 'Maker-checker approvals, teller tills, bank reconciliation and period close, with a trial balance that always balances.',
  },
  {
    tag: 'Fair to borrowers',
    title: 'Clear costs, humane rules',
    body: 'The APR and every fee on the agreement, holidays that move due dates, and no penalties while a credit-life claim is open.',
  },
]

const FEATURES = [
  { icon: 'loans', title: 'Salary & group loans', sub: 'Monthly, fortnightly, weekly' },
  { icon: 'phone', title: 'Mobile money', sub: 'Payments post on arrival' },
  { icon: 'shield', title: 'IFRS 9 ready', sub: 'Staging and provisions' },
]

const SLIDE_MS = 6500
const photo = (n) => `${import.meta.env.BASE_URL}login/slide-${n}.jpg`

function Icon({ name }) {
  const common = {
    width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
  }
  if (name === 'phone') {
    return (
      <svg {...common}>
        <rect x="6" y="2.5" width="12" height="19" rx="2.5" />
        <path d="M10.5 18.5h3M9 8.5l2 2 4-4" />
      </svg>
    )
  }
  if (name === 'shield') {
    return (
      <svg {...common}>
        <path d="M12 2.8 19.5 6v5.6c0 4.6-3.1 8.2-7.5 9.6-4.4-1.4-7.5-5-7.5-9.6V6z" />
        <path d="m8.8 12 2.2 2.2 4.3-4.4" />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <rect x="2.5" y="6" width="19" height="12.5" rx="2.5" />
      <circle cx="12" cy="12.25" r="2.6" />
      <path d="M6 9.5v5.5M18 9.5v5.5" />
    </svg>
  )
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!query) return undefined
    setReduced(query.matches)
    const onChange = (e) => setReduced(e.matches)
    query.addEventListener?.('change', onChange)
    return () => query.removeEventListener?.('change', onChange)
  }, [])
  return reduced
}

function Story() {
  const [active, setActive] = useState(0)
  const reduced = useReducedMotion()

  // With reduced motion asked for, the slides change only when a bar is chosen.
  useEffect(() => {
    if (reduced) return undefined
    const timer = setTimeout(() => setActive((i) => (i + 1) % SLIDES.length), SLIDE_MS)
    return () => clearTimeout(timer)
  }, [active, reduced])

  const slide = SLIDES[active]
  return (
    <section className="story" aria-label="About the system">
      {SLIDES.map((s, i) => (
        <div
          key={s.title}
          className={`story-photo${i === active ? ' on' : ''}`}
          style={{ '--photo': `url("${photo(i + 1)}")` }}
          aria-hidden="true"
        />
      ))}
      <div className="story-shade" aria-hidden="true" />

      <div className="story-brand">
        <span className="story-logo">
          <HexMark size={30} />
        </span>
        <span>
          <strong>Loan Management System</strong>
          <small>Microfinance lending, collections and books</small>
        </span>
      </div>

      <div className="story-copy" key={active}>
        <span className="story-tag">
          <HexMark size={12} />
          {slide.tag}
        </span>
        <h2>{slide.title}</h2>
        <p>{slide.body}</p>
      </div>

      <div className="story-progress" role="tablist" aria-label="Choose a slide">
        {SLIDES.map((s, i) => (
          <button
            key={s.title}
            type="button"
            role="tab"
            aria-selected={i === active}
            aria-label={s.title}
            className={i < active ? 'done' : i === active ? 'on' : ''}
            style={{ '--slide-ms': `${SLIDE_MS}ms` }}
            onClick={() => setActive(i)}
          >
            <span />
          </button>
        ))}
      </div>

      <ul className="story-features">
        {FEATURES.map((f) => (
          <li key={f.title}>
            <span className="story-icon">
              <Icon name={f.icon} />
            </span>
            <span>
              <strong>{f.title}</strong>
              <small>{f.sub}</small>
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function Login() {
  const { signIn, completeSignIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
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
      <Story />

      <main className="signin-side">
        <form className="signin-card" onSubmit={onSubmit} aria-labelledby="signin-title">
          <div className="signin-mark">
            <HexMark size={40} />
          </div>
          <h1 className="signin-wordmark" id="signin-title">
            {mfaToken ? 'One more step' : 'Welcome back'}
          </h1>
          <p className="signin-welcome">
            {mfaToken
              ? 'Confirm it is you with your authenticator app.'
              : 'Sign in to the Loan Management System.'}
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
                placeholder="123 456"
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
                placeholder="e.g. tmoyo"
                required
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
              <label className="signin-label" htmlFor="signin-password">
                Password
              </label>
              <div className="signin-password">
                <input
                  id="signin-password"
                  className="signin-input"
                  type={showPassword ? 'text' : 'password'}
                  name="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="signin-reveal"
                  aria-pressed={showPassword}
                  onClick={() => setShowPassword((v) => !v)}
                >
                  {showPassword ? 'Hide' : 'Show'}
                </button>
              </div>
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
