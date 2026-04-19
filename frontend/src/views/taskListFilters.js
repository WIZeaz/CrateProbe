export function filterTasksByCrateName(tasks, query) {
  const normalized = query.trim().toLowerCase()
  if (!normalized) {
    return tasks
  }

  return tasks.filter(task => String(task.crate_name ?? '').toLowerCase().includes(normalized))
}
