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
    if (candidate && Number.isFinite(parsed.getTime())) {
      return candidate
    }
  }

  return null
}

export function formatHoverLabel({ index, timestamp }) {
  const parsed = new Date(timestamp)
  if (timestamp && Number.isFinite(parsed.getTime())) {
    return { timeText: parsed.toLocaleString() }
  }

  return { timeText: `Sample #${Number(index) + 1}` }
}

export function pickXTicks({ count }) {
  const numericCount = Number(count)
  if (!Number.isInteger(numericCount) || numericCount <= 0) {
    return []
  }
  if (numericCount === 1) {
    return [0]
  }
  return [0, numericCount - 1]
}
