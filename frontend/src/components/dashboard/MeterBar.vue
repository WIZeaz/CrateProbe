<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: {
    type: String,
    required: true,
  },
  value: {
    type: [Number, String],
    default: null,
  },
  max: {
    type: Number,
    default: 100,
  },
  unit: {
    type: String,
    default: '%',
  },
  warnAt: {
    type: Number,
    default: 75,
  },
  criticalAt: {
    type: Number,
    default: 90,
  },
  showTicks: {
    type: Boolean,
    default: true,
  },
  muted: {
    type: Boolean,
    default: false,
  },
})

const numeric = computed(() => {
  if (props.value === null || props.value === undefined || props.value === '') {
    return null
  }
  const number = Number(props.value)
  if (!Number.isFinite(number)) {
    return null
  }
  return Math.min(Math.max(number, 0), props.max)
})

const percent = computed(() =>
  numeric.value === null ? 0 : (numeric.value / props.max) * 100
)

const tone = computed(() => {
  if (numeric.value === null || props.muted) {
    return 'neutral'
  }
  if (numeric.value >= props.criticalAt) {
    return 'alarm'
  }
  if (numeric.value >= props.warnAt) {
    return 'warn'
  }
  return 'signal'
})

const displayValue = computed(() => {
  if (numeric.value === null) {
    return '--'
  }
  const rounded = Math.round(numeric.value * 10) / 10
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}${props.unit}`
})

const valueText = computed(() =>
  numeric.value === null ? 'No data' : `${props.label} ${displayValue.value}`
)
</script>

<template>
  <div>
    <div class="mb-1 flex items-baseline justify-between gap-3">
      <span class="font-sans text-[11px] font-medium text-muted">{{ label }}</span>
      <span class="font-mono text-[11px] tabular-nums text-ink">{{ displayValue }}</span>
    </div>
    <div
      class="meter-track"
      :class="`tone-${tone}`"
      role="meter"
      aria-valuemin="0"
      :aria-valuemax="max"
      :aria-valuenow="numeric ?? undefined"
      :aria-valuetext="valueText"
      :aria-label="label"
    >
      <div class="meter-fill" :style="{ width: `${Math.min(percent, 100)}%` }"></div>
      <template v-if="showTicks && max === 100">
        <span class="meter-tick" style="left: 75%" aria-hidden="true"></span>
        <span class="meter-tick" style="left: 90%" aria-hidden="true"></span>
      </template>
    </div>
  </div>
</template>
