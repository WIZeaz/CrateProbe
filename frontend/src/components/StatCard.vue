<script setup>
defineProps({
  title: String,
  value: [String, Number],
  icon: String,
  color: {
    type: String,
    default: 'blue'
  },
  clickable: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['click'])

const colorClasses = {
  blue: 'bg-blue-50 text-blue-600',
  green: 'bg-green-50 text-green-600',
  red: 'bg-red-50 text-red-600',
  yellow: 'bg-yellow-50 text-yellow-600',
  purple: 'bg-purple-50 text-purple-600',
  gray: 'bg-gray-50 text-gray-600'
}

function handleClick() {
  if (clickable) {
    emit('click')
  }
}
</script>

<template>
  <div
    :class="['bento-card', { 'cursor-pointer hover:shadow-lg transition-shadow': clickable }]"
    @click="handleClick"
  >
    <div class="flex items-center justify-between">
      <div>
        <p class="text-sm font-medium text-gray-600">{{ title }}</p>
        <p class="mt-2 text-3xl font-bold text-gray-900">{{ value }}</p>
      </div>
      <div :class="['p-3 rounded-lg', colorClasses[color]]" v-if="icon">
        <span class="text-2xl">{{ icon }}</span>
      </div>
    </div>
  </div>
</template>
