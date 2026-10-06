/* One loan from application to its first repayment and a statement, each step
   taken by the user whose job it is. The steps share the loan, so they run in
   order; a failure skips the rest rather than letting them fail confusingly. */
import { readFile } from 'node:fs/promises'

import { expect, test } from '@playwright/test'

import { USERS, fmt, pageAs, signIn, toasts, unique, valueOf } from './helpers.js'

test.describe.configure({ mode: 'serial' })

const suffix = unique()
const borrower = {
  first: 'Ruvimbo',
  last: `E2e${suffix}`,
  nationalId: `63-${suffix}`,
  phone: '0771234567',
  salary: '1200',
}
let loanPath = null // /loans/<id>
let instalment = null

/** The page title of a loan: its number and a status badge. */
const loanTitle = (page) => page.getByRole('heading', { level: 2, name: /^LN/ })

test('an officer registers a borrower, previews a quote and applies', async ({ page }) => {
  await signIn(page, USERS.officer)

  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Customers' }).click()
  await expect(page.getByRole('heading', { name: 'Borrowers', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'New borrower' }).click()
  await expect(page.getByRole('heading', { name: 'New borrower' })).toBeVisible()

  // The guarantor fieldset repeats some labels; the borrower's own come first.
  await page.getByLabel('First name').fill(borrower.first)
  await page.getByLabel('Last name').fill(borrower.last)
  await page.getByLabel('National ID', { exact: true }).first().fill(borrower.nationalId)
  await page.getByLabel('Phone', { exact: true }).first().fill(borrower.phone)
  await page.getByLabel('Employer', { exact: true }).first().fill('Ministry of Education')
  await page.getByLabel('Net monthly salary').fill(borrower.salary)
  await page.getByLabel('Payday').fill('25')
  await page.getByRole('checkbox', { name: 'KYC verified' }).check()
  await page.getByRole('button', { name: 'Save borrower' }).click()

  await expect(toasts(page).getByText('Borrower saved')).toBeVisible()
  await expect(page).toHaveURL(/\/borrowers\/\d+$/)
  const borrowerId = page.url().match(/\/borrowers\/(\d+)$/)[1]

  await page.getByRole('link', { name: 'New loan' }).click()
  await expect(page.getByRole('heading', { name: 'New loan application' })).toBeVisible()
  // (A regex: the header's global search is labelled "Search borrowers, ...".)
  await expect(page.getByLabel(/^Borrower/)).toHaveValue(borrowerId)

  const product = page.getByLabel('Product')
  const salaryAdvance = await product
    .locator('option')
    .filter({ hasText: /^Salary Advance - / })
    .getAttribute('value')
  await product.selectOption(salaryAdvance)
  // By role: a <select> inside its <label> lends its options to the label's text,
  // so "Term" alone would also find the product list ("Salary Term Loan").
  await page.getByRole('spinbutton', { name: /^Principal/ }).fill('600')
  await page.getByRole('spinbutton', { name: /^Term/ }).fill('3')
  await page.getByLabel('Purpose').fill('School fees')

  await page.getByRole('button', { name: 'Preview quote' }).click()
  const schedule = page.getByRole('table', { name: 'Indicative repayment schedule' })
  await expect(schedule.getByRole('row')).toHaveCount(1 + 3)
  await expect(page.getByText('(affordable)')).toBeVisible()
  instalment = (await valueOf(page, 'Monthly instalment').innerText()).replace(/[^\d.]/g, '')
  expect(Number(instalment)).toBeGreaterThan(200)

  await page.getByRole('button', { name: 'Submit application' }).click()
  await expect(toasts(page).getByText(/^Application LN\S+ captured$/)).toBeVisible()
  await expect(page).toHaveURL(/\/loans\/\d+$/)
  loanPath = new URL(page.url()).pathname
  await expect(loanTitle(page)).toContainText('pending')
  await expect(page.getByText('The schedule is generated on disbursement.')).toBeVisible()
})

test('the originator cannot approve; a second officer approves and disburses', async ({
  page,
  browser,
}) => {
  test.skip(!loanPath, 'needs the application from the previous step')

  // Maker-checker: the officer holds the approve right, but not for their own loan.
  await signIn(page, USERS.officer)
  await page.goto(loanPath)
  await page.getByRole('button', { name: 'Approve' }).click()
  await expect(toasts(page).getByText('Whoever originated a loan cannot approve it')).toBeVisible()
  await page.reload()
  await expect(loanTitle(page)).toContainText('pending')

  const checker = await pageAs(browser, USERS.officer2)
  try {
    await checker.goto(loanPath)
    await checker.getByRole('button', { name: 'Approve' }).click()
    await expect(toasts(checker).getByText('Loan approved')).toBeVisible()
    await expect(loanTitle(checker)).toContainText('approved')

    await checker.getByRole('button', { name: 'Disburse' }).click()
    const dialog = checker.getByRole('dialog', { name: 'Disburse loan' })
    await dialog.getByLabel('Method').selectOption('bank_transfer')
    await dialog.getByLabel('Reference').fill(`TRF-${suffix}`)
    await dialog.getByRole('button', { name: 'Disburse' }).click()
    await expect(toasts(checker).getByText('Loan disbursed')).toBeVisible()
    await expect(loanTitle(checker)).toContainText('active')

    // Disbursement generates the schedule: three instalments, nothing paid yet.
    const schedule = checker.getByRole('table', { name: 'Repayment schedule' })
    await expect(schedule.getByRole('row')).toHaveCount(1 + 3)
    await expect(valueOf(checker, 'Total paid')).toHaveText('USD 0.00')
    await expect(valueOf(checker, 'Principal outstanding')).toHaveText('USD 600.00')
  } finally {
    await checker.context().close()
  }
})

test('a teller opens a till and posts a cash repayment that the schedule reflects', async ({
  page,
}) => {
  test.skip(!loanPath, 'needs the disbursed loan from the previous step')
  await signIn(page, USERS.teller)

  await page.goto('/till')
  await expect(page.getByRole('heading', { name: 'Teller till' })).toBeVisible()
  const openTill = page.getByRole('button', { name: 'Open my till' })
  const countAndClose = page.getByRole('button', { name: 'Count and close' })
  await expect(openTill.or(countAndClose)).toBeVisible()
  if (await openTill.isVisible()) {
    await openTill.click()
    const dialog = page.getByRole('dialog', { name: 'Open my till' })
    await dialog.getByLabel('Opening float').fill('500')
    await dialog.getByRole('button', { name: 'Open the till' }).click()
    await expect(toasts(page).getByText('Till open')).toBeVisible()
  }
  await expect(countAndClose).toBeVisible()

  await page.goto(loanPath)
  await expect(loanTitle(page)).toContainText('active')
  const loanNo = (await loanTitle(page).innerText()).split(/\s/)[0]
  const outstandingBefore = await valueOf(page, 'Total outstanding').innerText()

  await page.getByRole('button', { name: 'Post repayment' }).click()
  const dialog = page.getByRole('dialog', { name: 'Post repayment' })
  // The amount offered is the instalment, since nothing is in arrears.
  await expect(dialog.getByLabel('Amount')).toHaveValue(instalment)
  await dialog.getByLabel('Method').selectOption('cash')
  await dialog.getByLabel('Reference').fill(`RCPT-${suffix}`)
  await dialog.getByRole('button', { name: 'Post repayment' }).click()
  await expect(toasts(page).getByText('Repayment posted')).toBeVisible()

  await expect(valueOf(page, 'Total paid')).toHaveText(`USD ${fmt(instalment)}`)
  await expect(valueOf(page, 'Total outstanding')).not.toHaveText(outstandingBefore)
  const firstInstalment = page.getByRole('table', { name: 'Repayment schedule' }).getByRole('row').nth(1)
  await expect(firstInstalment.getByRole('cell').nth(8)).toHaveText(fmt(instalment)) // Paid
  await expect(firstInstalment.getByRole('cell').nth(9)).toHaveText('0.00') // Balance
  await expect(firstInstalment.getByRole('cell').nth(11)).toHaveText('paid') // Status
  await page.getByRole('tab', { name: /^Transactions/ }).click()
  await expect(page.getByRole('table', { name: 'Transactions' })).toContainText(`RCPT-${suffix}`)

  // The cash is in the teller's drawer.
  await page.goto('/till')
  await expect(page.getByRole('table', { name: 'Cash movements in this till' })).toContainText(loanNo)
})

test('the loan statement downloads as a PDF', async ({ page }) => {
  test.skip(!loanPath, 'needs the loan from the earlier steps')
  await signIn(page, USERS.officer)
  await page.goto(loanPath)

  await page.getByRole('button', { name: 'Statement' }).click()
  const dialog = page.getByRole('dialog', { name: /^Loan statement - LN/ })
  await expect(dialog.getByRole('table', { name: 'Statement lines' })).toContainText(`RCPT-${suffix}`)

  const [response, download] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/statement') && r.url().includes('fmt=pdf')),
    page.waitForEvent('download'),
    dialog.getByRole('button', { name: 'Download PDF' }).click(),
  ])
  expect(response.status()).toBe(200)
  expect(response.headers()['content-type']).toContain('application/pdf')
  // The file the browser saved is a PDF, not an error page with a .pdf name.
  expect(download.suggestedFilename()).toMatch(/\.pdf$/)
  const file = await readFile(await download.path())
  expect(file.subarray(0, 5).toString('latin1')).toBe('%PDF-')
  expect(file.length).toBeGreaterThan(1000)
})
