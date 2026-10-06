import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import * as api from './api.js'
import { AuthProvider, useAuth } from './auth.jsx'

const USER = { id: 1, username: 'teller', full_name: 'Teller', role: 'user', rights: ['cash'] }

function Probe() {
  const { user, status, signIn, signOut, signOutEverywhere, can } = useAuth()
  // signOutEverywhere RETHROWS on purpose: the local session is cleared either
  // way, but the user needs to be told when the server could not be reached,
  // because their other devices are then still signed in. Every caller therefore
  // has to catch — Account.jsx does, and so does this probe.
  const [failure, setFailure] = useState('')
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="user">{user?.username ?? 'none'}</span>
      <span data-testid="can-cash">{String(can('cash'))}</span>
      <span data-testid="can-approve">{String(can('approve'))}</span>
      <span data-testid="is-admin">{String(can('admin'))}</span>
      <span data-testid="failure">{failure}</span>
      <button type="button" onClick={() => signIn('teller', 'teller123')}>in</button>
      <button type="button" onClick={signOut}>out</button>
      <button
        type="button"
        onClick={() => signOutEverywhere().catch((err) => setFailure(err.message))}
      >
        out everywhere
      </button>
    </div>
  )
}

const mount = () => render(<AuthProvider><Probe /></AuthProvider>)

describe('AuthProvider', () => {
  it('starts anonymous with no stored token', () => {
    mount()
    expect(screen.getByTestId('status')).toHaveTextContent('anonymous')
  })

  it('stores BOTH tokens on sign-in', async () => {
    vi.spyOn(api, 'login').mockResolvedValue({
      access_token: 'a', refresh_token: 'r', user: USER,
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'in' }))

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'))
    expect(api.getToken()).toBe('a')
    // Without the refresh token the session would die in thirty minutes with no
    // way to renew, which is the whole point of storing it.
    expect(api.getRefreshToken()).toBe('r')
    expect(screen.getByTestId('can-cash')).toHaveTextContent('true')
  })

  it('resumes a session from a stored refresh token alone', async () => {
    // The usual case on a page reload: the access token expired hours ago, so
    // resuming depends on api.js renewing it rather than on it still being valid.
    api.setTokens({ access_token: 'stale', refresh_token: 'still-good' })
    vi.spyOn(api, 'get').mockResolvedValue(USER)
    mount()
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('teller'))
  })

  it('drops the session when resuming fails', async () => {
    api.setTokens({ access_token: 'dead', refresh_token: 'dead' })
    vi.spyOn(api, 'get').mockRejectedValue(new api.ApiError('expired', 401))
    mount()
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('anonymous'))
    expect(api.getToken()).toBeNull()
    expect(api.getRefreshToken()).toBeNull()
  })

  it('tells the server to retire the refresh token when signing out', async () => {
    api.setTokens({ access_token: 'a', refresh_token: 'r' })
    vi.spyOn(api, 'get').mockResolvedValue(USER)
    const post = vi.spyOn(api, 'post').mockResolvedValue({ detail: 'Signed out.' })
    mount()
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'))

    await userEvent.click(screen.getByRole('button', { name: 'out' }))

    // Clearing localStorage alone would leave a working refresh token on the device.
    expect(post).toHaveBeenCalledWith('/api/auth/logout', { refresh_token: 'r' })
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('anonymous'))
    expect(api.getRefreshToken()).toBeNull()
  })

  it('signs out locally even when the server cannot be reached', async () => {
    // A network error must not leave someone stuck signed in at a shared counter.
    api.setTokens({ access_token: 'a', refresh_token: 'r' })
    vi.spyOn(api, 'get').mockResolvedValue(USER)
    vi.spyOn(api, 'post').mockRejectedValue(new api.ApiError('Cannot reach the server', 0))
    mount()
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'))

    await userEvent.click(screen.getByRole('button', { name: 'out' }))

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('anonymous'))
    expect(api.getToken()).toBeNull()
  })

  it('clears the local session but reports the failure if the server refuses', async () => {
    api.setTokens({ access_token: 'a', refresh_token: 'r' })
    vi.spyOn(api, 'get').mockResolvedValue(USER)
    vi.spyOn(api, 'post').mockRejectedValue(new api.ApiError('boom', 500))
    mount()
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'))

    await userEvent.click(screen.getByRole('button', { name: 'out everywhere' }))

    // This device is signed out...
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('anonymous'))
    // ...but the caller is told, because the OTHER devices may still be signed in.
    expect(screen.getByTestId('failure')).toHaveTextContent('boom')
  })

  it('ends every session when the server accepts', async () => {
    api.setTokens({ access_token: 'a', refresh_token: 'r' })
    vi.spyOn(api, 'get').mockResolvedValue(USER)
    const post = vi.spyOn(api, 'post').mockResolvedValue({ detail: 'done' })
    mount()
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'))

    await userEvent.click(screen.getByRole('button', { name: 'out everywhere' }))

    expect(post).toHaveBeenCalledWith('/api/auth/sign-out-everywhere')
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('anonymous'))
    expect(screen.getByTestId('failure')).toHaveTextContent('')
  })

  it('does not sign in on the password alone when the account has two-factor on', async () => {
    vi.spyOn(api, 'login').mockResolvedValue({ mfa_required: true, mfa_token: 'half' })
    const verify = vi.spyOn(api, 'loginVerify').mockResolvedValue({
      access_token: 'a', refresh_token: 'r', user: USER,
    })
    let pending = null
    function TwoStep() {
      const { status, signIn, completeSignIn } = useAuth()
      return (
        <div>
          <span data-testid="status">{status}</span>
          <button type="button" onClick={async () => { pending = await signIn('teller', 'x') }}>
            password
          </button>
          <button type="button" onClick={() => completeSignIn(pending.mfaToken, '123456')}>
            code
          </button>
        </div>
      )
    }
    render(<AuthProvider><TwoStep /></AuthProvider>)

    await userEvent.click(screen.getByRole('button', { name: 'password' }))
    // Nothing stored, nobody signed in: the password was only the first half.
    expect(pending).toEqual({ mfaToken: 'half' })
    expect(screen.getByTestId('status')).toHaveTextContent('anonymous')
    expect(api.getToken()).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'code' }))
    expect(verify).toHaveBeenCalledWith('half', '123456')
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'))
    expect(api.getRefreshToken()).toBe('r')
  })

  it('answers can() for the rights the signed-in user holds, and no others', async () => {
    vi.spyOn(api, 'login').mockResolvedValue({
      access_token: 'a', refresh_token: 'r', user: USER,
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'in' }))
    await waitFor(() => expect(screen.getByTestId('can-cash')).toHaveTextContent('true'))
    expect(screen.getByTestId('can-approve')).toHaveTextContent('false')
    expect(screen.getByTestId('is-admin')).toHaveTextContent('false')
  })
})
