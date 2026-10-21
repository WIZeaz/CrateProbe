import test from 'node:test'
import assert from 'node:assert/strict'
import {
  groupStatusCounts,
  healthLabel,
  healthTone,
  isTerminalStatus,
  statusLabel,
  statusTone,
} from '../status.js'

test('statusLabel returns sentence-case labels for known statuses', () => {
  assert.equal(statusLabel('pending'), 'Pending')
  assert.equal(statusLabel('running'), 'Running')
  assert.equal(statusLabel('completed'), 'Completed')
  assert.equal(statusLabel('failed'), 'Failed')
  assert.equal(statusLabel('cancelled'), 'Cancelled')
  assert.equal(statusLabel('timeout'), 'Timeout')
  assert.equal(statusLabel('oom'), 'OOM')
  assert.equal(statusLabel('runner_failed'), 'Runner failed')
})

test('statusLabel preserves unknown values and names empty ones', () => {
  assert.equal(statusLabel('mystery'), 'mystery')
  assert.equal(statusLabel(null), 'Unknown')
  assert.equal(statusLabel(''), 'Unknown')
})

test('statusTone maps each status to a semantic tone', () => {
  assert.equal(statusTone('running'), 'signal')
  assert.equal(statusTone('completed'), 'good')
  assert.equal(statusTone('failed'), 'alarm')
  assert.equal(statusTone('runner_failed'), 'alarm')
  assert.equal(statusTone('timeout'), 'warn')
  assert.equal(statusTone('oom'), 'violet')
  assert.equal(statusTone('pending'), 'neutral')
  assert.equal(statusTone('cancelled'), 'neutral')
  assert.equal(statusTone('mystery'), 'neutral')
})

test('healthLabel and healthTone describe runner states', () => {
  assert.equal(healthLabel('online'), 'Online')
  assert.equal(healthLabel('offline'), 'Offline')
  assert.equal(healthLabel('disabled'), 'Disabled')
  assert.equal(healthLabel(null), 'Unknown')

  assert.equal(healthTone('online'), 'good')
  assert.equal(healthTone('offline'), 'alarm')
  assert.equal(healthTone('disabled'), 'neutral')
  assert.equal(healthTone('mystery'), 'neutral')
})

test('isTerminalStatus recognizes terminal job states', () => {
  assert.equal(isTerminalStatus('completed'), true)
  assert.equal(isTerminalStatus('failed'), true)
  assert.equal(isTerminalStatus('cancelled'), true)
  assert.equal(isTerminalStatus('timeout'), true)
  assert.equal(isTerminalStatus('oom'), true)
  assert.equal(isTerminalStatus('runner_failed'), true)
  assert.equal(isTerminalStatus('running'), false)
  assert.equal(isTerminalStatus('pending'), false)
})

test('groupStatusCounts derives lifecycle groups and shares from raw stats', () => {
  const result = groupStatusCounts({
    total: 100,
    pending: 4,
    running: 6,
    completed: 80,
    failed: 5,
    cancelled: 1,
    timeout: 2,
    oom: 1,
    runner_failed: 1,
  })

  assert.equal(result.total, 100)
  assert.equal(result.inFlight, 10)
  assert.equal(result.finished, 81)
  assert.equal(result.attention, 9)

  assert.deepEqual(
    result.segments.map(segment => [segment.key, segment.count, segment.share]),
    [
      ['completed', 80, 0.8],
      ['inFlight', 10, 0.1],
      ['attention', 9, 0.09],
      ['cancelled', 1, 0.01],
    ],
  )
  assert.equal(result.segments[0].percentLabel, '80%')
  assert.equal(result.segments[1].percentLabel, '10%')
  assert.equal(result.segments[2].percentLabel, '9%')
  assert.equal(result.segments[3].percentLabel, '1%')

  assert.deepEqual(
    result.groups.map(group => [group.key, group.statuses.map(item => [item.status, item.count])]),
    [
      ['inFlight', [['running', 6], ['pending', 4]]],
      ['finished', [['completed', 80], ['cancelled', 1]]],
      ['attention', [['failed', 5], ['timeout', 2], ['oom', 1], ['runner_failed', 1]]],
    ],
  )
})

test('groupStatusCounts uses the sum of statuses as its denominator', () => {
  const result = groupStatusCounts({ total: 999, completed: 5, cancelled: 1 })

  assert.equal(result.total, 6)
  assert.equal(result.segments[0].share, 5 / 6)
})

test('groupStatusCounts returns a fully zeroed structure when stats are missing', () => {
  const result = groupStatusCounts()

  assert.equal(result.total, 0)
  assert.equal(result.inFlight, 0)
  assert.equal(result.finished, 0)
  assert.equal(result.attention, 0)
  assert.equal(result.segments.every(segment => segment.count === 0), true)
  assert.equal(result.segments.every(segment => segment.share === 0), true)
  assert.equal(result.segments.every(segment => segment.percentLabel === '0%'), true)
  assert.equal(result.groups[2].statuses.length, 4)
})
