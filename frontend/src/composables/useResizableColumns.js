import { ref, onMounted } from 'vue'

/**
 * Composable for draggable column resizing.
 *
 * @param {Array<{key: string, width: number, minWidth?: number, resizable?: boolean}>} columns
 * @param {string} storageKey - localStorage key for persisting widths
 * @returns {{ columnWidths: import('vue').Ref<Record<string, number>>, startResize: (key: string, event: PointerEvent) => void }}
 */
export function useResizableColumns(columns, storageKey) {
  const defaults = Object.fromEntries(
    columns.map((column) => [column.key, column.width])
  )
  const minWidths = Object.fromEntries(
    columns.map((column) => [column.key, column.minWidth || 40])
  )
  const resizable = Object.fromEntries(
    columns.map((column) => [column.key, column.resizable !== false])
  )

  const columnWidths = ref({ ...defaults })

  function saveWidths() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(columnWidths.value))
    } catch (err) {
      console.warn('Failed to save column widths:', err)
    }
  }

  function loadWidths() {
    try {
      const saved = localStorage.getItem(storageKey)
      if (saved) {
        const parsed = JSON.parse(saved)
        columnWidths.value = { ...defaults, ...parsed }
      }
    } catch (err) {
      console.warn('Failed to load column widths:', err)
    }
  }

  onMounted(loadWidths)

  /**
   * @param {string} key
   * @param {PointerEvent} event
   */
  function startResize(key, event) {
    if (!resizable[key]) {
      return
    }

    const startX = event.clientX
    const startWidth = columnWidths.value[key]
    const minWidth = minWidths[key]

    function onPointerMove(moveEvent) {
      const delta = moveEvent.clientX - startX
      columnWidths.value[key] = Math.max(minWidth, startWidth + delta)
    }

    function onPointerUp() {
      document.removeEventListener('pointermove', onPointerMove)
      document.removeEventListener('pointerup', onPointerUp)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      saveWidths()
    }

    document.addEventListener('pointermove', onPointerMove)
    document.addEventListener('pointerup', onPointerUp)
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
  }

  return {
    columnWidths,
    startResize,
  }
}
