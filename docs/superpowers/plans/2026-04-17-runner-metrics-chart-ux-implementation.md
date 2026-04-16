# Runner Metrics Chart UX Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add readable axes and hover tooltips (time + current value) to runner resource charts while keeping the existing lightweight SVG approach.

**Architecture:** Keep `RunnerMetricsChart.vue` as the rendering component, but move chart math/formatting into a small pure helper module for deterministic logic and easy tests. Render axes/grid/ticks and add an SVG interaction layer (nearest-point hover, crosshair, marker, tooltip). Apply fixed Y domain `0..100` only for resource percent fields; preserve existing behavior for non-resource fields.

**Tech Stack:** Vue 3 SFC, SVG, JavaScript ESM helpers, Node built-in test runner (`node --test`), Vite build.

---

## File Structure

- Create: `frontend/src/components/runnerMetricsChartMath.js`
  - Pure functions for clamping, coordinate mapping, tick generation, timestamp formatting, nearest-index lookup, tooltip placement.
- Create: `frontend/src/components/__tests__/runnerMetricsChartMath.test.js`
  - Unit tests for helper behavior (including edge cases and fallbacks) using Node's built-in test runner.
- Modify: `frontend/src/components/RunnerMetricsChart.vue`
  - Consume helper functions, draw axes/grid/ticks, and implement hover interaction/tooltip.

## Chunk 1: Chart Math Helpers + TDD Baseline

### Task 1: Add deterministic helper module

**Files:**
- Create: `frontend/src/components/runnerMetricsChartMath.js`
- Test: `frontend/src/components/__tests__/runnerMetricsChartMath.test.js`

- [ ] **Step 1: Write the failing test file for helper contracts**

```js
import test from 'node:test'
import assert from 'node:assert/strict'
import { computeDomainMaxY, clampValue, pickXTicks, formatHoverLabel } from '../runnerMetricsChartMath.js'

test('resource fields use fixed maxY=100', () => {
  assert.equal(computeDomainMaxY('cpu_percent', 37), 100)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `node --test src/components/__tests__/runnerMetricsChartMath.test.js`
Expected: FAIL due to missing module/exports.

- [ ] **Step 3: Implement minimal helper exports**

```js
const RESOURCE_FIELDS = new Set(['cpu_percent', 'memory_percent', 'disk_percent'])

export function computeDomainMaxY(field, fallbackMaxY = 100) {
  if (RESOURCE_FIELDS.has(field)) return 100
  const numeric = Number(fallbackMaxY)
  return Number.isFinite(numeric) && numeric > 0 ? numeric : 100
}
```

- [ ] **Step 4: Expand tests for edge behavior and timestamp fallback chain**

```js
test('non-resource fields preserve caller maxY', () => {
  assert.equal(computeDomainMaxY('active_tasks', 9), 9)
})

test('timestamp fallback prefers timestamp over others', () => {
  const point = { timestamp: '2026-04-17T10:00:00Z', collected_at: '2026-04-17T09:59:00Z' }
  assert.equal(resolvePointTimestamp(point), '2026-04-17T10:00:00Z')
})

test('timestamp fallback uses collected_at then recorded_at', () => {
  assert.equal(resolvePointTimestamp({ collected_at: '2026-04-17T10:01:00Z' }), '2026-04-17T10:01:00Z')
  assert.equal(resolvePointTimestamp({ recorded_at: '2026-04-17T10:02:00Z' }), '2026-04-17T10:02:00Z')
})

test('hover label falls back to Sample # when timestamp invalid', () => {
  assert.equal(formatHoverLabel({ index: 0, timestamp: null }).timeText, 'Sample #1')
})
```

- [ ] **Step 5: Implement remaining helper functions to satisfy tests**

```js
export function clampValue(value, maxY) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 0
  return Math.min(maxY, Math.max(0, numeric))
}
```

- [ ] **Step 6: Re-run helper tests to verify pass**

Run (from `frontend/`): `node --test src/components/__tests__/runnerMetricsChartMath.test.js`
Expected: PASS.

- [ ] **Step 7: Commit helper foundation**

```bash
git add src/components/runnerMetricsChartMath.js src/components/__tests__/runnerMetricsChartMath.test.js
git commit -m "test(chart): add runner metrics helper contracts"
```

## Chunk 2: SVG Axes/Grid/Ticks Rendering

### Task 2: Add axis and tick rendering to chart component

**Files:**
- Modify: `frontend/src/components/RunnerMetricsChart.vue`
- Use: `frontend/src/components/runnerMetricsChartMath.js`
- Test: `frontend/src/components/__tests__/runnerMetricsChartMath.test.js`

- [ ] **Step 1: Write failing tests for deterministic tick selection**

```js
import { pickXTicks, buildXTicks } from '../runnerMetricsChartMath.js'

test('pickXTicks includes first and last index', () => {
  const ticks = pickXTicks({ count: 10, width: 480, minLabelSpacing: 60 })
  assert.equal(ticks.length, 4)
  assert.equal(ticks[0], 0)
  assert.equal(ticks[ticks.length - 1], 9)
  assert.equal(ticks[1], 3)
  assert.equal(ticks[2], 6)
})

test('pickXTicks reduces to 3 ticks for narrow widths', () => {
  const ticks = pickXTicks({ count: 10, width: 150, minLabelSpacing: 60 })
  assert.equal(ticks.length, 3)
  assert.equal(ticks[0], 0)
  assert.equal(ticks[ticks.length - 1], 9)
})

test('sampled x ticks dedupe equal formatted labels deterministically', () => {
  const points = [
    { timestamp: '2026-04-17T10:00:00Z' },
    { timestamp: '2026-04-17T10:00:10Z' },
    { timestamp: '2026-04-17T10:05:00Z' },
  ]
  const ticks = buildXTicks(points, { width: 480, minLabelSpacing: 60, formatter: () => '10:00' })
  assert.equal(ticks.length, 1)
  assert.equal(ticks[0].index, 0)
})
```

- [ ] **Step 2: Run helper tests to verify initial failure**

Run (from `frontend/`): `node --test src/components/__tests__/runnerMetricsChartMath.test.js`
Expected: FAIL for missing `pickXTicks` behavior.

- [ ] **Step 3: Implement/adjust helper logic for deterministic ticks and label deduplication**

```js
export function pickXTicks({ count, width, minLabelSpacing = 60 }) {
  const target = width >= minLabelSpacing * 4 ? 4 : 3
  // include first/last + evenly spaced interior indices
}

export function buildXTicks(points, options) {
  // sample indices deterministically, then dedupe by formatted label while preserving order
}
```

- [ ] **Step 4: Update `RunnerMetricsChart.vue` plot geometry and static layers**

```vue
<line :x1="plot.left" :x2="plot.right" :y1="plot.bottom" :y2="plot.bottom" stroke="#cbd5e1" />
<line :x1="plot.left" :x2="plot.left" :y1="plot.top" :y2="plot.bottom" stroke="#cbd5e1" />
```

- [ ] **Step 5: Add conditional Y ticks and X tick labels from helper output**

- Resource fields (`cpu_percent`, `memory_percent`, `disk_percent`): Y ticks `0,25,50,75,100`.
- Non-resource fields: keep existing domain/tick behavior derived from preserved `maxY` path.
- Render horizontal Y-grid lines aligned to each rendered Y tick (including resource fixed ticks).

```vue
<g v-for="tick in yTicks" :key="`y-${tick}`">...</g>
<g v-for="tick in xTicks" :key="`x-${tick.index}`">...</g>
```

- [ ] **Step 6: Re-run helper tests and build**

Run (from `frontend/`): `node --test src/components/__tests__/runnerMetricsChartMath.test.js && npm run build`
Expected: tests PASS, Vite build SUCCESS.

- [ ] **Step 7: Commit axis/tick rendering**

```bash
git add src/components/RunnerMetricsChart.vue src/components/runnerMetricsChartMath.js src/components/__tests__/runnerMetricsChartMath.test.js
git commit -m "feat(chart): add axes and deterministic ticks for runner metrics"
```

## Chunk 3: Hover Interaction + Tooltip

### Task 3: Implement nearest-point hover tooltip

**Files:**
- Modify: `frontend/src/components/RunnerMetricsChart.vue`
- Modify: `frontend/src/components/runnerMetricsChartMath.js`
- Test: `frontend/src/components/__tests__/runnerMetricsChartMath.test.js`

- [ ] **Step 1: Add failing tests for nearest index and boundary-aware tooltip placement**

```js
import { nearestIndexFromX, computeTooltipPosition } from '../runnerMetricsChartMath.js'

test('nearest index maps cursor x to closest point', () => {
  assert.equal(nearestIndexFromX({ x: 240, plotLeft: 40, plotWidth: 400, count: 5 }), 2)
})

test('tooltip prefers top-right in normal space', () => {
  const pos = computeTooltipPosition({ x: 120, y: 50, chartWidth: 480, chartHeight: 120, tipWidth: 120, tipHeight: 44 })
  assert.equal(pos.x >= 120, true)
  assert.equal(pos.y <= 50, true)
})

test('tooltip flips left/down near top-right edge', () => {
  const pos = computeTooltipPosition({ x: 470, y: 8, chartWidth: 480, chartHeight: 120, tipWidth: 120, tipHeight: 44 })
  assert.equal(pos.x + 120 <= 480, true)
  assert.equal(pos.y + 44 <= 120, true)
})
```

- [ ] **Step 2: Run helper tests to confirm failure**

Run (from `frontend/`): `node --test src/components/__tests__/runnerMetricsChartMath.test.js`
Expected: FAIL for missing hover helpers.

- [ ] **Step 3: Implement hover helper utilities (nearest index + boundary-aware tooltip positioning)**

```js
export function nearestIndexFromX({ x, plotLeft, plotWidth, count }) {
  const ratio = (x - plotLeft) / Math.max(plotWidth, 1)
  return Math.min(count - 1, Math.max(0, Math.round(ratio * (count - 1))))
}

export function computeTooltipPosition({ x, y, chartWidth, chartHeight, tipWidth, tipHeight, gap = 10 }) {
  let nextX = x + gap
  let nextY = y - tipHeight - gap
  if (nextX + tipWidth > chartWidth) nextX = x - tipWidth - gap
  if (nextX < 0) nextX = 0
  if (nextY < 0) nextY = y + gap
  if (nextY + tipHeight > chartHeight) nextY = Math.max(0, chartHeight - tipHeight)
  return { x: nextX, y: nextY }
}
```

- [ ] **Step 4: Add pointer event handlers in `RunnerMetricsChart.vue`**

```vue
<rect :x="plot.left" :y="plot.top" :width="plot.width" :height="plot.height" fill="transparent"
  @mousemove="onPointerMove" @mouseleave="onPointerLeave" />
```

- [ ] **Step 5: Render hover crosshair, marker, and tooltip (time + value)**

- Tooltip text must use formatted local timestamp, or `Sample #<index+1>` when no parseable timestamp exists, plus formatted metric value.

```vue
<line v-if="hover" :x1="hover.x" :x2="hover.x" :y1="plot.top" :y2="plot.bottom" stroke="#94a3b8" stroke-dasharray="4 4" />
<circle v-if="hover" :cx="hover.x" :cy="hover.y" r="3.5" :fill="stroke" />
```

- [ ] **Step 6: Verify tests/build and manual behavior in RunnerList**

Run (from `frontend/`): `node --test src/components/__tests__/runnerMetricsChartMath.test.js && npm run build`
Expected: PASS + SUCCESS.

Manual checks (`frontend/src/views/RunnerList.vue`):
- CPU/Memory/Disk charts show tooltip with local time + value formatted to one decimal place (e.g., `42.3%`).
- Hover out clears tooltip.
- Tooltip stays in bounds near chart edges.

- [ ] **Step 7: Commit hover UX**

```bash
git add src/components/RunnerMetricsChart.vue src/components/runnerMetricsChartMath.js src/components/__tests__/runnerMetricsChartMath.test.js
git commit -m "feat(chart): add hover tooltip and crosshair for runner metrics"
```

## Chunk 4: Final Verification and Cleanup

### Task 4: End-to-end verification and docs alignment

**Files:**
- Verify: `frontend/src/components/RunnerMetricsChart.vue`
- Verify: `docs/superpowers/specs/2026-04-17-runner-metrics-chart-ux-design.md`

- [ ] **Step 1: Run full frontend build one more time**

Run (from `frontend/`): `npm run build`
Expected: SUCCESS with no new warnings/errors from this feature.

- [ ] **Step 2: Validate acceptance criteria against spec**

Checklist:
- CPU/Memory/Disk charts display visible axes and ticks (Y ticks `0/25/50/75/100`; deterministic sampled X time ticks).
- Hover shows exact local time + current value and clears on mouse leave.
- Tooltip remains inside bounds near chart edges.
- `1h`/`6h`/`24h` window switches still refresh metrics correctly in RunnerList.
- Narrow-width layout keeps labels readable and tooltip usable.
- Active Tasks chart behavior remains unchanged.

- [ ] **Step 3: Final commit for verification notes (only if files changed in this task)**

```bash
git add docs/superpowers/plans/2026-04-17-runner-metrics-chart-ux-implementation.md
git commit -m "chore(chart): finalize runner metrics chart verification checklist"
```
