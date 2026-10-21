<script setup>
import { ref, onMounted } from 'vue'
import api from '../services/api'

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
const updatingId = ref(null)
const updateLoading = ref(false)
const updateError = ref(null)
const updateFileInput = ref(null)

// Delete state: track the row being deleted so only its button disables.
const deletingId = ref(null)
const deleteError = ref(null)

// Inline task.toml viewer state.
const expandedTomlId = ref(null)
const tomlContents = ref({})
const tomlErrors = ref({})
const tomlLoading = ref(null)

async function fetchTasks() {
  // Any list refresh may mean a task's zip changed; drop cached TOML.
  tomlContents.value = {}
  tomlErrors.value = {}
  expandedTomlId.value = null
  try {
    tasks.value = await api.getTasks()
    loading.value = false
  } catch (err) {
    error.value = extractError(err)
    loading.value = false
  }
}

async function toggleToml(task) {
  if (expandedTomlId.value === task.id) {
    expandedTomlId.value = null
    return
  }
  expandedTomlId.value = task.id
  if (tomlContents.value[task.id] !== undefined) return

  tomlLoading.value = task.id
  try {
    tomlContents.value[task.id] = await api.getTaskToml(task.id)
  } catch (err) {
    tomlErrors.value[task.id] = extractError(err)
  } finally {
    tomlLoading.value = null
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
  updatingId.value = taskId
  updateError.value = null
  updateFileInput.value?.click()
}

async function onUpdateFileChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || updatingId.value === null) return

  updateLoading.value = true
  updateError.value = null
  try {
    await api.updateTask(updatingId.value, file)
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

function formatDate(dateStr) {
  if (!dateStr) return 'N/A'
  const date = new Date(dateStr)
  return date.toLocaleString()
}

onMounted(() => {
  fetchTasks()
})
</script>

<template>
  <div>
    <header class="mb-6">
      <h1 class="text-3xl font-bold text-gray-900">Tasks</h1>
      <p class="mt-1 text-sm text-gray-500">
        Reusable task definitions. A task is a zip with a <code class="font-mono text-gray-700">task.toml</code> at its root.
      </p>
    </header>

    <div class="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_24rem] gap-6 items-start">
      <!-- Left column: upload + task list -->
      <div class="min-w-0 space-y-6">
        <!-- Upload form -->
        <div class="bento-card">
          <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
            <h2 class="text-lg font-semibold text-gray-900">Upload Task Definition</h2>
            <a
              href="/api/tasks/example"
              download="example-task.zip"
              class="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-blue-700 bg-blue-50 border border-blue-200 rounded-lg hover:bg-blue-100 transition-colors"
            >
              <svg class="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                <path d="M8 2v8m0 0 3-3m-3 3L5 7" stroke-linecap="round" stroke-linejoin="round" />
                <path d="M2.5 11.5v1a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-1" stroke-linecap="round" />
              </svg>
              Download example task
            </a>
          </div>

          <div class="flex flex-wrap items-center gap-3">
            <input
              id="task-upload-file"
              type="file"
              accept=".zip"
              @change="onUploadFileChange"
              class="block w-72 max-w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-blue-50 file:text-blue-600 hover:file:bg-blue-100"
            />
            <button
              @click="handleUpload"
              :disabled="uploadLoading || !uploadFile"
              class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {{ uploadLoading ? 'Uploading...' : 'Upload' }}
            </button>
          </div>

          <div
            v-if="uploadError"
            class="mt-4 bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg"
          >
            Upload failed: {{ uploadError }}
          </div>

          <div
            v-if="uploadResult"
            class="mt-4 bg-green-50 border border-green-200 text-green-800 px-4 py-3 rounded-lg"
          >
            <p class="font-medium">
              Task "{{ uploadResult.name }}" uploaded successfully.<span v-if="uploadResult.existed"> (Already existed)</span>
            </p>
            <p class="mt-1 text-sm"><span class="font-medium">Image:</span> {{ uploadResult.spec?.image }}</p>
            <p class="mt-1 text-sm break-all"><span class="font-medium">Command:</span> {{ uploadResult.spec?.cmd }}</p>
          </div>
        </div>

        <!-- Task list -->
        <div>
          <div class="flex items-baseline justify-between mb-3">
            <h2 class="text-sm font-semibold text-gray-900">Task definitions</h2>
            <span v-if="!loading && !error" class="text-xs text-gray-500">
              {{ tasks.length }} {{ tasks.length === 1 ? 'task' : 'tasks' }}
            </span>
          </div>

          <div
            v-if="updateError"
            class="mb-4 bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg"
          >
            Update failed: {{ updateError }}
          </div>

          <div
            v-if="deleteError"
            class="mb-4 bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg"
          >
            Delete failed: {{ deleteError }}
          </div>

          <!-- Hidden file input for inline updates -->
          <input
            ref="updateFileInput"
            type="file"
            accept=".zip"
            class="hidden"
            @change="onUpdateFileChange"
          />

          <div v-if="loading" class="flex justify-center py-12">
            <div class="spinner border-blue-500"></div>
          </div>

          <div v-else-if="error" class="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg">
            Failed to load tasks: {{ error }}
          </div>

          <div v-else-if="tasks.length === 0" class="bento-card text-center py-12">
            <p class="text-gray-900 font-medium">No task definitions yet</p>
            <p class="mt-1 text-sm text-gray-500">
              Upload a zip above, or download the example task to see the expected structure.
            </p>
          </div>

          <div v-else class="space-y-4">
            <article v-for="task in tasks" :key="task.id" class="bento-card">
              <div class="flex items-start justify-between gap-4">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <h3 class="text-base font-semibold text-gray-900 break-words">{{ task.name }}</h3>
                    <span class="text-xs font-medium text-gray-400">#{{ task.id }}</span>
                  </div>
                  <p class="mt-1 text-sm text-gray-500 break-words">
                    {{ task.desc || 'No description' }}
                  </p>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                  <a
                    :href="api.taskZipUrl(task.id)"
                    :download="`task-${task.id}.zip`"
                    class="px-3 py-1.5 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
                  >
                    Download
                  </a>
                  <button
                    @click="startUpdate(task.id)"
                    :disabled="updateLoading"
                    class="px-3 py-1.5 text-sm font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 disabled:opacity-50 transition-colors"
                  >
                    Update
                  </button>
                  <button
                    @click="handleDelete(task)"
                    :disabled="deletingId === task.id"
                    class="px-3 py-1.5 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
                  >
                    {{ deletingId === task.id ? 'Deleting...' : 'Delete' }}
                  </button>
                </div>
              </div>

              <dl class="mt-4 pt-4 border-t border-gray-100 space-y-2 text-xs">
                <div class="flex gap-3">
                  <dt class="w-16 shrink-0 text-gray-400">SHA256</dt>
                  <dd class="font-mono text-gray-600 break-all">{{ task.sha256 }}</dd>
                </div>
                <div class="flex gap-3">
                  <dt class="w-16 shrink-0 text-gray-400">Created</dt>
                  <dd class="text-gray-600">{{ formatDate(task.created_at) }}</dd>
                </div>
                <div class="flex gap-3">
                  <dt class="w-16 shrink-0 text-gray-400">Updated</dt>
                  <dd class="text-gray-600">{{ task.updated_at ? formatDate(task.updated_at) : 'Never' }}</dd>
                </div>
              </dl>

              <div class="mt-3 flex flex-wrap items-center gap-2">
                <button
                  @click="toggleToml(task)"
                  class="px-3 py-1.5 text-xs font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
                >
                  {{ expandedTomlId === task.id ? 'Hide task.toml' : 'View task.toml' }}
                </button>
              </div>

              <div v-if="expandedTomlId === task.id" class="mt-3">
                <div v-if="tomlLoading === task.id" class="text-xs text-gray-500">
                  Loading task.toml...
                </div>
                <div v-else-if="tomlErrors[task.id]" class="text-xs text-red-600">
                  Failed to load task.toml: {{ tomlErrors[task.id] }}
                </div>
                <pre
                  v-else
                  class="bg-gray-900 text-gray-100 text-xs rounded-lg p-3 overflow-x-auto whitespace-pre"
                >{{ tomlContents[task.id] }}</pre>
              </div>
            </article>
          </div>
        </div>
      </div>

      <!-- Rules card -->
      <aside class="lg:sticky lg:top-6">
        <div class="bento-card">
          <h2 class="text-lg font-semibold text-gray-900 mb-3">Task Rules</h2>

          <h3 class="text-sm font-semibold text-gray-800 mb-1">Zip layout</h3>
          <ul class="text-xs text-gray-600 space-y-1 list-disc list-inside">
            <li><code class="bg-gray-100 px-1 rounded">task.toml</code> must sit at the zip root</li>
            <li>Every other entry is copied into the job workspace (zip wins name conflicts)</li>
            <li>Absolute paths and <code class="bg-gray-100 px-1 rounded">..</code> entries are rejected</li>
            <li>sha256 of the zip is the task fingerprint; re-uploading identical content is a no-op</li>
          </ul>

          <h3 class="text-sm font-semibold text-gray-800 mt-4 mb-1">task.toml fields</h3>
          <ul class="text-xs text-gray-600 space-y-1 list-disc list-inside">
            <li><code class="bg-gray-100 px-1 rounded">name</code>, <code class="bg-gray-100 px-1 rounded">image</code>, <code class="bg-gray-100 px-1 rounded">cmd</code>, <code class="bg-gray-100 px-1 rounded">workspace</code> — required</li>
            <li><code class="bg-gray-100 px-1 rounded">desc</code> — optional description</li>
            <li><code class="bg-gray-100 px-1 rounded">artifacts</code> — paths collected when the job ends</li>
            <li><code class="bg-gray-100 px-1 rounded">watch</code> — paths viewable live during the run</li>
            <li><code class="bg-gray-100 px-1 rounded">[limit]</code> cpu / mem / timeout — all optional, missing = unlimited</li>
            <li><code class="bg-gray-100 px-1 rounded">[env]</code> — extra container environment variables</li>
          </ul>

          <h3 class="text-sm font-semibold text-gray-800 mt-4 mb-1">Execution</h3>
          <ul class="text-xs text-gray-600 space-y-1 list-disc list-inside">
            <li>The crate source is downloaded into a per-job directory, then mounted at <code class="bg-gray-100 px-1 rounded">workspace</code></li>
            <li><code class="bg-gray-100 px-1 rounded">cmd</code> runs via <code class="bg-gray-100 px-1 rounded">sh -c</code> with <code class="bg-gray-100 px-1 rounded">workspace</code> as the working directory</li>
            <li>stdout/stderr are redirected to <code class="bg-gray-100 px-1 rounded">stdout.log</code> / <code class="bg-gray-100 px-1 rounded">stderr.log</code> in the workspace — list them in <code class="bg-gray-100 px-1 rounded">watch</code> or <code class="bg-gray-100 px-1 rounded">artifacts</code> to inspect them</li>
            <li>artifacts/watch paths are relative to <code class="bg-gray-100 px-1 rounded">workspace</code></li>
          </ul>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.bento-card {
  width: 100%;
}
</style>
