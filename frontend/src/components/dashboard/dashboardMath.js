const STATUS_LABELS = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  timeout: 'Timeout',
  oom: 'OOM',
  runner_failed: 'Runner failed',
}

const STATUS_TONES = {
  running: 'signal',
  completed: 'good',
  failed: 'alarm',
  runner_failed: 'alarm',
  timeout: 'warn',
  oom: 'violet',
  pending: 'neutral',
  cancelled: 'neutral',
}

const HEALTH_LABELS = {
  online: 'Online',
  offline: 'Offline',
  disabled: 'Disabled',
}

const HEALTH_TONES = {
  online: 'good',
  offline: 'alarm',
  disabled: 'neutral',
}

function toSafeCount(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) {
    return 0
  }
  return Math.floor(number)
}

export function statusLabel(status) {
  if (!status) {
    return 'Unknown'
  }
  return STATUS_LABELS[status] || status
}

export function statusTone(status) {
  return STATUS_TONES[status] || 'neutral'
}

export function healthLabel(status) {
  if (!status) {
    return 'Unknown'
  }
  return HEALTH_LABELS[status] || 'Unknown'
}

export function healthTone(status) {
  return HEALTH_TONES[status] || 'neutral'
}

export function formatCount(value) {
  return toSafeCount(value).toLocaleString('en-US')
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

  const diff = Math.max(0, Math.floor((end - start) / 1000))
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

export function groupStatusCounts(stats = {}) {
  const counts = {
    pending: toSafeCount(stats.pending),
    running: toSafeCount(stats.running),
    completed: toSafeCount(stats.completed),
    failed: toSafeCount(stats.failed),
    cancelled: toSafeCount(stats.cancelled),
    timeout: toSafeCount(stats.timeout),
    oom: toSafeCount(stats.oom),
    runner_failed: toSafeCount(stats.runner_failed),
  }

  const total = Object.values(counts).reduce((sum, count) => sum + count, 0)
  const inFlight = counts.pending + counts.running
  const finished = counts.completed + counts.cancelled
  const attention = counts.failed + counts.timeout + counts.oom + counts.runner_failed

  const segments = [
    { key: 'completed', label: 'Completed', count: counts.completed },
    { key: 'inFlight', label: 'In flight', count: inFlight },
    { key: 'attention', label: 'Needs attention', count: attention },
    { key: 'cancelled', label: 'Cancelled', count: counts.cancelled },
  ].map(segment => ({
    ...segment,
    share: total > 0 ? segment.count / total : 0,
    percentLabel: formatPercent(segment.count, total),
  }))

  const groups = [
    { key: 'inFlight', label: 'In flight', statuses: ['running', 'pending'] },
    { key: 'finished', label: 'Finished', statuses: ['completed', 'cancelled'] },
    {
      key: 'attention',
      label: 'Needs attention',
      statuses: ['failed', 'timeout', 'oom', 'runner_failed'],
    },
  ].map(group => ({
    key: group.key,
    label: group.label,
    statuses: group.statuses.map(status => ({
      status,
      label: statusLabel(status),
      count: counts[status],
    })),
  }))

  return {
    total,
    inFlight,
    finished,
    attention,
    counts,
    segments,
    groups,
  }
}
