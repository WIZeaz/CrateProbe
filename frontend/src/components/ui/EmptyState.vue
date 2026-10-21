<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
  description: {
    type: String,
    default: '',
  },
})
</script>

<template>
  <div class="flex flex-col items-center justify-center px-4 py-12 text-center">
    <p class="text-sm font-medium text-ink">{{ title }}</p>
    <p
      v-if="description"
      class="mt-1 max-w-sm text-xs leading-relaxed text-muted"
    >
      {{ description }}
    </p>
    <div
      v-if="$slots.default"
      class="mt-4 flex flex-wrap items-center justify-center gap-2"
    >
      <slot />
    </div>
  </div>
</template>
