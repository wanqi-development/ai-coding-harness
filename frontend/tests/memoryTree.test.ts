import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildMemoryTree,
  flattenMemoryTree,
  memoryFolderKeys,
} from '../src/lib/memoryTree.ts'

const documents = [
  { path: 'features/payment/refund.md', summary: '退款' },
  { path: 'features/payment/callback.md', summary: '回调' },
  { path: 'features/product/list.md', summary: '商品列表' },
  { path: 'architecture/overview.md', summary: '架构' },
]

test('memory paths become a nested folder tree', () => {
  const tree = buildMemoryTree(documents)

  assert.deepEqual(
    tree.map((node) => [node.name, node.kind]),
    [
      ['architecture', 'folder'],
      ['features', 'folder'],
    ],
  )
  const features = tree[1]
  assert.deepEqual(
    features.children.map((node) => node.name),
    ['payment', 'product'],
  )
  assert.deepEqual(
    features.children[0].children.map((node) => node.name),
    ['callback.md', 'refund.md'],
  )
})

test('collapsed folders hide all descendants', () => {
  const tree = buildMemoryTree(documents)
  const all = new Set(memoryFolderKeys(tree))
  const expanded = new Set(all)
  expanded.delete('features')

  const rows = flattenMemoryTree(tree, expanded)

  assert.deepEqual(
    rows.map((row) => row.label),
    [
      'architecture',
      'overview.md',
      'features',
    ],
  )
  assert.equal(rows.find((row) => row.label === 'features')?.expanded, false)
})

test('nested folders retain their level when expanded', () => {
  const tree = buildMemoryTree(documents)
  const expanded = new Set(memoryFolderKeys(tree))

  const rows = flattenMemoryTree(tree, expanded)
  const refund = rows.find((row) => row.label === 'refund.md')

  assert.equal(refund?.depth, 2)
  assert.equal(refund?.document?.summary, '退款')
})
