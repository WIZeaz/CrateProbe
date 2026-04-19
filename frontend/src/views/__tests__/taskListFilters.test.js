import test from 'node:test'
import assert from 'node:assert/strict'

import { filterTasksByCrateName } from '../taskListFilters.js'

const TASKS = [
  { id: 1, crate_name: 'serde', status: 'pending' },
  { id: 2, crate_name: 'tokio', status: 'running' },
  { id: 3, crate_name: 'serde_json', status: 'completed' },
]

test('filterTasksByCrateName returns all tasks when query is empty', () => {
  assert.deepEqual(filterTasksByCrateName(TASKS, ''), TASKS)
  assert.deepEqual(filterTasksByCrateName(TASKS, '   '), TASKS)
})

test('filterTasksByCrateName matches crate_name case-insensitively', () => {
  assert.deepEqual(filterTasksByCrateName(TASKS, 'SERDE').map(t => t.id), [1, 3])
})

test('filterTasksByCrateName returns empty array when no match', () => {
  assert.deepEqual(filterTasksByCrateName(TASKS, 'axum'), [])
})
