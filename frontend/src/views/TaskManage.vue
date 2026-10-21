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

async function fetchTasks() {
  try {
    tasks.value = await api.getTasks()
    loading.value = false
  } catch (err) {
    error.value = extractError(err)
    loading.value = false
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
    <div class="flex items-center justify-between mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Tasks</h1>
    </div>

    <!-- Upload form -->
    <div class="bento-card mb-6">
      <h2 class="text-lg font-semibold text-gray-900 mb-4">Upload Task Definition</h2>
      <div class="flex items-center gap-3">
        <input
          id="task-upload-file"
          type="file"
          accept=".zip"
          @change="onUploadFileChange"
          class="block w-80 text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-blue-50 file:text-blue-600 hover:file:bg-blue-100"
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

    <div
      v-if="updateError"
      class="mb-6 bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg"
    >
      Update failed: {{ updateError }}
    </div>

    <div
      v-if="deleteError"
      class="mb-6 bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-lg"
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
      <p class="text-gray-500">No task definitions found. Upload one above to get started!</p>
    </div>

    <div v-else class="bento-card overflow-x-auto">
      <table class="min-w-full divide-y divide-gray-200">
        <thead class="bg-gray-50">
          <tr>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">ID</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Description</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">SHA256</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Created At</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Updated At</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-200">
          <tr v-for="task in tasks" :key="task.id" class="hover:bg-gray-50 transition-colors">
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900">#{{ task.id }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">{{ task.name }}</td>
            <td class="px-4 py-3 text-sm text-gray-500 max-w-xs truncate">{{ task.desc || '-' }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500 font-mono">{{ task.sha256?.slice(0, 12) }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ formatDate(task.created_at) }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-500">{{ formatDate(task.updated_at) }}</td>
            <td class="px-4 py-3 whitespace-nowrap text-sm">
              <div class="flex items-center gap-2">
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
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.bento-card {
  width: 100%;
}
</style>
