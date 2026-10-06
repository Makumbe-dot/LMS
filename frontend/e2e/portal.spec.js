import { readFileSync } from 'node:fs'

import { expect, test } from '@playwright/test'

import { apiURL, messagesFile } from './env.js'
import { USERS, pageAs } from './helpers.js'

/**
 * The borrower portal end to end: an administrator opens it, a borrower signs in
 * with ID, phone and the code texted to them (read back from the message file the
 * test server writes), sees their loan, and asks for a top-up that staff then see.
 */

test.describe.configure({ mode: 'serial' })

/** The newest code texted to this phone, of this kind. */
function lastCode(phone, kind) {
  const lines = readFileSync(messagesFile, 'utf8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line))
  const sent = lines.filter((m) => m.to === phone && m.kind === kind).pop()
  expect(sent, `a ${kind} message to ${phone}`).toBeTruthy()
  return sent.body.match(/\b(\d{6})\b/)[1]
}

/** A seeded borrower with an active loan, read through the staff API. */
async function borrowerWithLoan(request) {
  const login = await request.post(`${apiURL}/api/auth/login`, { data: USERS.admin })
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` }
  const read = async (path) => (await request.get(`${apiURL}${path}`, { headers })).json()
  const loan = (await read('/api/loans?status=active&page_size=1')).results[0]
  const borrower = await read(`/api/borrowers/${loan.borrower_id}`)
  return { loan, borrower }
}

test('an administrator opens the portal and a borrower signs in and asks for a top-up', async ({
  browser,
  page,
  request,
}) => {
  const admin = await pageAs(browser, USERS.admin)
  await admin.goto('/settings')
  const toggle = admin.getByLabel('Borrowers can sign in to the portal')
  await toggle.check()
  await admin.getByRole('button', { name: 'Save settings' }).click()
  await expect(admin.getByRole('status').filter({ hasText: 'Settings saved' })).toBeVisible()

  const { loan, borrower } = await borrowerWithLoan(request)

  await page.goto('/portal')
  await page.getByLabel('National ID').fill(borrower.national_id)
  await page.getByLabel('Phone number').fill(borrower.phone)
  await page.getByRole('button', { name: 'Text me a code' }).click()
  await expect(page.getByText(/a code is on its way/i)).toBeVisible()
  await page.getByLabel(/code from the text message/i).fill(lastCode(borrower.phone, 'portal_code'))
  await page.getByRole('button', { name: 'Sign in' }).click()

  await expect(page.getByRole('heading', { name: `Hello, ${borrower.first_name}` })).toBeVisible()
  await page.getByRole('link', { name: new RegExp(loan.loan_no) }).click()
  await expect(page.getByRole('heading', { name: 'Payments due' })).toBeVisible()
  await page.getByRole('link', { name: /all my loans/i }).click()

  await page.getByRole('button', { name: /ask for a top-up/i }).click()
  await page.getByLabel('How much more').fill('300')
  await page.getByLabel('Anything we should know').fill('School fees')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByRole('cell', { name: 'Waiting' })).toBeVisible()

  await admin.goto('/portal-requests')
  await expect(admin.getByRole('cell', { name: 'School fees' })).toBeVisible()
  await admin.close()
})

