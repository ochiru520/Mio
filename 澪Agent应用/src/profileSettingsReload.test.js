import assert from 'node:assert/strict'
import test from 'node:test'
import { useSettings } from './composables/useSettings.js'

function setup() {
  const profile = { identity: { name: 'Mio' }, preferences: { custom_notes: [] }, speaking_style: { avoid: [] } }
  const sandbox = {
    mioProfileBusy: { value: false }, mioProfileReady: { value: true },
    mioProfileDraft: { value: structuredClone(profile) },
    mioProfileNotesDraft: { value: '' }, mioProfileAvoidDraft: { value: '' },
    savedMioProfile: { value: JSON.stringify({ profile, notes: '', avoid: '' }) },
    profileAvatarCustom: { value: false }, userAvatarCustom: { value: false }, chatBackgroundCustom: { value: false },
    profileAvatarNonce: { value: 0 }, userAvatarNonce: { value: 0 }, chatBackgroundNonce: { value: 0 },
    activeView: { value: 'settings' }, activeSettingsSection: { value: 'profile' },
    settingsFeedback: { value: { section: '', message: '' } }, errorMessage: { value: '' },
    serializeSettings: JSON.stringify, showSettingsFeedback: () => {},
  }
  sandbox.isSettingsSectionDirty = () => sandbox.mioProfileReady.value && JSON.stringify({
    profile: sandbox.mioProfileDraft.value, notes: sandbox.mioProfileNotesDraft.value, avoid: sandbox.mioProfileAvoidDraft.value,
  }) !== sandbox.savedMioProfile.value
  return { sandbox, profile, actions: useSettings(sandbox) }
}

test('background profile refresh preserves an existing unsaved draft', async t => {
  const state = setup()
  state.sandbox.mioProfileDraft.value.identity.name = '未保存的名字'
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ profile: state.profile }))
  await state.actions.loadMioProfileSettings({ quiet: true })
  assert.equal(state.sandbox.mioProfileDraft.value.identity.name, '未保存的名字')
  assert.equal(fetch.mock.callCount(), 0)
})

test('a late profile response cannot erase edits made while the read was pending', async t => {
  const state = setup()
  let finishRead
  t.mock.method(globalThis, 'fetch', () => new Promise(resolve => { finishRead = resolve }))
  const pending = state.actions.loadMioProfileSettings({ quiet: true })
  state.sandbox.mioProfileDraft.value.identity.name = '读取期间输入的名字'
  finishRead(Response.json({ profile: state.profile }))
  await pending
  assert.equal(state.sandbox.mioProfileDraft.value.identity.name, '读取期间输入的名字')
  assert.equal(state.sandbox.mioProfileBusy.value, false)
  assert.equal(state.sandbox.isSettingsSectionDirty(), true)
})

test('explicit cancel restores the saved profile and clears the dirty draft', async t => {
  const state = setup()
  state.sandbox.mioProfileDraft.value.identity.name = '要取消的名字'
  t.mock.method(globalThis, 'fetch', async () => Response.json({ profile: state.profile }))
  await state.actions.resetActiveSettings()
  assert.equal(state.sandbox.mioProfileDraft.value.identity.name, 'Mio')
  assert.equal(state.sandbox.isSettingsSectionDirty(), false)
})

test('cancel during a pending background read restores the saved draft immediately', async t => {
  const state = setup()
  let finishRead
  t.mock.method(globalThis, 'fetch', () => new Promise(resolve => { finishRead = resolve }))
  const pending = state.actions.loadMioProfileSettings({ quiet: true })
  state.sandbox.mioProfileDraft.value.identity.name = '读取中要取消的名字'
  await state.actions.resetActiveSettings()
  const cancelledName = state.sandbox.mioProfileDraft.value.identity.name
  const cancelledDirty = state.sandbox.isSettingsSectionDirty()
  finishRead(Response.json({ profile: state.profile }))
  await pending
  assert.equal(cancelledName, 'Mio')
  assert.equal(cancelledDirty, false)
  assert.equal(state.sandbox.mioProfileDraft.value.identity.name, 'Mio')
})

test('public analysis expression persists in shared profile and survives reload', async t => {
  const state = setup()
  const style = '用自然中文短段落说明进度，保留称呼和角色语气。'
  state.sandbox.mioProfileDraft.value.speaking_style.analysis_style = style
  state.sandbox.memoryData = { value: {} }
  let stored
  state.sandbox.request = async (url, options) => {
    assert.equal(url, '/api/settings/profile')
    stored = JSON.parse(options.body).profile
    return { profile: structuredClone(stored) }
  }
  const storageDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { setItem() {} } })
  t.after(() => storageDescriptor ? Object.defineProperty(globalThis, 'localStorage', storageDescriptor) : delete globalThis.localStorage)
  assert.equal(state.sandbox.isSettingsSectionDirty(), true)
  assert.equal(await state.actions.saveMioProfileSettings(), true)
  assert.equal(stored.speaking_style.analysis_style, style)
  assert.equal(state.sandbox.memoryData.value.profile.speaking_style.analysis_style, style)
  t.mock.method(globalThis, 'fetch', async () => Response.json({ profile: structuredClone(stored) }))
  await state.actions.loadMioProfileSettings({ quiet: true })
  assert.equal(state.sandbox.mioProfileDraft.value.speaking_style.analysis_style, style)
  assert.equal(state.sandbox.isSettingsSectionDirty(), false)
})
