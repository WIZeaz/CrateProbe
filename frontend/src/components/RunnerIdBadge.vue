<script setup>
const props = defineProps({
  runnerId: {
    type: String,
    default: '',
  },
})
</script>

<template>
  <span
    v-if="runnerId"
    class="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700"
  >
    {{ runnerId }}
  </span>
  <span v-else class="text-sm text-gray-400">-</span>
</template>
