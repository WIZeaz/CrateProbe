<script setup>
import { ref, watch } from 'vue'
import { useSettings } from '../composables/useSettings'
import api from '../services/api'

const { settings, updateSetting, saveSettings } = useSettings()

const tokenInput = ref(settings.security?.adminToken || '')
const showToken = ref(false)
const saving = ref(false)
const message = ref('')
const messageType = ref('') // 'success' | 'error'

watch(() => settings.security?.adminToken, (newVal) => {
  if (newVal !== tokenInput.value) {
    tokenInput.value = newVal || ''
  }
})

async function handleSave() {
  if (saving.value) return
  saving.value = true
  message.value = ''
  messageType.value = ''

  const token = tokenInput.value.trim()
  const previousToken = settings.security?.adminToken || ''

  if (!token) {
    updateSetting('security.adminToken', '')
    saveSettings()
    message.value = 'Admin token cleared.'
    messageType.value = 'success'
    saving.value = false
    return
  }

  // Temporarily set token so the interceptor sends it for validation
  updateSetting('security.adminToken', token)

  try {
    const isValid = await api.validateAdminToken()
    if (isValid) {
      saveSettings()
      message.value = 'Settings saved successfully.'
      messageType.value = 'success'
    } else {
      updateSetting('security.adminToken', previousToken)
      saveSettings()
      message.value = 'Admin token is invalid.'
      messageType.value = 'error'
    }
  } catch (err) {
    updateSetting('security.adminToken', previousToken)
    saveSettings()
    if (err.response?.status === 403) {
      message.value = 'Admin token is invalid.'
      messageType.value = 'error'
    } else {
      message.value = 'Unable to reach server. Please try again.'
      messageType.value = 'error'
    }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-8">
      <h1 class="text-3xl font-bold text-gray-900">Settings</h1>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-4 gap-6">
      <!-- Category sidebar -->
      <aside class="lg:col-span-1 space-y-2">
        <div class="bento-card p-4 bg-blue-50 border-blue-200">
          <h2 class="font-semibold text-gray-900">Security</h2>
        </div>
      </aside>

      <!-- Form area -->
      <section class="bento-card lg:col-span-3">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Security</h2>

        <div class="space-y-4 max-w-xl">
          <div>
            <label for="admin-token" class="block text-sm font-medium text-gray-700 mb-1">
              Admin Token
            </label>
            <div class="relative">
              <input
                id="admin-token"
                v-model="tokenInput"
                :type="showToken ? 'text' : 'password'"
                placeholder="Enter your admin token"
                class="w-full px-3 py-2 pr-20 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                :disabled="saving"
              />
              <button
                type="button"
                @click="showToken = !showToken"
                class="absolute right-2 top-1/2 -translate-y-1/2 px-2 py-1 text-xs font-medium text-gray-600 hover:text-gray-900"
                :disabled="saving"
              >
                {{ showToken ? 'Hide' : 'Show' }}
              </button>
            </div>
            <p class="mt-1 text-xs text-gray-500">
              Used to access the Runners management API. Set the same value as
              <code class="bg-gray-100 px-1 rounded">security.admin_token</code> in
              <code class="bg-gray-100 px-1 rounded">config.toml</code>.
            </p>
          </div>

          <div
            v-if="message"
            class="px-3 py-2 rounded-lg text-sm"
            :class="messageType === 'success'
              ? 'bg-green-50 border border-green-200 text-green-800'
              : 'bg-red-50 border border-red-200 text-red-800'"
          >
            {{ message }}
          </div>

          <div class="pt-2">
            <button
              type="button"
              @click="handleSave"
              :disabled="saving"
              class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center gap-2"
            >
              <span v-if="saving" class="spinner border-white"></span>
              {{ saving ? 'Saving...' : 'Save Settings' }}
            </button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
