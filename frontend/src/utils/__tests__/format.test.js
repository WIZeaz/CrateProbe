import test from 'node:test'
import assert from 'node:assert/strict'
import {
  formatBytes,
  formatCount,
  formatDateTime,
  formatDuration,
  formatDurationMs,
  formatPercent,
  formatRelativeTime,
  formatTime,
} from '../format.js'

const NOW = new Date('2026-10-21T14:30:00Z').getTime()

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

test('formatDurationMs handles milliseconds directly', () => {
  assert.equal(formatDurationMs(30_000), '30s')
  assert.equal(formatDurationMs(90_000), '1m 30s')
  assert.equal(formatDurationMs(3_661_000), '1h 1m')
  assert.equal(formatDurationMs(-1), '—')
  assert.equal(formatDurationMs(Number.NaN), '—')
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

test('formatDateTime and formatTime handle missing and invalid values', () => {
  assert.equal(formatDateTime(null), '—')
  assert.equal(formatDateTime('not-a-date'), '—')
  assert.equal(formatTime(null), '—')
  assert.equal(formatTime('not-a-date'), '—')
  assert.notEqual(formatDateTime('2026-10-21T14:30:00Z'), '—')
  assert.notEqual(formatTime('2026-10-21T14:30:00Z'), '—')
})

test('formatBytes formats file sizes and handles invalid values', () => {
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatBytes(512), '512 B')
  assert.equal(formatBytes(1024), '1.0 KB')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(1024 * 1024), '1.0 MB')
  assert.equal(formatBytes(null), '—')
  assert.equal(formatBytes('nope'), '—')
})
