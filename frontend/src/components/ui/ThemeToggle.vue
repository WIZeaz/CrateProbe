<script setup>
import { useTheme } from '../../composables/useTheme'

const { mode, setMode } = useTheme()

const options = [
  { value: 'system', label: 'Follow system appearance' },
  { value: 'light', label: 'Light theme' },
  { value: 'dark', label: 'Dark theme' },
]
</script>

<template>
  <div
    class="inline-flex flex-none items-center gap-0.5 rounded-[3px] border border-rule bg-face p-0.5"
    role="group"
    aria-label="Color theme"
  >
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      class="inline-flex h-8 w-8 items-center justify-center rounded-[2px] transition-colors"
      :class="
        mode === option.value
          ? 'bg-signal/10 text-signal'
          : 'text-muted hover:text-ink dark:hover:bg-white/[0.06]'
      "
      :aria-pressed="mode === option.value"
      :title="option.label"
      @click="setMode(option.value)"
    >
      <span class="sr-only">{{ option.label }}</span>

      <svg
        v-if="option.value === 'system'"
        class="h-4 w-4"
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden="true"
      >
        <rect
          x="2"
          y="3"
          width="12"
          height="8.5"
          rx="1"
          stroke="currentColor"
          stroke-width="1.3"
        />
        <path
          d="M5.5 14h5M8 11.5V14"
          stroke="currentColor"
          stroke-width="1.3"
          stroke-linecap="round"
        />
      </svg>

      <svg
        v-else-if="option.value === 'light'"
        class="h-4 w-4"
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden="true"
      >
        <circle cx="8" cy="8" r="3" stroke="currentColor" stroke-width="1.3" />
        <path
          d="M8 1.5V3M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1.06 1.06M11.54 11.54l1.06 1.06M12.6 3.4l-1.06 1.06M4.46 11.54 3.4 12.6"
          stroke="currentColor"
          stroke-width="1.3"
          stroke-linecap="round"
        />
      </svg>

      <svg
        v-else
        class="h-4 w-4"
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M13.5 9.6A5.9 5.9 0 0 1 6.4 2.5a5.9 5.9 0 1 0 7.1 7.1Z"
          stroke="currentColor"
          stroke-width="1.3"
          stroke-linejoin="round"
        />
      </svg>
    </button>
  </div>
</template>
