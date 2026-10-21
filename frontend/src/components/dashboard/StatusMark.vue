<script setup>
import { computed } from 'vue'
import { statusLabel, statusTone } from './dashboardMath.js'

const props = defineProps({
  status: {
    type: String,
    required: true,
  },
})

const label = computed(() => statusLabel(props.status))
const tone = computed(() => statusTone(props.status))
const isHollow = computed(() => props.status === 'pending')
</script>

<template>
  <span :class="['status-mark', `tone-${tone}`]">
    <span
      :class="['status-dot', { 'status-dot--hollow': isHollow }]"
      aria-hidden="true"
    ></span>
    <span>{{ label }}</span>
  </span>
</template>
