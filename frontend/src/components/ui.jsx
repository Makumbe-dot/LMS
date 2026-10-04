/* Small presentational pieces shared by every page. */
import { useState } from 'react'

import { downloadFile } from '../lib/api.js'
import { humanise } from '../lib/format.js'
import { useToast } from './Toast.jsx'

/**
 * Download buttons for a listing or a statement: Excel first, because it is what
 * people open, then whichever else the endpoint offers. `path` is the JSON
 * endpoint; the format is added as ?fmt=.
 */
export function ExportButtons({ path, name, formats = ['xlsx', 'csv'], small = false }) {
  const { toastError } = useToast()
  const [busy, setBusy] = useState(null)
  const LABEL = { xlsx: 'Excel', csv: 'CSV', pdf: 'PDF' }
  return (
    <span className="export-buttons" role="group" aria-label="Download">
      {formats.map((fmt) => (
        <button
          key={fmt}
          type="button"
          className={`btn${small ? ' small' : ''}`}
          disabled={busy !== null}
          onClick={async () => {
            setBusy(fmt)
            try {
              await downloadFile(path, fmt, name)
            } catch (err) {
              toastError(err)
            } finally {
              setBusy(null)
            }
          }}
        >
          {busy === fmt ? 'Preparing…' : `Download ${LABEL[fmt]}`}
        </button>
      ))}
    </span>
  )
}

/** The wordmark's hexagon: one cell of the honeycomb the pages sit on. */
export function HexMark({ size = 30 }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={Math.round(size * 1.12)}
      viewBox="0 0 28 32"
      aria-hidden="true"
    >
      <path d="M14 1.2 26.6 8.5v15L14 30.8 1.4 23.5v-15z" fill="var(--accent)" />
      <path
        d="M14 8.6 20.3 12.2v7.6L14 23.4l-6.3-3.6v-7.6z"
        fill="none"
        stroke="var(--accent-ink)"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function Badge({ value }) {
  if (value === null || value === undefined || value === '') return <span>-</span>
  return <span className={`badge ${value}`}>{humanise(value)}</span>
}

export function Kpi({ label, value, sub }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="sub">{sub || ''}</div>
    </div>
  )
}

export function PageHeader({ title, meta, children }) {
  return (
    <div className="page-head">
      <div>
        <h2>{title}</h2>
        {meta ? <p className="muted" style={{ margin: '4px 0 0' }}>{meta}</p> : null}
      </div>
      {children ? <div className="row">{children}</div> : null}
    </div>
  )
}

export function Loading({ what = 'Loading' }) {
  return <p className="loading">{what}…</p>
}

export function ErrorBanner({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="banner" role="alert">
      <strong>Could not load this page. </strong>
      {error.message}
      {onRetry ? (
        <>
          {' '}
          <button type="button" className="btn-link" onClick={onRetry}>
            Try again
          </button>
        </>
      ) : null}
    </div>
  )
}

export function KeyValues({ items }) {
  return (
    <dl className="kv">
      {items.map(([key, value]) => (
        <div key={key} style={{ display: 'contents' }}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** A labelled form control. Pass `as="select"` or `as="textarea"` to change the element. */
export function Field({ label, hint, as = 'input', children, ...props }) {
  const Element = as
  return (
    <label className="field">
      <span className="label-text">{label}</span>
      <Element {...props}>{children}</Element>
      {hint ? <span className="hint">{hint}</span> : null}
    </label>
  )
}

export function Check({ label, ...props }) {
  return (
    <label className="check">
      <input type="checkbox" {...props} />
      {label}
    </label>
  )
}

export function Money({ value, danger = false }) {
  return <span className={danger ? 'tag-danger' : undefined}>{value}</span>
}

/** Page controls for a {count, page, num_pages, page_size} envelope. */
export function Pager({ meta, onPage, noun = 'rows' }) {
  if (!meta || meta.num_pages <= 1) {
    return meta ? (
      <div className="pager">
        <span className="pager-info">
          {meta.count} {noun}
        </span>
      </div>
    ) : null
  }
  const from = (meta.page - 1) * meta.page_size + 1
  const to = Math.min(meta.page * meta.page_size, meta.count)
  return (
    <div className="pager">
      <span className="pager-info">
        Showing {from}–{to} of {meta.count} {noun}
      </span>
      <div className="pager-controls">
        <button
          type="button"
          className="btn small"
          disabled={meta.page <= 1}
          onClick={() => onPage(meta.page - 1)}
        >
          Previous
        </button>
        <span className="pager-info">
          Page {meta.page} of {meta.num_pages}
        </span>
        <button
          type="button"
          className="btn small"
          disabled={meta.page >= meta.num_pages}
          onClick={() => onPage(meta.page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  )
}

/** A stage chip for IFRS 9 reporting — colour plus the label, never colour alone. */
export function StageTag({ stage, label }) {
  return <span className={`stage-${stage}`}>{label || `Stage ${stage}`}</span>
}
