const TABLE_RE = /^\[\[?.+\]\]?$/
const KEY_RE =
  /^(\s*)((?:"(?:\\.|[^"\\])*"|'[^']*'|[A-Za-z0-9_-]+)(?:\s*\.\s*(?:"(?:\\.|[^"\\])*"|'[^']*'|[A-Za-z0-9_-]+))*)(\s*=\s*)(.*)$/

const TOKEN_RE =
  /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'[^']*'|\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})?)?|\d{2}:\d{2}:\d{2}(?:\.\d+)?|[+-]?(?:0x[0-9A-Fa-f_]+|0o[0-7_]+|0b[01_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?)|\b(?:true|false)\b|[\[\]{},=])/g

function escapeHtml(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function splitComment(line) {
  let quote = null
  let escaped = false

  for (let index = 0; index < line.length; index += 1) {
    const character = line[index]

    if (quote) {
      if (quote === '"' && character === '\\' && !escaped) {
        escaped = true
        continue
      }
      if (character === quote && !escaped) {
        quote = null
      }
      escaped = false
      continue
    }

    if (character === '"' || character === "'") {
      quote = character
      continue
    }

    if (character === '#') {
      return [line.slice(0, index), line.slice(index)]
    }
  }

  return [line, '']
}

function highlightValue(value) {
  return value.replace(TOKEN_RE, token => {
    let className = 'toml-punct'

    if (token.startsWith('"') || token.startsWith("'")) {
      className = 'toml-string'
    } else if (/^(?:true|false)$/.test(token)) {
      className = 'toml-boolean'
    } else if (/^(?:\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2})/.test(token)) {
      className = 'toml-date'
    } else if (/^[+-]?(?:0x|0o|0b|\d)/.test(token)) {
      className = 'toml-number'
    }

    return `<span class="${className}">${token}</span>`
  })
}

function highlightLine(line) {
  const [code, comment] = splitComment(line)
  const trimmed = code.trim()
  let html = ''

  if (trimmed && TABLE_RE.test(trimmed) && !trimmed.includes('=')) {
    const leading = code.match(/^\s*/)[0]
    const trailing = code.slice(leading.length + trimmed.length)
    html = `${leading}<span class="toml-table">${trimmed}</span>${trailing}`
  } else {
    const keyMatch = code.match(KEY_RE)

    if (keyMatch) {
      const [, leading, key, separator, value] = keyMatch
      html =
        `${leading}<span class="toml-key">${key}</span>` +
        `<span class="toml-punct">${separator}</span>` +
        highlightValue(value)
    } else {
      html = highlightValue(code)
    }
  }

  if (comment) {
    html += `<span class="toml-comment">${comment}</span>`
  }

  return html
}

export function highlightToml(source) {
  if (!source) {
    return ''
  }

  const normalized = String(source).replace(/\r\n?/g, '\n')
  return escapeHtml(normalized).split('\n').map(highlightLine).join('\n')
}
