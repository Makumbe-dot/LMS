import { expect } from '@playwright/test'

/** The seeded demo users (backend/core/management/commands/seed.py). */
export const USERS = {
  admin: { username: 'admin', password: 'admin123' },
  officer: { username: 'officer', password: 'officer123' },
  officer2: { username: 'officer2', password: 'officer123' },
  teller: { username: 'teller', password: 'teller123' },
  viewer: { username: 'viewer', password: 'viewer123' },
}

/** Sign in through the form and wait for the application shell. */
export async function signIn(page, { username, password }) {
  await page.goto('/')
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible()
}

/** A page in a fresh browser context, signed in as `who`. Close it when done. */
export async function pageAs(browser, who) {
  const context = await browser.newContext()
  const page = await context.newPage()
  await signIn(page, who)
  return page
}

/** 1234.5 -> "1,234.50", as the app's fmt() writes money. */
export const fmt = (value) =>
  Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** The <dd> beside a <dt> in a KeyValues list, e.g. "Total paid". */
export const valueOf = (scope, term) =>
  scope.locator('dt', { hasText: new RegExp(`^${term}$`) }).locator('xpath=following-sibling::dd[1]')

/** The toast region, where the app reports what an action did or why it failed. */
export const toasts = (page) => page.getByRole('status')

/** A short suffix that keeps names unique across runs and retries. */
export const unique = () => `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`
