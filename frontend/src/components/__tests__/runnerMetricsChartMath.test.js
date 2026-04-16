import test from 'node:test'
import assert from 'node:assert/strict'
import {
  computeDomainMaxY,
  resolvePointTimestamp,
  formatHoverLabel,
} from '../runnerMetricsChartMath.js'

test('resource fields use fixed maxY=100', () => {
  assert.equal(computeDomainMaxY('cpu_percent', 37), 100)
  assert.equal(computeDomainMaxY('memory_percent', 12), 100)
  assert.equal(computeDomainMaxY('disk_percent', 88), 100)
})

test('non-resource fields preserve caller maxY', () => {
  assert.equal(computeDomainMaxY('active_tasks', 9), 9)
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

test('hover label falls back to Sample # when timestamp invalid', () => {
  assert.equal(formatHoverLabel({ index: 0, timestamp: null }).timeText, 'Sample #1')
  assert.equal(formatHoverLabel({ index: 1, timestamp: 'not-a-date' }).timeText, 'Sample #2')
})
