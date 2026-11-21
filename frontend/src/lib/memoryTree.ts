export interface MemoryTreeNode<T extends { path: string }> {
  key: string
  name: string
  kind: 'folder' | 'file'
  children: MemoryTreeNode<T>[]
  document?: T
}

export interface MemoryTreeRow<T extends { path: string }> {
  key: string
  label: string
  depth: number
  kind: 'folder' | 'file'
  expanded?: boolean
  document?: T
}

interface MutableNode<T extends { path: string }> {
  key: string
  name: string
  kind: 'folder' | 'file'
  children: Map<string, MutableNode<T>>
  document?: T
}

export function buildMemoryTree<T extends { path: string }>(
  documents: readonly T[],
): MemoryTreeNode<T>[] {
  const root = new Map<string, MutableNode<T>>()
  for (const document of documents) {
    const parts = document.path.split('/').filter(Boolean)
    if (!parts.length) continue
    let current = root
    let key = ''
    for (const folder of parts.slice(0, -1)) {
      key = key ? `${key}/${folder}` : folder
      let node = current.get(folder)
      if (!node) {
        node = { key, name: folder, kind: 'folder', children: new Map() }
        current.set(folder, node)
      }
      current = node.children
    }
    const filename = parts.at(-1)
    if (!filename) continue
    current.set(filename, {
      key: document.path,
      name: filename,
      kind: 'file',
      children: new Map(),
      document,
    })
  }
  return finalize(root)
}

export function memoryFolderKeys<T extends { path: string }>(
  nodes: readonly MemoryTreeNode<T>[],
): string[] {
  const keys: string[] = []
  for (const node of nodes) {
    if (node.kind === 'folder') keys.push(node.key, ...memoryFolderKeys(node.children))
  }
  return keys
}

export function flattenMemoryTree<T extends { path: string }>(
  nodes: readonly MemoryTreeNode<T>[],
  expanded: ReadonlySet<string>,
  depth = 0,
): MemoryTreeRow<T>[] {
  const rows: MemoryTreeRow<T>[] = []
  for (const node of nodes) {
    const isFolder = node.kind === 'folder'
    rows.push({
      key: node.key,
      label: node.name,
      depth,
      kind: node.kind,
      expanded: isFolder ? expanded.has(node.key) : undefined,
      document: node.document,
    })
    if (isFolder && expanded.has(node.key)) {
      rows.push(...flattenMemoryTree(node.children, expanded, depth + 1))
    }
  }
  return rows
}

function finalize<T extends { path: string }>(
  nodes: Map<string, MutableNode<T>>,
): MemoryTreeNode<T>[] {
  return [...nodes.values()]
    .sort((left, right) => {
      if (left.kind !== right.kind) return left.kind === 'folder' ? -1 : 1
      return left.name.localeCompare(right.name)
    })
    .map((node) => ({
      key: node.key,
      name: node.name,
      kind: node.kind,
      document: node.document,
      children: finalize(node.children),
    }))
}
