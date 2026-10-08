import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { OFFICER, VIEWER, renderPage, stubApi } from '../test/harness.jsx'
import SignaturePanel from './SignaturePanel.jsx'

const LOAN = { id: 5, status: 'approved' }
const UNSIGNED = { required: true, signed: false, stale: false, pending: true }

describe('the signature panel', () => {
  it('sends the code the borrower reads out', async () => {
    const { postSpy } = stubApi({
      get: { '/api/loans/5/signature': UNSIGNED },
      post: { '/verify': {} },
    })
    renderPage(<SignaturePanel loan={LOAN} />, { user: OFFICER })
    expect(
      await screen.findByText(/must sign before the loan can be disbursed/i),
    ).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/code from the borrower/i), '123456')
    await userEvent.click(screen.getByRole('button', { name: 'Sign' }))
    await waitFor(() =>
      expect(postSpy).toHaveBeenCalledWith('/api/loans/5/signature/verify', { code: '123456' }),
    )
  })

  it('says when the terms changed after signing', async () => {
    stubApi({ get: { '/api/loans/5/signature': { ...UNSIGNED, stale: true, pending: false } } })
    renderPage(<SignaturePanel loan={LOAN} />, { user: VIEWER })
    expect(await screen.findByText(/no longer matches the terms/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /signing code/i })).toBeNull()
  })
})
