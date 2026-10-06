/* Before the suite: bring the dedicated database to the current schema, empty it,
   and load the demo data, so every run starts from the same book. */
import { spawnSync } from 'node:child_process'
import { rmSync } from 'node:fs'

import { backendDir, backendEnv, dbName, messagesFile, pythonPath } from './env.js'

function manage(...args) {
  const result = spawnSync(pythonPath(), ['manage.py', ...args], {
    cwd: backendDir,
    env: backendEnv,
    stdio: 'inherit',
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`manage.py ${args.join(' ')} failed against ${dbName} (exit ${result.status})`)
  }
}

export default function globalSetup() {
  rmSync(messagesFile, { force: true })
  if (process.env.E2E_SKIP_SEED) return
  console.log(`\nPreparing ${dbName}: migrate, flush, seed`)
  manage('migrate', '--noinput', '--verbosity', '0')
  manage('flush', '--noinput')
  manage('seed')
}
