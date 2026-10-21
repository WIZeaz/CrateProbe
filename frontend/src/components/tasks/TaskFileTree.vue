<script setup>
import { formatBytes } from '../../utils/format.js'

const props = defineProps({
  nodes: {
    type: Array,
    default: () => [],
  },
  depth: {
    type: Number,
    default: 0,
  },
  selectedPath: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['select'])

function onNodeClick(node) {
  if (!node.isDir) {
    emit('select', node)
  }
}
</script>

<template>
  <ul class="space-y-0.5">
    <li v-for="node in nodes" :key="node.path">
      <component
        :is="node.isDir ? 'div' : 'button'"
        :type="node.isDir ? undefined : 'button'"
        class="file-tree-row flex w-full items-center gap-2 rounded-[2px] py-1 pr-1 text-left font-mono text-[11px]"
        :class="[
          node.isDir ? 'font-medium text-ink' : 'text-muted file-tree-row--clickable',
          {
            'file-tree-row--highlight':
              !node.isDir && node.path === selectedPath,
          },
        ]"
        :style="{ paddingLeft: `${depth * 14 + 4}px` }"
        @click="onNodeClick(node)"
      >
        <svg
          v-if="node.isDir"
          class="h-3.5 w-3.5 flex-none text-muted"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          stroke-width="1.2"
          aria-hidden="true"
        >
          <path
            d="M1.75 3.75h4l1.5 1.75h7v7.25H1.75z"
            stroke-linejoin="round"
          />
        </svg>
        <svg
          v-else
          class="h-3.5 w-3.5 flex-none text-muted"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          stroke-width="1.2"
          aria-hidden="true"
        >
          <path
            d="M9.5 1.75H4.5a1 1 0 0 0-1 1v10.5a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V4.75l-3-3Z"
            stroke-linejoin="round"
          />
          <path d="M9.25 1.75v3.25h3.25" stroke-linejoin="round" />
        </svg>
        <span class="truncate" :title="node.path">{{ node.name }}</span>
        <span
          v-if="!node.isDir"
          class="ml-auto flex-none text-[10px] text-muted"
        >
          {{ formatBytes(node.size) }}
        </span>
      </component>

      <TaskFileTree
        v-if="node.isDir && node.children.length"
        :nodes="node.children"
        :depth="depth + 1"
        :selected-path="selectedPath"
        @select="emit('select', $event)"
      />
    </li>
  </ul>
</template>
