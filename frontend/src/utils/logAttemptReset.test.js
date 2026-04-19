import test from 'node:test'
import assert from 'node:assert/strict'

import { shouldResetLogViewer } from './logAttemptReset.js'

test('returns true when status enters running', () => {
  const previousTask = { status: 'completed', attempt: 1, started_at: '2026-01-01T00:00:00' }
  const nextTask = { status: 'running', attempt: 1, started_at: '2026-01-01T00:01:00' }

  assert.equal(shouldResetLogViewer(previousTask, nextTask), true)
})

test('returns true when attempt changes', () => {
  const previousTask = { status: 'running', attempt: 1, started_at: '2026-01-01T00:00:00' }
  const nextTask = { status: 'running', attempt: 2, started_at: '2026-01-01T00:00:01' }

  assert.equal(shouldResetLogViewer(previousTask, nextTask), true)
})

test('returns true when started_at changes to a new non-empty value', () => {
  const previousTask = { status: 'running', attempt: 1, started_at: '2026-01-01T00:00:00' }
  const nextTask = { status: 'running', attempt: 1, started_at: '2026-01-01T00:00:02' }

  assert.equal(shouldResetLogViewer(previousTask, nextTask), true)
})

test('returns false when task metadata does not indicate new attempt', () => {
  const previousTask = { status: 'running', attempt: 1, started_at: '2026-01-01T00:00:00' }
  const nextTask = { status: 'running', attempt: 1, started_at: '2026-01-01T00:00:00' }

  assert.equal(shouldResetLogViewer(previousTask, nextTask), false)
})

test('returns false when previous task is missing', () => {
  const nextTask = { status: 'running', attempt: 1, started_at: '2026-01-01T00:00:00' }

  assert.equal(shouldResetLogViewer(null, nextTask), false)
})
