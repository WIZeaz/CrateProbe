<script setup>
import { ref, computed, nextTick, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import PageHeader from '../components/ui/PageHeader.vue'
import TaskSelect from '../components/ui/TaskSelect.vue'

const router = useRouter()
const textarea = ref('')
const loading = ref(false)
const progress = ref({ current: 0, total: 0 })
const logs = ref([])
const logContainer = ref(null)

// Task selector state
const tasks = ref([])
const tasksLoading = ref(true)
const tasksError = ref(null)
const selectedTaskId = ref(null)

async function fetchTasks() {
  tasksLoading.value = true
  tasksError.value = null
  try {
    tasks.value = await api.getTasks()
    if (tasks.value.length > 0) {
      selectedTaskId.value = tasks.value[0].id
    }
  } catch (err) {
    tasksError.value = err.response?.data?.detail || err.message
  } finally {
    tasksLoading.value = false
  }
}

// Parse input text into crate list
function parseInput(text) {
  const lines = text.split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)

  const parsed = []
  const errors = []

  lines.forEach((line, index) => {
    // Try comma-separated format first: crate_name, version
    let parts = line.split(',').map(p => p.trim())

    // If no comma found, try space-separated format: crate_name version
    if (parts.length === 1) {
      // Split by whitespace (handles multiple spaces)
      parts = line.split(/\s+/).filter(p => p.length > 0)
    }

    if (parts.length === 1) {
      // Format: crate_name
      parsed.push({ crate_name: parts[0], version: null })
    } else if (parts.length === 2) {
      // Format: crate_name, version or crate_name version
      parsed.push({ crate_name: parts[0], version: parts[1] })
    } else {
      // Invalid format
      errors.push({ line: index + 1, text: line, reason: 'Invalid format (expected: crate_name or crate_name, version or crate_name version)' })
    }
  })

  return { parsed, errors }
}

// Computed properties
const inputStats = computed(() => {
  const lines = textarea.value.split('\n').filter(line => line.trim().length > 0)
  return {
    lines: lines.length,
    hasContent: lines.length > 0
  }
})

const validationResult = computed(() => {
  if (!textarea.value.trim()) {
    return { valid: false, errors: [] }
  }
  const { parsed, errors } = parseInput(textarea.value)
  return { valid: errors.length === 0, errors, parsed }
})

const progressPercentage = computed(() => {
  if (progress.value.total === 0) return 0
  return Math.round((progress.value.current / progress.value.total) * 100)
})

const failedCount = computed(() => logs.value.filter(entry => !entry.ok).length)

// Auto-scroll the log window to the newest entry.
watch(
  () => logs.value.length,
  async () => {
    await nextTick()
    if (logContainer.value) {
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  }
)

// Batch create jobs
async function createBatchJobs() {
  if (!validationResult.value.valid || !selectedTaskId.value) {
    return
  }

  const { parsed } = validationResult.value

  loading.value = true
  progress.value = { current: 0, total: parsed.length }
  logs.value = []

  const CONCURRENCY = 16

  async function createOne({ crate_name, version }) {
    const label = `${crate_name}${version ? `@${version}` : ''}`
    try {
      const result = await api.createJob(crate_name, version, selectedTaskId.value)
      logs.value.push({ ok: true, text: `[OK] ${label} -> job #${result.job_id}` })
    } catch (err) {
      const message = err.response?.data?.detail || err.message
      logs.value.push({ ok: false, text: `[FAIL] ${label}: ${message}` })
    } finally {
      progress.value.current += 1
    }
  }

  for (let i = 0; i < parsed.length; i += CONCURRENCY) {
    const batch = parsed.slice(i, i + CONCURRENCY)
    await Promise.all(batch.map(createOne))
  }

  loading.value = false
  // Stay on the page so the user can read the creation log.
}

onMounted(() => {
  fetchTasks()
})
</script>

<template>
  <div>
    <PageHeader
      title="Batch create jobs"
      description="Queue many crate versions against one task."
    />

    <div class="grid grid-cols-1 gap-5 lg:grid-cols-5">
      <section class="instrument-panel lg:col-span-3">
        <div class="panel-header">
          <h2 class="panel-title">Batch input</h2>
        </div>

        <div class="mt-5 max-w-2xl space-y-5">
          <TaskSelect
            v-model="selectedTaskId"
            label="Task *"
            :tasks="tasks"
            :loading="tasksLoading"
            :error="tasksError"
            :disabled="loading"
            @retry="fetchTasks"
          />

          <div>
            <div class="mb-1.5 flex items-baseline justify-between gap-3">
              <label for="crate_list" class="ui-label mb-0">Crate list</label>
              <span class="font-mono text-[11px] text-muted">
                {{ inputStats.lines }}
                {{ inputStats.lines === 1 ? 'line' : 'lines' }} entered
              </span>
            </div>
            <textarea
              id="crate_list"
              v-model="textarea"
              rows="10"
              placeholder="serde&#10;tokio, 1.0.0&#10;regex&#10;actix-web 4.4.0"
              class="ui-textarea"
              :disabled="loading"
            ></textarea>
            <p class="mt-1 text-[11px] text-muted">
              One crate per line. Empty lines are ignored.
            </p>
          </div>

          <div
            v-if="
              inputStats.hasContent &&
              !validationResult.valid &&
              validationResult.errors.length > 0
            "
            class="notice notice--error"
            role="alert"
          >
            <div class="min-w-0 flex-1">
              <p class="notice__title">Format errors found.</p>
              <ul class="mt-1 space-y-0.5 text-[11px] text-muted">
                <li
                  v-for="error in validationResult.errors"
                  :key="error.line"
                >
                  Line {{ error.line }}: "{{ error.text }}" ({{ error.reason }})
                </li>
              </ul>
            </div>
          </div>

          <div v-if="progress.total > 0">
            <div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
              <span class="text-[12px] font-medium text-ink">
                {{ loading ? 'Creating jobs...' : 'Done' }}
              </span>
              <span class="flex items-center gap-3 font-mono text-[11px] text-muted">
                <span>{{ progress.current }} / {{ progress.total }}</span>
                <span
                  v-if="failedCount > 0"
                  class="inline-flex items-center gap-1.5"
                >
                  <span class="status-dot tone-alarm" aria-hidden="true"></span>
                  {{ failedCount }} failed
                </span>
              </span>
            </div>
            <div
              class="ui-progress"
              :class="
                loading
                  ? ''
                  : failedCount > 0
                    ? 'ui-progress--warn'
                    : 'ui-progress--good'
              "
            >
              <div
                class="ui-progress__fill"
                :style="{ width: `${progressPercentage}%` }"
              ></div>
            </div>
            <div
              ref="logContainer"
              class="code-block mt-3 max-h-64 overflow-y-auto"
            >
              <div
                v-for="(entry, index) in logs"
                :key="index"
                :class="entry.ok ? 'text-[#7bd88f]' : 'text-[#ff7b72]'"
              >
                {{ entry.text }}
              </div>
              <div v-if="logs.length === 0" class="text-[#8a9698]">
                Waiting...
              </div>
            </div>
          </div>

          <div class="flex items-center justify-end gap-2 border-t border-rule pt-4">
            <button
              type="button"
              class="btn btn--ghost"
              :disabled="loading"
              @click="router.push('/jobs')"
            >
              Cancel
            </button>
            <button
              type="button"
              class="btn btn--primary"
              :disabled="
                loading ||
                !validationResult.valid ||
                !inputStats.hasContent ||
                !selectedTaskId
              "
              @click="createBatchJobs"
            >
              Create {{ validationResult.parsed?.length || 0 }}
              {{ validationResult.parsed?.length === 1 ? 'job' : 'jobs' }}
            </button>
          </div>
        </div>
      </section>

      <aside class="lg:col-span-2">
        <section class="instrument-panel lg:sticky lg:top-20">
          <div class="panel-header">
            <h2 class="panel-title">Input format</h2>
          </div>

          <ul class="mt-4 space-y-2 text-[12px] leading-relaxed text-muted">
            <li>
              <code class="inline-code">crate_name</code> uses the latest
              version.
            </li>
            <li>
              <code class="inline-code">crate_name, version</code> pins a
              version.
            </li>
            <li>
              <code class="inline-code">crate_name version</code> also pins a
              version.
            </li>
            <li>Empty lines are ignored; large batches are submitted in chunks.</li>
          </ul>

          <p class="panel-kicker mt-5">Example</p>
          <pre class="code-block mt-2">serde
tokio, 1.0.0
regex
actix-web 4.4.0
rayon, 1.5.0</pre>
        </section>
      </aside>
    </div>
  </div>
</template>
