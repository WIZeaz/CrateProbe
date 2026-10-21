<script setup>
import { useRouter } from 'vue-router'
import EmptyState from '../ui/EmptyState.vue'
import StatusMark from '../ui/StatusMark.vue'
import { formatDuration, formatRelativeTime } from '../../utils/format.js'

defineProps({
  jobs: {
    type: Array,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
})

defineEmits(['retry'])

const router = useRouter()

function viewJob(jobId) {
  router.push(`/jobs/${jobId}`)
}
</script>

<template>
  <section class="instrument-panel flex h-full flex-col" aria-label="Recent jobs">
    <div class="panel-header">
      <h2 class="panel-title">Recent jobs</h2>
      <router-link to="/jobs" class="text-link">View all jobs</router-link>
    </div>

    <div
      v-if="loading"
      class="mt-4 space-y-4"
      aria-busy="true"
      aria-label="Loading recent jobs"
    >
      <div v-for="index in 6" :key="index" class="flex items-center gap-4">
        <div class="skeleton h-3 w-16"></div>
        <div class="skeleton h-3 flex-1"></div>
        <div class="skeleton h-3 w-20"></div>
        <div class="skeleton h-3 w-10"></div>
      </div>
    </div>

    <div v-else-if="error" class="flex flex-1 items-center py-6">
      <div class="notice notice--error w-full" role="alert">
        <div class="min-w-0 flex-1">
          <p class="notice__title">Couldn't load recent jobs.</p>
          <p class="notice__detail break-words">{{ error }}</p>
        </div>
        <button
          type="button"
          class="btn btn--ghost btn--sm"
          @click="$emit('retry')"
        >
          Try again
        </button>
      </div>
    </div>

    <EmptyState
      v-else-if="jobs.length === 0"
      title="No recent jobs yet"
      description="New jobs will appear here as they are created."
    >
      <router-link to="/jobs/new" class="text-link">Create a job</router-link>
    </EmptyState>

    <template v-else>
      <table class="mt-1 hidden w-full table-fixed border-collapse text-left md:table">
        <thead>
          <tr class="border-b border-rule">
            <th scope="col" class="w-[120px] py-2 pl-2.5 pr-3 text-[11px] font-medium text-muted">
              Status
            </th>
            <th scope="col" class="py-2 pr-3 text-[11px] font-medium text-muted">
              Crate
            </th>
            <th scope="col" class="w-[140px] py-2 pr-3 text-[11px] font-medium text-muted">
              Task
            </th>
            <th scope="col" class="w-[80px] py-2 pr-3 text-right text-[11px] font-medium text-muted">
              Runtime
            </th>
            <th
              scope="col"
              class="hidden w-[90px] py-2 text-right text-[11px] font-medium text-muted lg:table-cell"
            >
              Started
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="job in jobs"
            :key="job.id"
            class="job-row border-b border-rule last:border-0"
            @click="viewJob(job.id)"
          >
            <td class="py-3 pl-2.5 pr-3 align-middle whitespace-nowrap">
              <StatusMark :status="job.status" />
            </td>
            <td class="py-3 pr-3 align-middle">
              <router-link
                :to="`/jobs/${job.id}`"
                class="flex min-w-0 items-baseline gap-1.5 text-[13px] font-medium text-ink hover:text-signal"
                :title="`${job.crate_name} ${job.version}`"
                @click.stop
              >
                <span class="truncate">{{ job.crate_name }}</span>
                <span class="flex-none font-mono text-[11px] font-normal text-muted">
                  {{ job.version }}
                </span>
              </router-link>
            </td>
            <td class="py-3 pr-3 align-middle text-[12px] text-muted">
              <span class="block truncate" :title="job.task_name || ''">
                {{ job.task_name || '—' }}
              </span>
            </td>
            <td
              class="py-3 pr-3 align-middle text-right font-mono text-[11px] tabular-nums text-ink"
            >
              {{ formatDuration(job.started_at, job.finished_at) }}
            </td>
            <td
              class="hidden py-3 align-middle text-right font-mono text-[11px] text-muted lg:table-cell"
            >
              {{ formatRelativeTime(job.started_at) }}
            </td>
          </tr>
        </tbody>
      </table>

      <ul class="md:hidden">
        <li
          v-for="job in jobs"
          :key="job.id"
          class="border-b border-rule py-3 last:border-0"
        >
          <div class="flex items-center justify-between gap-3">
            <StatusMark :status="job.status" />
            <span class="font-mono text-[11px] text-muted">
              {{ formatDuration(job.started_at, job.finished_at) }}
            </span>
          </div>
          <router-link
            :to="`/jobs/${job.id}`"
            class="mt-1 block truncate text-[13px] font-medium text-ink"
          >
            {{ job.crate_name }}
            <span class="font-mono text-[11px] font-normal text-muted">
              {{ job.version }}
            </span>
          </router-link>
          <dl class="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            <div class="flex min-w-0 gap-1.5">
              <dt class="flex-none text-muted">Task</dt>
              <dd class="truncate text-ink">{{ job.task_name || '—' }}</dd>
            </div>
            <div class="flex min-w-0 gap-1.5">
              <dt class="flex-none text-muted">Started</dt>
              <dd class="truncate text-ink">{{ formatRelativeTime(job.started_at) }}</dd>
            </div>
          </dl>
        </li>
      </ul>
    </template>
  </section>
</template>
