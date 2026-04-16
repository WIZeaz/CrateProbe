import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildXTicks,
  clampValue,
  computeTooltipPosition,
  computeDomainMaxY,
  formatHoverLabel,
  nearestIndexFromX,
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

test('timestamp fallback treats epoch 0 as valid and keeps priority', () => {
  assert.equal(
    resolvePointTimestamp({
      timestamp: 0,
      collected_at: '2026-04-17T10:01:00Z',
      recorded_at: '2026-04-17T10:00:00Z',
    }),
    0,
  )
})

test('hover label falls back to Sample # when timestamp invalid', () => {
  assert.equal(formatHoverLabel({ index: 0, timestamp: null }).timeText, 'Sample #1')
  assert.equal(formatHoverLabel({ index: 1, timestamp: 'not-a-date' }).timeText, 'Sample #2')
})

test('hover label with epoch 0 timestamp does not fallback to Sample #1', () => {
  assert.notEqual(formatHoverLabel({ index: 0, timestamp: 0 }).timeText, 'Sample #1')
})

test('pickXTicks returns empty array for zero count', () => {
  assert.deepEqual(pickXTicks({ count: 0 }), [])
})

test('pickXTicks returns [0] for single point', () => {
  assert.deepEqual(pickXTicks({ count: 1 }), [0])
})

test('pickXTicks chooses 4 ticks for wide chart', () => {
  assert.deepEqual(pickXTicks({ count: 10, width: 480, minLabelSpacing: 60 }), [0, 3, 6, 9])
})

test('pickXTicks chooses 3 ticks for narrow chart', () => {
  assert.deepEqual(pickXTicks({ count: 10, width: 150, minLabelSpacing: 60 }), [0, 4, 9])
})

test('pickXTicks always includes first and last index for multi-point series', () => {
  const ticks = pickXTicks({ count: 7, width: 480, minLabelSpacing: 60 })
  assert.equal(ticks[0], 0)
  assert.equal(ticks[ticks.length - 1], 6)
})

test('buildXTicks dedupes equal labels while preserving deterministic order', () => {
  const points = [
    { timestamp: '2026-04-17T10:00:00Z' },
    { timestamp: '2026-04-17T10:00:10Z' },
    { timestamp: '2026-04-17T10:05:00Z' },
    { timestamp: '2026-04-17T10:10:00Z' },
    { timestamp: '2026-04-17T10:15:00Z' },
  ]

  const ticks = buildXTicks(points, {
    width: 480,
    minLabelSpacing: 60,
    formatter: (point, index) => (index < 3 ? '10:00' : '10:15'),
  })

  assert.deepEqual(ticks, [
    { index: 0, label: '10:00' },
    { index: 4, label: '10:15' },
  ])
})

test('buildXTicks preserves endpoint ticks even when labels match', () => {
  const points = [
    { timestamp: '2026-04-17T10:00:00Z' },
    { timestamp: '2026-04-17T10:02:00Z' },
    { timestamp: '2026-04-17T10:04:00Z' },
    { timestamp: '2026-04-17T10:06:00Z' },
    { timestamp: '2026-04-17T10:08:00Z' },
  ]

  const ticks = buildXTicks(points, {
    width: 480,
    minLabelSpacing: 60,
    formatter: () => '10:00',
  })

  assert.deepEqual(ticks, [
    { index: 0, label: '10:00' },
    { index: 4, label: '10:00' },
  ])
})

test('nearestIndexFromX maps x positions to nearest clamped index', () => {
  const shared = { plotLeft: 40, plotWidth: 400, count: 5 }

  assert.equal(nearestIndexFromX({ ...shared, x: 40 }), 0)
  assert.equal(nearestIndexFromX({ ...shared, x: 90 }), 1)
  assert.equal(nearestIndexFromX({ ...shared, x: 244 }), 2)
  assert.equal(nearestIndexFromX({ ...shared, x: 339 }), 3)
  assert.equal(nearestIndexFromX({ ...shared, x: 440 }), 4)
  assert.equal(nearestIndexFromX({ ...shared, x: -999 }), 0)
  assert.equal(nearestIndexFromX({ ...shared, x: 9999 }), 4)
})

test('computeTooltipPosition prefers top-right when there is space', () => {
  assert.deepEqual(
    computeTooltipPosition({
      x: 150,
      y: 90,
      chartWidth: 480,
      chartHeight: 140,
      tipWidth: 120,
      tipHeight: 44,
      gap: 10,
    }),
    { x: 160, y: 36 },
  )
})

test('computeTooltipPosition flips near top-right and clamps in bounds', () => {
  assert.deepEqual(
    computeTooltipPosition({
      x: 474,
      y: 8,
      chartWidth: 480,
      chartHeight: 140,
      tipWidth: 120,
      tipHeight: 44,
      gap: 10,
    }),
    { x: 344, y: 18 },
  )
})
