import test from 'node:test'
import assert from 'node:assert/strict'
import {
  clampValue,
  computeDomainMaxY,
  formatHoverLabel,
  pickXTicks,
  resolvePointTimestamp,
} from '../runnerMetricsChartMath.js'

test('resource fields use fixed maxY=100', () => {
  assert.equal(computeDomainMaxY('cpu_percent', 37), 100)
  assert.equal(computeDomainMaxY('memory_percent', 12), 100)
  assert.equal(computeDomainMaxY('disk_percent', 88), 100)
})

test('non-resource fields preserve caller maxY', () => {
  assert.equal(computeDomainMaxY('active_tasks', 9), 9)
})

test('clampValue clamps negative values to zero', () => {
  assert.equal(clampValue(-4, 100), 0)
})

test('clampValue clamps values above maxY to maxY', () => {
  assert.equal(clampValue(140, 80), 80)
})

test('clampValue returns zero for non-numeric values', () => {
  assert.equal(clampValue('abc', 100), 0)
})

test('timestamp fallback priority is timestamp then collected_at then recorded_at', () => {
  assert.equal(
    resolvePointTimestamp({
      timestamp: '2026-04-17T10:00:00Z',
      collected_at: '2026-04-17T09:59:00Z',
      recorded_at: '2026-04-17T09:58:00Z',
    }),
    '2026-04-17T10:00:00Z',
  )
  assert.equal(
    resolvePointTimestamp({
      collected_at: '2026-04-17T10:01:00Z',
      recorded_at: '2026-04-17T10:00:00Z',
    }),
    '2026-04-17T10:01:00Z',
  )
  assert.equal(
    resolvePointTimestamp({ recorded_at: '2026-04-17T10:02:00Z' }),
    '2026-04-17T10:02:00Z',
  )
  assert.equal(resolvePointTimestamp({}), null)
})

test('timestamp fallback skips invalid timestamp and uses collected_at when parseable', () => {
  assert.equal(
    resolvePointTimestamp({
      timestamp: 'not-a-date',
      collected_at: '2026-04-17T10:01:00Z',
      recorded_at: '2026-04-17T10:00:00Z',
    }),
    '2026-04-17T10:01:00Z',
  )
})

test('timestamp fallback skips invalid timestamp and collected_at, then uses recorded_at', () => {
  assert.equal(
    resolvePointTimestamp({
      timestamp: 'not-a-date',
      collected_at: 'still-not-a-date',
      recorded_at: '2026-04-17T10:02:00Z',
    }),
    '2026-04-17T10:02:00Z',
  )
})

test('hover label falls back to Sample # when timestamp invalid', () => {
  assert.equal(formatHoverLabel({ index: 0, timestamp: null }).timeText, 'Sample #1')
  assert.equal(formatHoverLabel({ index: 1, timestamp: 'not-a-date' }).timeText, 'Sample #2')
})

test('pickXTicks returns empty array for zero count', () => {
  assert.deepEqual(pickXTicks({ count: 0 }), [])
})

test('pickXTicks returns [0] for single point', () => {
  assert.deepEqual(pickXTicks({ count: 1 }), [0])
})

test('pickXTicks includes first and last indices when count is greater than one', () => {
  assert.deepEqual(pickXTicks({ count: 5 }), [0, 4])
})
