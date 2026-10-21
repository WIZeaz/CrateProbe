import test from 'node:test'
import assert from 'node:assert/strict'
import { buildFileTree } from '../taskFiles.js'

test('buildFileTree returns an empty tree for missing entries', () => {
  assert.deepEqual(buildFileTree(), [])
  assert.deepEqual(buildFileTree([]), [])
})

test('buildFileTree creates implicit folders and sorts folders first', () => {
  const tree = buildFileTree([
    { path: 'task.toml', size: 120, is_dir: false },
    { path: 'scripts/run.sh', size: 20, is_dir: false },
    { path: 'assets/logo.svg', size: 8, is_dir: false },
    { path: 'assets/icon.txt', size: 4, is_dir: false },
  ])

  assert.deepEqual(
    tree.map(node => [node.name, node.isDir, node.path]),
    [
      ['assets', true, 'assets'],
      ['scripts', true, 'scripts'],
      ['task.toml', false, 'task.toml'],
    ]
  )

  const assets = tree[0]
  assert.deepEqual(
    assets.children.map(node => [node.name, node.isDir, node.size]),
    [
      ['icon.txt', false, 4],
      ['logo.svg', false, 8],
    ]
  )
  assert.equal(tree[1].children[0].name, 'run.sh')
  assert.equal(tree[1].children[0].size, 20)
})

test('buildFileTree keeps explicit empty folders', () => {
  const tree = buildFileTree([
    { path: 'assets', size: 0, is_dir: true },
    { path: 'assets/icon.txt', size: 4, is_dir: false },
    { path: 'empty', size: 0, is_dir: true },
  ])

  assert.deepEqual(
    tree.map(node => [node.name, node.isDir, node.children.length]),
    [
      ['assets', true, 1],
      ['empty', true, 0],
    ]
  )
})

test('buildFileTree creates nested folders and normalizes slashes', () => {
  const tree = buildFileTree([
    { path: '/nested/deep/file.txt', size: 10, is_dir: false },
    { path: 'logs/', size: 0, is_dir: true },
  ])

  assert.deepEqual(
    tree.map(node => node.name),
    ['logs', 'nested']
  )
  assert.equal(tree[1].children[0].name, 'deep')
  assert.equal(tree[1].children[0].children[0].name, 'file.txt')
  assert.equal(tree[1].children[0].children[0].size, 10)
})

test('buildFileTree ignores empty paths and sorts files by name', () => {
  const tree = buildFileTree([
    { path: '', size: 1, is_dir: false },
    { path: 'z.txt', size: 1, is_dir: false },
    { path: 'A.txt', size: 2, is_dir: false },
  ])

  assert.deepEqual(
    tree.map(node => node.name),
    ['A.txt', 'z.txt']
  )
})
