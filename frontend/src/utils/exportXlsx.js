import * as XLSX from 'xlsx'

function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return ''
  return d.toISOString().replace('T', ' ').slice(0, 19)
}

function getRuntimeSeconds(startStr, endStr) {
  if (!startStr) return ''
  const start = new Date(startStr)
  const end = endStr ? new Date(endStr) : new Date()
  const seconds = Math.floor((end - start) / 1000)
  return seconds
}

export function exportTasksToXlsx(tasks) {
  if (!tasks || tasks.length === 0) {
    throw new Error('No tasks to export')
  }

  const data = tasks.map((task) => ({
    ID: task.id,
    Crate: task.crate_name,
    Version: task.version,
    Status: task.status,
    Cases: task.case_count,
    'POCs': task.poc_count,
    'Compile Failed': task.compile_failed ?? '',
    'Runtime (s)': getRuntimeSeconds(task.started_at, task.finished_at),
    Runner: task.runner_id || '',
    'Created At': formatDate(task.created_at),
    'Started At': formatDate(task.started_at),
    'Finished At': formatDate(task.finished_at),
  }))

  const ws = XLSX.utils.json_to_sheet(data)

  const colWidths = [
    { wch: 8 },
    { wch: 24 },
    { wch: 12 },
    { wch: 12 },
    { wch: 8 },
    { wch: 8 },
    { wch: 16 },
    { wch: 14 },
    { wch: 20 },
    { wch: 20 },
    { wch: 20 },
    { wch: 20 },
  ]
  ws['!cols'] = colWidths

  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, 'Tasks')

  const now = new Date()
  const timestamp = now.toISOString().slice(0, 19).replace(/[-T:]/g, '')
  const filename = `tasks_${timestamp.slice(0, 8)}_${timestamp.slice(8)}.xlsx`

  XLSX.writeFile(wb, filename)
}
