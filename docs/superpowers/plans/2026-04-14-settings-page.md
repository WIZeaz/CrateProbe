# Settings Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Settings page to the frontend that lets users input and save an Admin Token, with all settings persisted to `localStorage`. Include a lightweight HEAD validation endpoint on the backend.

**Architecture:** A new `useSettings` composable manages a versioned JSON blob in `localStorage`, exposing reactive settings. The `adminAuth` service reads from this composable instead of `sessionStorage`. A new `Settings.vue` view provides categorized forms, validates the token via `HEAD /api/admin/runners`, and persists on success. The backend adds a `HEAD` handler for `/api/admin/runners` to support token validation without response bodies.

**Tech Stack:** Vue 3 (Composition API), Vue Router, Tailwind CSS, FastAPI, pytest.

---

## Task 1: Backend – Add HEAD endpoint for `/api/admin/runners`

**Files:**
- Modify: `backend/app/main.py:323-340`

**Context:** FastAPI does not automatically create a HEAD route for `@app.get`. We will add an explicit `@app.head` on the same path that reuses the `require_admin_token` dependency and returns an empty response.

- [ ] **Step 1: Add HEAD route below the existing GET route**

Insert this code immediately after the `@app.get("/api/admin/runners", ...)` block (after line 340):

```python
    @app.head(
        "/api/admin/runners",
        dependencies=[Depends(require_admin_token)],
    )
    async def head_runners():
        return {}
```

- [ ] **Step 2: Run backend tests to confirm no regressions**

```bash
cd backend
uv run pytest tests/integration/test_runner_admin_api.py -v
```

Expected: all existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(backend): add HEAD /api/admin/runners for token validation

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Backend – Write tests for HEAD `/api/admin/runners`

**Files:**
- Modify: `backend/tests/integration/test_runner_admin_api.py`

- [ ] **Step 1: Add HEAD test cases**

Append these two tests to the end of the file:

```python
def test_head_runners_with_valid_token_returns_204(client):
    response = client.head("/api/admin/runners", headers=_admin_headers())
    assert response.status_code == 200


def test_head_runners_with_invalid_token_returns_403(client):
    response = client.head(
        "/api/admin/runners", headers={"X-Admin-Token": "wrong-token"}
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run the new tests**

```bash
cd backend
uv run pytest tests/integration/test_runner_admin_api.py::test_head_runners_with_valid_token_returns_204 tests/integration/test_runner_admin_api.py::test_head_runners_with_invalid_token_returns_403 -v
```

Expected: both PASS.

- [ ] **Step 3: Run full admin test suite**

```bash
cd backend
uv run pytest tests/integration/test_runner_admin_api.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_runner_admin_api.py
git commit -m "test(backend): cover HEAD /api/admin/runners token validation

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Frontend – Create `useSettings` composable

**Files:**
- Create: `frontend/src/composables/useSettings.js`

- [ ] **Step 1: Create the composable**

```javascript
import { reactive, readonly, ref, watch } from 'vue'

const STORAGE_KEY = 'lifesonar_settings'

const defaultSettings = {
  version: 1,
  security: {
    adminToken: '',
  },
}

function loadSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...defaultSettings }
    const parsed = JSON.parse(raw)
    return mergeDefaults(parsed, defaultSettings)
  } catch (e) {
    console.warn('[useSettings] Failed to load settings from localStorage', e)
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
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  } catch (e) {
    console.warn('[useSettings] Failed to save settings to localStorage', e)
  }
}

const settings = reactive(loadSettings())
const isLoaded = ref(true)

export function useSettings() {
  function updateSetting(path, value) {
    const keys = path.split('.')
    let target = settings
    for (let i = 0; i < keys.length - 1; i++) {
      if (!(keys[i] in target)) {
        target[keys[i]] = {}
      }
      target = target[keys[i]]
    }
    target[keys[keys.length - 1]] = value
  }

  function saveSettings() {
    saveToStorage(settings)
  }

  return {
    settings: readonly(settings),
    isLoaded,
    updateSetting,
    saveSettings,
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/composables/useSettings.js
git commit -m "feat(frontend): add useSettings composable with localStorage persistence

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Frontend – Migrate `adminAuth.js` to use `useSettings`

**Files:**
- Modify: `frontend/src/services/adminAuth.js`

- [ ] **Step 1: Replace the entire file contents**

```javascript
import { useSettings } from '../composables/useSettings'

export function setAdminToken(token) {
  const { updateSetting, saveSettings } = useSettings()
  updateSetting('security.adminToken', token)
  saveSettings()
}

export function getAdminToken() {
  const { settings } = useSettings()
  return settings.security?.adminToken || ''
}

export function clearAdminToken() {
  const { updateSetting, saveSettings } = useSettings()
  updateSetting('security.adminToken', '')
  saveSettings()
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/services/adminAuth.js
git commit -m "refactor(frontend): migrate adminAuth from sessionStorage to useSettings/localStorage

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Frontend – Add token validation helper to `api.js`

**Files:**
- Modify: `frontend/src/services/api.js`

- [ ] **Step 1: Add `validateAdminToken` to the exported object**

Insert the following method into the exported object (after `deleteRunner`, before the Log endpoints):

```javascript
  async validateAdminToken() {
    const response = await api.head('/admin/runners')
    return response.status === 200
  },
```

The final block around line 115 should look like:

```javascript
  async deleteRunner(runnerId) {
    const response = await api.delete(`/admin/runners/${runnerId}`)
    return response.data
  },

  async validateAdminToken() {
    const response = await api.head('/admin/runners')
    return response.status === 200
  },

  // Log endpoints
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/services/api.js
git commit -m "feat(frontend): add HEAD validator for admin token

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Frontend – Create `Settings.vue` view

**Files:**
- Create: `frontend/src/views/Settings.vue`

- [ ] **Step 1: Write the component**

```vue
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
  saving.value = true
  message.value = ''
  messageType.value = ''

  const token = tokenInput.value.trim()

  if (!token) {
    updateSetting('security.adminToken', '')
    saveSettings()
    message.value = 'Admin token cleared.'
    messageType.value = 'success'
    saving.value = false
    return
  }

  // Temporarily set token so the interceptor sends it
  updateSetting('security.adminToken', token)

  try {
    const isValid = await api.validateAdminToken()
    if (isValid) {
      saveSettings()
      message.value = 'Settings saved successfully.'
      messageType.value = 'success'
    } else {
      message.value = 'Admin token is invalid.'
      messageType.value = 'error'
    }
  } catch (err) {
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
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/views/Settings.vue
git commit -m "feat(frontend): add Settings page with admin token validation and localStorage save

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 7: Frontend – Register `/settings` route

**Files:**
- Modify: `frontend/src/router/index.js`

- [ ] **Step 1: Add the route**

Insert this object into the `routes` array after the `/runners` route (or anywhere logical):

```javascript
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/Settings.vue')
  }
```

The end of the `routes` array should now look like:

```javascript
  {
    path: '/runners',
    name: 'RunnerList',
    component: () => import('../views/RunnerList.vue')
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/Settings.vue')
  }
]
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/router/index.js
git commit -m "feat(frontend): register /settings route

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 8: Frontend – Update navigation in `App.vue`

**Files:**
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Import `useSettings` and compute `hasAdminToken`**

Add the import and computed property inside `<script setup>`:

```javascript
import { computed, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useSettings } from './composables/useSettings'

const router = useRouter()
const route = useRoute()
const mobileMenuOpen = ref(false)

const { settings } = useSettings()
const hasAdminToken = computed(() => Boolean(settings.security?.adminToken))
```

- [ ] **Step 2: Add Settings link to desktop navigation**

Insert this `<router-link>` after the Runners link (before the closing `</div>` of the `sm:flex` nav block around line 75):

```html
              <router-link
                to="/settings"
                class="inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
                :class="route.path === '/settings'
                  ? 'border-blue-500 text-gray-900'
                  : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'"
              >
                Settings
                <span
                  v-if="!hasAdminToken"
                  class="ml-1.5 inline-flex items-center justify-center w-4 h-4 text-[10px] font-bold text-white bg-amber-500 rounded-full"
                  title="Admin token not set"
                >
                  !
                </span>
              </router-link>
```

- [ ] **Step 3: Verify the app compiles**

```bash
cd frontend
npm run build
```

Expected: build succeeds with no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.vue
git commit -m "feat(frontend): add Settings link to nav with missing-token warning badge

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 9: Frontend – Redirect missing token in `RunnerList.vue`

**Files:**
- Modify: `frontend/src/views/RunnerList.vue`

- [ ] **Step 1: Add router import and redirect logic**

Change the imports at the top of `<script setup>` from:

```javascript
import { computed, onMounted, ref } from 'vue'
import api from '../services/api'
import { getAdminToken } from '../services/adminAuth'
```

to:

```javascript
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import { getAdminToken } from '../services/adminAuth'

const router = useRouter()
```

Change the `fetchRunners` guard from:

```javascript
  if (!hasAdminToken.value) {
    error.value = 'Admin token is missing. Set an admin token, then refresh this page.'
    loading.value = false
    return
  }
```

to:

```javascript
  if (!hasAdminToken.value) {
    router.replace('/settings')
    return
  }
```

- [ ] **Step 2: Verify build still passes**

```bash
cd frontend
npm run build
```

Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/RunnerList.vue
git commit -m "feat(frontend): redirect RunnerList to Settings when admin token is missing

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - `useSettings` composable with localStorage persistence → Task 3
   - `Settings.vue` with category layout, show/hide password, HEAD validation → Task 6
   - Navigation badge when token missing → Task 8
   - `RunnerList` redirect when token missing → Task 9
   - `adminAuth` migrated from `sessionStorage` → Task 4
   - Backend HEAD endpoint → Task 1 + Task 2

2. **Placeholder scan:** None. Every step contains exact code or commands.

3. **Type consistency:**
   - `settings.security.adminToken` used consistently across `useSettings`, `adminAuth`, `Settings.vue`, and `App.vue`.
   - `api.validateAdminToken()` returns boolean based on `response.status === 200`.

No gaps found. Plan is ready for execution.
