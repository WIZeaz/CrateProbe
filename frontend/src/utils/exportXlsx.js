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

export function exportJobsToXlsx(jobs) {
  if (!jobs || jobs.length === 0) {
    throw new Error('No jobs to export')
  }

  const data = jobs.map((job) => ({
    ID: job.id,
    Crate: job.crate_name,
    Version: job.version,
    Task: job.task_name || '',
    Status: job.status,
    'Runtime (s)': getRuntimeSeconds(job.started_at, job.finished_at),
    Runner: job.runner_id || '',
    'Created At': formatDate(job.created_at),
    'Started At': formatDate(job.started_at),
    'Finished At': formatDate(job.finished_at),
  }))

  const ws = XLSX.utils.json_to_sheet(data)

  const colWidths = [
    { wch: 8 },
    { wch: 24 },
    { wch: 12 },
    { wch: 20 },
    { wch: 12 },
    { wch: 12 },
    { wch: 16 },
    { wch: 20 },
    { wch: 20 },
    { wch: 20 },
  ]
  ws['!cols'] = colWidths

  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, 'Jobs')

  const now = new Date()
  const timestamp = now.toISOString().slice(0, 19).replace(/[-T:]/g, '')
  const filename = `jobs_${timestamp.slice(0, 8)}_${timestamp.slice(8)}.xlsx`

  XLSX.writeFile(wb, filename)
}
