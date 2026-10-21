<script setup>
import { computed, ref, onMounted } from 'vue'
import api from '../services/api'
import PageHeader from '../components/ui/PageHeader.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import TaskFileTree from '../components/tasks/TaskFileTree.vue'
import { formatBytes, formatDateTime } from '../utils/format.js'
import { highlightToml } from '../utils/highlightToml.js'
import { buildFileTree } from '../utils/taskFiles.js'

const tasks = ref([])
const loading = ref(true)
const error = ref(null)

// Upload state
const uploadFile = ref(null)
const uploadLoading = ref(false)
const uploadError = ref(null)
const uploadResult = ref(null)

// Inline update state: a single hidden file input reused for whichever
// row the user clicked "Update" on.
const pendingUpdateId = ref(null)
const updatingId = ref(null)
const updateLoading = ref(false)
const updateError = ref(null)
const updateFileInput = ref(null)

// Delete state: track the row being deleted so only its button disables.
const deletingId = ref(null)
const deleteError = ref(null)

// Task detail dialog state.
const detailDialog = ref(null)
const detailTask = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const tomlContent = ref('')
const taskFiles = ref({ entries: [], total_size: 0 })
const highlightedToml = computed(() => highlightToml(tomlContent.value))
const fileTree = computed(() => buildFileTree(taskFiles.value.entries || []))
const treeStats = computed(() => countTree(fileTree.value))
const fileCount = computed(() => treeStats.value.files)
const folderCount = computed(() => treeStats.value.folders)
const taskTomlSize = computed(() => {
  const entry = (taskFiles.value.entries || []).find(
    item => item.path === 'task.toml' && !item.is_dir
  )
  return entry ? formatBytes(entry.size) : '—'
})
const detailSpec = computed(() => detailTask.value?.spec || {})
const detailLimits = computed(() => detailSpec.value.limits || {})
const detailWatch = computed(() =>
  Array.isArray(detailSpec.value.watch) ? detailSpec.value.watch : []
)
const detailArtifacts = computed(() =>
  Array.isArray(detailSpec.value.artifacts) ? detailSpec.value.artifacts : []
)
const detailEnvCount = computed(() => {
  const env = detailSpec.value.env
  return env && typeof env === 'object' ? Object.keys(env).length : 0
})
const detailLimitsText = computed(() => {
  const limits = detailLimits.value
  const parts = []
  if (limits.cpu) {
    parts.push(`CPU ${limits.cpu}`)
  }
  if (limits.mem) {
    parts.push(`memory ${limits.mem}`)
  }
  if (limits.timeout_seconds) {
    parts.push(`timeout ${limits.timeout_seconds}s`)
  }
  return parts.length ? parts.join(', ') : '—'
})
let detailRequestId = 0

function countTree(nodes) {
  const stats = { files: 0, folders: 0 }
  for (const node of nodes) {
    if (node.isDir) {
      stats.folders += 1
      const childStats = countTree(node.children)
      stats.files += childStats.files
      stats.folders += childStats.folders
    } else {
      stats.files += 1
    }
  }
  return stats
}

async function fetchTasks() {
  try {
    tasks.value = await api.getTasks()
    loading.value = false
  } catch (err) {
    error.value = extractError(err)
    loading.value = false
  }
}

async function openDetail(task) {
  detailTask.value = task
  detailError.value = ''
  tomlContent.value = ''
  taskFiles.value = { entries: [], total_size: 0 }
  detailLoading.value = true

  if (detailDialog.value && !detailDialog.value.open) {
    detailDialog.value.showModal()
  }

  const requestId = ++detailRequestId
  try {
    const [toml, files] = await Promise.all([
      api.getTaskToml(task.id),
      api.getTaskFiles(task.id),
    ])
    if (requestId === detailRequestId) {
      tomlContent.value = toml
      taskFiles.value = files
    }
  } catch (err) {
    if (requestId === detailRequestId) {
      detailError.value = extractError(err)
    }
  } finally {
    if (requestId === detailRequestId) {
      detailLoading.value = false
    }
  }
}

function closeDetail() {
  detailDialog.value?.close()
}

function handleDetailClosed() {
  detailRequestId += 1
  detailTask.value = null
  tomlContent.value = ''
  taskFiles.value = { entries: [], total_size: 0 }
  detailError.value = ''
  detailLoading.value = false
}

function retryDetail() {
  if (detailTask.value) {
    openDetail(detailTask.value)
  }
}

function extractError(err) {
  const detail = err.response?.data?.detail
  // FastAPI 422 responses carry detail as an array of {msg, ...} objects.
  if (Array.isArray(detail)) {
    return detail.map(d => d.msg || JSON.stringify(d)).join('; ')
  }
  return detail || err.message
}

function onUploadFileChange(event) {
  uploadFile.value = event.target.files?.[0] || null
  uploadError.value = null
}

async function handleUpload() {
  if (!uploadFile.value) {
    uploadError.value = 'Please select a task zip file first.'
    return
  }

  uploadLoading.value = true
  uploadError.value = null
  uploadResult.value = null
  try {
    const result = await api.uploadTask(uploadFile.value)
    uploadResult.value = result
    uploadFile.value = null
    // Reset the file input so the same file can be re-selected.
    const input = document.getElementById('task-upload-file')
    if (input) input.value = ''
    fetchTasks()
  } catch (err) {
    uploadError.value = extractError(err)
  } finally {
    uploadLoading.value = false
  }
}

function startUpdate(taskId) {
  pendingUpdateId.value = taskId
  updateError.value = null
  updateFileInput.value?.click()
}

function cancelPendingUpdate() {
  pendingUpdateId.value = null
}

async function onUpdateFileChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  const taskId = pendingUpdateId.value
  pendingUpdateId.value = null
  if (!file || taskId === null) return

  updatingId.value = taskId
  updateLoading.value = true
  updateError.value = null
  try {
    await api.updateTask(taskId, file)
    fetchTasks()
  } catch (err) {
    updateError.value = extractError(err)
  } finally {
    updateLoading.value = false
    updatingId.value = null
  }
}

async function handleDelete(task) {
  if (!confirm(`Delete task "${task.name}" (#${task.id})? This cannot be undone.`)) return

  deletingId.value = task.id
  deleteError.value = null
  try {
    await api.deleteTask(task.id)
    fetchTasks()
  } catch (err) {
    deleteError.value = extractError(err)
  } finally {
    deletingId.value = null
  }
}

onMounted(() => {
  fetchTasks()
})
</script>

<template>
  <div>
    <PageHeader title="Tasks">
      <template #description>
        Reusable task definitions. A task is a zip with a
        <code class="inline-code">task.toml</code> at its root.
      </template>
    </PageHeader>

    <div
      class="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]"
    >
      <div class="min-w-0 space-y-5">
        <section class="instrument-panel">
          <div class="panel-header">
            <h2 class="panel-title">Upload task definition</h2>
            <a
              href="/api/tasks/example"
              download="example-task.zip"
              class="btn btn--ghost btn--sm"
            >
              Download example
            </a>
          </div>

          <div class="mt-4 flex flex-wrap items-center gap-3">
            <input
              id="task-upload-file"
              type="file"
              accept=".zip"
              class="ui-file min-w-0 flex-1"
              @change="onUploadFileChange"
            />
            <button
              class="btn btn--primary"
              :disabled="uploadLoading || !uploadFile"
              @click="handleUpload"
            >
              {{ uploadLoading ? 'Uploading...' : 'Upload' }}
            </button>
          </div>

          <div v-if="uploadError" class="notice notice--error mt-3" role="alert">
            Upload failed: {{ uploadError }}
          </div>

          <div
            v-if="uploadResult"
            class="notice notice--success mt-3"
            role="status"
          >
            <div class="min-w-0 flex-1">
              <p class="notice__title">
                Task "{{ uploadResult.name }}" uploaded successfully.
                <span v-if="uploadResult.existed"> It already existed.</span>
              </p>
              <p class="notice__detail">
                Image: {{ uploadResult.spec?.image }}
              </p>
              <p class="notice__detail break-all">
                Command: {{ uploadResult.spec?.cmd }}
              </p>
            </div>
          </div>
        </section>

        <section class="instrument-panel">
          <div class="panel-header">
            <h2 class="panel-title">Task definitions</h2>
            <span
              v-if="!loading && !error"
              class="font-mono text-[11px] text-muted"
            >
              {{ tasks.length }} {{ tasks.length === 1 ? 'task' : 'tasks' }}
            </span>
          </div>

          <div v-if="updateError" class="notice notice--error mt-4" role="alert">
            Update failed: {{ updateError }}
          </div>
          <div v-if="deleteError" class="notice notice--error mt-4" role="alert">
            Delete failed: {{ deleteError }}
          </div>

          <input
            ref="updateFileInput"
            type="file"
            accept=".zip"
            class="hidden"
            @change="onUpdateFileChange"
            @cancel="cancelPendingUpdate"
          />

          <div
            v-if="loading"
            class="mt-4 space-y-4"
            aria-busy="true"
            aria-label="Loading task definitions"
          >
            <div v-for="index in 3" :key="index" class="space-y-2">
              <div class="skeleton h-4 w-1/3"></div>
              <div class="skeleton h-3 w-2/3"></div>
              <div class="skeleton h-3 w-1/2"></div>
            </div>
          </div>

          <div
            v-else-if="error"
            class="notice notice--error mt-4"
            role="alert"
          >
            Failed to load tasks: {{ error }}
          </div>

          <EmptyState
            v-else-if="tasks.length === 0"
            title="No task definitions yet"
            description="Upload a zip above, or download the example task to see the expected structure."
          />

          <div v-else class="mt-1">
            <article
              v-for="task in tasks"
              :key="task.id"
              class="border-b border-rule py-4 first:pt-2 last:border-0 last:pb-0"
            >
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-baseline gap-2">
                    <h3 class="break-words text-[14px] font-semibold text-ink">
                      {{ task.name }}
                    </h3>
                    <span class="ui-tag">#{{ task.id }}</span>
                  </div>
                  <p class="mt-1 break-words text-[12px] leading-relaxed text-muted">
                    {{ task.desc || 'No description' }}
                  </p>
                </div>

                <div class="flex flex-wrap items-center gap-2">
                  <a
                    :href="api.taskZipUrl(task.id)"
                    :download="`task-${task.id}.zip`"
                    class="btn btn--ghost btn--sm"
                  >
                    Download
                  </a>
                  <button
                    class="btn btn--ghost btn--sm"
                    @click="openDetail(task)"
                  >
                    Detail
                  </button>
                  <button
                    class="btn btn--ghost btn--sm"
                    :disabled="updateLoading"
                    @click="startUpdate(task.id)"
                  >
                    {{
                      updateLoading && updatingId === task.id
                        ? 'Updating...'
                        : 'Update'
                    }}
                  </button>
                  <button
                    class="btn btn--danger-ghost btn--sm"
                    :disabled="deletingId === task.id"
                    @click="handleDelete(task)"
                  >
                    {{ deletingId === task.id ? 'Deleting...' : 'Delete' }}
                  </button>
                </div>
              </div>

              <dl class="mt-3 space-y-1 text-[11px]">
                <div class="flex gap-3">
                  <dt class="w-16 shrink-0 text-muted">SHA256</dt>
                  <dd class="break-all font-mono text-ink">{{ task.sha256 }}</dd>
                </div>
                <div class="grid grid-cols-1 gap-1 sm:grid-cols-2 sm:gap-x-6">
                  <div class="flex gap-3">
                    <dt class="w-16 shrink-0 text-muted">Created</dt>
                    <dd class="text-ink">{{ formatDateTime(task.created_at) }}</dd>
                  </div>
                  <div class="flex gap-3">
                    <dt class="w-16 shrink-0 text-muted">Updated</dt>
                    <dd class="text-ink">
                      {{ task.updated_at ? formatDateTime(task.updated_at) : 'Never' }}
                    </dd>
                  </div>
                </div>
              </dl>

            </article>
          </div>
        </section>
      </div>

      <aside class="lg:sticky lg:top-20">
        <section
          class="instrument-panel lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto"
        >
          <div class="panel-header">
            <h2 class="panel-title">Task rules</h2>
          </div>

          <h3 class="mt-4 text-[12px] font-semibold text-ink">Zip layout</h3>
          <ul
            class="mt-1.5 list-inside list-disc space-y-1.5 text-[11px] leading-relaxed text-muted"
          >
            <li>
              <code class="inline-code">task.toml</code> must sit at the zip
              root.
            </li>
            <li>
              Every other entry is copied into the job workspace; the zip wins
              name conflicts.
            </li>
            <li>
              Absolute paths and <code class="inline-code">..</code> entries are
              rejected.
            </li>
            <li>
              sha256 of the zip is the task fingerprint; re-uploading identical
              content is a no-op.
            </li>
          </ul>

          <h3 class="mt-4 text-[12px] font-semibold text-ink">
            task.toml fields
          </h3>
          <ul
            class="mt-1.5 list-inside list-disc space-y-1.5 text-[11px] leading-relaxed text-muted"
          >
            <li>
              <code class="inline-code">name</code>,
              <code class="inline-code">image</code>,
              <code class="inline-code">cmd</code>,
              <code class="inline-code">workspace</code> are required.
            </li>
            <li>
              <code class="inline-code">desc</code> is an optional description.
            </li>
            <li>
              <code class="inline-code">artifacts</code> lists paths collected
              when the job ends.
            </li>
            <li>
              <code class="inline-code">watch</code> lists paths viewable live
              during the run.
            </li>
            <li>
              <code class="inline-code">[limit]</code> cpu, mem and timeout are
              optional; missing means unlimited.
            </li>
            <li>
              <code class="inline-code">[env]</code> adds container environment
              variables.
            </li>
          </ul>

          <h3 class="mt-4 text-[12px] font-semibold text-ink">Execution</h3>
          <ul
            class="mt-1.5 list-inside list-disc space-y-1.5 text-[11px] leading-relaxed text-muted"
          >
            <li>
              The crate source is downloaded into a per-job directory, then
              mounted at <code class="inline-code">workspace</code>.
            </li>
            <li>
              <code class="inline-code">cmd</code> runs via
              <code class="inline-code">sh -c</code> with
              <code class="inline-code">workspace</code> as the working
              directory.
            </li>
            <li>
              stdout/stderr are redirected to
              <code class="inline-code">stdout.log</code> /
              <code class="inline-code">stderr.log</code>; list them in
              <code class="inline-code">watch</code> or
              <code class="inline-code">artifacts</code> to inspect them.
            </li>
            <li>
              artifacts/watch paths are relative to
              <code class="inline-code">workspace</code>.
            </li>
          </ul>
        </section>
      </aside>
    </div>

    <dialog
      ref="detailDialog"
      class="task-dialog"
      aria-labelledby="task-detail-title"
      @close="handleDetailClosed"
      @click.self="closeDetail"
    >
      <div class="task-dialog__panel">
        <div class="panel-header">
          <div class="min-w-0">
            <h2 id="task-detail-title" class="panel-title">Task detail</h2>
            <p class="mt-1 truncate text-[11px] text-muted">
              {{ detailTask?.name }}
              <span class="font-mono">#{{ detailTask?.id }}</span>
            </p>
          </div>
          <button
            type="button"
            class="btn btn--ghost btn--sm"
            @click="closeDetail"
          >
            Close
          </button>
        </div>

        <div class="task-dialog__body">
          <dl
            class="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-4"
          >
            <div>
              <dt class="text-[10px] text-muted">Name</dt>
              <dd class="mt-1 truncate text-[12px] font-medium text-ink">
                {{ detailTask?.name || '—' }}
              </dd>
            </div>
            <div>
              <dt class="text-[10px] text-muted">Task ID</dt>
              <dd class="mt-1 font-mono text-[12px] text-ink">
                #{{ detailTask?.id ?? '—' }}
              </dd>
            </div>
            <div class="sm:col-span-2 xl:col-span-2">
              <dt class="text-[10px] text-muted">SHA256</dt>
              <dd
                class="mt-1 break-all font-mono text-[10px] leading-relaxed text-ink"
              >
                {{ detailTask?.sha256 || '—' }}
              </dd>
            </div>
            <div>
              <dt class="text-[10px] text-muted">Created</dt>
              <dd class="mt-1 text-[11px] text-ink">
                {{ formatDateTime(detailTask?.created_at) }}
              </dd>
            </div>
            <div>
              <dt class="text-[10px] text-muted">Updated</dt>
              <dd class="mt-1 text-[11px] text-ink">
                {{
                  detailTask?.updated_at
                    ? formatDateTime(detailTask.updated_at)
                    : 'Never'
                }}
              </dd>
            </div>
            <div class="sm:col-span-2 xl:col-span-2">
              <dt class="text-[10px] text-muted">Description</dt>
              <dd class="mt-1 break-words text-[11px] leading-relaxed text-ink">
                {{ detailTask?.desc || 'No description' }}
              </dd>
            </div>
          </dl>

          <dl
            class="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 border-t border-rule pt-4 sm:grid-cols-2 xl:grid-cols-4"
          >
            <div>
              <dt class="text-[10px] text-muted">Image</dt>
              <dd class="mt-1 break-all font-mono text-[11px] text-ink">
                {{ detailSpec.image || '—' }}
              </dd>
            </div>
            <div>
              <dt class="text-[10px] text-muted">Command</dt>
              <dd class="mt-1 break-all font-mono text-[11px] text-ink">
                {{ detailSpec.cmd || '—' }}
              </dd>
            </div>
            <div>
              <dt class="text-[10px] text-muted">Workspace</dt>
              <dd class="mt-1 break-all font-mono text-[11px] text-ink">
                {{ detailSpec.workspace || '—' }}
              </dd>
            </div>
            <div>
              <dt class="text-[10px] text-muted">Limits</dt>
              <dd class="mt-1 text-[11px] text-ink">
                {{ detailLimitsText }}
              </dd>
            </div>
          </dl>

          <div
            class="mt-4 grid grid-cols-1 gap-3 border-t border-rule pt-4 sm:grid-cols-3"
          >
            <div>
              <p class="text-[10px] text-muted">Watch</p>
              <div class="mt-1 flex flex-wrap gap-1">
                <span
                  v-for="path in detailWatch"
                  :key="path"
                  class="ui-tag"
                >
                  {{ path }}
                </span>
                <span v-if="!detailWatch.length" class="text-[11px] text-muted">
                  —
                </span>
              </div>
            </div>
            <div>
              <p class="text-[10px] text-muted">Artifacts</p>
              <div class="mt-1 flex flex-wrap gap-1">
                <span
                  v-for="path in detailArtifacts"
                  :key="path"
                  class="ui-tag"
                >
                  {{ path }}
                </span>
                <span
                  v-if="!detailArtifacts.length"
                  class="text-[11px] text-muted"
                >
                  —
                </span>
              </div>
            </div>
            <div>
              <p class="text-[10px] text-muted">Environment</p>
              <p class="mt-1 text-[11px] text-ink">
                {{ detailEnvCount ? `${detailEnvCount} variables` : '—' }}
              </p>
            </div>
          </div>

          <div
            v-if="detailLoading"
            class="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]"
            aria-busy="true"
            aria-label="Loading task detail"
          >
            <div v-for="index in 2" :key="index" class="detail-subpanel">
              <div class="skeleton h-4 w-1/3"></div>
              <div class="skeleton mt-3 h-40 w-full"></div>
            </div>
          </div>

          <div
            v-else-if="detailError"
            class="notice notice--error mt-5"
            role="alert"
          >
            <div class="min-w-0 flex-1">
              <p class="notice__title">Couldn't load task detail.</p>
              <p class="notice__detail">{{ detailError }}</p>
            </div>
            <button
              type="button"
              class="btn btn--ghost btn--sm"
              @click="retryDetail"
            >
              Try again
            </button>
          </div>

          <div
            v-else
            class="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]"
          >
            <section class="detail-subpanel">
              <div class="flex items-baseline justify-between gap-3">
                <h3 class="text-[12px] font-semibold text-ink">Files</h3>
                <span class="font-mono text-[10px] text-muted">
                  {{ fileCount }} files, {{ folderCount }} folders
                </span>
              </div>
              <p class="mt-0.5 font-mono text-[10px] text-muted">
                {{ formatBytes(taskFiles.total_size) }}
              </p>
              <div class="mt-2 max-h-[40vh] overflow-y-auto pr-1">
                <TaskFileTree :nodes="fileTree" />
              </div>
            </section>

            <section class="detail-subpanel">
              <div class="flex items-baseline justify-between gap-3">
                <h3 class="text-[12px] font-semibold text-ink">task.toml</h3>
                <span class="font-mono text-[10px] text-muted">
                  {{ taskTomlSize }}
                </span>
              </div>
              <pre class="code-block toml-code mt-2"><code v-html="highlightedToml"></code></pre>
            </section>
          </div>
        </div>
      </div>
    </dialog>
  </div>
</template>
