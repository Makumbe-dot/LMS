import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { renderPage, stubApi } from '../test/harness.jsx'
import Jobs from './Jobs.jsx'

const JOBS = [
  {
    key: 'penalties',
    label: 'Penalty accrual',
    schedule: 'daily',
    about: 'Penalties on overdue instalments.',
    last_status: 'failed',
    last_run_at: '2026-04-10T22:00:00Z',
    last_error: 'RuntimeError: boom',
    last_success_at: null,
    stale: true,
    needs_attention: true,
  },
]

describe('the scheduled jobs page', () => {
  it('says which job needs attention and runs it on request', async () => {
    const { postSpy } = stubApi({
      get: { '/api/jobs/runs': { count: 0, results: [] }, '/api/jobs': JOBS },
      post: { '/api/jobs/penalties/run': { status: 'ok' } },
    })
    renderPage(<Jobs />)
    expect(await screen.findByText(/1 job\(s\) need attention/)).toBeInTheDocument()
    expect(screen.getByText(/scheduled task on the server/)).toBeInTheDocument()
    expect(screen.getByText('RuntimeError: boom')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(postSpy.mock.calls[0][0]).toBe('/api/jobs/penalties/run'))
  })
})
