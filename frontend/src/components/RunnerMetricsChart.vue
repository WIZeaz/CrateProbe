<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  RESOURCE_FIELDS,
  buildXTicks,
  clampValue,
  computeTooltipPosition,
  computeDomainMaxY,
  formatHoverLabel,
  nearestIndexFromX,
  resolvePointTimestamp,
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
const tooltipWidth = 150
const tooltipHeight = 44
const svgRef = ref(null)
const renderedWidth = ref(width)
const hoverIndex = ref(null)
const hoverPointer = ref(null)
let resizeObserver = null

const plot = {
  left: 32,
  right: width - 4,
  top: 4,
  bottom: height - 20,
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

const hoverData = computed(() => {
  if (hoverIndex.value == null || hoverIndex.value < 0 || hoverIndex.value >= props.points.length) {
    return null
  }

  const point = props.points[hoverIndex.value]
  const rawValue = point?.[props.field] ?? 0
  const clampedValue = clampValue(rawValue, domainMaxY.value)
  const x = xForIndex(hoverIndex.value)
  const y = yForValue(clampedValue)

  const { timeText } = formatHoverLabel({
    index: hoverIndex.value,
    timestamp: resolvePointTimestamp(point),
  })

  const valueText = isResourceField.value ? `${clampedValue.toFixed(1)}%` : `${clampedValue}`
  const anchor = hoverPointer.value ?? { x, y }
  const tooltip = computeTooltipPosition({
    x: anchor.x,
    y: anchor.y,
    chartWidth: width,
    chartHeight: height,
    tipWidth: tooltipWidth,
    tipHeight: tooltipHeight,
    gap: 10,
  })

  return {
    x,
    y,
    timeText,
    valueText,
    tooltip,
  }
})

function pointerToChartCoordinates(event) {
  if (!svgRef.value) {
    return null
  }

  const pt = svgRef.value.createSVGPoint()
  pt.x = event.clientX
  pt.y = event.clientY

  const ctm = svgRef.value.getScreenCTM()
  if (!ctm) {
    return null
  }

  const svgPt = pt.matrixTransform(ctm.inverse())
  return { x: svgPt.x, y: svgPt.y }
}

function handleHoverMove(event) {
  if (!props.points.length) {
    hoverIndex.value = null
    hoverPointer.value = null
    return
  }

  const pointer = pointerToChartCoordinates(event)
  if (!pointer) {
    return
  }

  hoverPointer.value = {
    x: Math.min(plot.right, Math.max(plot.left, pointer.x)),
    y: Math.min(plot.bottom, Math.max(plot.top, pointer.y)),
  }

  hoverIndex.value = nearestIndexFromX({
    x: hoverPointer.value.x,
    plotLeft: plot.left,
    plotWidth: plot.width,
    count: props.points.length,
  })
}

function clearHover() {
  hoverIndex.value = null
  hoverPointer.value = null
}
</script>

<template>
  <svg ref="svgRef" :viewBox="`0 0 ${width} ${height}`" class="w-full h-32">
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

    <g v-if="hoverData">
      <line
        :x1="hoverData.x"
        :x2="hoverData.x"
        :y1="plot.top"
        :y2="plot.bottom"
        stroke="#94a3b8"
        stroke-width="1"
        stroke-dasharray="3 3"
      />
      <circle :cx="hoverData.x" :cy="hoverData.y" r="3.5" :fill="stroke" stroke="#ffffff" stroke-width="1.5" />
      <g>
        <rect
          :x="hoverData.tooltip.x"
          :y="hoverData.tooltip.y"
          :width="tooltipWidth"
          :height="tooltipHeight"
          rx="6"
          fill="#0f172a"
          fill-opacity="0.95"
        />
        <text :x="hoverData.tooltip.x + 8" :y="hoverData.tooltip.y + 16" font-size="10" fill="#cbd5e1">
          {{ hoverData.timeText }}
        </text>
        <text :x="hoverData.tooltip.x + 8" :y="hoverData.tooltip.y + 33" font-size="12" font-weight="600" fill="#f8fafc">
          {{ hoverData.valueText }}
        </text>
      </g>
    </g>

    <rect
      v-if="points.length"
      :x="plot.left"
      :y="plot.top"
      :width="plot.width"
      :height="plot.height"
      fill="transparent"
      @pointermove="handleHoverMove"
      @pointerleave="clearHover"
      @pointerout="clearHover"
      @pointerup="clearHover"
      @pointercancel="clearHover"
    />
  </svg>
</template>
