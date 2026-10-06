/* Small presentational pieces shared by every page. */
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { downloadFile } from '../lib/api.js'
import { humanise } from '../lib/format.js'
import Icon from './Icons.jsx'
import Sparkline from './Sparkline.jsx'
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

export function Badge({ value }) {
  if (value === null || value === undefined || value === '') return <span>-</span>
  return <span className={`badge ${value}`}>{humanise(value)}</span>
}

/**
 * Which way a figure moved. `good` says whether that direction is good news, so
 * the colour is never the only signal: the arrow and the words carry it too.
 */
export function Delta({ dir, text, vs, good }) {
  const mood = dir === 'flat' || good === null ? 'flat' : good ? 'good' : 'bad'
  return (
    <span className={`delta ${mood}`}>
      {dir !== 'flat' ? <Icon name={dir === 'up' ? 'arrowUp' : 'arrowDown'} size={12} /> : null}
      <span className="delta-text">{text}</span>
      {vs ? <span className="delta-vs">{vs}</span> : null}
    </span>
  )
}

/**
 * A stat tile: one number that matters. With `icon` and `tone` it carries a
 * tinted icon chip (tone: brand | ink | blue | green | amber | red | violet | slate); with `to`
 * the whole tile is a link to the page behind the number. The foot holds either
 * a `delta` (which way it moved) or a line of context (`foot`), and a `trend`.
 * A `lead` tile is one of the few figures the page opens with: a larger value,
 * and its trend drawn across the full width of the tile. `title` is shown on
 * hover over the value, for the exact figure behind a shortened one.
 */
export function Kpi({ label, value, sub, icon, tone, to, delta, foot, trend, trendLabel, lead = false, title }) {
  const className = `kpi${lead ? ' lead' : ''}${tone ? ` tone-${tone}` : ''}${to ? ' linked' : ''}`
  const body = (
    <>
      <div className="kpi-top">
        {icon ? (
          <span className="kpi-icon" aria-hidden="true">
            <Icon name={icon} size={lead ? 18 : 16} />
          </span>
        ) : null}
        <div className="label">{label}</div>
        {to ? <Icon name="chevron" size={14} className="kpi-go" /> : null}
      </div>
      <div className="kpi-body">
        <div className="value" title={title}>
          {value}
        </div>
        <div className="sub">{sub || ''}</div>
        {delta || foot || (trend && !lead) ? (
          <div className="kpi-foot">
            {delta ? <Delta {...delta} /> : <span className="kpi-note">{foot}</span>}
            {trend && !lead ? <Sparkline values={trend} label={trendLabel} /> : null}
          </div>
        ) : null}
      </div>
      {trend && lead ? (
        <div className="kpi-trend">
          <Sparkline values={trend} label={trendLabel} fluid height={44} />
        </div>
      ) : null}
    </>
  )
  if (to) {
    return (
      <Link className={className} to={to}>
        {body}
      </Link>
    )
  }
  return <div className={className}>{body}</div>
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
