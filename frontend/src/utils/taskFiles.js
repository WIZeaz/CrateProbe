function normalizePath(path) {
  return String(path || '').replace(/^\/+|\/+$/g, '')
}

export function buildFileTree(entries = []) {
  const root = new Map()

  for (const entry of entries) {
    const path = normalizePath(entry.path)
    if (!path) {
      continue
    }

    const parts = path.split('/').filter(Boolean)
    let children = root

    for (let index = 0; index < parts.length; index += 1) {
      const name = parts[index]
      const isLeaf = index === parts.length - 1

      if (!children.has(name)) {
        children.set(name, {
          name,
          path: parts.slice(0, index + 1).join('/'),
          isDir: !isLeaf,
          size: 0,
          children: new Map(),
        })
      }

      const node = children.get(name)
      if (isLeaf) {
        node.isDir = Boolean(entry.is_dir)
        node.size = node.isDir ? 0 : Math.max(0, Number(entry.size) || 0)
      }

      children = node.children
    }
  }

  return toNodes(root)
}

function toNodes(children) {
  return [...children.values()]
    .map(node => ({
      name: node.name,
      path: node.path,
      isDir: node.isDir || node.children.size > 0,
      size: node.isDir ? 0 : node.size,
      children: toNodes(node.children),
    }))
    .sort((left, right) => {
      if (left.isDir !== right.isDir) {
        return left.isDir ? -1 : 1
      }
      return left.name.localeCompare(right.name, undefined, {
        sensitivity: 'base',
        numeric: true,
      })
    })
}
