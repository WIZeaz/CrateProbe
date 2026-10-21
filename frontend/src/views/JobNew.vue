<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import PageHeader from '../components/ui/PageHeader.vue'
import TaskSelect from '../components/ui/TaskSelect.vue'

const router = useRouter()
const form = ref({
  crate_name: '',
  version: '',
  task_id: null
})
const tasks = ref([])
const tasksLoading = ref(true)
const tasksError = ref(null)
const loading = ref(false)
const error = ref(null)

async function fetchTasks() {
  tasksLoading.value = true
  tasksError.value = null
  try {
    tasks.value = await api.getTasks()
    if (tasks.value.length > 0) {
      form.value.task_id = tasks.value[0].id
    }
  } catch (err) {
    tasksError.value = err.response?.data?.detail || err.message
  } finally {
    tasksLoading.value = false
  }
}

async function createJob() {
  if (!form.value.crate_name) {
    error.value = 'Crate name is required'
    return
  }

  if (!form.value.task_id) {
    error.value = 'Please select a task'
    return
  }

  loading.value = true
  error.value = null

  try {
    const version = form.value.version.trim() || null
    const result = await api.createJob(form.value.crate_name, version, form.value.task_id)

    // Redirect to job detail page
    router.push(`/jobs/${result.job_id}`)
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
    loading.value = false
  }
}

onMounted(() => {
  fetchTasks()
})
</script>

<template>
  <div>
    <PageHeader
      title="New job"
      description="Run a task against a crate version."
    />

    <div class="grid grid-cols-1 gap-5 lg:grid-cols-5">
      <section class="instrument-panel lg:col-span-3">
        <div class="panel-header">
          <h2 class="panel-title">Job configuration</h2>
        </div>

        <form class="mt-5 max-w-2xl space-y-5" @submit.prevent="createJob">
          <div>
            <TaskSelect
              v-model="form.task_id"
              label="Task *"
              :tasks="tasks"
              :loading="tasksLoading"
              :error="tasksError"
              :disabled="loading"
              @retry="fetchTasks"
            />
            <p class="mt-1 text-[11px] text-muted">
              The task definition that will execute this job.
            </p>
          </div>

          <div>
            <label for="crate_name" class="ui-label">Crate name *</label>
            <input
              id="crate_name"
              v-model="form.crate_name"
              type="text"
              required
              placeholder="e.g., serde, tokio, regex"
              class="ui-input"
              :disabled="loading"
            />
            <p class="mt-1 text-[11px] text-muted">
              Enter the name of the Rust crate from crates.io.
            </p>
          </div>

          <div>
            <label for="version" class="ui-label">Version (optional)</label>
            <input
              id="version"
              v-model="form.version"
              type="text"
              placeholder="e.g., 1.0.0 (leave empty for latest)"
              class="ui-input"
              :disabled="loading"
            />
            <p class="mt-1 text-[11px] text-muted">
              Leave empty to use the latest version.
            </p>
          </div>

          <div v-if="error" class="notice notice--error" role="alert">
            {{ error }}
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
              type="submit"
              class="btn btn--primary"
              :disabled="loading || tasks.length === 0"
            >
              <span v-if="loading" class="spinner border-white"></span>
              {{ loading ? 'Creating...' : 'Create job' }}
            </button>
          </div>
        </form>
      </section>

      <aside class="lg:col-span-2">
        <section class="instrument-panel lg:sticky lg:top-20">
          <div class="panel-header">
            <h2 class="panel-title">What happens next</h2>
          </div>
          <ol class="mt-4 space-y-3">
            <li class="flex gap-2.5">
              <span class="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-signal"></span>
              <p class="text-[12px] leading-relaxed text-muted">
                The job is queued for the next available runner.
              </p>
            </li>
            <li class="flex gap-2.5">
              <span class="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-signal"></span>
              <p class="text-[12px] leading-relaxed text-muted">
                A runner executes the selected task against the crate.
              </p>
            </li>
            <li class="flex gap-2.5">
              <span class="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-signal"></span>
              <p class="text-[12px] leading-relaxed text-muted">
                Logs, watched files and artifacts appear on the job page.
              </p>
            </li>
            <li class="flex gap-2.5">
              <span class="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-signal"></span>
              <p class="text-[12px] leading-relaxed text-muted">
                Progress is visible on the dashboard and in the queue.
              </p>
            </li>
          </ol>
        </section>
      </aside>
    </div>
  </div>
</template>
