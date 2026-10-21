<script setup>
import { computed } from 'vue'
import OutcomeBar from './OutcomeBar.vue'
import StatusMark from './StatusMark.vue'
import { formatCount, groupStatusCounts } from './dashboardMath.js'

const props = defineProps({
  stats: {
    type: Object,
    default: () => ({}),
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

const grouped = computed(() => groupStatusCounts(props.stats))
</script>

<template>
  <div
    v-if="loading"
    class="instrument-panel"
    aria-busy="true"
    aria-label="Loading job pipeline"
  >
    <div class="pipeline-grid">
      <div>
        <div class="skeleton h-3 w-24"></div>
        <div class="skeleton mt-3 h-16 w-28"></div>
        <div class="skeleton mt-3 h-3 w-40"></div>
      </div>
      <div class="pipeline-divider" aria-hidden="true"></div>
      <div>
        <div class="skeleton h-3 w-32"></div>
        <div class="skeleton mt-4 h-3.5 w-full"></div>
        <div class="skeleton mt-3 h-3 w-full"></div>
        <div class="skeleton mt-2 h-3 w-2/3"></div>
      </div>
    </div>
    <div class="pipeline-ledger">
      <div v-for="index in 3" :key="index">
        <div class="skeleton h-3 w-20"></div>
        <div class="skeleton mt-3 h-3 w-full"></div>
        <div class="skeleton mt-2 h-3 w-4/5"></div>
      </div>
    </div>
  </div>

  <div v-else-if="error" class="instrument-panel" role="alert">
    <p class="panel-kicker">Job pipeline</p>
    <p class="mt-2 text-sm text-ink">Couldn't load job totals.</p>
    <p class="mt-1 break-words text-xs text-muted">{{ error }}</p>
    <button type="button" class="btn btn--ghost mt-4" @click="$emit('retry')">
      Try again
    </button>
  </div>

  <div
    v-else-if="grouped.total === 0"
    class="instrument-panel"
    aria-label="Job pipeline"
  >
    <div class="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <p class="panel-kicker">Jobs in flight</p>
        <p class="hero-figure">0</p>
        <p class="hero-caption">No jobs running or queued.</p>
      </div>
      <div class="max-w-sm sm:text-right">
        <p class="text-sm text-ink">Create a job to start testing crates.</p>
        <router-link to="/jobs/new" class="btn btn--primary mt-3">
          Create a job
        </router-link>
      </div>
    </div>
  </div>

  <section v-else class="instrument-panel" aria-label="Job pipeline">
    <div class="pipeline-grid">
      <div>
        <p class="panel-kicker">Jobs in flight</p>
        <p class="hero-figure">{{ formatCount(grouped.inFlight) }}</p>
        <p class="hero-caption">
          <template v-if="grouped.inFlight > 0">
            <router-link to="/jobs?status=running" class="text-link">
              {{ formatCount(grouped.counts.running) }} running
            </router-link>
            <span>, </span>
            <router-link to="/jobs?status=pending" class="text-link">
              {{ formatCount(grouped.counts.pending) }} pending
            </router-link>
          </template>
          <template v-else>Queue is clear.</template>
        </p>
      </div>

      <div class="pipeline-divider" aria-hidden="true"></div>

      <div>
        <div class="mb-2 flex items-baseline justify-between gap-4">
          <p class="panel-kicker">All recorded jobs</p>
          <p class="font-mono text-[12px] text-ink">{{ formatCount(grouped.total) }}</p>
        </div>
        <OutcomeBar :segments="grouped.segments" :total="grouped.total" />
      </div>
    </div>

    <div class="pipeline-ledger">
      <div
        v-for="group in grouped.groups"
        :key="group.key"
        class="ledger-group"
        :class="{
          'ledger-group--attention':
            group.key === 'attention' && grouped.attention > 0,
        }"
      >
        <p class="ledger-heading">{{ group.label }}</p>
        <router-link
          v-for="item in group.statuses"
          :key="item.status"
          :to="`/jobs?status=${item.status}`"
          class="ledger-row"
        >
          <StatusMark :status="item.status" />
          <span class="ledger-count">{{ formatCount(item.count) }}</span>
        </router-link>
      </div>
    </div>
  </section>
</template>
