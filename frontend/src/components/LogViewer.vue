<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import api from '../services/api'

const props = defineProps({
  taskId: [String, Number],
  autoScroll: {
    type: Boolean,
    default: true
  }
})

const activeLog = ref('runner')
const logs = ref({
  runner: '',
  stdout: '',
  stderr: '',
  miri_report: ''
})
const loading = ref({
  runner: false,
  stdout: false,
  stderr: false,
  miri_report: false
})
const fetched = ref({
  runner: false,
  stdout: false,
  stderr: false,
  miri_report: false
})
const logContainer = ref(null)
let refreshInterval = null

const logFiles = [
  { id: 'runner', label: 'runner', icon: '⚙' },
  { id: 'stdout', label: 'stdout', icon: '📄' },
  { id: 'stderr', label: 'stderr', icon: '📄' },
  { id: 'miri_report', label: 'miri_report', icon: '📄' },
]

async function loadLog(logType, isRefresh = false) {
  if (!isRefresh && loading.value[logType]) return

  if (!isRefresh) {
    loading.value[logType] = true
  }

  try {
    const data = await api.getLog(props.taskId, logType, 1000)

    let wasAtBottom = false
    if (isRefresh && logContainer.value) {
      const threshold = 50
      wasAtBottom =
        logContainer.value.scrollHeight -
          logContainer.value.scrollTop -
          logContainer.value.clientHeight <
        threshold
    }

    if (data.lines && Array.isArray(data.lines)) {
      logs.value[logType] = data.lines.join('\n') || 'No content available'
    } else {
      logs.value[logType] = data.content || 'No content available'
    }

    if ((!isRefresh && props.autoScroll) || (isRefresh && wasAtBottom)) {
      scrollToBottom()
    }
  } catch (err) {
    if (err.response?.status === 404) {
      logs.value[logType] = 'No content available'
    } else {
      logs.value[logType] = `Error loading log: ${err.response?.data?.detail || err.message}`
    }
  } finally {
    if (!isRefresh) {
      loading.value[logType] = false
      fetched.value[logType] = true
    }
  }
}

function startAutoRefresh() {
  refreshInterval = setInterval(() => {
    loadLog(activeLog.value, true)
  }, 5000)
}

function stopAutoRefresh() {
  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
}

async function downloadLog() {
  try {
    const blob = await api.downloadLog(props.taskId, activeLog.value)
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const ext = activeLog.value === 'miri_report' ? 'txt' : 'log'
    a.download = `task-${props.taskId}-${activeLog.value}.${ext}`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
  } catch (err) {
    console.error('Failed to download log:', err)
    alert(`Failed to download log: ${err.response?.data?.detail || err.message}`)
  }
}

function scrollToBottom() {
  setTimeout(() => {
    if (logContainer.value) {
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  }, 100)
}

watch(activeLog, (newLog) => {
  if (!fetched.value[newLog]) {
    loadLog(newLog)
  } else if (props.autoScroll) {
    scrollToBottom()
  }
})

onMounted(() => {
  loadLog(activeLog.value)
  startAutoRefresh()
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<template>
  <div class="bento-card">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text-lg font-semibold text-gray-900">Logs</h3>
      <button
        @click="downloadLog"
        class="px-3 py-1.5 text-sm font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
        :title="`Download ${activeLog}`"
      >
        Download {{ activeLog }}
      </button>
    </div>

    <div class="flex" style="min-height: 400px;">
      <!-- Left: file list -->
      <div
        class="flex flex-col flex-shrink-0 border-r border-gray-200"
        style="width: 160px;"
      >
        <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide px-3 py-2">
          Files
        </div>

        <div class="flex flex-col gap-1 px-2 flex-1">
          <button
            v-for="file in logFiles"
            :key="file.id"
            @click="activeLog = file.id"
            :class="[
              'flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-left transition-colors w-full',
              activeLog === file.id
                ? 'bg-blue-50 border border-blue-200 text-blue-700'
                : 'text-gray-600 hover:bg-gray-100'
            ]"
          >
            <span>{{ file.icon }}</span>
            <span class="truncate">{{ file.label }}</span>
          </button>
        </div>

        <div class="px-3 py-2 text-xs text-gray-400 border-t border-gray-100 mt-2">
          ↻ 5s refresh
        </div>
      </div>

      <!-- Right: log content -->
      <div class="flex-1 min-w-0">
        <div
          ref="logContainer"
          class="log-viewer h-full"
          style="max-height: 500px; overflow-y: auto;"
        >
          <div v-if="loading[activeLog]" class="flex justify-center py-8">
            <div class="spinner"></div>
          </div>
          <pre v-else class="text-sm">{{ logs[activeLog] || 'No content available' }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>
