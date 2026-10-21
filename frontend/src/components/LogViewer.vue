<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { AnsiUp } from 'ansi_up'
import api from '../services/api'

// Create ansi_up instance
const ansiUp = new AnsiUp()

const props = defineProps({
  jobId: [String, Number],
  autoScroll: {
    type: Boolean,
    default: true
  },
  // Relative paths from the job's task spec (task_spec.watch).
  watchFiles: {
    type: Array,
    default: () => []
  }
})

const logItems = [
  { id: 'log:runner', kind: 'log', label: 'runner', path: 'runner.log' },
  { id: 'log:stdout', kind: 'log', label: 'stdout', path: 'stdout.log' },
  { id: 'log:stderr', kind: 'log', label: 'stderr', path: 'stderr.log' },
]

const watchItems = computed(() =>
  props.watchFiles.map(path => ({
    id: `watch:${path}`,
    kind: 'watch',
    label: path,
    path,
  }))
)

const allItems = computed(() => [...watchItems.value, ...logItems])

const activeId = ref('log:runner')

const activeItem = computed(
  () => allItems.value.find(item => item.id === activeId.value) || logItems[0]
)

const contents = ref({})
const html = ref({})
const loading = ref({})
const fetched = ref({})

const logContainer = ref(null)
let refreshInterval = null

function renderHtml(text) {
  if (!text || text === 'No content available') {
    return '<span class="log-empty">No content available</span>'
  }
  return ansiUp.ansi_to_html(text)
}

async function loadItem(itemId, isRefresh = false) {
  const item = allItems.value.find(entry => entry.id === itemId)
  if (!item) return
  if (!isRefresh && loading.value[itemId]) return

  if (!isRefresh) {
    loading.value[itemId] = true
  }

  try {
    const data = await api.getJobFile(props.jobId, item.path, { lines: 1000 })

    let wasAtBottom = false
    if (isRefresh && logContainer.value && itemId === activeId.value) {
      const threshold = 50
      wasAtBottom =
        logContainer.value.scrollHeight -
          logContainer.value.scrollTop -
          logContainer.value.clientHeight <
        threshold
    }

    const content =
      data.lines && Array.isArray(data.lines) && data.lines.length > 0
        ? data.lines.join('\n')
        : 'No content available'
    contents.value[itemId] = content
    html.value[itemId] = renderHtml(content)

    if ((!isRefresh && props.autoScroll) || (isRefresh && wasAtBottom)) {
      scrollToBottom()
    }
  } catch (err) {
    const content =
      err.response?.status === 404
        ? 'No content available'
        : `Error loading file: ${err.response?.data?.detail || err.message}`
    contents.value[itemId] = content
    html.value[itemId] = renderHtml(content)
  } finally {
    if (!isRefresh) {
      loading.value[itemId] = false
      fetched.value[itemId] = true
    }
  }
}

function startAutoRefresh() {
  refreshInterval = setInterval(() => {
    // Watched files refresh on demand; logs follow the run.
    if (activeItem.value.kind === 'log') {
      loadItem(activeId.value, true)
    }
  }, 5000)
}

function stopAutoRefresh() {
  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
}

const refreshing = ref(false)

async function refreshActive() {
  refreshing.value = true
  try {
    await loadItem(activeId.value, true)
  } finally {
    refreshing.value = false
  }
}

async function downloadActive() {
  const item = activeItem.value
  try {
    const text = await api.getJobFile(props.jobId, item.path, { raw: true })
    const blob = new Blob([text], { type: 'text/plain' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `job-${props.jobId}-${item.path.replace(/\//g, '_')}`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
  } catch (err) {
    console.error('Failed to download file:', err)
    alert(`Failed to download file: ${err.response?.data?.detail || err.message}`)
  }
}

function scrollToBottom() {
  setTimeout(() => {
    if (logContainer.value) {
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  }, 100)
}

function resetForNewAttempt() {
  contents.value = {}
  html.value = {}
  fetched.value = {}
  loadItem(activeId.value)
}

defineExpose({
  resetForNewAttempt
})

watch(activeId, (newId) => {
  if (!fetched.value[newId]) {
    loadItem(newId)
  } else if (props.autoScroll) {
    scrollToBottom()
  }
})

onMounted(() => {
  loadItem(activeId.value)
  startAutoRefresh()
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<template>
  <section class="instrument-panel">
    <div class="panel-header">
      <h3 class="panel-title">Watches &amp; logs</h3>
      <div class="flex items-center gap-2">
        <button
          v-if="activeItem.kind === 'watch'"
          type="button"
          class="btn btn--ghost btn--sm"
          :disabled="refreshing"
          @click="refreshActive"
        >
          {{ refreshing ? 'Refreshing...' : 'Refresh' }}
        </button>
        <button
          type="button"
          class="btn btn--ghost btn--sm"
          :title="`Download ${activeItem.label}`"
          @click="downloadActive"
        >
          Download
        </button>
      </div>
    </div>

    <div class="mt-4 flex flex-col gap-4 lg:flex-row">
      <aside class="lg:w-[220px] lg:shrink-0 lg:border-r lg:border-rule lg:pr-4">
        <p class="panel-kicker">Watched files</p>
        <div class="mt-1 flex flex-wrap gap-1 lg:flex-col">
          <button
            v-for="item in watchItems"
            :key="item.id"
            type="button"
            :class="[
              'log-tab w-auto lg:w-full',
              { 'log-tab--active': activeId === item.id },
            ]"
            @click="activeId = item.id"
          >
            <span class="flex h-4 w-4 shrink-0 items-center justify-center">
              <svg
                class="h-4 w-4"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                aria-hidden="true"
              >
                <path d="M1.5 8s2.4-4 6.5-4 6.5 4 6.5 4-2.4 4-6.5 4S1.5 8 1.5 8Z" />
                <circle cx="8" cy="8" r="1.75" />
              </svg>
            </span>
            <span class="truncate font-mono text-[11px]">{{ item.label }}</span>
          </button>
          <p v-if="watchItems.length === 0" class="px-2 py-1 text-[11px] text-muted">
            No watched files
          </p>
        </div>

        <p class="panel-kicker mt-4">Logs</p>
        <div class="mt-1 flex flex-wrap gap-1 lg:flex-col">
          <button
            v-for="item in logItems"
            :key="item.id"
            type="button"
            :class="[
              'log-tab w-auto lg:w-full',
              { 'log-tab--active': activeId === item.id },
            ]"
            @click="activeId = item.id"
          >
            <span class="flex h-4 w-4 shrink-0 items-center justify-center">
              <svg
                v-if="item.id === 'log:runner'"
                class="h-4 w-4"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                aria-hidden="true"
              >
                <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5" />
                <path
                  d="m5 6 2 2-2 2M9 10h2.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              <svg
                v-else
                class="h-4 w-4"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                aria-hidden="true"
              >
                <path d="M9.5 1.75H4.5a1 1 0 0 0-1 1v10.5a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V4.75l-3-3Z" />
                <path
                  d="M9.25 1.75v3.25h3.25M5.75 8h4.5M5.75 10.5h4.5"
                  stroke-linecap="round"
                />
              </svg>
            </span>
            <span class="truncate text-[11px]">{{ item.label }}</span>
          </button>
        </div>

        <p class="mt-3 border-t border-rule pt-2 font-mono text-[10px] text-muted">
          Logs refresh every 5s
        </p>
      </aside>

      <div class="min-w-0 flex-1">
        <div
          ref="logContainer"
          class="log-viewer h-full max-h-[500px] overflow-y-auto"
        >
          <div v-if="loading[activeId]" class="flex justify-center py-8">
            <div class="spinner"></div>
          </div>
          <pre
            v-else
            class="ansi-color text-[11px] leading-relaxed"
            v-html="html[activeId]"
          ></pre>
        </div>
      </div>
    </div>
  </section>
</template>
