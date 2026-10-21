function toSafeCount(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) {
    return 0
  }
  return Math.floor(number)
}

export function formatCount(value) {
  return toSafeCount(value).toLocaleString('en-US')
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || bytes === '') {
    return '—'
  }

  const value = Number(bytes)
  if (!Number.isFinite(value) || value < 0) {
    return '—'
  }

  if (value < 1024) {
    return `${Math.round(value)} B`
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`
  }
  if (value < 1024 * 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MB`
  }
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

export function formatPercent(part, total) {
  const count = toSafeCount(part)
  const whole = toSafeCount(total)

  if (count === 0 || whole === 0) {
    return '0%'
  }

  const raw = (count / whole) * 100
  if (raw < 0.1) {
    return '<0.1%'
  }
  if (raw >= 99.95) {
    return '100%'
  }

  const rounded = Math.round(raw * 10) / 10
  return Number.isInteger(rounded) ? `${rounded.toFixed(0)}%` : `${rounded.toFixed(1)}%`
}

export function formatDuration(startStr, endStr, now = Date.now()) {
  if (!startStr) {
    return '—'
  }

  const start = new Date(startStr).getTime()
  if (!Number.isFinite(start)) {
    return '—'
  }

  let end = now
  if (endStr) {
    end = new Date(endStr).getTime()
    if (!Number.isFinite(end)) {
      return '—'
    }
  }

  return formatDurationMs(Math.max(0, end - start))
}

export function formatDurationMs(ms) {
  if (!Number.isFinite(ms) || ms < 0) {
    return '—'
  }

  const diff = Math.floor(ms / 1000)
  if (diff < 60) {
    return `${diff}s`
  }
  if (diff < 3600) {
    return `${Math.floor(diff / 60)}m ${diff % 60}s`
  }

  const hours = Math.floor(diff / 3600)
  const minutes = Math.floor((diff % 3600) / 60)
  return `${hours}h ${minutes}m`
}

export function formatRelativeTime(dateStr, now = Date.now()) {
  if (!dateStr) {
    return '—'
  }

  const timestamp = new Date(dateStr).getTime()
  if (!Number.isFinite(timestamp)) {
    return '—'
  }

  const diff = Math.max(0, Math.floor((now - timestamp) / 1000))
  if (diff < 5) {
    return 'just now'
  }
  if (diff < 60) {
    return `${diff}s ago`
  }
  if (diff < 3600) {
    return `${Math.floor(diff / 60)}m ago`
  }
  if (diff < 86400) {
    return `${Math.floor(diff / 3600)}h ago`
  }
  return `${Math.floor(diff / 86400)}d ago`
}

export function formatDateTime(dateStr) {
  if (!dateStr) {
    return '—'
  }

  const date = new Date(dateStr)
  if (Number.isNaN(date.getTime())) {
    return '—'
  }
  return date.toLocaleString()
}

export function formatTime(dateStr) {
  if (!dateStr) {
    return '—'
  }

  const date = new Date(dateStr)
  if (Number.isNaN(date.getTime())) {
    return '—'
  }
  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
