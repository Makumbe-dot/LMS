/* Line icons, drawn on a 24px grid with a 1.8 stroke so they sit with the text.
   Inline SVG rather than an icon package: thirty-odd paths do not justify a
   dependency, and inline paths take the text colour with currentColor. */

const PATHS = {
  dashboard: (
    <>
      <rect x="3.5" y="3.5" width="7" height="8" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="5" rx="1.5" />
      <rect x="13.5" y="11.5" width="7" height="9" rx="1.5" />
      <rect x="3.5" y="14.5" width="7" height="6" rx="1.5" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4.5 20.5c1.2-3.6 4-5.5 7.5-5.5s6.3 1.9 7.5 5.5" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8.5" r="3.5" />
      <path d="M2.5 19.5c.9-3 3.4-4.8 6.5-4.8s5.6 1.8 6.5 4.8" />
      <path d="M15.5 5.3a3.5 3.5 0 0 1 0 6.4M17.5 14.9c2 .6 3.4 2.2 4 4.6" />
    </>
  ),
  loans: (
    <>
      <rect x="2.5" y="6" width="19" height="12.5" rx="2.5" />
      <circle cx="12" cy="12.25" r="2.6" />
      <path d="M6 9.5v5.5M18 9.5v5.5" />
    </>
  ),
  savings: (
    <>
      <path d="M4 10.5h16v8.5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 19z" />
      <path d="M3 7.5h18v3H3zM12 7.5v13M12 7.5c-1.8 0-4-1-4-2.8 0-1.4 1.6-2 2.7-1.2L12 4.5l1.3-1c1.1-.8 2.7-.2 2.7 1.2 0 1.8-2.2 2.8-4 2.8" />
    </>
  ),
  calendar: (
    <>
      <rect x="3.5" y="5" width="17" height="15.5" rx="2" />
      <path d="M3.5 9.5h17M8 3v4M16 3v4M8 13.5h2M14 13.5h2M8 17h2" />
    </>
  ),
  till: (
    <>
      <path d="M4 11h16l-1.2 8.3a1.5 1.5 0 0 1-1.5 1.2H6.7a1.5 1.5 0 0 1-1.5-1.2z" />
      <path d="M7 11V5.5A1.5 1.5 0 0 1 8.5 4h7A1.5 1.5 0 0 1 17 5.5V11M9.5 7.5h5M10 15.5h4" />
    </>
  ),
  trending: <path d="m3 7 6.5 6.5 4-4L21 17M21 11v6h-6" />,
  briefcase: (
    <>
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <path d="M8.5 7V5.5A1.5 1.5 0 0 1 10 4h4a1.5 1.5 0 0 1 1.5 1.5V7M3 12.5h18" />
    </>
  ),
  upload: <path d="M12 15.5V4M7.5 8.5 12 4l4.5 4.5M4 15v3.5A2 2 0 0 0 6 20.5h12a2 2 0 0 0 2-2V15" />,
  inbox: (
    <>
      <path d="M12 3.5v8M8.5 8.5 12 12l3.5-3.5" />
      <path d="M3.5 13.5 5.6 6.3A1.5 1.5 0 0 1 7 5.2h.5M16.5 5.2h.5a1.5 1.5 0 0 1 1.4 1.1l2.1 7.2v5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2zM3.5 13.5h4.6l1.4 2.5h5l1.4-2.5h4.6" />
    </>
  ),
  message: <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4.5 4v-4h0A1.5 1.5 0 0 1 4 14.5zM8 8.5h8M8 11.5h5" />,
  shield: (
    <>
      <path d="M12 2.8 19.5 6v5.6c0 4.6-3.1 8.2-7.5 9.6-4.4-1.4-7.5-5-7.5-9.6V6z" />
      <path d="m8.8 12 2.2 2.2 4.3-4.4" />
    </>
  ),
  list: <path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" />,
  book: (
    <>
      <path d="M4.5 5A1.5 1.5 0 0 1 6 3.5h13.5v14H6a1.5 1.5 0 0 0-1.5 1.5z" />
      <path d="M4.5 19A1.5 1.5 0 0 0 6 20.5h13.5v-3M8.5 8h7M8.5 11.5h5" />
    </>
  ),
  pen: <path d="M14.5 5.5 18.5 9.5M4 20l1-4.5L15.8 4.7a1.8 1.8 0 0 1 2.5 0l1 1a1.8 1.8 0 0 1 0 2.5L8.5 19z" />,
  scale: (
    <>
      <path d="M12 3.5v17M7 20.5h10M5 7.5h14M12 5.5 5 7.5M12 5.5l7 2" />
      <path d="m5 7.5-2.5 6a2.8 2.8 0 0 0 5 0zM19 7.5l-2.5 6a2.8 2.8 0 0 0 5 0z" />
    </>
  ),
  landmark: <path d="M3 20.5h18M4.5 17.5h15M6 17.5v-7M10 17.5v-7M14 17.5v-7M18 17.5v-7M3.5 9 12 3.5 20.5 9z" />,
  sheet: (
    <>
      <rect x="3.5" y="3.5" width="17" height="17" rx="2" />
      <path d="M3.5 9h17M3.5 14.5h17M9.5 9v11.5" />
    </>
  ),
  chart: <path d="M4 4v15.5A.5.5 0 0 0 4.5 20H20M8 16v-4M12 16V8M16 16v-6.5" />,
  umbrella: <path d="M12 3.5c4.7 0 8.5 3.3 8.5 7.5h-17c0-4.2 3.8-7.5 8.5-7.5zM12 11v7a2 2 0 0 1-4 0M12 2.5v1" />,
  lock: (
    <>
      <rect x="4.5" y="10.5" width="15" height="10" rx="2" />
      <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3M12 14.5v2" />
    </>
  ),
  package: (
    <>
      <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z" />
      <path d="m4 7.5 8 4.5 8-4.5M12 12v9M8 5.3l8 4.4" />
    </>
  ),
  tag: (
    <>
      <path d="M3.5 12.4V4.5a1 1 0 0 1 1-1h7.9a1 1 0 0 1 .7.3l7.6 7.6a1.5 1.5 0 0 1 0 2.1l-7.2 7.2a1.5 1.5 0 0 1-2.1 0l-7.6-7.6a1 1 0 0 1-.3-.7z" />
      <circle cx="8" cy="8" r="1.4" />
    </>
  ),
  userCog: (
    <>
      <circle cx="9" cy="8" r="3.8" />
      <path d="M2.5 20c.9-3.3 3.4-5.2 6.5-5.2 1.1 0 2.1.2 3 .7" />
      <circle cx="17.5" cy="17" r="2.2" />
      <path d="M17.5 13v1.3M17.5 19.7V21M21 15l-1.1.6M15.1 18.4 14 19M21 19l-1.1-.6M15.1 15.6 14 15" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5v.2a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1h-.2a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3h0a1.6 1.6 0 0 0 1-1.5v-.2a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8v0a1.6 1.6 0 0 0 1.5 1h.2a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="5.5" rx="7.5" ry="2.5" />
      <path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5" />
    </>
  ),
  history: <path d="M3.5 12a8.5 8.5 0 1 0 2.5-6M3.5 3.5V8H8M12 7.5V12l3 2" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
    </>
  ),
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />,
  monitor: (
    <>
      <rect x="3" y="4" width="18" height="12.5" rx="2" />
      <path d="M8.5 20.5h7M12 16.5v4" />
    </>
  ),
  logout: <path d="M9.5 20.5h-4a1.5 1.5 0 0 1-1.5-1.5V5a1.5 1.5 0 0 1 1.5-1.5h4M15.5 16.5 20 12l-4.5-4.5M20 12H9" />,
  collapse: <path d="M15 5.5 8.5 12l6.5 6.5M20 5v14" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  arrowUp: <path d="M12 19V5M6 11l6-6 6 6" />,
  arrowDown: <path d="M12 5v14M6 13l6 6 6-6" />,
  alert: (
    <>
      <path d="M10.3 4.2 2.8 17.5A2 2 0 0 0 4.5 20.5h15a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z" />
      <path d="M12 9.5v4M12 17h.01" />
    </>
  ),
  check: <path d="M20 6.5 9.5 17 4 11.5" />,
  sparkle: <path d="M12 3.5 13.8 10.2 20.5 12 13.8 13.8 12 20.5 10.2 13.8 3.5 12 10.2 10.2z" />,
  play: <path d="M7 4.5v15l12.5-7.5z" />,
}

export default function Icon({ name, size = 18, className, title }) {
  const path = PATHS[name] || PATHS.dashboard
  return (
    <svg
      className={className ? `icon ${className}` : 'icon'}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {path}
    </svg>
  )
}
