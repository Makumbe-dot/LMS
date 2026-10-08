import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ToastProvider } from '../components/Toast.jsx'
import Portal from './Portal.jsx'

/**
 * The portal talks to Django on its own line, with a portal token and never a
 * staff one. Driven here through fetch, the way it really runs.
 */

const ME = {
  first_name: 'Tendai',
  full_name: 'Tendai Moyo',
  institution: 'Zinmad Capital',
  loans: [
    {
      id: 3,
      loan_no: 'LN-000003',
      status: 'active',
      status_label: 'Active',
      product: 'Salary Advance',
      currency: 'USD',
      outstanding: '591.06',
      arrears: '0',
      next_due_date: '2026-04-25',
      next_due_amount: '197.02',
    },
  ],
}

const json = (body, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }),
  )

function mount() {
  return render(
    <MemoryRouter initialEntries={['/portal']}>
      <ToastProvider>
        <Routes>
          <Route path="/portal/*" element={<Portal />} />
        </Routes>
      </ToastProvider>
    </MemoryRouter>,
  )
}

describe('the borrower portal', () => {
  let fetchSpy
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.setItem('lms_token', 'a-staff-token')
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      if (url.endsWith('/api/portal/login'))
        return json({ challenge: 'ch-1', detail: 'If those details match, a code is on its way.' })
      if (url.endsWith('/api/portal/login/verify')) return json({ token: 'portal-tok' })
      if (url.endsWith('/api/portal/me')) return json(ME)
      if (url.endsWith('/api/portal/requests')) return json([])
      return json({ detail: 'unexpected' }, 404)
    })
  })
  afterEach(() => {
    fetchSpy.mockRestore()
    localStorage.clear()
  })

  it('signs in with ID, phone and code, then shows the loans', async () => {
    mount()
    await userEvent.type(screen.getByLabelText('National ID'), '63-123456A63')
    await userEvent.type(screen.getByLabelText('Phone number'), '0771234567')
    await userEvent.click(screen.getByRole('button', { name: /text me a code/i }))
    await userEvent.type(await screen.findByLabelText(/code from the text/i), '246810')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Hello, Tendai')).toBeInTheDocument()
    expect(screen.getByText('LN-000003')).toBeInTheDocument()
    expect(sessionStorage.getItem('lms_portal_token')).toBe('portal-tok')

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith('/api/portal/me', expect.anything()))
    const meCall = fetchSpy.mock.calls.find(([url]) => url === '/api/portal/me')
    // The portal's own token, never the staff one sitting in localStorage.
    expect(meCall[1].headers.Authorization).toBe('Portal portal-tok')
  })
})
