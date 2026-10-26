import assert from 'node:assert/strict'
import test from 'node:test'
import { useAppDialog } from './composables/useAppDialog.js'

test('confirm exposes its options and settles only once with a boolean', async () => {
  const actions = useAppDialog()
  const pending = actions.showAppConfirm({ title: '删除记录？', message: '删除后无法恢复', confirmText: '删除', cancelText: '保留', danger: true })
  assert.equal(actions.appDialog.open, true)
  assert.equal(actions.appDialog.mode, 'confirm')
  assert.equal(actions.appDialog.title, '删除记录？')
  assert.equal(actions.appDialog.message, '删除后无法恢复')
  assert.equal(actions.appDialog.confirmText, '删除')
  assert.equal(actions.appDialog.cancelText, '保留')
  assert.equal(actions.appDialog.danger, true)
  actions.confirmAppDialog()
  actions.cancelAppDialog()
  assert.equal(await pending, true)
  assert.equal(actions.appDialog.open, false)
  assert.equal(actions.appDialog.resolve, null)
})

test('cancel resolves a confirm as false and a prompt as null', async () => {
  const actions = useAppDialog()
  const confirm = actions.showAppConfirm()
  actions.cancelAppDialog()
  assert.equal(await confirm, false)
  const prompt = actions.showAppPrompt({ title: '重命名', value: '原名字' })
  actions.cancelAppDialog()
  assert.equal(await prompt, null)
})

test('prompt submits the edited value including an intentional empty string', async () => {
  const actions = useAppDialog()
  const pending = actions.showAppPrompt({ title: '编辑笔记', value: '原内容', multiline: true })
  assert.equal(actions.appDialog.multiline, true)
  assert.equal(actions.appDialog.confirmText, '保存')
  actions.appDialog.value = '修改\n后的内容'
  actions.confirmAppDialog()
  assert.equal(await pending, '修改\n后的内容')
  const empty = actions.showAppPrompt({ title: '清空笔记', value: '原内容' })
  actions.appDialog.value = ''
  actions.confirmAppDialog()
  assert.equal(await empty, '')
})

test('successive dialogs reset prompt and danger flags and keep instance state separate', async () => {
  const first = useAppDialog()
  const second = useAppDialog()
  const prompt = first.showAppPrompt({ title: '笔记', value: '草稿', multiline: true })
  first.cancelAppDialog()
  await prompt
  const confirm = first.showAppConfirm({ danger: true })
  assert.equal(first.appDialog.value, '')
  assert.equal(first.appDialog.multiline, false)
  assert.equal(second.appDialog.open, false)
  first.confirmAppDialog()
  await confirm
  const nextPrompt = first.showAppPrompt({ title: '名字' })
  assert.equal(first.appDialog.danger, false)
  assert.equal(first.appDialog.cancelText, '取消')
  first.cancelAppDialog()
  await nextPrompt
})
