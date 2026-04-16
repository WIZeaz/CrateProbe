import { reactive, readonly } from 'vue'

const STORAGE_KEY = 'lifesonar_settings'
const LEGACY_ADMIN_TOKEN_KEY = 'admin_token'

const defaultSettings = {
  version: 1,
  security: {
    adminToken: '',
  },
}

function loadSettings() {
  if (typeof window === 'undefined') {
    return { ...defaultSettings }
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      const legacyToken = sessionStorage.getItem(LEGACY_ADMIN_TOKEN_KEY) || ''
      return mergeDefaults({ security: { adminToken: legacyToken } }, defaultSettings)
    }
    const parsed = JSON.parse(raw)
    return mergeDefaults(parsed, defaultSettings)
  } catch (e) {
    console.warn('[settings] Failed to load settings from localStorage', e)
    return { ...defaultSettings }
  }
}

function mergeDefaults(target, defaults) {
  if (typeof target !== 'object' || target === null) {
    return { ...defaults }
  }
  const result = {}
  for (const key of Object.keys(defaults)) {
    if (defaults[key] && typeof defaults[key] === 'object' && !Array.isArray(defaults[key])) {
      result[key] = mergeDefaults(target[key], defaults[key])
    } else {
      result[key] = key in target ? target[key] : defaults[key]
    }
  }
  return result
}

function saveToStorage(settings) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  } catch (e) {
    console.warn('[settings] Failed to save settings to localStorage', e)
  }
}

// Intentional module-level singleton: all consumers share the same settings state.
const settings = reactive(loadSettings())
export const readonlySettings = readonly(settings)

export function updateSetting(path, value) {
  const keys = path.split('.')
  let target = settings
  for (let i = 0; i < keys.length - 1; i++) {
    if (!(keys[i] in target)) {
      target[keys[i]] = {}
    }
    target = target[keys[i]]
    if (typeof target !== 'object' || target === null) {
      throw new Error(`[useSettings] Cannot set "${path}" because "${keys[i]}" is not an object`)
    }
  }
  target[keys[keys.length - 1]] = value
}

export function saveSettings() {
  saveToStorage(JSON.parse(JSON.stringify(settings)))
}
