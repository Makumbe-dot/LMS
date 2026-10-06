/* The map of the application.

   Three levels, and each has one place on the screen: a SECTION is a caption in
   the sidebar, an ENTRY is a row under it, and the PAGES inside an entry are the
   tabs across the top of the page. Twenty-six pages as twenty-six sidebar rows was
   a list to read; ten entries is a menu, and pages that are used together (the
   ledger, its journals, the bank reconciliation) sit one click apart.

   `rights` on a page hides it from users holding none of them ('admin' meaning an
   administrator); an entry with no page left for the signed-in user disappears,
   and so does a section with no entry left. */

export const HOME = { to: '/dashboard', label: 'Dashboard', icon: 'dashboard', end: true }
export const ACCOUNT = { to: '/account', label: 'My account', icon: 'user' }

const CASH = ['cash']
const ADMIN = ['admin']

export const NAV = [
  {
    key: 'lending',
    heading: 'Lending',
    entries: [
      {
        key: 'customers',
        label: 'Customers',
        icon: 'users',
        pages: [
          { to: '/borrowers', label: 'Borrowers', icon: 'user' },
          { to: '/groups', label: 'Groups', icon: 'users' },
        ],
      },
      {
        key: 'loans',
        label: 'Loans',
        icon: 'loans',
        // A count from /api/nav-summary shown beside the row: work that is waiting.
        badge: { key: 'pending_applications', one: 'application waiting', many: 'applications waiting' },
        pages: [{ to: '/loans', label: 'Loans', icon: 'loans' }],
      },
      {
        key: 'savings',
        label: 'Savings',
        icon: 'savings',
        pages: [{ to: '/savings', label: 'Savings', icon: 'savings' }],
      },
    ],
  },
  {
    key: 'operations',
    heading: 'Operations',
    entries: [
      {
        key: 'collections',
        label: 'Collections',
        icon: 'calendar',
        badge: { key: 'loans_in_arrears', one: 'loan in arrears', many: 'loans in arrears', tone: 'warn' },
        pages: [
          { to: '/collections', label: 'Collections due', icon: 'calendar' },
          { to: '/arrears', label: 'Arrears / PAR', icon: 'trending' },
          { to: '/payroll', label: 'Payroll deductions', icon: 'briefcase' },
          { to: '/imports', label: 'Bulk repayments', icon: 'upload', rights: CASH, end: true },
          { to: '/notifications', label: 'Messages', icon: 'message' },
        ],
      },
      {
        key: 'till',
        label: 'Teller till',
        icon: 'till',
        pages: [{ to: '/till', label: 'Teller till', icon: 'till', rights: CASH }],
      },
    ],
  },
  {
    key: 'finance',
    heading: 'Finance',
    entries: [
      {
        key: 'accounting',
        label: 'Accounting',
        icon: 'book',
        pages: [
          { to: '/ledger', label: 'General ledger', icon: 'book' },
          { to: '/journals', label: 'Journals & expenses', icon: 'pen' },
          { to: '/bank-reconciliation', label: 'Bank reconciliation', icon: 'scale' },
          { to: '/provisioning', label: 'Provisioning', icon: 'umbrella' },
          { to: '/periods', label: 'Period close', icon: 'lock' },
        ],
      },
      {
        key: 'treasury',
        label: 'Treasury',
        icon: 'landmark',
        pages: [
          { to: '/funding', label: 'Funding & capital', icon: 'landmark' },
          { to: '/currencies', label: 'Currencies', icon: 'coins' },
        ],
      },
      {
        key: 'reports',
        label: 'Reports',
        icon: 'chart',
        pages: [
          { to: '/performance', label: 'Performance', icon: 'chart' },
          { to: '/transactions', label: 'Transactions', icon: 'list' },
          { to: '/spreadsheets', label: 'Spreadsheets', icon: 'sheet' },
        ],
      },
    ],
  },
  {
    key: 'administration',
    heading: 'Administration',
    entries: [
      {
        key: 'products',
        label: 'Products & charges',
        icon: 'package',
        pages: [
          { to: '/products', label: 'Products', icon: 'package' },
          { to: '/charges', label: 'Charges', icon: 'tag' },
        ],
      },
      {
        key: 'system',
        label: 'System',
        icon: 'settings',
        pages: [
          { to: '/settings', label: 'Settings', icon: 'settings', rights: ADMIN },
          { to: '/users', label: 'Users', icon: 'userCog', rights: ADMIN },
          { to: '/imports/loan-book', label: 'Loan book migration', icon: 'database', rights: ADMIN },
          { to: '/audit', label: 'Audit log', icon: 'history', rights: ADMIN },
        ],
      },
    ],
  },
]

function matches(page, pathname) {
  if (page.end) return pathname === page.to
  return pathname === page.to || pathname.startsWith(`${page.to}/`)
}

/**
 * Where an address sits: its section, its sidebar entry and its page.
 * `section` and `entry` are null for the dashboard and "My account". The longest
 * matching address wins, so /imports/loan-book is not mistaken for /imports.
 */
export function findPage(pathname) {
  let best = null
  const consider = (section, entry, item) => {
    if (matches(item, pathname) && (!best || item.to.length > best.item.to.length)) {
      best = { section, entry, item }
    }
  }
  consider(null, null, HOME)
  consider(null, null, ACCOUNT)
  for (const section of NAV) {
    for (const entry of section.entries) {
      for (const page of entry.pages) consider(section, entry, page)
    }
  }
  return best
}

/**
 * The menu as one user sees it. Each entry gains `to`: the first of its pages
 * that user can open, which is where the sidebar row leads.
 */
export function visibleNav(can) {
  return NAV.map((section) => ({
    ...section,
    entries: section.entries
      .map((entry) => {
        const pages = entry.pages.filter((page) => !page.rights || can(...page.rights))
        return { ...entry, pages, to: pages[0]?.to }
      })
      .filter((entry) => entry.pages.length),
  })).filter((section) => section.entries.length)
}

/** Every page a user can open, flat, for the search box's "go to" results. */
export function allPages(can) {
  const pages = [{ ...HOME, entry: null }]
  for (const section of visibleNav(can)) {
    for (const entry of section.entries) {
      for (const page of entry.pages) pages.push({ ...page, entry: entry.label })
    }
  }
  pages.push({ ...ACCOUNT, entry: null })
  return pages
}
