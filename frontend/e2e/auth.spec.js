import { expect, test } from '@playwright/test'

import { USERS, signIn } from './helpers.js'

test('signing in with the right password opens the dashboard', async ({ page }) => {
  await signIn(page, USERS.officer)
  await expect(page).toHaveURL(/\/dashboard$/)

  // The account menu says who is signed in, and signs them out again.
  await page.getByRole('button', { name: 'Account menu' }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
})

test('a wrong password is refused and nobody is signed in', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Username').fill(USERS.viewer.username)
  await page.getByLabel('Password').fill('not-the-password')
  await page.getByRole('button', { name: 'Sign in' }).click()

  await expect(page.getByRole('alert')).toHaveText('Incorrect username or password')
  await expect(page.getByRole('button', { name: 'Account menu' })).toHaveCount(0)
  // No token was stored, so a reload still lands on the sign-in form.
  await page.reload()
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
})
