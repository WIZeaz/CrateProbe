import test from 'node:test'
import assert from 'node:assert/strict'
import {
  formatCount,
  formatDuration,
  formatPercent,
  formatRelativeTime,
  groupStatusCounts,
  healthLabel,
  healthTone,
  statusLabel,
  statusTone,
} from '../dashboardMath.js'

const NOW = new Date('2026-10-21T14:30:00Z').getTime()

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

test('formatCount adds thousands separators and clamps invalid values', () => {
  assert.equal(formatCount(0), '0')
  assert.equal(formatCount(1234), '1,234')
  assert.equal(formatCount(1234567), '1,234,567')
  assert.equal(formatCount(null), '0')
  assert.equal(formatCount(undefined), '0')
  assert.equal(formatCount('nope'), '0')
  assert.equal(formatCount(-5), '0')
})

test('formatPercent keeps whole percentages clean and tiny shares readable', () => {
  assert.equal(formatPercent(0, 0), '0%')
  assert.equal(formatPercent(70, 100), '70%')
  assert.equal(formatPercent(1, 3), '33.3%')
  assert.equal(formatPercent(1, 2000), '<0.1%')
  assert.equal(formatPercent(2000, 2000), '100%')
})

test('formatDuration returns an em dash without a valid start time', () => {
  assert.equal(formatDuration(null, null, NOW), '—')
  assert.equal(formatDuration('not-a-date', null, NOW), '—')
})

test('formatDuration formats seconds, minutes and hours', () => {
  const start = new Date(NOW - 90_000).toISOString()
  assert.equal(formatDuration(start, null, NOW), '1m 30s')

  const longStart = new Date(NOW - 3_661_000).toISOString()
  assert.equal(formatDuration(longStart, null, NOW), '1h 1m')

  const shortStart = new Date(NOW - 30_000).toISOString()
  assert.equal(formatDuration(shortStart, null, NOW), '30s')
})

test('formatDuration clamps negative and invalid ranges', () => {
  const future = new Date(NOW + 10_000).toISOString()
  assert.equal(formatDuration(future, null, NOW), '0s')
  assert.equal(formatDuration('not-a-date', null, NOW), '—')
})

test('formatRelativeTime returns readable ages', () => {
  const at = seconds => new Date(NOW - seconds * 1000).toISOString()

  assert.equal(formatRelativeTime(at(3), NOW), 'just now')
  assert.equal(formatRelativeTime(at(45), NOW), '45s ago')
  assert.equal(formatRelativeTime(at(120), NOW), '2m ago')
  assert.equal(formatRelativeTime(at(7200), NOW), '2h ago')
  assert.equal(formatRelativeTime(at(172800), NOW), '2d ago')
})

test('formatRelativeTime handles future, missing and invalid values', () => {
  assert.equal(formatRelativeTime(new Date(NOW + 5000).toISOString(), NOW), 'just now')
  assert.equal(formatRelativeTime(null, NOW), '—')
  assert.equal(formatRelativeTime('not-a-date', NOW), '—')
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
