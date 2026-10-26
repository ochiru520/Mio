/** Read-only Vue/JS syntax and layer checks, using Vue's existing parser. */
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { babelParse, compileScript, parse } from 'vue/compiler-sfc'

function walk(node, visit) {
  if (!node || typeof node !== 'object') return
  if (Array.isArray(node)) { for (const item of node) walk(item, visit); return }
  if (typeof node.type === 'string' || typeof node.type === 'number') visit(node)
  for (const [key, value] of Object.entries(node)) {
    if (!['loc', 'start', 'end', 'tokens', 'comments'].includes(key)) walk(value, visit)
  }
}

function staticPrefix(node, bindings = new Map(), seen = new Set()) {
  if (node?.type === 'Identifier' && bindings.has(node.name) && !seen.has(node.name)) {
    return staticPrefix(bindings.get(node.name), bindings, new Set([...seen, node.name]))
  }
  if (node?.type === 'StringLiteral') return node.value
  if (node?.type === 'TemplateLiteral') return node.quasis[0]?.value?.cooked || ''
  if (node?.type === 'BinaryExpression' && node.operator === '+') return staticPrefix(node.left, bindings, seen)
  return ''
}

function callName(node, bindings = new Map()) {
  if (node?.type === 'Identifier') return node.name
  if (['MemberExpression', 'OptionalMemberExpression'].includes(node?.type)) {
    return node.computed ? staticPrefix(node.property, bindings) : node.property?.name
  }
  return ''
}

export function inspectSource(source, path) {
  const findings = []
  let ast
  try {
    if (path.endsWith('.vue')) {
      const { descriptor, errors } = parse(source, { filename: path })
      if (errors.length) return errors.map(error => ({ path, line: error.loc?.start?.line || 1, rule: 'syntax', detail: String(error.message || error) }))
      const compiled = descriptor.script || descriptor.scriptSetup ? compileScript(descriptor, { id: 'architecture' }) : {}
      ast = [...(compiled.scriptAst || []), ...(compiled.scriptSetupAst || [])]
      // Event handlers can bypass script-only checks; inspect their expressions too.
      walk(descriptor.template?.ast, node => {
        if (node.type === 7 && node.name === 'on' && node.exp?.content) {
          try { ast.push(babelParse(node.exp.content, { sourceType: 'module' })) }
          catch { /* Vue build validates handler expressions with its template parser. */ }
        }
      })
    } else {
      ast = [babelParse(source, { sourceType: 'module' })]
    }
  } catch (error) { return [{ path, line: error.loc?.line || 1, rule: 'syntax', detail: error.message }] }
  const transportNames = new Set(['fetch', 'request', 'apiRequest', 'EventSource', 'WebSocket', 'XMLHttpRequest', 'sendBeacon'])
  const bindings = new Map()
  walk(ast, node => {
    if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier' && node.init) bindings.set(node.id.name, node.init)
    if (node.type === 'VariableDeclarator' && node.id.type === 'ObjectPattern') {
      for (const property of node.id.properties) {
        const name = property.key?.name || property.key?.value
        if (transportNames.has(name) && property.value?.type === 'Identifier') transportNames.add(property.value.name)
      }
    }
  })
  walk(ast, node => {
    if (node.type !== 'ImportDeclaration') return
    if (/\/services\/api\.js$/.test(node.source.value)) {
      for (const specifier of node.specifiers) {
        if (specifier.imported?.name === 'apiRequest') {
          transportNames.add(specifier.local.name)
        }
      }
    }
    if (/^(services|composables|state)\//.test(path) && (node.source.value.endsWith('.vue') || node.source.value.includes('/components/'))) {
      findings.push({ path, line: node.loc.start.line, rule: 'view-layer', detail: node.source.value })
    }
  })
  for (let pass = 0; pass < bindings.size; pass++) {
    for (const [name, value] of bindings) {
      if (transportNames.has(callName(value, bindings))) transportNames.add(name)
    }
  }
  const isView = !path.startsWith('services/')
  if (!isView) return findings
  walk(ast, node => {
    if (!['CallExpression', 'OptionalCallExpression', 'NewExpression'].includes(node.type)) return
    const name = callName(node.callee, bindings)
    if (!transportNames.has(name)) return
    const prefix = staticPrefix(node.arguments[0], bindings)
    const settingsBoundary = prefix.startsWith('/api/settings/') || path === 'composables/useSettings.js'
    findings.push({ path, line: node.loc.start.line, rule: settingsBoundary ? 'settings-layer' : 'transport-layer', detail: prefix || 'dynamic transport request' })
  })
  return findings
}

export function violations(findings) {
  // Transport extraction is complete. Legacy budgets must never suppress a finding.
  return [...findings]
}

export function inspectTransportBudget(budget) {
  return Object.keys(budget.transportLimits || {}).map(path => ({
    path: 'scripts/architecture-debt.json', line: 1, rule: 'transport-budget',
    detail: `Transport exceptions are forbidden: ${path}`,
  }))
}

export function inspectTree(root) {
  const findings = []
  const visit = directory => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name)
      if (entry.isDirectory()) visit(path)
      else if (/\.(js|vue)$/.test(entry.name) && !entry.name.endsWith('.test.js')) {
        findings.push(...inspectSource(readFileSync(path, 'utf8'), relative(root, path).replaceAll('\\', '/')))
      }
    }
  }
  visit(root)
  return findings
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const directory = dirname(fileURLToPath(import.meta.url))
  const root = resolve(process.argv[2] || join(directory, '../src'))
  const budget = JSON.parse(readFileSync(join(directory, 'architecture-debt.json'), 'utf8'))
  const findings = inspectTree(root)
  const errors = [...violations(findings), ...inspectTransportBudget(budget)]
  for (const error of errors) console.error(`${error.path}:${error.line}: [${error.rule}] ${error.detail}`)
  console.log(`Architecture: ${errors.length} errors; legacy transport sites: ${findings.filter(item => item.rule === 'transport-layer').length}`)
  process.exitCode = errors.length ? 1 : 0
}
