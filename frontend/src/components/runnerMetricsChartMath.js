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

  const seen = new Set()
  const ticks = []

  for (const index of tickIndices) {
    const point = safePoints[index]
    const label = String(formatLabel(point, index, safePoints))
    if (seen.has(label)) {
      continue
    }
    seen.add(label)
    ticks.push({ index, label })
  }

  return ticks
}
