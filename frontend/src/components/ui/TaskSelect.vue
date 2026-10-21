<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: {
    type: [Number, String],
    default: null,
  },
  tasks: {
    type: Array,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  label: {
    type: String,
    default: 'Task',
  },
})

const emit = defineEmits(['update:modelValue', 'retry'])

const selected = computed({
  get: () => props.modelValue,
  set: value => emit('update:modelValue', value),
})
</script>

<template>
  <div>
    <label for="task-select" class="ui-label">{{ label }}</label>

    <div
      v-if="loading"
      class="flex items-center gap-2 text-[12px] text-muted"
    >
      <span class="spinner text-signal"></span>
      Loading tasks...
    </div>

    <div v-else-if="error" class="notice notice--error" role="alert">
      <div class="min-w-0 flex-1">
        <p class="notice__title">Couldn't load task definitions.</p>
        <p class="notice__detail">{{ error }}</p>
      </div>
      <button type="button" class="btn btn--ghost btn--sm" @click="$emit('retry')">
        Retry
      </button>
    </div>

    <div v-else-if="tasks.length === 0" class="notice notice--warn">
      <div class="min-w-0 flex-1">
        <p class="notice__title">No task definitions found.</p>
        <p class="notice__detail">Create a task before running a job.</p>
      </div>
      <router-link to="/tasks" class="btn btn--ghost btn--sm">
        Open tasks
      </router-link>
    </div>

    <select
      v-else
      id="task-select"
      v-model="selected"
      class="ui-select"
      :disabled="disabled"
    >
      <option v-for="task in tasks" :key="task.id" :value="task.id">
        {{ task.name }} (#{{ task.id }})
      </option>
    </select>
  </div>
</template>
