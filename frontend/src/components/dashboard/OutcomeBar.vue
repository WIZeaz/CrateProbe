<script setup>
import { computed } from 'vue'
import { formatCount } from './dashboardMath.js'

const props = defineProps({
  segments: {
    type: Array,
    default: () => [],
  },
  total: {
    type: Number,
    default: 0,
  },
})

const visibleSegments = computed(() =>
  props.segments.filter(segment => segment.count > 0)
)

const ariaLabel = computed(() => {
  const parts = props.segments
    .filter(segment => segment.count > 0)
    .map(
      segment =>
        `${segment.label} ${formatCount(segment.count)} (${segment.percentLabel})`
    )
  return `All recorded jobs: ${formatCount(props.total)}. ${parts.join(', ')}.`
})

function tooltipFor(segment) {
  return `${segment.label}: ${formatCount(segment.count)} (${segment.percentLabel})`
}
</script>

<template>
  <div>
    <div
      v-if="visibleSegments.length"
      class="outcome-bar"
      role="img"
      :aria-label="ariaLabel"
    >
      <span
        v-for="segment in visibleSegments"
        :key="segment.key"
        :class="['outcome-segment', `outcome-segment--${segment.key}`]"
        :style="{ flexGrow: segment.count, flexBasis: 0 }"
        :title="tooltipFor(segment)"
        aria-hidden="true"
      ></span>
    </div>
    <div
      v-else
      class="outcome-bar outcome-bar--empty"
      role="img"
      aria-label="No recorded jobs yet."
    ></div>

    <ul class="mt-3 flex flex-wrap gap-x-6 gap-y-2">
      <li
        v-for="segment in segments"
        :key="segment.key"
        class="flex items-center gap-1.5 whitespace-nowrap"
      >
        <span
          :class="['legend-swatch', `legend-swatch--${segment.key}`]"
          aria-hidden="true"
        ></span>
        <span class="text-[11px] text-muted">{{ segment.label }}</span>
        <span class="font-mono text-[11px] text-ink">
          {{ formatCount(segment.count) }}
        </span>
        <span class="font-mono text-[11px] text-muted">
          {{ segment.percentLabel }}
        </span>
      </li>
    </ul>
  </div>
</template>
