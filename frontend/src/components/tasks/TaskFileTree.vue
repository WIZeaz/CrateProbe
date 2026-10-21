<script setup>
import { formatBytes } from '../../utils/format.js'

defineProps({
  nodes: {
    type: Array,
    default: () => [],
  },
  depth: {
    type: Number,
    default: 0,
  },
  highlightPath: {
    type: String,
    default: 'task.toml',
  },
})
</script>

<template>
  <ul class="space-y-0.5">
    <li v-for="node in nodes" :key="node.path">
      <div
        class="flex items-center gap-2 rounded-[2px] py-1 pr-1 font-mono text-[11px]"
        :class="[
          node.isDir ? 'font-medium text-ink' : 'text-muted',
          {
            'file-tree-row--highlight':
              !node.isDir && node.path === highlightPath,
          },
        ]"
        :style="{ paddingLeft: `${depth * 14 + 4}px` }"
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
      </div>

      <TaskFileTree
        v-if="node.isDir && node.children.length"
        :nodes="node.children"
        :depth="depth + 1"
        :highlight-path="highlightPath"
      />
    </li>
  </ul>
</template>
