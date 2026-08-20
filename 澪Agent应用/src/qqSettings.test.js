import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const appSource = fs.readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const settingsSource = fs.readFileSync(new URL('./components/SettingsPage.vue', import.meta.url), 'utf8')

test('QQ account mismatch can enter forced QR mode instead of stopping at diagnosis', () => {
  assert.match(appSource, /if \(!result\.force_qr_login\)/)
  assert.doesNotMatch(appSource, /status\.logged_in && !status\.account_ready\) throw new Error/)
  assert.match(settingsSource, /切换账号二维码/)
  assert.doesNotMatch(settingsSource, /Boolean\(qqBusy\) \|\| qqStatus\.logged_in/)
})

test('QQ QR panel remains visible while resolving an account mismatch', () => {
  assert.match(settingsSource, /qqStatus\.diagnostic_code === 'account_mismatch'/)
  assert.match(settingsSource, /切换账号会先退出由 NapCat 管理的旧机器人 QQ/)
})
