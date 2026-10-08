import { expect, test } from '@playwright/test'

import { USERS, pageAs, signIn, unique } from './helpers.js'

test('a read-only viewer sees no buttons that would change anything', async ({ page }) => {
  await signIn(page, USERS.viewer)

  await page.goto('/loans')
  await expect(page.getByRole('heading', { name: 'Loans', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'New application' })).toHaveCount(0)

  // A pending application, which an officer would see Approve and Reject on.
  await page.getByLabel('Filter by status').selectOption('pending')
  const rows = page.getByRole('table', { name: 'Loans' }).getByRole('row')
  await expect(rows.nth(1)).toContainText('pending')
  await rows.nth(1).click()
  await expect(page.getByRole('heading', { level: 2, name: /^LN/ })).toContainText('pending')
  await expect(page.getByRole('button', { name: 'Statement' })).toBeVisible()
  for (const name of ['Approve', 'Reject', 'Disburse', 'Post repayment']) {
    await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0)
  }

  await page.goto('/borrowers')
  await expect(page.getByRole('link', { name: 'New borrower' })).toHaveCount(0)
  await page.getByRole('table', { name: 'Borrower register' }).getByRole('row').nth(1).click()
  await expect(page).toHaveURL(/\/borrowers\/\d+$/)
  await expect(page.getByRole('link', { name: 'New loan' })).toHaveCount(0)

  // Pages behind a right send a viewer back to the dashboard.
  await page.goto('/till')
  await expect(page).toHaveURL(/\/dashboard$/)
  await page.goto('/users')
  await expect(page).toHaveURL(/\/dashboard$/)
})

test('a right granted on the Users page takes effect for that user', async ({ page, browser }) => {
  const username = `e2e_${unique()}`
  const password = 'Kopje-Sunrise-2026'

  // An administrator adds a user with nothing ticked: read only.
  await signIn(page, USERS.admin)
  await page.goto('/users')
  await page.getByRole('button', { name: 'New user' }).click()
  let dialog = page.getByRole('dialog', { name: 'New user' })
  await dialog.getByLabel('Username').fill(username)
  await dialog.getByLabel('Full name').fill('End To End')
  await dialog.getByLabel('Password').fill(password)
  await expect(dialog.getByRole('checkbox', { name: /Loan applications/ })).not.toBeChecked()
  await dialog.getByRole('button', { name: 'Save user' }).click()
  // The dialog closes only once the save succeeded. Not the toast: a second save can
  // land while the first one's toast is still showing, and then there are two.
  await expect(dialog).toBeHidden()
  const row = page.getByRole('table', { name: 'Users' }).getByRole('row').filter({ hasText: username })
  await expect(row).toContainText('Read only')

  // Signed in, they cannot start an application.
  const user = await pageAs(browser, { username, password })
  try {
    await user.goto('/loans')
    await expect(user.getByRole('heading', { name: 'Loans', exact: true })).toBeVisible()
    await expect(user.getByRole('link', { name: 'New application' })).toHaveCount(0)

    // The administrator ticks "Loan applications".
    await row.click()
    dialog = page.getByRole('dialog', { name: `Edit ${username}` })
    await dialog.getByRole('checkbox', { name: /Loan applications/ }).check()
    await dialog.getByRole('button', { name: 'Save user' }).click()
    await expect(dialog).toBeHidden()
    await expect(row).toContainText('Loan applications')

    // The same session picks the right up on its next load, with no new sign-in.
    await user.reload()
    await user.getByRole('link', { name: 'New application' }).click()
    await expect(user.getByRole('heading', { name: 'New loan application' })).toBeVisible()
    // Only the right that was granted: still no Borrowers right.
    await user.goto('/borrowers')
    await expect(user.getByRole('heading', { name: 'Borrowers', exact: true })).toBeVisible()
    await expect(user.getByRole('link', { name: 'New borrower' })).toHaveCount(0)
  } finally {
    await user.context().close()
  }
})
