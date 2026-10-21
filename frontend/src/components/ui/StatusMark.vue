<script setup>
import { computed } from 'vue'
import { statusLabel, statusTone } from '../../utils/status.js'

const props = defineProps({
  status: {
    type: String,
    default: '',
  },
  tone: {
    type: String,
    default: '',
  },
  label: {
    type: String,
    default: '',
  },
  hollow: {
    type: Boolean,
    default: false,
  },
  size: {
    type: String,
    default: 'sm',
  },
})

const resolvedLabel = computed(() => props.label || statusLabel(props.status))
const resolvedTone = computed(() => props.tone || statusTone(props.status))
const isHollow = computed(
  () => props.hollow || props.status === 'pending'
)
</script>

<template>
  <span
    :class="[
      'status-mark',
      { 'status-mark--md': size === 'md' },
      `tone-${resolvedTone}`,
    ]"
  >
    <span
      :class="['status-dot', { 'status-dot--hollow': isHollow }]"
      aria-hidden="true"
    ></span>
    <span>{{ resolvedLabel }}</span>
  </span>
</template>
