import assert from 'node:assert/strict'
import test from 'node:test'
import { focusModal, restoreModalFocus, trapModalFocus } from './modalFocus.js'

function focusable(name, document) {
  return {
    name,
    hidden: false,
    isConnected: true,
    getAttribute: () => null,
    focus() { document.activeElement = this },
  }
}

test('弹窗优先聚焦 autofocus 元素', () => {
  const document = { activeElement: null }
  const automatic = focusable('automatic', document)
  const first = focusable('first', document)
  const container = {
    querySelector: (selector) => selector === '[autofocus]' ? automatic : null,
    querySelectorAll: () => [first],
  }
  assert.equal(focusModal(container), automatic)
  assert.equal(document.activeElement, automatic)
})

test('Tab 在弹窗首尾之间循环', () => {
  const document = { activeElement: null }
  const first = focusable('first', document)
  const last = focusable('last', document)
  const container = {
    ownerDocument: document,
    querySelectorAll: () => [first, last],
    contains: (element) => [first, last].includes(element),
  }
  let prevented = false
  document.activeElement = last
  trapModalFocus({ key: 'Tab', shiftKey: false, preventDefault: () => { prevented = true } }, container)
  assert.equal(prevented, true)
  assert.equal(document.activeElement, first)

  prevented = false
  trapModalFocus({ key: 'Tab', shiftKey: true, preventDefault: () => { prevented = true } }, container)
  assert.equal(prevented, true)
  assert.equal(document.activeElement, last)
})

test('弹窗关闭后只恢复仍连接的触发元素', () => {
  const document = { activeElement: null }
  const trigger = focusable('trigger', document)
  assert.equal(restoreModalFocus(trigger), true)
  assert.equal(document.activeElement, trigger)
  trigger.isConnected = false
  assert.equal(restoreModalFocus(trigger), false)
})
