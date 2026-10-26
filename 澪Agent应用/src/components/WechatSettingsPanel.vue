<script setup>
import * as wechatApi from '../services/wechatApi.js'

import { onBeforeUnmount, onMounted, ref } from 'vue'
import { MessageSquareText, QrCode, RefreshCw } from '../icons/index.js'

const status = ref({}), login = ref(null), busy = ref(false), error = ref(''), verify = ref('')
const labels = { wait: '用手机微信扫描二维码', scaned: '已扫码，请在手机上继续确认', confirmed: '绑定成功', expired: '二维码已过期，请重新获取', need_verifycode: '请输入手机微信显示的验证码', verify_code_blocked: '验证次数过多，请重新获取二维码', binded_redirect: '这个 Bot 已绑定，请重新获取二维码或在手机上检查绑定' }
let disposed = false, timer, pollController, loadController
async function load() {
  loadController?.abort(); loadController = new AbortController()
  try { status.value = await wechatApi.loadStatus({signal: loadController.signal}) }
  catch (cause) { if (!disposed && cause.code !== 'request_cancelled') error.value = cause.message }
}
function stopPolling() { clearTimeout(timer); pollController?.abort(); pollController = null }
async function start() {
  stopPolling(); busy.value = true; error.value = ''; verify.value = ''; login.value = null
  try {
    const result = await wechatApi.submitLogin({})
    if (disposed) return
    login.value = result; timer = setTimeout(poll, 500)
  } catch (cause) { if (!disposed) error.value = cause.message }
  finally { busy.value = false }
}
async function poll() {
  if (disposed || !login.value) return
  pollController = new AbortController()
  const id = login.value.login_id
  try {
    const result = await wechatApi.submitLoginStatus({ login_id: id, verify_code: verify.value }, {timeoutMs: 45000, signal: pollController.signal})
    if (disposed || login.value?.login_id !== id) return
    login.value.status = result.status
    if (result.status === 'confirmed') { login.value = null; await load(); return }
    if (['expired', 'need_verifycode', 'verify_code_blocked', 'binded_redirect'].includes(result.status)) return
    timer = setTimeout(poll, 1200)
  } catch (cause) {
    if (!disposed && cause.code !== 'request_cancelled') { error.value = cause.message; timer = setTimeout(poll, 5000) }
  }
}
async function change(enabled) {
  busy.value = true; error.value = ''
  try { status.value = await wechatApi.updateConnection({ enabled }, {}) }
  catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
async function forget() {
  busy.value = true; error.value = ''; stopPolling(); login.value = null
  try { status.value = await wechatApi.deleteConnection({}) }
  catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true; stopPolling(); loadController?.abort() })
</script>

<template>
  <div class="settings-section-block wechat-settings">
    <div class="settings-section-heading"><div><h2><MessageSquareText :size="18" /> 微信</h2><p>普通微信扫码绑定，在 Bot 会话里与澪聊天、发送文档和交办任务。</p></div>
      <button type="button" :disabled="busy" aria-label="刷新微信状态" @click="load"><RefreshCw :size="15" /></button></div>
    <div class="settings-list"><div class="settings-item"><span><strong>{{ status.connected ? '微信已连接' : status.configured ? '已绑定微信' : '尚未绑定' }}</strong><small>{{ status.message || '正在读取连接状态' }}</small></span><b :class="['connection-label', { online: status.connected }]">{{ status.paused ? '隐私暂停' : status.connected ? '在线' : status.enabled ? '连接中' : '未连接' }}</b></div></div>
    <p class="settings-note">使用腾讯 iLink Bot 接口，仅接受扫码账号的私聊。电脑需要开机并运行澪；无需公网端口。若微信提示不可用，请按微信端要求更新或开通对应功能。</p>
    <div class="settings-actions-row"><button class="primary-button" type="button" :disabled="busy || status.paused" @click="start"><QrCode :size="15" />{{ status.configured ? '重新扫码绑定' : '扫码连接微信' }}</button>
      <button v-if="status.configured" type="button" :disabled="busy" @click="change(!status.enabled)">{{ status.enabled ? '暂停微信' : '恢复连接' }}</button>
      <button v-if="status.configured" type="button" :disabled="busy" @click="forget">解除本机绑定</button></div>
    <div v-if="login" class="wechat-qr" aria-live="polite"><img v-if="!['expired', 'verify_code_blocked', 'binded_redirect'].includes(login.status)" :src="login.qr_image" alt="用手机微信扫描此二维码连接澪" /><p>{{ labels[login.status] || '等待微信确认' }}</p>
      <form v-if="login.status === 'need_verifycode'" @submit.prevent="poll"><label>手机验证码<input v-model.trim="verify" inputmode="numeric" maxlength="12" autocomplete="one-time-code" required /></label><button type="submit">确认验证码</button></form></div>
    <p v-if="error" class="wechat-error" role="alert">{{ error }}</p>
    <p class="settings-note">连接后先发“你好”，再发送“帮我把这份文档整理成报告”。任务会在电脑后台继续，结果回到当前微信会话。也可发送 <code>/任务 帮助</code> 查看操作方法。</p>
  </div>
</template>

<style scoped>
.wechat-settings h2{display:flex;align-items:center;gap:8px}.wechat-qr{margin:18px 0;padding:18px;background:var(--surface);border:1px solid var(--border);border-radius:10px;text-align:center}.wechat-qr img{width:224px;max-width:100%;height:auto;background:var(--surface-raised);padding:8px;border-radius:8px}.wechat-qr p{font-size:calc(13px * var(--fs,1));color:var(--text-muted)}.wechat-qr form{display:flex;justify-content:center;gap:10px;align-items:end;flex-wrap:wrap}.wechat-qr label{display:grid;gap:8px;text-align:left}.wechat-qr input{max-width:180px;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text)}.wechat-error{color:var(--danger);font-size:calc(13px * var(--fs,1))}.wechat-settings code{white-space:nowrap}
</style>
