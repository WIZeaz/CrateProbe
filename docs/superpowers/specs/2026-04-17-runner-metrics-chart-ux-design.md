# Runner Metrics Chart UX Design (Axes + Hover Values)

## Context

Runner metrics charts on the Runners page currently render as a simple polyline over a light background. They do not include visible axes, tick labels, or interactive value inspection, which makes trend reading and point-in-time diagnosis harder.

This design improves readability and operator confidence while keeping the existing lightweight SVG implementation and current page structure.

## Goals

- Add clear chart axes and readable ticks to runner resource charts.
- Show exact metric values on hover with timestamp context.
- Keep integration unchanged for the existing `RunnerList` usage.
- Avoid introducing third-party chart dependencies.

## Non-Goals

- Replacing charts with an external charting library.
- Adding zoom, pan, or brush interactions.
- Changing backend metrics schema or API contracts.

## Scope

In scope:

- `frontend/src/components/RunnerMetricsChart.vue`
- Resource percent charts in Runner Details (`cpu_percent`, `memory_percent`, `disk_percent`)

Out of scope:

- Backend services, APIs, or database schema
- Global theme/styling changes outside this chart component
- Active Tasks chart domain/tick redesign (kept as-is for this request)

## Chosen Approach

Use the existing SVG chart and extend it with:

1. Plot area margins (for axis labels)
2. Y-axis ticks/grid lines (fixed 0-100 for this request)
3. X-axis time ticks (sampled from metric points)
4. Hover interaction layer:
   - nearest-point lookup by cursor X position
   - vertical guide line
   - highlighted point marker
   - tooltip with `time + current value`

This was selected because it delivers the requested UX improvements with minimal risk, no additional dependency footprint, and full control over UI details.

## Data and Rendering Rules

### Input Data

- `points`: ordered time-series metric points
- `field`: metric key in this request is limited to `cpu_percent`, `memory_percent`, `disk_percent`
- `maxY`: may still be passed by caller but is not authoritative for this scoped resource-chart rendering
- `stroke`: line color

### Time Source

X-axis timestamp extraction priority:

1. `point.timestamp`
2. `point.collected_at`
3. `point.recorded_at`
4. fallback to index-based label when no parseable time exists

### Axis Rules

- Y-axis domain: `0..100` (fixed)
- Y ticks: `0, 25, 50, 75, 100`
- X ticks: sample 3-5 labels depending on point count to avoid overlap

### Value Rules

- Numeric values are clamped to `0..100` before plotting.
- Tooltip value formatting:
  - percent metrics: one decimal + `%`

## Interaction Design

### Hover Behavior

- On mouse move inside plot area:
  - map pointer X to nearest data index
  - activate hover state for that point
- Render:
  - vertical crosshair at hovered X
  - point highlight at `(x, y)`
  - tooltip near pointer with:
    - formatted local timestamp
    - formatted metric value
- On mouse leave:
  - clear hover state

### Tooltip Placement

- Prefer top-right of cursor.
- If near right/top boundary, flip left/down to stay within SVG bounds.

## Error Handling and Fallbacks

- Empty `points`: no line/axes interaction layer; caller already shows "No monitoring data yet".
- Invalid/missing metric values: coerce to `0` and continue rendering.
- Invalid/missing timestamps: keep plot and fallback X labels to index-based markers.
- Very small datasets (1 point): still render axes and hover for that point.

## Testing and Verification

### Manual Verification

On `frontend/src/views/RunnerList.vue`:

- Verify CPU/Memory/Disk charts display axes and ticks.
- Verify hover displays exact `time + value` and follows cursor.
- Verify `1h`, `6h`, `24h` window switches still refresh data correctly.
- Verify responsive behavior on narrow width (labels remain readable, tooltip stays in bounds).

### Build Validation

- Run `npm run build` in `frontend/` to ensure no compile/type regressions.

## Risks and Mitigations

- Tick label overlap on dense data
  - Mitigation: capped tick count and sampled label rendering.
- Tooltip jitter near edges
  - Mitigation: deterministic boundary-aware positioning.
- Different timestamp fields from backend revisions
  - Mitigation: multi-key timestamp extraction fallback chain.

## Rollout Plan

1. Implement chart component enhancements.
2. Build frontend and fix any regressions.
3. Validate interaction manually with runner metrics data.
4. Merge without backend changes.
