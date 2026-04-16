export const RESOURCE_FIELDS = new Set(['cpu_percent', 'memory_percent', 'disk_percent'])

export function computeDomainMaxY(field, fallbackMaxY = 100) {
  if (RESOURCE_FIELDS.has(field)) {
    return 100
  }

  const numericFallback = Number(fallbackMaxY)
  return Number.isFinite(numericFallback) && numericFallback > 0 ? numericFallback : 100
}

export function clampValue(value, maxY) {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) {
    return 0
  }

  const numericMaxY = Number(maxY)
  const upperBound = Number.isFinite(numericMaxY) && numericMaxY > 0 ? numericMaxY : 100
  return Math.min(upperBound, Math.max(0, numericValue))
}

export function resolvePointTimestamp(point) {
  if (!point || typeof point !== 'object') {
    return null
  }

  const candidates = [point.timestamp, point.collected_at, point.recorded_at]
  for (const candidate of candidates) {
    const parsed = new Date(candidate)
    if (candidate != null && Number.isFinite(parsed.getTime())) {
      return candidate
    }
  }

  return null
}

export function formatHoverLabel({ index, timestamp }) {
  const parsed = new Date(timestamp)
  if (timestamp != null && Number.isFinite(parsed.getTime())) {
    return { timeText: parsed.toLocaleString() }
  }

  return { timeText: `Sample #${Number(index) + 1}` }
}

export function pickXTicks({ count, width, minLabelSpacing = 60 } = {}) {
  const numericCount = Number(count)
  if (!Number.isInteger(numericCount) || numericCount <= 0) {
    return []
  }
  if (numericCount === 1) {
    return [0]
  }

  const targetTickCount = Number(width) >= Number(minLabelSpacing) * 4 ? 4 : 3
  const tickCount = Math.min(targetTickCount, numericCount)

  if (tickCount <= 2) {
    return [0, numericCount - 1]
  }

  const ticks = []
  for (let i = 0; i < tickCount; i += 1) {
    const rawIndex = Math.floor((i * (numericCount - 1)) / (tickCount - 1))
    const index = i === tickCount - 1 ? numericCount - 1 : rawIndex
    if (ticks[ticks.length - 1] !== index) {
      ticks.push(index)
    }
  }

  if (ticks[0] !== 0) {
    ticks.unshift(0)
  }
  if (ticks[ticks.length - 1] !== numericCount - 1) {
    ticks.push(numericCount - 1)
  }

  return ticks
}

export function buildXTicks(points, { width, minLabelSpacing = 60, formatter } = {}) {
  const safePoints = Array.isArray(points) ? points : []
  const tickIndices = pickXTicks({
    count: safePoints.length,
    width,
    minLabelSpacing,
  })

  const formatLabel =
    typeof formatter === 'function'
      ? formatter
      : (point, index) => {
          const timestamp = resolvePointTimestamp(point)
          if (timestamp != null) {
            const parsed = new Date(timestamp)
            if (Number.isFinite(parsed.getTime())) {
              return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            }
          }
          return `#${index + 1}`
        }

  const firstIndex = tickIndices[0]
  const lastIndex = tickIndices[tickIndices.length - 1]
  const seenLabels = new Set()
  const ticks = []

  for (const index of tickIndices) {
    const point = safePoints[index]
    const label = String(formatLabel(point, index, safePoints))

    const isEndpoint = index === firstIndex || index === lastIndex
    if (isEndpoint) {
      ticks.push({ index, label })
      seenLabels.add(label)
      continue
    }

    if (seenLabels.has(label)) {
      continue
    }
    seenLabels.add(label)
    ticks.push({ index, label })
  }

  return ticks
}

export function nearestIndexFromX({ x, plotLeft, plotWidth, count }) {
  const numericCount = Number(count)
  if (!Number.isInteger(numericCount) || numericCount <= 0) {
    return 0
  }
  if (numericCount === 1) {
    return 0
  }

  const numericPlotLeft = Number(plotLeft)
  const safePlotLeft = Number.isFinite(numericPlotLeft) ? numericPlotLeft : 0
  const numericPlotWidth = Number(plotWidth)
  const safePlotWidth = Number.isFinite(numericPlotWidth) && numericPlotWidth > 0 ? numericPlotWidth : 1
  const numericX = Number(x)
  const safeX = Number.isFinite(numericX) ? numericX : safePlotLeft

  const ratio = (safeX - safePlotLeft) / safePlotWidth
  const clampedRatio = Math.min(1, Math.max(0, ratio))
  const nearest = Math.round(clampedRatio * (numericCount - 1))
  return Math.min(numericCount - 1, Math.max(0, nearest))
}

export function computeTooltipPosition({
  x,
  y,
  chartWidth,
  chartHeight,
  tipWidth,
  tipHeight,
  gap = 10,
}) {
  const safeChartWidth = Math.max(0, Number(chartWidth) || 0)
  const safeChartHeight = Math.max(0, Number(chartHeight) || 0)
  const safeTipWidth = Math.max(0, Number(tipWidth) || 0)
  const safeTipHeight = Math.max(0, Number(tipHeight) || 0)
  const safeGap = Math.max(0, Number(gap) || 0)
  const safeX = Number.isFinite(Number(x)) ? Number(x) : 0
  const safeY = Number.isFinite(Number(y)) ? Number(y) : 0

  let nextX = safeX + safeGap
  let nextY = safeY - safeTipHeight - safeGap

  if (nextX + safeTipWidth > safeChartWidth) {
    nextX = safeX - safeTipWidth - safeGap
  }
  if (nextY < 0) {
    nextY = safeY + safeGap
  }

  nextX = Math.min(Math.max(0, nextX), Math.max(0, safeChartWidth - safeTipWidth))
  nextY = Math.min(Math.max(0, nextY), Math.max(0, safeChartHeight - safeTipHeight))

  return {
    x: nextX,
    y: nextY,
  }
}
