<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSettings } from './composables/useSettings'

const route = useRoute()
const mobileMenuOpen = ref(false)
const { settings } = useSettings()
const hasAdminToken = computed(() => Boolean(settings.security?.adminToken))

const navItems = [
  {
    to: '/dashboard',
    label: 'Dashboard',
    match: path => path === '/dashboard',
  },
  {
    to: '/queue',
    label: 'Queue',
    match: path => path === '/queue',
  },
  {
    to: '/jobs',
    label: 'Jobs',
    match: path =>
      path.startsWith('/jobs') && path !== '/jobs/new' && path !== '/jobs/batch',
  },
  {
    to: '/jobs/new',
    label: 'New job',
    match: path => path === '/jobs/new',
  },
  {
    to: '/jobs/batch',
    label: 'Batch jobs',
    match: path => path === '/jobs/batch',
  },
  {
    to: '/tasks',
    label: 'Tasks',
    match: path => path.startsWith('/tasks'),
  },
  {
    to: '/runners',
    label: 'Runners',
    match: path => path.startsWith('/runners'),
  },
  {
    to: '/settings',
    label: 'Settings',
    match: path => path === '/settings',
  },
]

function isActive(item) {
  return item.match(route.path)
}

function closeMobileMenu() {
  mobileMenuOpen.value = false
}

watch(
  () => route.path,
  () => {
    mobileMenuOpen.value = false
  }
)
</script>

<template>
  <div class="flex min-h-screen flex-col bg-bench text-ink">
    <nav class="sticky top-0 z-40 border-b border-rule bg-face">
      <div class="mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8">
        <div class="flex h-16 items-center justify-between gap-6">
          <div class="flex min-w-0 items-center gap-8">
            <router-link
              to="/dashboard"
              class="flex flex-none items-center gap-2.5 text-ink"
              aria-label="CrateProbe dashboard"
            >
              <svg
                class="h-4 w-4"
                viewBox="0 0 16 16"
                fill="none"
                aria-hidden="true"
              >
                <rect
                  x="2.25"
                  y="2.25"
                  width="11.5"
                  height="11.5"
                  rx="1"
                  stroke="currentColor"
                  stroke-width="1.2"
                />
                <circle cx="8" cy="8" r="2.25" class="text-signal" fill="currentColor" />
              </svg>
              <span class="text-[18px] font-semibold tracking-[-0.02em]">CrateProbe</span>
            </router-link>

            <div class="hidden lg:flex lg:items-center lg:gap-6">
              <router-link
                v-for="item in navItems"
                :key="item.to"
                :to="item.to"
                class="inline-flex h-16 items-center border-b-2 text-[13px] font-medium transition-colors"
                :class="
                  isActive(item)
                    ? 'border-signal text-ink'
                    : 'border-transparent text-muted hover:text-ink'
                "
              >
                {{ item.label }}
                <span
                  v-if="item.to === '/settings' && !hasAdminToken"
                  class="ml-1.5 h-1.5 w-1.5 rounded-full bg-warn"
                  title="Admin token not set"
                  aria-hidden="true"
                ></span>
                <span
                  v-if="item.to === '/settings' && !hasAdminToken"
                  class="sr-only"
                >
                  Admin token not set
                </span>
              </router-link>
            </div>
          </div>

          <button
            type="button"
            class="inline-flex h-10 w-10 items-center justify-center rounded-[3px] text-ink hover:bg-black/5 lg:hidden"
            :aria-expanded="mobileMenuOpen"
            aria-controls="mobile-nav"
            @click="mobileMenuOpen = !mobileMenuOpen"
          >
            <span class="sr-only">{{ mobileMenuOpen ? 'Close menu' : 'Open menu' }}</span>
            <span class="relative block h-4 w-4" aria-hidden="true">
              <span
                class="absolute left-0 h-px w-4 bg-ink transition-transform duration-150"
                :class="mobileMenuOpen ? 'top-2 rotate-45' : 'top-0.5'"
              ></span>
              <span
                class="absolute left-0 h-px w-4 bg-ink transition-transform duration-150"
                :class="mobileMenuOpen ? 'top-2 -rotate-45' : 'top-3'"
              ></span>
            </span>
          </button>
        </div>
      </div>

      <div
        v-if="mobileMenuOpen"
        id="mobile-nav"
        class="border-t border-rule bg-face lg:hidden"
      >
        <nav class="mx-auto w-full max-w-7xl px-4 py-2 sm:px-6">
          <router-link
            v-for="item in navItems"
            :key="item.to"
            :to="item.to"
            class="flex min-h-11 items-center border-l-2 py-2 pl-3 text-[13px] font-medium"
            :class="
              isActive(item)
                ? 'border-signal bg-black/[0.03] text-ink'
                : 'border-transparent text-muted hover:text-ink'
            "
            @click="closeMobileMenu"
          >
            {{ item.label }}
            <span
              v-if="item.to === '/settings' && !hasAdminToken"
              class="ml-1.5 h-1.5 w-1.5 rounded-full bg-warn"
              title="Admin token not set"
              aria-hidden="true"
            ></span>
            <span
              v-if="item.to === '/settings' && !hasAdminToken"
              class="sr-only"
            >
              Admin token not set
            </span>
          </router-link>
        </nav>
      </div>
    </nav>

    <main class="flex-1">
      <div class="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <router-view />
      </div>
    </main>

    <footer class="border-t border-rule bg-face">
      <div
        class="mx-auto flex w-full max-w-7xl flex-col gap-1 px-4 py-4 font-mono text-[11px] text-muted sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8"
      >
        <span>CrateProbe</span>
        <span>Automated Rust crate testing</span>
      </div>
    </footer>
  </div>
</template>
