<script setup>
import { computed } from 'vue'
import MeterBar from './MeterBar.vue'
import {
  formatRelativeTime,
  healthLabel,
  healthTone,
} from './dashboardMath.js'

const props = defineProps({
  runners: {
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
  authError: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['retry'])

const onlineCount = computed(
  () => props.runners.filter(runner => runner.health_status === 'online').length
)
const offlineCount = computed(
  () => props.runners.filter(runner => runner.health_status === 'offline').length
)

function seenLabel(runner) {
  if (!runner.last_seen_at) {
    return 'Never seen'
  }
  return `Seen ${formatRelativeTime(runner.last_seen_at)}`
}

function activeLabel(metrics) {
  const active = metrics?.active_tasks
  if (active === null || active === undefined) {
    return 'Active --'
  }
  if (Number(active) === 0) {
    return 'Idle'
  }
  return `${active} active`
}
</script>

<template>
  <section class="instrument-panel flex h-full flex-col" aria-label="Runner fleet">
    <div class="panel-header">
      <h2 class="panel-title">Runner fleet</h2>
      <div
        v-if="!loading && !error && runners.length > 0"
        class="flex items-center gap-3 font-mono text-[11px] text-muted"
      >
        <span class="inline-flex items-center gap-1.5">
          <span class="status-dot tone-good" aria-hidden="true"></span>
          {{ onlineCount }} online
        </span>
        <span v-if="offlineCount > 0" class="inline-flex items-center gap-1.5">
          <span class="status-dot tone-alarm" aria-hidden="true"></span>
          {{ offlineCount }} offline
        </span>
      </div>
    </div>

    <div v-if="loading" class="space-y-5 pt-4" aria-busy="true" aria-label="Loading runners">
      <div v-for="index in 2" :key="index">
        <div class="flex items-center justify-between gap-3">
          <div class="skeleton h-3.5 w-28"></div>
          <div class="skeleton h-3 w-16"></div>
        </div>
        <div class="skeleton mt-2 h-2.5 w-24"></div>
        <div class="mt-4 space-y-2.5">
          <div class="skeleton h-4 w-full"></div>
          <div class="skeleton h-4 w-full"></div>
          <div class="skeleton h-4 w-full"></div>
        </div>
      </div>
    </div>

    <div v-else-if="error" class="flex flex-1 items-center py-6" role="alert">
      <div class="w-full">
        <template v-if="authError">
          <p class="text-sm text-ink">Runner details need an admin token.</p>
          <p class="mt-1 text-xs text-muted">{{ error }}</p>
          <router-link to="/settings" class="btn btn--ghost mt-4">
            Open Settings
          </router-link>
        </template>
        <template v-else>
          <p class="text-sm text-ink">Couldn't load runner status.</p>
          <p class="mt-1 break-words text-xs text-muted">{{ error }}</p>
          <button type="button" class="btn btn--ghost mt-4" @click="$emit('retry')">
            Try again
          </button>
        </template>
      </div>
    </div>

    <div v-else-if="runners.length === 0" class="flex flex-1 items-center py-6">
      <div>
        <p class="text-sm text-ink">No runners connected.</p>
        <p class="mt-1 text-xs text-muted">Start a runner to process jobs.</p>
        <router-link to="/runners" class="btn btn--ghost mt-4">
          Set up a runner
        </router-link>
      </div>
    </div>

    <div v-else class="min-h-0 flex-1 overflow-y-auto">
      <article
        v-for="runner in runners"
        :key="runner.runner_id"
        class="runner-block"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="runner-name truncate">{{ runner.runner_id }}</p>
            <p class="runner-seen">{{ seenLabel(runner) }}</p>
          </div>
          <span
            :class="[
              'status-mark',
              `tone-${healthTone(runner.health_status)}`,
            ]"
          >
            <span class="status-dot" aria-hidden="true"></span>
            <span>{{ healthLabel(runner.health_status) }}</span>
          </span>
        </div>

        <div class="mt-3 space-y-2.5">
          <MeterBar
            label="CPU"
            :value="runner.latest_metrics?.cpu_percent"
            :muted="runner.health_status !== 'online'"
          />
          <MeterBar
            label="MEM"
            :value="runner.latest_metrics?.memory_percent"
            :muted="runner.health_status !== 'online'"
          />
          <MeterBar
            label="DISK"
            :value="runner.latest_metrics?.disk_percent"
            :muted="runner.health_status !== 'online'"
          />
        </div>

        <p class="mt-3 font-mono text-[11px] text-muted">
          {{ activeLabel(runner.latest_metrics) }}
        </p>
      </article>
    </div>

    <div v-if="!loading && !error && runners.length > 0" class="panel-footer">
      <router-link to="/runners" class="text-link">Manage runners</router-link>
    </div>
  </section>
</template>
