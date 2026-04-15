<script setup>
import { computed } from 'vue'

const props = defineProps({
  points: {
    type: Array,
    default: () => [],
  },
  field: {
    type: String,
    required: true,
  },
  maxY: {
    type: Number,
    default: 100,
  },
  stroke: {
    type: String,
    default: '#2563eb',
  },
})

const width = 480
const height = 120

const polyline = computed(() => {
  if (!props.points.length) return ''
  return props.points
    .map((point, index) => {
      const x = (index / Math.max(props.points.length - 1, 1)) * width
      const value = Math.max(0, Math.min(Number(point[props.field] ?? 0), props.maxY))
      const y = height - (value / props.maxY) * height
      return `${x},${y}`
    })
    .join(' ')
})
</script>

<template>
  <svg :viewBox="`0 0 ${width} ${height}`" class="w-full h-28">
    <rect x="0" y="0" :width="width" :height="height" fill="#f8fafc" rx="8" />
    <polyline
      v-if="polyline"
      :points="polyline"
      fill="none"
      :stroke="stroke"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>
