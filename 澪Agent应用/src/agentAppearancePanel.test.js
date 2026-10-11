import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'

const { descriptor } = parse(fs.readFileSync(new URL('./components/AgentAppearancePanel.vue', import.meta.url), 'utf8'))
const compiled = compileScript(descriptor, { id: 'appearance-test', inlineTemplate: true, genDefaultAs: 'component' }).content
  .replace(/import \{([^}]+)\} from ['"]vue['"]/g, (_, names) => `const {${names.replace(/\bas\b/g, ':')}} = Vue`)
  .replace(/import \{ apiRequest \} from ['"][^'"]+['"]/, '')
const createComponent = new Function('Vue', 'apiRequest', 'localStorage', 'window', 'FileReader', 'Event', `${compiled}; return component`)

function mountPanel({ preferences = {}, context = {}, failure = '' } = {}) {
  const state = { preferences: { hero_shape: 'original', ...preferences }, failure, requests: [], events: [], reads: 0 }
  const makeNode = type => ({ type, props: {}, children: [], parent: null, text: '' })
  const renderer = Vue.createRenderer({
    createElement: makeNode,
    createText: text => ({ ...makeNode('text'), text }),
    createComment: text => ({ ...makeNode('comment'), text }),
    setText: (node, text) => { node.text = text },
    setElementText: (node, text) => { node.text = text; node.children = [] },
    patchProp: (node, key, previous, value) => { node.props[key] = value },
    parentNode: node => node.parent,
    nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1],
    insert(node, parent, anchor) {
      if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
      node.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      parent.children.splice(index < 0 ? parent.children.length : index, 0, node)
    },
    remove(node) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1) },
  })
  const api = async (url, options = {}) => {
    const method = options.method || 'GET'
    state.requests.push({ url, method, body: options.body && JSON.parse(options.body) })
    if (state.failure === method) throw new Error(`${method}失败`)
    if (method === 'PUT') Object.assign(state.preferences, JSON.parse(options.body))
    if (url.includes('/images/')) {
      const kind = url.split('/').at(-1)
      state.preferences[`${kind}_url`] = method === 'POST' ? `/api/agent/surface/images/${kind}` : ''
    }
    return { ...state.preferences }
  }
  class Reader {
    readAsDataURL() { state.reads++; this.result = 'data:image/png;base64,test'; this.onload() }
  }
  const component = createComponent({ ...Vue, vModelSelect: {} }, api, { setItem() {} }, { dispatchEvent: event => state.events.push(event.type) }, Reader, class { constructor(type) { this.type = type } })
  const root = makeNode('root')
  const chat = Vue.reactive({ avatarUrl: '/api/settings/avatar?v=7', ...context })
  const app = renderer.createApp(component).provide('mio-chat-page', chat)
  app.mount(root)
  const all = (node = root) => [node, ...node.children.flatMap(child => all(child))]
  const find = (type, label) => all().find(node => node.type === type && (!label || node.props['aria-label'] === label || node.props.alt === label || node.text === label))
  const image = kind => find('img', `${kind === 'hero' ? '首页问候图片' : '我的空间图片'}当前预览`)
  const row = kind => find('article', kind === 'hero' ? '首页问候图片' : '我的空间图片')
  const button = (kind, text) => all(row(kind)).find(node => node.type === 'button' && node.text === text)
  const flush = async () => { await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
  const upload = async (kind, file = { type: 'image/png', size: 1 }) => {
    const input = all(row(kind)).find(node => node.type === 'input')
    const event = { target: { files: [file], value: 'selected.png' } }
    await input.props.onChange(event); await flush()
    assert.equal(event.target.value, '')
  }
  return { state, chat, app, all, find, image, button, flush, upload }
}

test('Agent image previews use the same default avatars as the live surface', async () => {
  const panel = mountPanel()
  await panel.flush()
  assert.equal(panel.image('hero').props.src, '/api/settings/avatar?v=7')
  assert.equal(panel.image('personal').props.src, '/api/settings/avatar?v=7')
  assert.equal(panel.button('hero', '恢复默认').props.disabled, true)
  panel.chat.userAvatarCustom = true; panel.chat.userAvatarUrl = '/api/settings/user-avatar?v=9'
  await panel.flush()
  assert.equal(panel.image('personal').props.src, '/api/settings/user-avatar?v=9')
  assert.match(panel.image('personal').props.class, /circle/)
  panel.app.unmount()
})

test('upload and default reset refresh the actual preview and publish the saved change', async () => {
  const panel = mountPanel({ preferences: { hero_url: '/api/agent/surface/images/hero' } })
  await panel.flush()
  const first = panel.image('hero').props.src
  await panel.upload('hero')
  assert.notEqual(panel.image('hero').props.src, first, 'replacement at the same endpoint must reload')
  assert.equal(panel.state.requests.at(-1).body.data_url, 'data:image/png;base64,test')
  assert.equal(panel.state.events.at(-1), 'mio-agent-appearance')
  await panel.upload('personal')
  assert.match(panel.image('personal').props.src, /^\/api\/agent\/surface\/images\/personal\?v=/)
  await panel.button('hero', '恢复默认').props.onClick(); await panel.flush()
  assert.equal(panel.image('hero').props.src, '/api/settings/avatar?v=7')
  assert.equal(panel.button('hero', '恢复默认').props.disabled, true)
  panel.app.unmount()
})

test('failed image mutations retain current previews, and invalid files never upload', async () => {
  const panel = mountPanel({ preferences: { hero_url: '/custom.png' } })
  await panel.flush()
  const before = panel.image('hero').props.src
  panel.state.failure = 'POST'; await panel.upload('hero')
  assert.equal(panel.image('hero').props.src, before)
  assert.equal(panel.find('p', 'POST失败').props.role, 'alert')
  panel.state.failure = 'DELETE'; await panel.button('hero', '恢复默认').props.onClick(); await panel.flush()
  assert.equal(panel.image('hero').props.src, before)
  const requests = panel.state.requests.length
  await panel.upload('hero', { type: 'text/plain', size: 1 })
  await panel.upload('hero', { type: 'image/png', size: 8 * 1024 * 1024 + 1 })
  assert.equal(panel.state.requests.length, requests)
  assert.equal(panel.state.events.length, 0)
  panel.app.unmount()
})

test('circle shape previews immediately, then restores the saved shape if saving fails', async () => {
  const panel = mountPanel()
  await panel.flush()
  const select = panel.find('select', '首页图片形状')
  select.props['onUpdate:modelValue']('circle')
  await select.props.onChange(); await panel.flush()
  assert.match(panel.image('hero').props.class, /circle/)
  assert.equal(panel.state.preferences.hero_shape, 'circle')
  panel.state.failure = 'PUT'
  select.props['onUpdate:modelValue']('original')
  await select.props.onChange(); await panel.flush()
  assert.match(panel.image('hero').props.class, /circle/)
  assert.equal(panel.find('p', 'PUT失败').props.role, 'alert')
  panel.app.unmount()
})

test('failed settings load allows retry without displaying guessed current preferences', async () => {
  const panel = mountPanel({ failure: 'GET' })
  await panel.flush()
  assert.equal(panel.image('hero'), undefined)
  assert.equal(panel.button('hero', '恢复默认').props.disabled, true)
  panel.state.failure = ''
  await panel.find('button', '重新读取图片设置').props.onClick(); await panel.flush()
  assert.ok(panel.image('hero'))
  panel.app.unmount()
})

test('broken custom and default previews offer a retry instead of silently substituting images', async () => {
  const panel = mountPanel({ preferences: { hero_url: '/custom.png' } })
  await panel.flush()
  const before = panel.image('hero').props.src
  panel.image('hero').props.onError(); panel.image('personal').props.onError(); await panel.flush()
  assert.equal(panel.image('hero'), undefined)
  assert.equal(panel.image('personal'), undefined)
  panel.button('hero', '重试预览').props.onClick(); panel.button('personal', '重试预览').props.onClick(); await panel.flush()
  assert.notEqual(panel.image('hero').props.src, before)
  assert.equal(panel.image('personal').props.src, '/api/settings/avatar?v=7')
  assert.equal(panel.state.requests.length, 1, 'retrying preview must not overwrite preferences')
  panel.app.unmount()
})
