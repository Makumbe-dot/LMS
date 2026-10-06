import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { get, qs } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { humanise } from '../lib/format.js'
import { allPages } from '../lib/nav.js'
import { useDebounced } from '../lib/useApi.js'
import Icon from './Icons.jsx'

/**
 * One box over borrowers, loans and the pages of the app. Ctrl+K focuses it.
 *
 * Pages are matched in the browser, so "ledger" or "audit" jumps straight there
 * without knowing which section it lives under; borrowers and loans come from
 * the server. Arrow keys move through the results and Enter opens one.
 */
export default function GlobalSearch() {
  const navigate = useNavigate()
  const { can } = useAuth()
  const inputRef = useRef(null)
  const boxRef = useRef(null)
  const [term, setTerm] = useState('')
  const [results, setResults] = useState(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
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
    inputRef.current?.blur()
    navigate(path)
  }

  const needle = term.trim().toLowerCase()
  const pages = useMemo(() => allPages(can), [can])
  const hits = useMemo(() => {
    if (needle.length < 2) return []
    const pageHits = pages
      .filter((p) => `${p.label} ${p.entry || ''}`.toLowerCase().includes(needle))
      .slice(0, 5)
      .map((p) => ({ key: `page-${p.to}`, path: p.to, group: 'Page', label: p.label, sub: p.entry || '' }))
    return [
      ...pageHits,
      ...(results?.borrowers || []).map((r) => ({
        key: `borrower-${r.id}`,
        path: `/borrowers/${r.id}`,
        group: 'Borrower',
        label: r.label,
        sub: humanise(r.sub),
      })),
      ...(results?.loans || []).map((r) => ({
        key: `loan-${r.id}`,
        path: `/loans/${r.id}`,
        group: 'Loan',
        label: r.label,
        sub: humanise(r.sub),
      })),
    ]
  }, [needle, pages, results])

  // The highlighted row never points past the end of a list that just shrank.
  const current = Math.min(active, Math.max(hits.length - 1, 0))

  function onKeyDown(event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setOpen(true)
      setActive(Math.min(current + 1, hits.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive(Math.max(current - 1, 0))
    } else if (event.key === 'Enter' && hits.length) {
      go(hits[current].path)
    }
  }

  return (
    <div className="global-search" ref={boxRef}>
      <Icon name="search" size={16} className="search-icon" />
      <input
        ref={inputRef}
        type="search"
        value={term}
        placeholder="Search borrowers, loans and pages"
        aria-label="Search borrowers, loans and pages"
        onChange={(e) => {
          setTerm(e.target.value)
          setActive(0)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {term ? null : (
        <kbd className="search-kbd" aria-hidden="true">
          Ctrl K
        </kbd>
      )}
      {open && needle.length >= 2 ? (
        <div className="search-results" role="listbox">
          {hits.length === 0 ? (
            <div className="search-empty">No matches for “{term}”</div>
          ) : (
            hits.map((hit, index) => (
              <button
                type="button"
                role="option"
                aria-selected={index === current}
                key={hit.key}
                className={index === current ? 'search-hit active' : 'search-hit'}
                onMouseEnter={() => setActive(index)}
                onClick={() => go(hit.path)}
              >
                <span className="search-kind">{hit.group}</span>
                <span className="search-label">{hit.label}</span>
                <span className="search-sub">{hit.sub}</span>
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  )
}
