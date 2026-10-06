/* Where the end-to-end run finds Python, which ports it uses, and the environment
   the Django process gets. Shared by playwright.config.js and global-setup.js so
   the server and the seeding always point at the same database. */
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const frontendDir = resolve(here, '..')
export const backendDir = resolve(frontendDir, '..', 'backend')

export const apiPort = Number(process.env.E2E_API_PORT || 8765)
export const webPort = Number(process.env.E2E_WEB_PORT || 5174)
export const apiURL = `http://127.0.0.1:${apiPort}`
export const webURL = `http://127.0.0.1:${webPort}`

/** E2E_PYTHON, else the repository's .venv, else whatever `python` is on the PATH. */
export function pythonPath() {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON
  const venv = resolve(frontendDir, '..', '.venv')
  for (const candidate of [resolve(venv, 'bin', 'python'), resolve(venv, 'Scripts', 'python.exe')]) {
    if (existsSync(candidate)) return candidate
  }
  return 'python'
}

/**
 * The database the run owns. Global setup flushes it, so it must never be the
 * one anybody works in: a name without "e2e" in it is refused outright.
 */
export const dbName = process.env.E2E_DB_NAME || 'LMS_e2e'
if (!/e2e/i.test(dbName)) {
  throw new Error(
    `E2E_DB_NAME is "${dbName}". The end-to-end run empties its database, so the name must contain "e2e".`,
  )
}

/**
 * The Django process's environment. Connection settings (DB_HOST, DB_USER, ...)
 * come from the shell or from backend/.env as usual; only the database name is
 * forced, along with what a test run needs: DEBUG on so plain HTTP is served, and
 * a login throttle loose enough for a suite that signs in dozens of times a minute.
 */
/** Texts go to this file, one JSON line each, so a test can read a code it was sent. */
export const messagesFile = resolve(frontendDir, 'test-results', 'e2e-messages.log')

export const backendEnv = {
  ...process.env,
  MESSAGE_SMS_BACKEND: 'file',
  MESSAGE_FILE_PATH: messagesFile,
  DB_NAME: dbName,
  DEBUG: '1',
  SECURE_SSL_REDIRECT: '0',
  THROTTLE_LOGIN: process.env.E2E_THROTTLE_LOGIN || '1000/min',
  ALLOWED_HOSTS: 'localhost,127.0.0.1',
  // Output from the server and the seed is easier to read unbuffered.
  PYTHONUNBUFFERED: '1',
}
