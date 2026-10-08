import { useState } from 'react'

import { post } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'
import { useToast } from './Toast.jsx'

const SIGNABLE = ['pending', 'approved', 'active']

/**
 * The borrower's electronic signature on this loan's agreement: whether it holds
 * for the terms as they are now, and, for whoever handles applications, a code
 * texted to the borrower and the box to enter it at the counter.
 */
export default function SignaturePanel({ loan }) {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const path = SIGNABLE.includes(loan.status) ? `/api/loans/${loan.id}/signature` : null
  const { data, reload } = useApi(path)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  if (!path || !data) return null
  const mayAct = can('loans')

  async function run(promise, message) {
    setBusy(true)
    try {
      await promise
      toast(message)
      setCode('')
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  let tone = 'hint'
  let text
  if (data.signed) {
    text = (
      <>
        <strong>Signed electronically</strong> {dateTime(data.signed_at)}, by a code sent to{' '}
        {data.phone}
        {data.channel === 'portal' ? ' and entered by the borrower online' : ' at the counter'}.
      </>
    )
  } else if (data.stale) {
    tone = 'banner'
    text = (
      <>
        <strong>The signature no longer matches the terms.</strong> Something on the agreement
        changed after the borrower signed; they need to sign again.
      </>
    )
  } else {
    tone = data.required && loan.status === 'approved' ? 'banner' : 'hint'
    text = (
      <>
        <strong>Not signed electronically.</strong>{' '}
        {data.required
          ? 'The borrower must sign before the loan can be disbursed.'
          : 'Optional: the printed agreement can be signed on paper instead.'}
      </>
    )
  }

  return (
    <div className={tone} role={tone === 'banner' ? 'status' : undefined}>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ flex: 1, minWidth: 240 }}>{text}</span>
        {mayAct && !data.signed ? (
          <>
            <button
              type="button"
              className="btn small"
              disabled={busy}
              onClick={() =>
                run(post(`/api/loans/${loan.id}/signature/code`), 'Code sent to the borrower')
              }
            >
              {data.pending ? 'Send a new code' : 'Text a signing code'}
            </button>
            {data.pending ? (
              <form
                className="row"
                style={{ gap: 6 }}
                onSubmit={(e) => {
                  e.preventDefault()
                  run(
                    post(`/api/loans/${loan.id}/signature/verify`, { code }),
                    'Agreement signed',
                  )
                }}
              >
                <input
                  aria-label="Code from the borrower's phone"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  placeholder="6-digit code"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  style={{ width: 120 }}
                />
                <button
                  type="submit"
                  className="btn small primary"
                  disabled={busy || code.length < 6}
                >
                  Sign
                </button>
              </form>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  )
}
