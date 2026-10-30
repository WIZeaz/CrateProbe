import { computed, readonly, ref, watch } from 'vue'
import {
  applyThemeToDocument,
  loadThemeMode,
  normalizeThemeMode,
  resolveTheme,
  saveThemeMode,
} from '../services/theme'

const storage = typeof window !== 'undefined' ? window.localStorage : null
const mode = ref(loadThemeMode(storage))
const systemPrefersDark = ref(false)

const mediaQuery =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null

if (mediaQuery) {
  systemPrefersDark.value = mediaQuery.matches

  const handleSystemChange = event => {
    systemPrefersDark.value = event.matches
  }

  if (typeof mediaQuery.addEventListener === 'function') {
    mediaQuery.addEventListener('change', handleSystemChange)
  } else if (typeof mediaQuery.addListener === 'function') {
    // Safari < 14
    mediaQuery.addListener(handleSystemChange)
  }
}

// Intentional module-level singleton: every consumer shares the same theme state.
const resolvedTheme = computed(() =>
  resolveTheme(mode.value, systemPrefersDark.value)
)

// Keep the document in sync with both manual choices and OS changes.
watch(
  resolvedTheme,
  theme => {
    if (typeof document !== 'undefined') {
      applyThemeToDocument(document, theme)
    }
  },
  { immediate: true, flush: 'sync' }
)

function setMode(nextMode) {
  mode.value = normalizeThemeMode(nextMode)
  saveThemeMode(storage, mode.value)
}

export function useTheme() {
  return {
    mode: readonly(mode),
    resolvedTheme,
    setMode,
  }
}
