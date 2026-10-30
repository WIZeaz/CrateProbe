import test from 'node:test'
import assert from 'node:assert/strict'

import {
  THEME_COLORS,
  THEME_STORAGE_KEY,
  applyThemeToDocument,
  loadThemeMode,
  resolveTheme,
  saveThemeMode,
} from '../theme.js'

function createMemoryStorage() {
  const data = new Map()
  return {
    getItem: key => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, value),
  }
}

test('system mode follows the operating system preference', () => {
  assert.equal(resolveTheme('system', true), 'dark')
  assert.equal(resolveTheme('system', false), 'light')
})

test('unknown mode falls back to the system preference', () => {
  assert.equal(resolveTheme('sepia', true), 'dark')
  assert.equal(resolveTheme(null, false), 'light')
})

test('explicit light and dark modes override the system preference', () => {
  assert.equal(resolveTheme('dark', false), 'dark')
  assert.equal(resolveTheme('light', true), 'light')
})

test('theme mode is persisted and restored', () => {
  const storage = createMemoryStorage()

  assert.equal(loadThemeMode(storage), 'system')
  saveThemeMode(storage, 'dark')
  assert.equal(storage.getItem(THEME_STORAGE_KEY), 'dark')
  assert.equal(loadThemeMode(storage), 'dark')
})

test('unavailable storage or invalid stored values fall back to system', () => {
  const throwingStorage = {
    getItem() {
      throw new Error('denied')
    },
    setItem() {
      throw new Error('denied')
    },
  }

  assert.equal(loadThemeMode(throwingStorage), 'system')
  assert.doesNotThrow(() => saveThemeMode(throwingStorage, 'dark'))
  assert.equal(loadThemeMode({ getItem: () => 'sepia' }), 'system')
  assert.equal(loadThemeMode(null), 'system')
})

test('applying a theme updates the document class, color scheme and theme-color meta', () => {
  const classes = new Set()
  const meta = {
    content: '',
    setAttribute(name, value) {
      this[name] = value
    },
  }
  const document = {
    documentElement: {
      classList: {
        toggle(name, force) {
          if (force) {
            classes.add(name)
          } else {
            classes.delete(name)
          }
        },
      },
      style: {},
    },
    querySelector: selector =>
      selector === 'meta[name="theme-color"]' ? meta : null,
  }

  applyThemeToDocument(document, 'dark')
  assert.ok(classes.has('dark'))
  assert.equal(document.documentElement.style.colorScheme, 'dark')
  assert.equal(meta.content, THEME_COLORS.dark)

  applyThemeToDocument(document, 'light')
  assert.ok(!classes.has('dark'))
  assert.equal(document.documentElement.style.colorScheme, 'light')
  assert.equal(meta.content, THEME_COLORS.light)
})
