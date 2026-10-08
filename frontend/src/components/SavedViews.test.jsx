import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { OFFICER, renderPage, stubApi } from '../test/harness.jsx'
import SavedViews from './SavedViews.jsx'

const VIEWS = [{ id: 4, page: 'loans', name: 'My arrears', params: { in_arrears: true } }]

describe('saved filters', () => {
  it('applies a saved view and saves only the filters that are set', async () => {
    const { postSpy } = stubApi({
      get: { '/api/saved-views': VIEWS },
      post: { '/api/saved-views': { id: 5, name: 'Active' } },
    })
    const onApply = vi.fn()
    const current = { q: '', status: 'active', in_arrears: false }
    renderPage(<SavedViews page="loans" current={current} onApply={onApply} />, { user: OFFICER })
    const picker = await screen.findByLabelText('Saved filters', { selector: 'select' })
    await userEvent.selectOptions(picker, '4')
    expect(onApply).toHaveBeenCalledWith({ in_arrears: true })

    vi.spyOn(window, 'prompt').mockReturnValue('Active')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(postSpy).toHaveBeenCalledWith('/api/saved-views', {
        page: 'loans',
        name: 'Active',
        params: { status: 'active' },
      }),
    )
  })
})
