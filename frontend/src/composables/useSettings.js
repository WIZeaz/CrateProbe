import { readonlySettings, updateSetting, saveSettings } from '../services/settings'

// Thin Vue-facing wrapper around the global settings singleton.
// Returns the same readonly state and mutators for all callers.
export function useSettings() {
  return {
    settings: readonlySettings,
    updateSetting,
    saveSettings,
  }
}
