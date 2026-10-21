<script setup>
defineProps({
  runnerId: {
    type: String,
    default: '',
  },
})
</script>

<template>
  <span v-if="runnerId" class="ui-tag">{{ runnerId }}</span>
  <span v-else class="text-[11px] text-muted">—</span>
</template>
