import { formatPercent } from './format.js'

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

const TERMINAL_STATUSES = new Set([
  'completed',
  'failed',
  'cancelled',
  'timeout',
  'oom',
  'runner_failed',
])

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

export function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(status)
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
