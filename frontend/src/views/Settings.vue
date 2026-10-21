<script setup>
import { ref, watch } from 'vue'
import { useSettings } from '../composables/useSettings'
import api from '../services/api'
import PageHeader from '../components/ui/PageHeader.vue'

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
    <PageHeader
      title="Settings"
      description="Admin access for runner management."
    />

    <div class="grid grid-cols-1 gap-5 lg:grid-cols-4">
      <aside class="lg:col-span-1">
        <nav class="instrument-panel p-2" aria-label="Settings categories">
          <span
            class="flex min-h-10 items-center border-l-2 border-signal bg-black/[0.03] px-3 text-[13px] font-medium text-ink"
            aria-current="page"
          >
            Security
          </span>
        </nav>
      </aside>

      <section class="instrument-panel lg:col-span-3">
        <div class="panel-header">
          <h2 class="panel-title">Security</h2>
        </div>

        <div class="mt-4 max-w-xl space-y-4">
          <div>
            <label for="admin-token" class="ui-label">Admin token</label>
            <div class="relative">
              <input
                id="admin-token"
                v-model="tokenInput"
                :type="showToken ? 'text' : 'password'"
                placeholder="Enter your admin token"
                class="ui-input pr-20"
                :disabled="saving"
              />
              <button
                type="button"
                class="absolute right-2 top-1/2 -translate-y-1/2 px-2 py-1 text-[11px] font-medium text-muted hover:text-ink disabled:opacity-50"
                :disabled="saving"
                @click="showToken = !showToken"
              >
                {{ showToken ? 'Hide' : 'Show' }}
              </button>
            </div>
            <p class="mt-1 text-[11px] leading-relaxed text-muted">
              Used to access the Runners management API. Set the same value as
              <code class="inline-code">security.admin_token</code> in
              <code class="inline-code">config.toml</code>.
            </p>
          </div>

          <div
            v-if="message"
            class="notice"
            :class="messageType === 'success' ? 'notice--success' : 'notice--error'"
            role="status"
          >
            {{ message }}
          </div>

          <div class="pt-1">
            <button
              type="button"
              class="btn btn--primary"
              :disabled="saving"
              @click="handleSave"
            >
              <span v-if="saving" class="spinner border-white"></span>
              {{ saving ? 'Saving...' : 'Save settings' }}
            </button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
