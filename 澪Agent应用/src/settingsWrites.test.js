import assert from 'node:assert/strict'
import test from 'node:test'
import { useSettings } from './composables/useSettings.js'
import { saveRuntimeSettings } from './services/settingsApi.js'

function fixture() {
  const feedback = []
  const deps = {
    mioProfileBusy: { value: false }, mioProfileReady: { value: true },
    errorMessage: { value: '' },
    profileAvatarCustom: { value: false }, profileAvatarNonce: { value: 0 },
    userAvatarCustom: { value: false }, userAvatarNonce: { value: 0 },
    chatBackgroundCustom: { value: false }, chatBackgroundNonce: { value: 0 },
    isImageAttachment: file => file.type.startsWith('image/'),
    readFileAsDataUrl: async () => 'data:image/png;base64,dGVzdA==',
    showSettingsFeedback: (section, type, message) => feedback.push({ section, type, message }),
    mioProfileDraft: { value: { identity: { name: ' Mio ' }, preferences: {}, speaking_style: {} } },
    mioProfileNotesDraft: { value: ' 一条偏好\n\n 第二条 ' },
    mioProfileAvoidDraft: { value: ' 避免重复\r\n ' },
    savedMioProfile: { value: 'saved-before-request' },
    memoryData: { value: {} }, serializeSettings: JSON.stringify,
  }
  return { deps, feedback, actions: useSettings(deps) }
}

function uploadEvent(file = { type: 'image/png', size: 1024 }) {
  return { target: { files: [file], value: 'selected.png' } }
}

const images = [
  { kind: 'profile', endpoint: '/api/settings/avatar', prefix: 'profileAvatar', section: 'profile' },
  { kind: 'user-avatar', endpoint: '/api/settings/user-avatar', prefix: 'userAvatar', section: 'appearance' },
  { kind: 'background', endpoint: '/api/settings/chat-background', prefix: 'chatBackground', section: 'appearance' },
]
const upload = (actions, kind, event) => kind === 'profile'
  ? actions.uploadProfileAvatar(event)
  : actions.uploadAppearanceImage(event, kind)
const reset = (actions, kind) => kind === 'profile'
  ? actions.resetProfileAvatar()
  : actions.resetAppearanceImage(kind)

for (const { kind, endpoint, prefix, section } of images) {
  test(`${kind} uploads and resets through the shared settings API`, async t => {
    const { deps, actions, feedback } = fixture()
    const requests = []
    t.mock.method(globalThis, 'fetch', async (url, options) => {
      requests.push({ url, method: options.method, body: options.body })
      return Response.json({ ok: true })
    })
    const event = uploadEvent()
    await upload(actions, kind, event)
    assert.equal(event.target.value, '')
    assert.equal(deps[`${prefix}Custom`].value, true)
    assert.ok(deps[`${prefix}Nonce`].value > 0)
    assert.equal(deps.mioProfileBusy.value, false)
    assert.deepEqual(requests[0], { url: endpoint, method: 'POST', body: JSON.stringify({ data_url: 'data:image/png;base64,dGVzdA==' }) })
    assert.equal(feedback.at(-1).section, section)
    assert.equal(feedback.at(-1).type, 'success')
    await reset(actions, kind)
    assert.deepEqual(requests[1], { url: endpoint, method: 'DELETE', body: undefined })
    assert.equal(deps[`${prefix}Custom`].value, false)
    assert.equal(deps.mioProfileBusy.value, false)
    await reset(actions, kind)
    assert.equal(requests.length, 2)
  })

  test(`${kind} rejects invalid images before sending any settings write`, async t => {
    const { actions, feedback } = fixture()
    const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('unexpected request') })
    await upload(actions, kind, uploadEvent({ type: 'text/plain', size: 10 }))
    assert.match(feedback.at(-1).message, /图片文件/)
    await upload(actions, kind, uploadEvent({ type: 'image/png', size: 12 * 1024 * 1024 + 1 }))
    assert.match(feedback.at(-1).message, /12MB/)
    assert.equal(fetch.mock.callCount(), 0)
  })

  test(`${kind} HTTP failures keep image state and release the busy flag`, async t => {
    const { deps, actions, feedback } = fixture()
    t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: '图片写入失败' }, { status: 500 }))
    await upload(actions, kind, uploadEvent())
    assert.equal(deps[`${prefix}Custom`].value, false)
    assert.equal(deps[`${prefix}Nonce`].value, 0)
    assert.equal(deps.errorMessage.value, '图片写入失败')
    assert.equal(deps.mioProfileBusy.value, false)
    assert.equal(feedback.at(-1).type, 'error')
    deps[`${prefix}Custom`].value = true
    await reset(actions, kind)
    assert.equal(deps[`${prefix}Custom`].value, true)
    assert.equal(deps[`${prefix}Nonce`].value, 0)
    assert.equal(deps.mioProfileBusy.value, false)
    assert.equal(feedback.at(-1).type, 'error')
  })
}

test('profile save normalizes a copy, persists the server snapshot and publishes the shared update', async t => {
  const { deps, actions, feedback } = fixture()
  const updates = []
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { setItem: (...args) => updates.push(args) } })
  t.after(() => descriptor ? Object.defineProperty(globalThis, 'localStorage', descriptor) : delete globalThis.localStorage)
  let sent
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/settings/profile')
    assert.equal(options.method, 'PATCH')
    sent = JSON.parse(options.body)
    assert.equal(deps.mioProfileDraft.value.identity.name, ' Mio ')
    return Response.json(sent)
  })
  assert.equal(await actions.saveMioProfileSettings(), true)
  assert.equal(sent.profile.identity.name, 'Mio')
  assert.deepEqual(sent.profile.preferences.custom_notes, ['一条偏好', '第二条'])
  assert.deepEqual(sent.profile.speaking_style.avoid, ['避免重复'])
  assert.deepEqual(deps.memoryData.value.profile, sent.profile)
  assert.deepEqual(JSON.parse(deps.savedMioProfile.value), { profile: sent.profile, notes: '一条偏好\n第二条', avoid: '避免重复' })
  assert.equal(updates[0][0], 'mio_profile_updated')
  assert.equal(feedback.at(-1).type, 'success')
  assert.equal(deps.mioProfileBusy.value, false)
})

test('profile HTTP failure preserves the original draft and saved snapshot', async t => {
  const { deps, actions, feedback } = fixture()
  const originalDraft = deps.mioProfileDraft.value
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: '人格保存失败' }, { status: 500 }))
  assert.equal(await actions.saveMioProfileSettings(), false)
  assert.equal(deps.mioProfileDraft.value, originalDraft)
  assert.equal(deps.savedMioProfile.value, 'saved-before-request')
  assert.equal(deps.mioProfileNotesDraft.value, ' 一条偏好\n\n 第二条 ')
  assert.equal(deps.errorMessage.value, '人格保存失败')
  assert.deepEqual(deps.memoryData.value, {})
  assert.equal(feedback.at(-1).type, 'error')
  assert.equal(deps.mioProfileBusy.value, false)
})

test('profile validation and busy guards do not send settings writes', async t => {
  const { deps, actions } = fixture()
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('unexpected request') })
  deps.mioProfileDraft.value.identity.name = ' '
  assert.equal(await actions.saveMioProfileSettings(), false)
  assert.equal(deps.errorMessage.value, '显示名字不能为空')
  assert.equal(deps.mioProfileDraft.value.identity.name, ' ')
  assert.equal(deps.mioProfileBusy.value, false)
  deps.mioProfileBusy.value = true
  assert.equal(await actions.saveMioProfileSettings(), false)
  assert.equal(fetch.mock.callCount(), 0)
})

test('settings API preserves structured revision conflicts for callers', async t => {
  const detail = { message: '设置已被其他窗口更新', revision: 'new-revision' }
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail }, { status: 409 }))
  await assert.rejects(saveRuntimeSettings({ qq_proactive_enabled: true }, { revision: 'old-revision' }), error => {
    assert.equal(error.code, 'http_error')
    assert.equal(error.status, 409)
    assert.deepEqual(error.detail, detail)
    assert.equal(error.message, detail.message)
    return true
  })
})
