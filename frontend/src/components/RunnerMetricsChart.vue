<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  RESOURCE_FIELDS,
  buildXTicks,
  clampValue,
  computeDomainMaxY,
} from './runnerMetricsChartMath.js'

const props = defineProps({
  points: {
    type: Array,
    default: () => [],
  },
  field: {
    type: String,
    required: true,
  },
  maxY: {
    type: Number,
    default: 100,
  },
  stroke: {
    type: String,
    default: '#2563eb',
  },
})

const width = 480
const height = 140
const svgRef = ref(null)
const renderedWidth = ref(width)
let resizeObserver = null

const plot = {
  left: 40,
  right: width - 8,
  top: 8,
  bottom: height - 28,
}

plot.width = plot.right - plot.left
plot.height = plot.bottom - plot.top

const domainMaxY = computed(() => computeDomainMaxY(props.field, props.maxY))

const isResourceField = computed(() => RESOURCE_FIELDS.has(props.field))

const yTicks = computed(() => {
  if (isResourceField.value) {
    return [0, 25, 50, 75, 100]
  }

  const max = domainMaxY.value
  return [0, max * 0.25, max * 0.5, max * 0.75, max]
})

const renderedLeftMargin = computed(() => (plot.left / width) * renderedWidth.value)
const renderedRightMargin = computed(() => ((width - plot.right) / width) * renderedWidth.value)
const renderedPlotWidth = computed(() => {
  const effectiveWidth = renderedWidth.value - renderedLeftMargin.value - renderedRightMargin.value
  return Math.max(0, effectiveWidth)
})

const xTicks = computed(() =>
  buildXTicks(props.points, {
    width: renderedPlotWidth.value,
    minLabelSpacing: 60,
  }),
)

function updateRenderedWidth() {
  const nextWidth = svgRef.value?.clientWidth
  if (Number.isFinite(nextWidth) && nextWidth > 0) {
    renderedWidth.value = nextWidth
  }
}

onMounted(() => {
  updateRenderedWidth()
  if (typeof ResizeObserver === 'undefined' || !svgRef.value) {
    return
  }

  resizeObserver = new ResizeObserver(() => {
    updateRenderedWidth()
  })
  resizeObserver.observe(svgRef.value)
})

onBeforeUnmount(() => {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
})

function xForIndex(index) {
  if (props.points.length <= 1) {
    return plot.left
  }
  return plot.left + (index / (props.points.length - 1)) * plot.width
}

function yForValue(value) {
  const clamped = clampValue(value, domainMaxY.value)
  return plot.bottom - (clamped / domainMaxY.value) * plot.height
}

function formatYTick(value) {
  if (Number.isInteger(value)) {
    return `${value}`
  }
  return value.toFixed(1)
}

const polyline = computed(() => {
  if (!props.points.length) return ''

  return props.points
    .map((point, index) => {
      const x = xForIndex(index)
      const y = yForValue(point[props.field] ?? 0)
      return `${x},${y}`
    })
    .join(' ')
})
</script>

<template>
  <svg ref="svgRef" :viewBox="`0 0 ${width} ${height}`" class="w-full h-32">
    <rect x="0" y="0" :width="width" :height="height" fill="#f8fafc" rx="8" />

    <g v-for="tick in yTicks" :key="`y-${tick}`">
      <line
        :x1="plot.left"
        :x2="plot.right"
        :y1="yForValue(tick)"
        :y2="yForValue(tick)"
        stroke="#e2e8f0"
        stroke-width="1"
      />
      <text
        :x="plot.left - 6"
        :y="yForValue(tick) + 4"
        font-size="10"
        fill="#64748b"
        text-anchor="end"
      >
        {{ formatYTick(tick) }}
      </text>
    </g>

    <line :x1="plot.left" :x2="plot.right" :y1="plot.bottom" :y2="plot.bottom" stroke="#cbd5e1" stroke-width="1" />
    <line :x1="plot.left" :x2="plot.left" :y1="plot.top" :y2="plot.bottom" stroke="#cbd5e1" stroke-width="1" />

    <g v-for="tick in xTicks" :key="`x-${tick.index}`">
      <line
        :x1="xForIndex(tick.index)"
        :x2="xForIndex(tick.index)"
        :y1="plot.bottom"
        :y2="plot.bottom + 4"
        stroke="#cbd5e1"
        stroke-width="1"
      />
      <text
        :x="xForIndex(tick.index)"
        :y="plot.bottom + 16"
        font-size="10"
        fill="#64748b"
        :text-anchor="tick.index === 0 ? 'start' : tick.index === points.length - 1 ? 'end' : 'middle'"
      >
        {{ tick.label }}
      </text>
    </g>

    <polyline
      v-if="polyline"
      :points="polyline"
      fill="none"
      :stroke="stroke"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>
