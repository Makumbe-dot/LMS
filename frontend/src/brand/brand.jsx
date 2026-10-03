/**
 * Cell Insurance Group branding, as in the group's Risk Management (ERM) app: the
 * companies' logos and the group's wordmark, on the sign-in page and in the
 * sidebar. Where the ERM app sets the middle word in gold, this one sets it in
 * blue, to match the rest of the LMS.
 *
 * Logos are picked up from ./logos/ by file name: cell-insurance.*, cellmed.*,
 * nectacare.* (png, jpg, svg or webp). Replace a file and rebuild; a company
 * without a file is shown by its name until its logo is added.
 */
import { HexMark } from '../components/ui.jsx'

export const GROUP_NAME = 'Cell Insurance Group'
export const PRODUCT_NAME = 'Cell Insurance Group LMS'

const files = import.meta.glob('./logos/*.{png,jpg,jpeg,svg,webp}', {
  eager: true,
  query: '?url',
  import: 'default',
})

function logoFor(key) {
  const hit = Object.keys(files).find(
    (path) => path.replace(/^.*\//, '').replace(/\.[^.]+$/, '').toLowerCase() === key,
  )
  return hit ? files[hit] : null
}

/** The group's companies, parent first. */
export const COMPANIES = [
  { key: 'cell-insurance', name: 'Cell Insurance', src: logoFor('cell-insurance') },
  { key: 'cellmed', name: 'CellMed', src: logoFor('cellmed') },
  { key: 'nectacare', name: 'NectaCare', src: logoFor('nectacare') },
]

/** One company's logo in its slot, or its name in the brand type while the file is missing. */
export function CompanyLogo({ company, height }) {
  return (
    <span className="brand-cell" style={{ height }}>
      {company.src ? (
        <img className="brand-logo" src={company.src} alt={company.name} />
      ) : (
        <span
          className="brand-name-tile"
          style={{ fontSize: Math.max(10, Math.min(15, Math.round(height / 3.6))) }}
          role="img"
          aria-label={company.name}
        >
          {company.name}
        </span>
      )}
    </span>
  )
}

/** The group's company logos in one row of equal slots. */
export function GroupLogos({ height = 56, className }) {
  return (
    <div
      className={`brand-logos${className ? ` ${className}` : ''}`}
      role="group"
      aria-label={`${GROUP_NAME} companies`}
    >
      {COMPANIES.map((company) => (
        <CompanyLogo key={company.key} company={company} height={height} />
      ))}
    </div>
  )
}

/** "Cell Insurance Group" with the middle word in the accent, as on the ERM app. */
export function GroupWordmark({ as: Tag = 'span', className, id, suffix }) {
  return (
    <Tag className={className} id={id}>
      Cell <span className="brand-accent">Insurance</span> Group{suffix ? ` ${suffix}` : ''}
    </Tag>
  )
}

/** The parent company's logo, or the hexagon mark while its file is missing. */
export function GroupMark({ height = 32 }) {
  const parent = COMPANIES[0]
  return parent.src ? (
    <img className="brand-parent-logo" src={parent.src} alt="" style={{ height }} />
  ) : (
    <HexMark size={height - 2} />
  )
}
