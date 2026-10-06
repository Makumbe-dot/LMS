/* The Zinmad Capital artwork, and the two things drawn in its spirit.

   The logo is the company's own and is never redrawn or recoloured here: both
   images are cropped from the file as supplied (scripts/make_brand_assets.py).
   It is always set on white, because the black of the Z vanishes on anything
   dark - so on the black sidebar and the sign-in panel it sits on a white tile.

   `Swoosh` is not the logo. It is a plain crescent in the brand's orange-to-red,
   used large and faint as decoration behind the sign-in panel and the dashboard
   banner. */
import { useId } from 'react'

import logo from './zinmad-logo.png'
import mark from './zinmad-mark.png'

// The cropped artwork's proportions, so the page reserves its space before it loads.
const MARK_RATIO = 360 / 212
const LOGO_RATIO = 640 / 602

/** The ZM monogram on its white tile: the sidebar, the sign-in banner. */
export function LogoMark({ height = 26, className = '' }) {
  return (
    <span className={`logo-tile ${className}`.trim()}>
      <img src={mark} alt="" height={height} width={Math.round(height * MARK_RATIO)} />
    </span>
  )
}

/** The whole logo: monogram, name and strapline. For the sign-in card. */
export function LogoFull({ width = 190 }) {
  return (
    <img
      className="logo-full"
      src={logo}
      alt="Zinmad Capital Private Limited. Reliable Finance, Real Impact."
      width={width}
      height={Math.round(width / LOGO_RATIO)}
    />
  )
}

/** A crescent in the brand gradient. Decoration only; always aria-hidden. */
export function Swoosh({ width = 120 }) {
  // Each instance needs its own gradient id: ids are document-wide in inline SVG.
  const id = `swoosh-${useId().replace(/[^a-zA-Z0-9]/g, '')}`
  return (
    <svg
      className="swoosh"
      width={width}
      height={Math.round(width * 0.42)}
      viewBox="0 0 200 84"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#f87000" />
          <stop offset="0.55" stopColor="#f04a00" />
          <stop offset="1" stopColor="#c00000" />
        </linearGradient>
      </defs>
      <path d="M3 40 Q92 122 197 14 Q104 92 3 40 Z" fill={`url(#${id})`} />
    </svg>
  )
}
