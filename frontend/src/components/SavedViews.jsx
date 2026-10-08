import { useState } from 'react'

import { del, post, qs } from '../lib/api.js'
import { useApi } from '../lib/useApi.js'
import { useToast } from './Toast.jsx'

/**
 * Named sets of filters for a list page, kept on the server for the signed-in
 * user. `current` is the page's filters now; choosing a view hands its filters
 * to `onApply`. Only filters that differ from the page's blank state are saved.
 */
export default function SavedViews({ page, current, onApply }) {
  const { toast, toastError } = useToast()
  const { data, reload } = useApi(`/api/saved-views${qs({ page })}`)
  const [chosen, setChosen] = useState('')
  const views = data || []
  const active = Object.fromEntries(
    Object.entries(current).filter(([, v]) => v !== '' && v !== false && v != null),
  )

  async function save() {
    const named = views.find((v) => String(v.id) === chosen)
    const name = window.prompt('Name these filters', named ? named.name : '')
    if (!name || !name.trim()) return
    try {
      const view = await post('/api/saved-views', { page, name: name.trim(), params: active })
      toast(`Saved “${view.name}”`)
      setChosen(String(view.id))
      reload()
    } catch (err) {
      toastError(err)
    }
  }

  async function remove() {
    const view = views.find((v) => String(v.id) === chosen)
    if (!view || !window.confirm(`Delete the saved view “${view.name}”?`)) return
    try {
      await del(`/api/saved-views/${view.id}`)
      setChosen('')
      reload()
    } catch (err) {
      toastError(err)
    }
  }

  return (
    <span className="saved-views" role="group" aria-label="Saved filters">
      <select
        value={chosen}
        aria-label="Saved filters"
        onChange={(e) => {
          setChosen(e.target.value)
          const view = views.find((v) => String(v.id) === e.target.value)
          if (view) onApply(view.params)
        }}
      >
        <option value="">Saved filters…</option>
        {views.map((v) => (
          <option key={v.id} value={v.id}>
            {v.name}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn small"
        onClick={save}
        disabled={!Object.keys(active).length}
        title={Object.keys(active).length ? 'Save the filters set now' : 'Set a filter first'}
      >
        Save
      </button>
      {chosen ? (
        <button type="button" className="btn small" onClick={remove} aria-label="Delete saved view">
          Delete
        </button>
      ) : null}
    </span>
  )
}
