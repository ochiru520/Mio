<script setup>
import { inject } from 'vue'
import PetAppearancePanel from './PetAppearancePanel.vue'
import { Check, Gamepad2, Monitor, Play, Power, RefreshCw, RotateCw, Sparkles, Volume2, Wifi } from '@lucide/vue'

const context = inject('mio-companion-page')
if (!context) throw new Error('桌宠页上下文未初始化')
</script>

<template>
  <section class="companion-hub">
    <nav class="companion-tab-bar compact-companion-tabs" aria-label="桌宠功能">
      <button type="button" :class="{ active: context.activeCompanionSection === 'pet-panel' }" @click="context.activeCompanionSection = 'pet-panel'"><Gamepad2 :size="16" />桌宠</button>
      <button type="button" :class="{ active: context.activeCompanionSection === 'voice-panel' }" @click="context.activeCompanionSection = 'voice-panel'"><Volume2 :size="16" />语音</button>
      <button type="button" :class="{ active: context.activeCompanionSection === 'screen-panel' }" @click="context.activeCompanionSection = 'screen-panel'"><Monitor :size="16" />观察</button>
    </nav>

    <PetAppearancePanel
      v-if="context.activeCompanionSection === 'pet-panel'"
      :status="context.companionStatus"
      :avatar-url="context.companionAvatarUrl"
      :display-name="context.mioDisplayName"
      :busy="Boolean(context.companionBusy)"
      control-mode
      @control="context.controlCompanion"
      @save="context.saveCompanionSettings('pet')"
      @save-size="context.saveCompanionSize"
      @manage="context.openSettingsSection('pet')"
    />

    <section v-else-if="context.activeCompanionSection === 'voice-panel'" class="companion-simple-panel voice-simple-panel">
      <header><div><Volume2 :size="19" /><span><h2>{{ context.mioDisplayName }}的语音</h2><p>运行、播放与常用朗读方式</p></span></div><span :class="['connection-label', { online: context.companionStatus.voice_runtime.service_running }]">{{ context.companionStatus.voice_runtime.service_running ? '在线' : '未启动' }}</span></header>
      <div class="voice-quick-grid">
        <label><span><strong>回复时朗读</strong><small>使用{{ context.mioDisplayName }}的专属音色</small></span><span class="switch-control"><input v-model="context.companionStatus.pet.settings.voice_enabled" type="checkbox" /><i /></span></label>
        <label><span><strong>主动消息朗读</strong><small>桌宠主动开口时播放</small></span><span class="switch-control"><input v-model="context.companionStatus.pet.settings.speak_proactive" type="checkbox" /><i /></span></label>
        <label><span><strong>观察回应朗读</strong><small>屏幕观察产生回应时播放</small></span><span class="switch-control"><input v-model="context.companionStatus.pet.settings.speak_screen_observations" type="checkbox" /><i /></span></label>
        <label><span><strong>流式播放</strong><small>生成一段，播放一段</small></span><span class="switch-control"><input v-model="context.companionStatus.pet.settings.voice_streaming_enabled" type="checkbox" /><i /></span></label>
      </div>
      <div class="voice-quick-settings"><label><span>音量</span><input v-model.number="context.companionStatus.pet.settings.voice_volume" type="range" min="0" max="100" :style="context.rangeInputStyle(context.companionStatus.pet.settings.voice_volume, 0, 100)" /><b>{{ context.companionStatus.pet.settings.voice_volume }}%</b></label></div>
      <div class="voice-runtime-summary"><div><strong>{{ context.mioDisplayName }}专属音色</strong><span>{{ context.companionStatus.voice_runtime.emotion_reference_ready ? `${context.companionStatus.voice_runtime.emotion_reference_count} 种情绪参考已就绪` : '情绪参考库未就绪' }}</span></div><p v-if="context.companionStatus.voice_runtime.last_error">{{ context.companionStatus.voice_runtime.last_error }}</p></div>
      <footer class="companion-action-bar"><button type="button" :disabled="Boolean(context.companionBusy)" @click="context.controlVoiceRuntime('start')"><Play :size="15" />启动</button><button type="button" :disabled="Boolean(context.companionBusy)" @click="context.controlVoiceRuntime('stop')"><Power :size="15" />停止</button><button type="button" :disabled="Boolean(context.companionBusy)" @click="context.controlVoiceRuntime('restart')"><RotateCw :size="15" />重启</button><button type="button" :disabled="Boolean(context.companionBusy)" @click="context.testCompanionVoice"><Volume2 :size="15" />试听</button><button class="primary-button" type="button" :disabled="Boolean(context.companionBusy)" @click="context.saveCompanionSettings('voice')"><Check :size="15" />应用修改</button></footer>
    </section>

    <section v-else class="companion-simple-panel observer-simple-panel">
      <header><div><Monitor :size="19" /><span><h2>屏幕观察</h2><p>选择{{ context.mioDisplayName }}现在能看到的范围与识别方式</p></span></div><span :class="['connection-label', { online: context.companionStatus.screen.running }]">{{ context.companionStatus.screen.running ? '观察中' : '未启动' }}</span></header>
      <div class="observer-choice-row"><div><strong>观察范围</strong><span>游戏窗口只上传选中的窗口</span></div><div class="observation-mode-switch"><button type="button" :class="{ active: context.observationMode === 'game' }" @click="context.observationMode = 'game'; context.loadGameWindows({ quiet: true })"><Gamepad2 :size="15" />游戏窗口</button><button type="button" :class="{ active: context.observationMode === 'screen' }" @click="context.observationMode = 'screen'"><Monitor :size="15" />整个屏幕</button></div></div>
      <div class="observer-choice-row"><div><strong>视觉方式</strong><span>本地视觉不上传画面，云端视觉响应更稳定</span></div><div class="observation-mode-switch"><button type="button" :class="{ active: context.companionStatus.pet.settings.screen_vision_route === 'local' }" @click="context.companionStatus.pet.settings.screen_vision_route = 'local'"><Monitor :size="15" />本地视觉</button><button type="button" :class="{ active: context.companionStatus.pet.settings.screen_vision_route === 'cloud' }" @click="context.companionStatus.pet.settings.screen_vision_route = 'cloud'"><Wifi :size="15" />云端视觉</button></div></div>
      <div v-if="context.observationMode === 'game'" class="observer-select-row"><select v-model="context.selectedGameHwnd"><option value="">选择要观察的游戏窗口</option><option v-for="item in context.gameWindows" :key="item.hwnd" :value="String(item.hwnd)">{{ item.title }}</option></select><button type="button" title="刷新窗口" @click="context.loadGameWindows()"><RefreshCw :class="{ spin: context.gameWindowsLoading }" :size="15" /></button></div>
      <div v-else class="observer-select-row"><select v-model="context.screenScope"><option value="primary">主屏幕</option><option value="all">全部屏幕</option></select></div>
      <div v-if="context.companionStatus.pet.settings.screen_vision_route === 'cloud'" class="observer-select-row"><select v-model="context.companionStatus.pet.settings.screen_vision_model_id"><option value="auto-fast">自动选择低延迟模型</option><option v-for="item in (context.companionStatus.screen_analysis?.vision_model_options || [])" :key="item.id" :value="item.id">{{ item.label }}</option></select></div>
      <footer class="companion-action-bar"><button type="button" :disabled="Boolean(context.companionBusy)" @click="context.controlObservation('start')"><Play :size="15" />开始观察</button><button type="button" :disabled="Boolean(context.companionBusy)" @click="context.controlObservation('stop')"><Power :size="15" />停止</button><button type="button" :disabled="Boolean(context.companionBusy)" @click="context.letMioSeeObservation"><Sparkles :size="15" />让{{ context.mioDisplayName }}看一眼</button><button class="primary-button" type="button" :disabled="Boolean(context.companionBusy)" @click="context.saveCompanionSettings('observation')"><Check :size="15" />应用修改</button></footer>
      <dl class="observer-diagnostics">
        <div><dt>观察对象</dt><dd>{{ context.companionStatus.screen.title || (context.observationMode === 'game' ? '尚未选择游戏' : context.screenScope === 'all' ? '全部屏幕' : '主屏幕') }}</dd></div>
        <div><dt>捕获方式</dt><dd>{{ context.companionStatus.screen.process_isolated ? '独立进程 · ' : '' }}{{ context.companionStatus.screen.capture_backend || '未确认' }}</dd></div>
        <div><dt>识别路线</dt><dd>{{ context.companionStatus.screen_analysis?.vision_route_label || (context.companionStatus.pet.settings.screen_vision_route === 'local' ? '本地视觉' : '云端视觉') }}</dd></div>
        <div><dt>视觉模型</dt><dd>{{ context.companionStatus.screen_analysis?.last_model || context.companionStatus.screen_analysis?.local_vision?.model || '等待首次分析' }}</dd></div>
        <div><dt>画面变化</dt><dd>{{ Number(context.companionStatus.screen.change_percent || 0).toFixed(1) }}%</dd></div>
        <div><dt>观察器状态</dt><dd>{{ context.companionStatus.screen.process_alive || context.companionStatus.screen.running ? '运行中' : '已停止' }}</dd></div>
        <div><dt>单轮超时</dt><dd>{{ context.companionStatus.screen_analysis?.request_timeout_seconds || context.companionStatus.pet.settings.screen_request_timeout_seconds || 25 }} 秒</dd></div>
        <div><dt>今日实扣</dt><dd>¥{{ Number(context.companionStatus.screen_analysis?.budget?.daily?.confirmed_cost_yuan || 0).toFixed(4) }}</dd></div>
        <div v-if="context.companionStatus.screen_analysis?.last_error"><dt>识别状态</dt><dd class="error-text">{{ context.companionStatus.screen_analysis.last_error }}</dd></div>
        <div v-if="context.companionStatus.screen?.capture_backend_error"><dt>捕获降级</dt><dd class="warning-text">{{ context.companionStatus.screen.capture_backend_error }}</dd></div>
      </dl>
    </section>
  </section>
</template>
