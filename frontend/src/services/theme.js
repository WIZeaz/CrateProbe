export const THEME_MODES = ['system', 'light', 'dark']
export const DEFAULT_THEME_MODE = 'system'
export const THEME_STORAGE_KEY = 'lifesonar_theme'
export const THEME_COLORS = {
  light: '#e8edeb',
  dark: '#0d1416',
}

export function normalizeThemeMode(value) {
  return THEME_MODES.includes(value) ? value : DEFAULT_THEME_MODE
}

export function resolveTheme(mode, systemPrefersDark) {
  const normalized = normalizeThemeMode(mode)
  if (normalized === 'system') {
    return systemPrefersDark ? 'dark' : 'light'
  }
  return normalized
}

export function loadThemeMode(storage) {
  if (!storage) {
    return DEFAULT_THEME_MODE
  }
  try {
    return normalizeThemeMode(storage.getItem(THEME_STORAGE_KEY))
  } catch {
    return DEFAULT_THEME_MODE
  }
}

export function saveThemeMode(storage, mode) {
  if (!storage) {
    return
  }
  try {
    storage.setItem(THEME_STORAGE_KEY, normalizeThemeMode(mode))
  } catch {
    // Storage can be unavailable (private mode, quota); theme still applies for this session.
  }
}

export function applyThemeToDocument(doc, theme) {
  const isDark = theme === 'dark'
  const root = doc.documentElement

  root.classList.toggle('dark', isDark)
  root.style.colorScheme = isDark ? 'dark' : 'light'

  const meta = doc.querySelector?.('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute('content', isDark ? THEME_COLORS.dark : THEME_COLORS.light)
  }
}
