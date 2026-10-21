import test from 'node:test'
import assert from 'node:assert/strict'
import { highlightToml } from '../highlightToml.js'

test('highlightToml returns an empty string for empty input', () => {
  assert.equal(highlightToml(''), '')
  assert.equal(highlightToml(null), '')
})

test('highlightToml highlights keys and strings', () => {
  const html = highlightToml('name = "demo"')

  assert.match(html, /<span class="toml-key">name<\/span>/)
  assert.match(html, /<span class="toml-string">"demo"<\/span>/)
})

test('highlightToml highlights table headers and comments', () => {
  const html = highlightToml('[limit]\n# resource limits')

  assert.match(html, /<span class="toml-table">\[limit\]<\/span>/)
  assert.match(html, /<span class="toml-comment"># resource limits<\/span>/)
})

test('highlightToml preserves the gap before a trailing comment', () => {
  const html = highlightToml('[limit] # resource limits')

  assert.match(
    html,
    /<span class="toml-table">\[limit\]<\/span> <span class="toml-comment"># resource limits<\/span>/
  )
})

test('highlightToml highlights numbers, booleans and dates', () => {
  const html = highlightToml(
    'cpu = 4\nenabled = true\nstarted = 2026-10-21T14:30:00Z'
  )

  assert.match(html, /<span class="toml-number">4<\/span>/)
  assert.match(html, /<span class="toml-boolean">true<\/span>/)
  assert.match(html, /<span class="toml-date">2026-10-21T14:30:00Z<\/span>/)
})

test('highlightToml handles dotted keys and arrays', () => {
  const html = highlightToml('profile.release = ["a", "b"]')

  assert.match(html, /<span class="toml-key">profile\.release<\/span>/)
  assert.match(html, /<span class="toml-punct">\[<\/span>/)
  assert.match(html, /<span class="toml-string">"a"<\/span>/)
})

test('highlightToml does not treat a hash inside a string as a comment', () => {
  const html = highlightToml('url = "a#b" # trailing')

  assert.match(html, /<span class="toml-string">"a#b"<\/span>/)
  assert.match(html, /<span class="toml-comment"># trailing<\/span>/)
})

test('highlightToml escapes HTML before adding markup', () => {
  const html = highlightToml('cmd = "<script>alert(1)</script>"\n# <b>note</b>')

  assert.doesNotMatch(html, /<script>/)
  assert.doesNotMatch(html, /<b>/)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /&lt;b&gt;note&lt;\/b&gt;/)
})

test('highlightToml preserves line breaks', () => {
  const html = highlightToml('a = 1\nb = 2')

  assert.equal(html.split('\n').length, 2)
})
