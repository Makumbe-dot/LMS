import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { get, qs } from '../lib/api.js'
import { humanise } from '../lib/format.js'
import { useDebounced } from '../lib/useApi.js'

/** One box over borrowers and loans. Ctrl+K focuses it. */
export default function GlobalSearch() {
  const navigate = useNavigate()
  const inputRef = useRef(null)
  const boxRef = useRef(null)
  const [term, setTerm] = useState('')
  const [results, setResults] = useState(null)
  const [open, setOpen] = useState(false)
  const debounced = useDebounced(term, 250)

  useEffect(() => {
    const onKey = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        inputRef.current?.focus()
      }
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const onClick = (event) => {
      if (boxRef.current && !boxRef.current.contains(event.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  useEffect(() => {
    if (debounced.trim().length < 2) {
      setResults(null)
      return
    }
    let live = true
    get(`/api/search${qs({ q: debounced })}`)
      .then((data) => live && setResults(data))
      .catch(() => live && setResults(null))
    return () => {
      live = false
    }
  }, [debounced])

  function go(path) {
    setOpen(false)
    setTerm('')
    setResults(null)
    navigate(path)
  }

  const hits = [
    ...(results?.borrowers || []).map((r) => ({ ...r, path: `/borrowers/${r.id}`, group: 'Borrower' })),
    ...(results?.loans || []).map((r) => ({ ...r, path: `/loans/${r.id}`, group: 'Loan' })),
  ]

  return (
    <div className="global-search" ref={boxRef}>
      <input
        ref={inputRef}
        type="search"
        value={term}
        placeholder="Search borrowers and loans   (Ctrl+K)"
        aria-label="Search borrowers and loans"
        onChange={(e) => {
          setTerm(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && hits.length) go(hits[0].path)
        }}
      />
      {open && term.trim().length >= 2 ? (
        <div className="search-results" role="listbox">
          {hits.length === 0 ? (
            <div className="search-empty">No matches for “{term}”</div>
          ) : (
            hits.map((hit) => (
              <button
                type="button"
                key={`${hit.group}-${hit.id}`}
                className="search-hit"
                onClick={() => go(hit.path)}
              >
                <span className="search-kind">{hit.group}</span>
                <span className="search-label">{hit.label}</span>
                <span className="search-sub">{humanise(hit.sub)}</span>
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  )
}
