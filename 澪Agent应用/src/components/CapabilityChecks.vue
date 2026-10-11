<script setup>
import { onMounted, onBeforeUnmount } from 'vue'
import { useCapabilityChecks } from '../composables/useCapabilityChecks.js'
const emit = defineEmits(['navigate'])
const { capabilities, busy, sending, error, confirmation, unsupported, refresh, prepare, confirm, cancel, dispose } = useCapabilityChecks()
const states = { unchecked: '未检查', running: '正在检查', passed: '检查通过', failed: '未通过',
  cancelled: '已取消', interrupted: '上次中断', stale: '需要重新验证' }
const levels = { unverified: '尚未取得执行证据', connection: '服务连接检查', inference: '实际推理检查' }
onMounted(refresh)
onBeforeUnmount(dispose)
</script>

<template>
  <section class="capability-checks" aria-label="能力实测">
    <div class="checks-heading"><div><h3>能力实测</h3><p>逐项验证实际执行结果；打开此页只读取历史状态。</p></div>
      <button type="button" :disabled="sending" @click="refresh">刷新状态</button></div>
    <p v-if="error" role="alert" class="checks-error">{{ error }}</p>
    <p v-if="unsupported" role="status">当前后端版本不支持能力实测，请更新完整的 Mio 程序后重试。</p>
    <div class="checks-grid">
      <article v-for="item in capabilities" :key="item.id">
        <div class="check-title"><strong>{{ item.label }}</strong><span :class="item.status">{{ states[item.status] || '未知状态' }}</span></div>
        <p>{{ item.description }}</p>
        <small>{{ levels[item.level] || levels.unverified }}<template v-if="item.duration_ms != null"> · {{ (item.duration_ms / 1000).toFixed(1) }} 秒</template></small>
        <small v-if="item.checked_at">检查于 {{ new Date(item.checked_at).toLocaleString() }}</small>
        <p v-if="item.message" role="status">{{ item.message }}</p>
        <small v-if="item.human_check_required">{{ item.id === 'qq' ? '消息实际送达需另行测试。' : '麦克风、听感和连续交互仍需真人确认。' }}</small>
        <div class="check-actions">
          <button v-if="item.can_cancel" type="button" :disabled="sending" @click="cancel(item)">取消检查</button>
          <button v-else type="button" :disabled="busy || sending" @click="prepare(item)">{{ item.id === 'qq' || item.id === 'comfyui' ? '检查连接' : '执行实测' }}</button>
          <button type="button" @click="emit('navigate', item.target)">打开对应设置</button>
        </div>
        <div v-if="confirmation?.id === item.id" class="check-confirm" role="group" :aria-label="`确认检查${item.label}`">
          <p>{{ item.description }}取消只停止本地等待；已提交的服务请求可能仍执行或计费。</p>
          <button type="button" :disabled="sending || busy" @click="confirm">确认执行</button>
          <button type="button" :disabled="sending" @click="confirmation = null">返回</button>
        </div>
      </article>
    </div>
    <p v-if="!capabilities.length && !error && !unsupported">正在读取能力状态…</p>
  </section>
</template>

<style scoped>
.capability-checks { margin: 16px 0; padding: 16px; border: 1px solid var(--line); border-radius: 12px; }
.checks-heading, .check-title, .check-actions { display: flex; gap: 10px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
h3 { margin: 0; font-size: calc(15px * var(--fs, 1)); }
p { font-size: calc(12px * var(--fs, 1)); line-height: 1.65; margin: 8px 0; }
small { display: block; font-size: calc(11px * var(--fs, 1)); line-height: 1.7; opacity: .8; }
.checks-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: 12px; margin-top: 12px; }
article { padding: 12px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; min-width: 0; overflow-wrap: anywhere; }
.check-title { font-size: calc(12px * var(--fs, 1)); }
.check-title span { font-size: calc(11px * var(--fs, 1)); }
.passed { color: var(--success); } .failed, .checks-error { color: var(--danger); } .stale, .interrupted { color: var(--warning); }
.check-actions { justify-content: flex-start; margin-top: 10px; }
button { padding: 6px 10px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface); color: inherit; font: inherit; font-size: calc(12px * var(--fs, 1)); cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.check-confirm { margin-top: 12px; border-top: 1px solid var(--line); padding-top: 5px; }
.check-confirm button { margin-right: 8px; }
</style>
