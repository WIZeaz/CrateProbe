import { readonlySettings, updateSetting, saveSettings } from './settings'

export function setAdminToken(token) {
  updateSetting('security.adminToken', token)
  saveSettings()
}

export function getAdminToken() {
  return readonlySettings.security?.adminToken || ''
}

export function clearAdminToken() {
  updateSetting('security.adminToken', '')
  saveSettings()
}
