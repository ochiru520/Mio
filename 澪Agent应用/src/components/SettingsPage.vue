<script>
import { inject } from 'vue'
import CharacterCardPanel from './CharacterCardPanel.vue'
import DependencyCenter from './DependencyCenter.vue'
import PetAppearancePanel from './PetAppearancePanel.vue'
import VoiceSettingsPanel from './VoiceSettingsPanel.vue'
import {
  Activity,
  Archive,
  Bot,
  CalendarDays,
  Check,
  Clock3,
  Download,
  Feather,
  FolderOpen,
  Heart,
  ImagePlus,
  KeyRound,
  LogIn,
  MessageSquareText,
  Monitor,
  Play,
  Plus,
  Power,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRound,
  Volume2,
  Wifi,
} from '@lucide/vue'

export default {
  name: 'SettingsPage',
  components: {
    Activity, Archive, Bot, CalendarDays, Check, Clock3, Download, Feather, FolderOpen,
    Heart, ImagePlus, KeyRound, LogIn, MessageSquareText, Monitor, CharacterCardPanel, DependencyCenter, PetAppearancePanel, Play, Plus,
    Power, RefreshCw, RotateCw, ShieldCheck, Sparkles, Trash2, UserRound, Volume2, VoiceSettingsPanel, Wifi,
  },
  setup() {
    const context = inject('mio-settings-page')
    if (!context) throw new Error('设置页上下文未初始化')
    return context
  },
  data() {
    return {
      modelSettingsTab: 'models',
      petSettingsTab: 'appearance',
    }
  },
  methods: {
    handleDependencyNavigate(target) {
      const section = target === 'settings-models' ? 'models' : 'pet'
      this.openSettingsSection(section)
    },
    openNapcatManualGuide() {
      window.open('https://napneko.pages.dev', '_blank', 'noopener')
    },
  },
}
</script>

<template>
  <section class="settings-page">
    <header class="settings-page-header">
      <div class="settings-page-title">
        <component :is="activeSettingsItem.icon" :size="20" />
        <div><h1>{{ activeSettingsItem.label }}</h1><p>{{ activeSettingsItem.description }}</p></div>
      </div>
      <span v-if="isSettingsSectionDirty(activeSettingsSection)" class="settings-unsaved">有未保存修改</span>
    </header>

    <div v-if="settingsFeedback.section === activeSettingsSection" :class="['settings-feedback', settingsFeedback.type]">{{ settingsFeedback.message }}</div>

    <section v-if="activeSettingsSection === 'general'" class="settings-content-section">
      <div class="settings-section-block">
        <h2>启动</h2>
        <div class="settings-list">
          <label class="settings-item"><span><strong>打开应用时主动打招呼</strong><small>每次真正启动应用后，由{{ mioDisplayName }}结合时间和最近记忆自然开场</small></span><span class="switch-control"><input v-model="startupGreetingEnabled" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>关闭窗口后留在后台</strong><small>关闭主窗口不会退出服务，可从系统托盘重新打开</small></span><span class="switch-control"><input v-model="desktopPreferencesDraft.close_to_background" type="checkbox" :disabled="!desktopPreferencesReady" /><i /></span></label>
          <label class="settings-item"><span><strong>开机启动</strong><small>写入 Windows 当前用户启动项，可随时关闭</small></span><span class="switch-control"><input v-model="desktopPreferencesDraft.windows_startup" type="checkbox" :disabled="!desktopPreferencesReady" /><i /></span></label>
          <label class="settings-item"><span><strong>默认打开页面</strong><small>下一次启动主应用时首先进入的页面</small></span><select v-model="appPreferencesDraft.default_open_page"><option value="home">首页</option><option value="chat">对话</option><option value="diaries">日记</option><option value="memory">记忆</option><option value="tasks">任务</option><option value="companion">桌宠</option></select></label>
        </div>
      </div>
      <div v-if="runtimeSettingsReady" class="settings-section-block">
        <h2>主动联系</h2>
        <div class="settings-list">
          <label class="settings-item"><span><strong>允许{{ mioDisplayName }}主动发消息</strong><small>应用开着时按空闲时间检查；QQ在线时同步发送到QQ</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.qq_proactive_enabled" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>主动消息间隔</strong><small>在最短与最长间隔之间，由 Mio 结合上下文决定是否联系</small></span><span class="settings-inline-fields"><input v-model.number="runtimeSettingsDraft.qq_proactive_min_idle_minutes" type="number" min="5" max="1440" /><b>至</b><input v-model.number="runtimeSettingsDraft.qq_proactive_max_idle_minutes" type="number" min="5" max="1440" /><b>分钟</b></span></label>
          <label class="settings-item"><span><strong>允许主动联系的时段</strong><small>时段外不主动打扰</small></span><span class="settings-inline-fields"><input v-model.number="runtimeSettingsDraft.qq_proactive_day_start_hour" type="number" min="0" max="23" /><b>时至</b><input v-model.number="runtimeSettingsDraft.qq_proactive_day_end_hour" type="number" min="0" max="23" /><b>时</b></span></label>
          <label class="settings-item"><span><strong>后台检查频率</strong><small>只检查是否达到联系条件，不代表每次都会调用模型</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.qq_proactive_check_seconds" type="number" min="30" max="3600" /><b>秒</b></span></label>
          <label class="settings-item"><span><strong>后台消息通知</strong><small>主窗口隐藏时，收到 Mio 的新消息会显示 Windows 通知</small></span><span class="switch-control"><input v-model="desktopPreferencesDraft.background_notifications" type="checkbox" :disabled="!desktopPreferencesReady" /><i /></span></label>
          <div class="settings-item"><span><strong>自动更新</strong><small>当前正式版不会静默下载或安装更新</small></span><b class="settings-readonly">未启用</b></div>
        </div>
      </div>
      <div class="settings-savebar"><span>启动行为和主动联系修改后保存生效</span><div><button type="button" :disabled="!isSettingsSectionDirty('general')" @click="resetActiveSettings">取消</button><button class="primary-button" type="button" :disabled="!isSettingsSectionDirty('general') || runtimeSettingsBusy || desktopPreferencesBusy" @click="saveActiveSettings"><Check :size="15" />保存</button></div></div>
    </section>

    <section v-else-if="activeSettingsSection === 'appearance'" class="settings-content-section">
      <div class="settings-section-block"><h2>外观</h2><div class="settings-list">
        <div class="settings-item theme-setting-item"><span><strong>主题</strong><small>点击即时预览，保存后在整个应用生效</small></span><div class="theme-picker-grid"><button v-for="theme in themeOptions" :key="theme.id" type="button" :class="['theme-picker-option', { active: appPreferencesDraft.theme === theme.id }]" @click="appPreferencesDraft.theme = theme.id"><i><b v-for="color in theme.colors" :key="color" :style="{ backgroundColor: color }" /></i><span><strong>{{ theme.label }}</strong><small>{{ theme.description }}</small></span><Check v-if="appPreferencesDraft.theme === theme.id" :size="14" /></button></div></div>
        <label class="settings-item"><span><strong>字体大小</strong><small>调整应用正文与控件的整体字号</small></span><select v-model="appPreferencesDraft.font_size"><option value="small">较小</option><option value="medium">标准</option><option value="large">较大</option></select></label>
        <label class="settings-item"><span><strong>轻量动画</strong><small>保留侧栏展开、悬停和状态切换的短动画</small></span><span class="switch-control"><input v-model="appPreferencesDraft.light_animations" type="checkbox" /><i /></span></label>
        <label class="settings-item"><span><strong>专注模式</strong><small>默认收起左右侧栏，让主内容获得更多空间</small></span><span class="switch-control"><input v-model="appPreferencesDraft.focus_mode" type="checkbox" /><i /></span></label>
      </div></div>
      <div class="settings-section-block"><h2>对话外观</h2><div class="appearance-resource-grid">
        <article class="appearance-resource-card">
          <img v-if="userAvatarCustom" class="appearance-user-preview" :src="userAvatarUrl" alt="用户头像预览" />
          <span v-else class="appearance-user-preview default"><UserRound :size="28" /></span>
          <div><strong>用户头像</strong><small>{{ userAvatarCustom ? '正在使用自定义头像' : '正在使用默认线框头像' }}</small><span><label><input type="file" accept="image/*" @change="uploadAppearanceImage($event, 'user')" /><ImagePlus :size="14" />更换头像</label><button type="button" :disabled="!userAvatarCustom || mioProfileBusy" @click="resetAppearanceImage('user')"><RotateCw :size="14" />恢复默认</button></span></div>
        </article>
        <article class="appearance-resource-card background-card">
          <div :class="['appearance-background-preview', { default: !chatBackgroundCustom }]" :style="chatBackgroundCustom ? { backgroundImage: `url(${chatBackgroundUrl})` } : {}"><ImagePlus v-if="!chatBackgroundCustom" :size="28" /></div>
          <div><strong>对话背景</strong><small>{{ chatBackgroundCustom ? '背景会覆盖消息区和输入框外侧' : '默认使用应用主题背景' }}</small><span><label><input type="file" accept="image/*" @change="uploadAppearanceImage($event, 'background')" /><ImagePlus :size="14" />选择背景</label><button type="button" :disabled="!chatBackgroundCustom || mioProfileBusy" @click="resetAppearanceImage('background')"><RotateCw :size="14" />恢复默认</button></span></div>
        </article>
      </div></div>
      <div class="settings-section-block"><h2>侧栏</h2><div class="settings-list">
        <label class="settings-item"><span><strong>默认显示左侧栏</strong><small>主导航仍可通过顶部图标随时打开</small></span><span class="switch-control"><input v-model="appPreferencesDraft.left_sidebar_visible" type="checkbox" /><i /></span></label>
        <label class="settings-item"><span><strong>鼠标悬停展开左侧栏</strong><small>收起状态下把鼠标移到侧栏即可查看完整名称</small></span><span class="switch-control"><input v-model="appPreferencesDraft.left_sidebar_hover_expand" type="checkbox" /><i /></span></label>
        <label class="settings-item"><span><strong>默认显示右侧栏</strong><small>右侧栏展示关系、今日状态与服务状态</small></span><span class="switch-control"><input v-model="appPreferencesDraft.right_sidebar_visible" type="checkbox" /><i /></span></label>
        <label class="settings-item"><span><strong>鼠标悬停展开右侧栏</strong><small>收起状态下悬停查看完整状态</small></span><span class="switch-control"><input v-model="appPreferencesDraft.right_sidebar_hover_expand" type="checkbox" /><i /></span></label>
        <label class="settings-item"><span><strong>记住侧栏状态</strong><small>保留上一次手动打开、关闭和固定状态</small></span><span class="switch-control"><input v-model="appPreferencesDraft.remember_sidebar_state" type="checkbox" /><i /></span></label>
      </div></div>
      <div class="settings-section-block"><h2>对话信息</h2><div class="settings-check-grid message-field-grid">
        <label v-for="item in visibilityOptions.filter((entry) => ['message_time','message_model','message_tokens','response_latency','message_cost','message_copy','message_play'].includes(entry.id))" :key="item.id" class="toggle-row"><input v-model="appPreferencesDraft.visibility[item.id]" type="checkbox" /><span>{{ item.label }}</span></label>
      </div></div>
      <div class="settings-savebar"><span>主题会即时预览；取消可恢复已保存状态</span><div><button type="button" :disabled="!isSettingsSectionDirty('appearance')" @click="resetActiveSettings">取消</button><button class="primary-button" type="button" :disabled="!isSettingsSectionDirty('appearance')" @click="saveActiveSettings"><Check :size="15" />保存</button></div></div>
    </section>

    <section v-else-if="activeSettingsSection === 'profile'" class="settings-content-section">
      <div class="settings-section-block"><h2>头像与名字</h2><template v-if="mioProfileReady && mioProfileDraft"><div class="profile-identity-editor">
        <img :src="avatarUrl" :alt="mioDisplayName" />
        <div><strong>{{ mioDisplayName }}</strong><span>{{ profileAvatarCustom ? '正在使用自定义头像' : '正在使用默认头像' }}</span><div><label class="profile-avatar-upload"><input type="file" accept="image/*" @change="uploadProfileAvatar" /><ImagePlus :size="15" />更换头像</label><button type="button" :disabled="!profileAvatarCustom || mioProfileBusy" @click="resetProfileAvatar"><RotateCw :size="14" />恢复默认</button></div></div>
      </div><div class="settings-form-grid profile-settings-grid profile-name-grid">
        <label><span>应用显示名字</span><input v-model.trim="mioProfileDraft.identity.name" type="text" maxlength="80" placeholder="例如：Mio" /><small>保存后同步到侧栏、对话名称和桌宠对话窗口</small></label>
        <label><span>年龄感</span><input v-model.trim="mioProfileDraft.identity.age_feel" type="text" maxlength="300" /></label>
      </div></template><div v-else class="settings-empty-state">正在读取人格设定</div></div>
      <CharacterCardPanel />
      <details v-if="mioProfileReady && mioProfileDraft" class="settings-section-block character-advanced-editor">
        <summary><strong>高级编辑</strong><small>完整人格文本、表达方式、行为与边界</small></summary>
        <div class="settings-section-block"><h2>人格与关系（完整编辑）</h2><div class="settings-form-grid profile-settings-grid">
          <label class="profile-wide-field"><span>核心身份</span><textarea v-model.trim="mioProfileDraft.identity.core" rows="3" maxlength="2000" /></label>
          <label class="profile-wide-field"><span>相处关系与亲密程度</span><textarea v-model.trim="mioProfileDraft.preferences.relationship_distance" rows="3" maxlength="2000" /></label>
          <label class="profile-wide-field"><span>对你的称呼</span><textarea v-model.trim="mioProfileDraft.preferences.user_address" rows="2" maxlength="1000" /></label>
        </div></div>
        <div class="settings-section-block"><h2>表达方式</h2><div class="settings-form-grid profile-settings-grid">
          <label class="profile-wide-field"><span>语气、性格与情绪表达</span><textarea v-model.trim="mioProfileDraft.speaking_style.tone" rows="4" maxlength="3000" /></label>
          <label class="profile-wide-field"><span>回复长度与拆分习惯</span><textarea v-model.trim="mioProfileDraft.speaking_style.bubble_style" rows="4" maxlength="3000" /></label>
          <label class="profile-wide-field"><span>避免的表达（一行一项）</span><textarea v-model="mioProfileAvoidDraft" rows="5" maxlength="10000" /></label>
        </div></div>
        <div class="settings-section-block"><h2>行为与边界</h2><div class="settings-form-grid profile-settings-grid">
          <label v-for="(value, key) in mioProfileDraft.behavior" :key="key" class="profile-wide-field"><span>{{ profileBehaviorLabels[key] || key }}</span><textarea v-model.trim="mioProfileDraft.behavior[key]" rows="3" maxlength="2000" /></label>
          <label class="profile-wide-field"><span>自定义属性与补充（一行一条）</span><textarea v-model="mioProfileNotesDraft" rows="7" maxlength="20000" /></label>
        </div></div>
        <details class="settings-details"><summary>开发者：查看原始 JSON</summary><pre>{{ JSON.stringify(mioProfileDraft, null, 2) }}</pre></details>
      </details>
      <div class="settings-savebar"><span>保存后从下一轮对话起生效，不授予密钥、命令或系统权限</span><div><button type="button" :disabled="!isSettingsSectionDirty('profile')" @click="resetActiveSettings">取消</button><button class="primary-button" type="button" :disabled="!mioProfileReady || !isSettingsSectionDirty('profile') || mioProfileBusy" @click="saveActiveSettings"><Check :size="15" />保存</button></div></div>
    </section>

    <section v-else-if="activeSettingsSection === 'conversation'" class="settings-content-section">
      <div class="settings-section-block"><h2>默认对话</h2><div class="settings-list">
        <label class="settings-item"><span><strong>默认模型</strong><small>自动模式会按话题难度选择已配置模型</small></span><select v-model="chatSettingsDraft.model_id" @change="handleSettingsModelChange"><option value="auto">自动选择</option><option v-for="model in modelOptions" :key="model.id" :value="model.id">{{ model.display_name || model.model }}</option></select></label>
        <label class="settings-item"><span><strong>思考方式</strong><small>只显示当前模型原生支持的档位</small></span><select v-model="chatSettingsDraft.reasoning_level"><option v-for="item in settingsReasoningOptions" :key="item.id" :value="item.id">{{ item.label }}</option></select></label>
        <label class="settings-item"><span><strong>对话语音</strong><small>应用对话气泡的小喇叭统一使用 Mio 音色朗读</small></span><select v-model="chatSettingsDraft.voice_language"><option value="auto">跟随原文</option><option value="zh">中文</option><option value="ja">日语</option></select></label>
        <div class="settings-item"><span><strong>当前上下文</strong><small>接近预算时自动压缩，保留最近消息与长期记忆</small></span><b class="settings-readonly">{{ contextUsageLabel }}</b></div>
      </div></div>
<template v-if="runtimeSettingsReady">
        <div class="settings-section-block"><h2>上下文与消息合并</h2><div class="settings-form-grid">
          <label><span>上下文最大字数</span><input v-model.number="runtimeSettingsDraft.chat_context_max_chars" type="number" min="4000" max="200000" step="1000" /></label>
          <label><span>压缩后保留消息</span><input v-model.number="runtimeSettingsDraft.chat_recent_keep_messages" type="number" min="4" max="100" /></label>
          <label><span>显示历史消息</span><input v-model.number="runtimeSettingsDraft.chat_history_limit" type="number" min="1" max="500" /></label>
          <label><span>原始历史上限</span><input v-model.number="runtimeSettingsDraft.chat_raw_history_limit" type="number" min="1" max="1000" /></label>
          <label><span>补充消息等待</span><input v-model.number="runtimeSettingsDraft.chat_follow_up_capture_seconds" type="number" min="0" max="20" step="0.5" /></label>
          <label><span>最多合并补充条数</span><input v-model.number="runtimeSettingsDraft.chat_follow_up_max_capture_count" type="number" min="0" max="5" /></label>
        </div></div>
        <div class="settings-section-block"><h2>长期记忆</h2><div class="settings-form-grid">
          <label><span>读取最近天数</span><input v-model.number="runtimeSettingsDraft.memory_context_days" type="number" min="1" max="90" /></label>
          <label><span>记忆最大字数</span><input v-model.number="runtimeSettingsDraft.memory_context_max_chars" type="number" min="1000" max="50000" step="500" /></label>
          <label><span>每天最多读取消息</span><input v-model.number="runtimeSettingsDraft.memory_context_messages_per_day" type="number" min="1" max="20" /></label>
          <div class="settings-info-tile"><strong>自动记忆</strong><small>明确要求“记住、写入、放进属性”时直接处理；其他内容按置信度进入长期记忆</small></div>
        </div></div>
        <div class="settings-section-block"><h2>联网与附件</h2><div class="settings-form-grid">
          <label class="toggle-row"><input v-model="runtimeSettingsDraft.web_search_enabled" type="checkbox" /><span>不知道时允许联网查询</span></label>
          <label><span>搜索结果数量</span><input v-model.number="runtimeSettingsDraft.web_search_max_results" type="number" min="1" max="20" /></label>
          <label><span>联网超时（秒）</span><input v-model.number="runtimeSettingsDraft.web_search_timeout_seconds" type="number" min="3" max="60" /></label>
          <label class="toggle-row"><input v-model="runtimeSettingsDraft.photo_archive_enabled" type="checkbox" /><span>归档聊天图片</span></label>
          <label><span>单次附件数量</span><input v-model.number="runtimeSettingsDraft.agent_attachment_max_count" type="number" min="1" max="20" /></label>
          <label><span>文本附件最大字数</span><input v-model.number="runtimeSettingsDraft.agent_text_attachment_max_chars" type="number" min="10000" max="2000000" step="10000" /></label>
          <label><span>文档最大体积（字节）</span><input v-model.number="runtimeSettingsDraft.agent_document_attachment_max_bytes" type="number" min="1048576" max="104857600" step="1048576" /></label>
          <label><span>PDF最大页数</span><input v-model.number="runtimeSettingsDraft.agent_pdf_max_pages" type="number" min="1" max="1000" /></label>
          <label><span>文档视觉/OCR页数</span><input v-model.number="runtimeSettingsDraft.agent_document_vision_max_pages" type="number" min="1" max="50" /></label>
        </div><div class="web-search-test-panel"><div><strong>联网搜索测试</strong><small>保存联网开关后，用真实搜索源检查触发、网络和解析是否正常</small></div><span><input v-model.trim="webSearchTestQuery" type="text" maxlength="160" /><button type="button" :disabled="webSearchTestBusy" @click="testWebSearch"><Wifi :size="14" />{{ webSearchTestBusy ? '正在搜索' : '测试联网' }}</button></span><p v-if="webSearchTestResult" :class="{ success: webSearchTestResult.ok, error: !webSearchTestResult.ok }">{{ webSearchTestResult.message }}<small v-if="webSearchTestResult.engine">搜索源：{{ webSearchTestResult.engine }}</small><small v-for="attempt in webSearchTestResult.attempts || []" :key="attempt">{{ attempt }}</small></p></div></div>
      </template>
      <div class="settings-savebar"><span>主应用、QQ与桌宠共享默认模型和记忆策略</span><div><button type="button" :disabled="!isSettingsSectionDirty('conversation')" @click="resetActiveSettings">取消</button><button class="primary-button" type="button" :disabled="!isSettingsSectionDirty('conversation') || runtimeSettingsBusy" @click="saveActiveSettings"><Check :size="15" />保存</button></div></div>
    </section>

    <section v-else-if="activeSettingsSection === 'diary'" class="settings-content-section">
      <template v-if="runtimeSettingsReady">
        <div class="settings-section-block"><h2>自动整理</h2><div class="settings-list">
          <label class="settings-item"><span><strong>自动生成每日成长日记</strong><small>当日没有手动生成时，系统按逻辑日期自动整理</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.daily_diary_auto_enabled" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>检查间隔</strong><small>检查是否缺少当天日记，不会每次都调用模型</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.daily_diary_check_seconds" type="number" min="30" max="3600" /><b>秒</b></span></label>
          <label class="settings-item"><span><strong>凌晨日界线</strong><small>此时间前生成的内容仍归入前一天</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.day_boundary_hour" type="number" min="0" max="23" /><b>时</b></span></label>
          <label class="settings-item"><span><strong>自动生成周记</strong><small>汇总已确认日记和一周成长变化</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.weekly_review_enabled" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>周记时间</strong><small>每周检查并生成周记的小时</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.weekly_review_hour" type="number" min="0" max="23" /><b>时</b></span></label>
          <label class="settings-item"><span><strong>周记完成后通知QQ</strong><small>仅在QQ通道在线时发送完成通知</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.weekly_review_notify_qq" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>周记检查间隔</strong><small>只检查生成条件，不代表每次都会调用模型</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.weekly_review_check_seconds" type="number" min="30" max="86400" /><b>秒</b></span></label>
          <label class="settings-item"><span><strong>自动生成月记</strong><small>每月汇总上一个完整自然月的日记与状态变化</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.monthly_review_enabled" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>月记时间</strong><small>进入新月份后检查并生成月记的小时</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.monthly_review_hour" type="number" min="0" max="23" /><b>时</b></span></label>
          <label class="settings-item"><span><strong>月记完成后通知QQ</strong><small>仅在QQ通道在线时发送完成通知</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.monthly_review_notify_qq" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>月记检查间隔</strong><small>已有月记时不会重复调用模型</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.monthly_review_check_seconds" type="number" min="30" max="86400" /><b>秒</b></span></label>
          <label class="settings-item"><span><strong>兼容旧版每日回顾</strong><small>回顾已并入日记，默认建议关闭</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.daily_review_auto_enabled" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>旧版回顾时间</strong><small>只有启用兼容回顾时使用</small></span><span class="settings-inline-fields"><input v-model.number="runtimeSettingsDraft.daily_review_auto_hour" type="number" min="0" max="23" /><b>时</b><input v-model.number="runtimeSettingsDraft.daily_review_auto_minute" type="number" min="0" max="59" /><b>分</b></span></label>
          <label class="settings-item"><span><strong>旧版回顾通知QQ</strong><small>仅兼容旧工作流</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.daily_review_auto_notify_qq" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>旧版回顾检查间隔</strong><small>只有启用兼容回顾时使用</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.daily_review_check_seconds" type="number" min="30" max="86400" /><b>秒</b></span></label>
        </div></div>
        <div class="settings-section-block"><h2>日记结构</h2><div class="diary-structure-list"><span>今日事件</span><span>今日情绪</span><span>今日成长</span><span>做得不错</span><span>可以调整的地方</span><span>明天最小行动</span></div><p class="settings-note">日记采用覆盖式保存；手动编辑后需要重新确认。今日成长包含任何能提升自己的持续行动。</p></div>
        <div class="settings-section-block"><h2>备份与夜间收尾</h2><div class="settings-list">
          <label class="settings-item"><span><strong>自动备份日记数据</strong><small>备份用于故障恢复，不在日记页面显示版本历史</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.backup_enabled" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>保留备份份数</strong><small>超过数量后轮换旧备份</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.backup_keep_count" type="number" min="1" max="365" /><b>份</b></span></label>
          <label class="settings-item"><span><strong>备份检查间隔</strong><small>检查每日自动备份是否缺失</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.backup_check_seconds" type="number" min="60" max="86400" /><b>秒</b></span></label>
          <label class="settings-item"><span><strong>夜间收尾判断</strong><small>在设定时段且长时间安静后整理当天素材</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.night_close_enabled" type="checkbox" /><i /></span></label>
          <label class="settings-item"><span><strong>夜间收尾时段</strong><small>跨午夜时段按逻辑日期处理</small></span><span class="settings-inline-fields"><input v-model.number="runtimeSettingsDraft.night_close_start_hour" type="number" min="0" max="23" /><b>时至</b><input v-model.number="runtimeSettingsDraft.night_close_end_hour" type="number" min="0" max="23" /><b>时</b></span></label>
          <label class="settings-item"><span><strong>夜间安静时间</strong><small>至少多久没有新消息才触发收尾</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.night_close_min_quiet_minutes" type="number" min="5" max="720" /><b>分钟</b></span></label>
        </div></div>
      </template><div v-else class="settings-empty-state">正在读取日记设置</div>
      <div class="settings-savebar"><span>取消独立每日回顾，回顾内容已合并进日记结构</span><div><button type="button" :disabled="!isSettingsSectionDirty('diary')" @click="resetActiveSettings">取消</button><button class="primary-button" type="button" :disabled="!isSettingsSectionDirty('diary') || runtimeSettingsBusy" @click="saveActiveSettings"><Check :size="15" />保存</button></div></div>
    </section>

    <section v-else-if="activeSettingsSection === 'models'" class="settings-content-section">
      <div class="settings-tabs"><button type="button" :class="{ active: modelSettingsTab === 'models' }" @click="modelSettingsTab = 'models'">模型</button><button type="button" :class="{ active: modelSettingsTab === 'providers' }" @click="modelSettingsTab = 'providers'">供应商</button><button type="button" :class="{ active: modelSettingsTab === 'auto' }" @click="modelSettingsTab = 'auto'">自动选择</button></div>
      <div v-if="modelSettingsTab === 'models'" class="settings-section-block"><h2>可用模型</h2><div v-for="group in modelGroups" :key="group.provider_id" class="provider-group"><div class="provider-group-heading"><div class="provider-mark">{{ group.provider.slice(0, 1).toUpperCase() }}</div><strong>{{ group.provider }}</strong><span>{{ group.models.length }}个版本</span></div><div v-for="model in group.models" :key="model.id" class="provider-row"><div class="provider-main"><strong>{{ model.display_name }}</strong><span>{{ model.model }}</span></div><div class="provider-details"><span>{{ model.context_window ? `${Number(model.context_window).toLocaleString('zh-CN')} Token上下文` : '上下文由供应商决定' }}</span><span>{{ pricingSourceLabel(model.pricing_source) }}</span><span v-if="model.requires_key_reentry" class="model-test-status error">密钥来自另一台电脑，请重新添加该供应商的 Key</span><span v-if="modelTestStatus[model.id]" :class="['model-test-status', modelTestStatus[model.id].state]">{{ modelTestStatus[model.id].text }}</span></div><span v-if="model.id === selectedModel || (selectedModel === 'auto' && model.is_default)" class="provider-status">当前</span><button class="icon-button" type="button" title="测试连接" :disabled="Boolean(modelTestBusy)" @click="testModel(model)"><RefreshCw :class="{ spin: modelTestBusy === model.id }" :size="16" /></button></div></div></div>
      <div v-else-if="modelSettingsTab === 'providers'" class="settings-section-block"><div class="settings-block-heading"><div><h2>供应商</h2><p>密钥只保存在本机安全存储，界面不会回显完整内容</p></div><button class="primary-button" type="button" @click="openProviderPanel"><Plus :size="15" />新增供应商</button></div><div v-for="group in modelGroups" :key="group.provider_id" class="provider-group"><div class="provider-group-heading"><div><div class="provider-mark">{{ group.provider.slice(0, 1).toUpperCase() }}</div><strong>{{ group.provider }}</strong><span>{{ group.models.length }}个模型</span></div><button class="icon-button danger-icon" type="button" title="删除供应商" :aria-label="`删除供应商${group.provider}`" :disabled="providerBusy" @click="deleteProviderGroup(group)"><Trash2 :size="16" /></button></div><div v-for="model in group.models" :key="model.id" class="provider-row"><div class="provider-main"><strong>{{ model.display_name }}</strong><span>{{ model.model }}</span></div><div class="provider-details"><span>输入 {{ model.input_price_cny_per_million || 0 }} / 输出 {{ model.output_price_cny_per_million || 0 }} 元每百万Token</span><span>{{ pricingSourceLabel(model.pricing_source) }}</span></div><button v-if="model.is_custom" class="icon-button danger-icon" type="button" title="只删除这个模型" :disabled="providerBusy" @click="deleteProvider(model)"><Trash2 :size="16" /></button></div></div><div v-if="hiddenProviderNames.length" class="provider-restore-list"><span>已隐藏的内置供应商</span><button v-for="provider in hiddenProviderNames" :key="provider.provider_id" type="button" :disabled="providerBusy" @click="restoreProvider(provider)"><RotateCw :size="14" />{{ provider.display_name }}</button></div></div>
      <div v-else class="settings-section-block"><h2>自动选择策略</h2><div class="settings-list"><div class="settings-item"><span><strong>简单日常对话</strong><small>优先选择低延迟模型，并保留模型默认思考</small></span><b class="settings-readonly">自动</b></div><div class="settings-item"><span><strong>复杂分析与任务</strong><small>按模型能力和原生思考档位提高推理强度</small></span><b class="settings-readonly">自动</b></div><div class="settings-item"><span><strong>降级顺序</strong><small>首选模型不可用时，依次尝试其他已连接模型</small></span><b class="settings-readonly">按优先级</b></div><div class="settings-item"><span><strong>费用</strong><small>优先显示供应商实扣；无法立即取得时显示“实扣待确认”并后台补账</small></span><b class="settings-readonly">后台补账</b></div></div></div>
    </section>

    <section v-else-if="activeSettingsSection === 'dependencies'" class="settings-content-section">
      <div class="settings-section-block">
        <DependencyCenter @navigate="handleDependencyNavigate" />
      </div>
    </section>

    <section v-else-if="activeSettingsSection === 'qq'" class="settings-content-section">
      <div class="settings-section-block qq-napcat-guide">
        <div class="settings-block-heading"><div><h2>一键接入机器人 QQ</h2><p>填写账号后自动安装 NapCat、写入 OneBot 并启动登录</p></div><span :class="['connection-label', { online: qqStatus.napcat_setup_ready }]">{{ qqStatus.napcat_setup_ready ? '已配置' : '待配置' }}</span></div>
        <div class="settings-form-grid qq-setup-fields">
          <label><span>机器人 QQ 号</span><input v-model.trim="qqAccountDraft" type="text" inputmode="numeric" maxlength="12" placeholder="用于登录 NapCat 的 QQ" /></label>
          <label><span>接收测试消息的 QQ</span><input v-model.trim="qqTestTargetDraft" type="text" inputmode="numeric" maxlength="12" placeholder="通常填写你自己的 QQ" /></label>
        </div>
        <ol class="qq-napcat-steps">
          <li :class="{ ready: qqStatus.napcat_executable_exists }"><strong>安装 NapCat</strong><span>{{ qqStatus.napcat_executable_exists ? '运行文件已找到' : '点击下方按钮自动下载' }}</span></li>
          <li :class="{ ready: qqStatus.onebot_config_ready }"><strong>写入 OneBot</strong><span>{{ qqStatus.onebot_config_ready ? '反向 WebSocket 已配置' : '安装后自动配置，无需手填 JSON' }}</span></li>
          <li :class="{ ready: qqStatus.webui_reachable }"><strong>启动 NapCat</strong><span>{{ qqStatus.webui_reachable ? '本地 WebUI 已在线' : '配置后自动启动' }}</span></li>
          <li :class="{ ready: qqStatus.account_ready }"><strong>登录账号</strong><span>{{ qqStatus.account_ready ? `机器人 QQ ${qqStatus.connected_account || qqStatus.configured_account} 已登录` : (qqStatus.diagnostic_code === 'account_mismatch' ? '实际账号与配置账号不一致' : '未登录时二维码会直接显示在本页') }}</span></li>
          <li :class="{ ready: qqStatus.connected }"><strong>连接 Mio</strong><span>{{ qqStatus.connected ? 'OneBot 账号核对通过' : '登录后自动核对实际账号' }}</span></li>
        </ol>
        <div class="settings-actions-row"><button class="primary-button" type="button" :disabled="Boolean(qqBusy)" @click="setupQqChannel"><Download :size="14" />{{ qqBusy === 'setup' ? '正在安装和配置' : '安装并配置' }}</button><button type="button" :disabled="Boolean(qqBusy) || !qqStatus.account_ready || !qqStatus.websocket_connected" @click="testQqDelivery"><Check :size="14" />发送测试消息</button><button type="button" @click="openNapcatManualGuide"><RefreshCw :size="14" />官方说明</button></div>
        <p v-if="qqSetupResult?.message || qqSetupResult?.diagnostic" class="qq-setup-result">{{ qqSetupResult.message || qqSetupResult.diagnostic }}</p>
      </div>
      <div class="settings-section-block"><div class="settings-block-heading"><div><h2>连接状态</h2><p>{{ qqStatusCopy }}</p></div><span :class="['connection-label', { online: qqConnected }]">{{ qqStatusLabel }}</span></div><div class="settings-list"><div class="settings-item"><span><strong>配置账号</strong><small>本次要求 NapCat 登录的机器人账号</small></span><b class="settings-readonly">{{ qqStatus.configured_account || '未配置' }}</b></div><div class="settings-item"><span><strong>实际账号</strong><small>以 OneBot 事件中的 self_id 为准</small></span><b :class="['connection-label', { online: qqStatus.account_matches }]">{{ qqStatus.connected_account || '尚未识别' }}</b></div></div><div class="qq-status-layers"><span :class="{ ready: qqStatus.qq_process_running }"><i />QQ进程<b>{{ qqStatus.qq_process_running ? '运行中' : '未运行' }}</b></span><span :class="{ ready: qqStatus.account_ready }"><i />QQ登录<b>{{ qqStatus.account_ready ? '账号正确' : (qqStatus.logged_in ? '账号不符' : '未登录') }}</b></span><span :class="{ ready: qqStatus.webui_reachable }"><i />NapCat<b>{{ qqStatus.webui_reachable ? '在线' : '离线' }}</b></span><span :class="{ ready: qqStatus.connected }"><i />OneBot<b>{{ qqStatus.connected ? '已核对' : (qqStatus.websocket_connected ? '待核对' : '未连接') }}</b></span></div><div class="settings-actions-row"><button type="button" :disabled="Boolean(qqBusy)" @click="controlQq('login')"><LogIn :size="15" />{{ qqStatus.logged_in ? '切换账号二维码' : '登录二维码' }}</button><button type="button" :disabled="Boolean(qqBusy)" @click="controlQq('start')"><Play :size="15" />启动</button><button type="button" :disabled="Boolean(qqBusy)" @click="controlQq('stop')"><Power :size="15" />停止</button><button type="button" :disabled="Boolean(qqBusy)" @click="controlQq('restart')"><RotateCw :size="15" />重启NapCat</button></div><div v-if="(!qqStatus.logged_in || qqStatus.diagnostic_code === 'account_mismatch' || qqQrImageUrl) && (qqStatus.login_checked || qqQrLoading || qqQrError || qqQrImageUrl)" class="qq-login-panel"><img v-if="qqQrImageUrl" :src="qqQrImageUrl" alt="QQ登录二维码" /><div v-else class="qq-login-placeholder"><RefreshCw v-if="qqQrLoading" class="spin" :size="24" /><LogIn v-else :size="24" /><span>{{ qqQrLoading ? '正在获取二维码' : '二维码未就绪' }}</span></div><div><strong>使用手机QQ扫码登录</strong><span v-if="qqQrError" class="qq-login-error">{{ qqQrError }}</span><span v-else>切换账号会先退出由 NapCat 管理的旧机器人 QQ，不会结束普通 QQ</span></div></div></div>
      <template v-if="runtimeSettingsReady"><div class="settings-section-block"><h2>通道与私聊</h2><div class="settings-list">
        <label class="settings-item"><span><strong>随应用启动QQ通道</strong><small>打开 Mio 时自动拉起NapCat和机器人QQ</small></span><span class="switch-control"><input v-model="qqStartupEnabled" type="checkbox" /><i /></span></label>
        <label class="settings-item"><span><strong>允许QQ接收消息</strong><small>关闭后保留登录状态但不处理私聊与群聊</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.qq_bot_enabled" type="checkbox" /><i /></span></label>
        <label class="settings-item"><span><strong>私聊账号白名单</strong><small>多个QQ号用英文逗号分隔</small></span><input v-model.trim="runtimeSettingsDraft.qq_allowed_user_ids" type="text" /></label>
        <label class="settings-item"><span><strong>允许接收图片</strong><small>仅支持视觉模型时发送给模型</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.qq_image_enabled" type="checkbox" /><i /></span></label>
        <label class="settings-item"><span><strong>发送图片给视觉模型</strong><small>关闭时只记录收到图片，不上传图像内容</small></span><span class="switch-control"><input v-model="runtimeSettingsDraft.qq_image_send_to_model" type="checkbox" /><i /></span></label>
        <label class="settings-item"><span><strong>单次图片数量</strong><small>超过后只处理允许范围内的图片</small></span><span class="settings-number"><input v-model.number="runtimeSettingsDraft.qq_image_max_count" type="number" min="1" max="10" /><b>张</b></span></label>
        <label class="settings-item"><span><strong>单张图片上限</strong><small>按字节限制，8 MB 为 8388608</small></span><input v-model.number="runtimeSettingsDraft.qq_image_max_bytes" type="number" min="1048576" max="52428800" step="1048576" /></label>
        <label class="settings-item"><span><strong>视觉图片细节</strong><small>由供应商视觉模型决定传输细节</small></span><select v-model="runtimeSettingsDraft.qq_image_detail"><option value="low">低</option><option value="auto">自动</option><option value="high">高</option></select></label>
      </div></div><div class="settings-section-block"><h2>消息节奏与可靠性</h2><div class="settings-form-grid"><label><span>普通消息合并等待</span><input v-model.number="runtimeSettingsDraft.qq_message_debounce_seconds" type="number" min="0" max="30" step="0.1" /></label><label><span>未说完消息等待</span><input v-model.number="runtimeSettingsDraft.qq_message_incomplete_debounce_seconds" type="number" min="0" max="60" step="0.1" /></label><label><span>首条回复前等待</span><input v-model.number="runtimeSettingsDraft.qq_reply_initial_delay_seconds" type="number" min="0" max="30" step="0.1" /></label><label><span>多条回复间隔</span><input v-model.number="runtimeSettingsDraft.qq_reply_delay_seconds" type="number" min="0" max="30" step="0.1" /></label><label><span>送达确认超时</span><input v-model.number="runtimeSettingsDraft.qq_delivery_ack_timeout_seconds" type="number" min="1" max="30" step="0.5" /></label><label><span>失败重试次数</span><input v-model.number="runtimeSettingsDraft.qq_delivery_max_retries" type="number" min="0" max="3" /></label></div></div></template>
      <div class="settings-section-block"><h2>群聊</h2><div class="settings-list"><label class="settings-item"><span><strong>允许白名单群回复</strong><small>群聊上下文不会进入Agent主对话</small></span><span class="switch-control"><input v-model="groupChatSettings.enabled" type="checkbox" /><i /></span></label><label class="settings-item"><span><strong>必须 @机器人账号</strong><small>按机器人 QQ 账号识别真实 @，与群昵称无关；关闭后白名单群内普通消息也可能触发回复</small></span><span class="switch-control"><input v-model="groupChatSettings.mention_required" type="checkbox" /><i /></span></label><label class="settings-item"><span><strong>群号白名单</strong><small>多个群号用逗号分隔</small></span><input v-model="groupIdsDraft" type="text" /></label></div><div class="settings-actions-row"><button type="button" :disabled="Boolean(qqBusy)" @click="clearGroupChatContext"><Trash2 :size="15" />清空群聊临时上下文</button></div></div>
      <div class="settings-savebar"><span>启动、登录和重启立即执行；其余配置保存后生效</span><div><button type="button" :disabled="!isSettingsSectionDirty('qq')" @click="resetActiveSettings">取消</button><button class="primary-button" type="button" :disabled="!isSettingsSectionDirty('qq') || Boolean(qqBusy) || runtimeSettingsBusy" @click="saveActiveSettings"><Check :size="15" />保存</button></div></div>
    </section>

    <section v-else-if="activeSettingsSection === 'pet'" class="settings-content-section">
      <div class="settings-tabs"><button type="button" :class="{ active: petSettingsTab === 'appearance' }" @click="petSettingsTab = 'appearance'">形象管理</button><button type="button" :class="{ active: petSettingsTab === 'voice' }" @click="petSettingsTab = 'voice'">语音</button><button type="button" :class="{ active: petSettingsTab === 'observation' }" @click="petSettingsTab = 'observation'">屏幕观察</button><button type="button" :class="{ active: petSettingsTab === 'behavior' }" @click="petSettingsTab = 'behavior'">行为</button></div>
      <template v-if="companionStatusReady">
        <PetAppearancePanel v-if="petSettingsTab === 'appearance'" class="settings-section-block embedded-pet-panel" :status="companionStatus" :avatar-url="companionAvatarUrl" :busy="Boolean(companionBusy)" management-mode @import-live2d="importLive2DModel" @delete-live2d="deleteLive2DModel" @replace-live2d-preview="replaceLive2DPreview" @preview-motion="previewLive2DMotion" @preview-expression="previewLive2DExpression" @control="controlCompanion" @save="saveCompanionSettings('pet')" @save-size="saveCompanionSize" />
        <VoiceSettingsPanel
          v-else-if="petSettingsTab === 'voice'"
          :status="companionStatus"
          :model-options="modelOptions"
          :busy="Boolean(companionBusy)"
          :range-input-style="rangeInputStyle"
          @control-runtime="controlVoiceRuntime"
          @test="testCompanionVoiceProfile"
          @upload-reference="uploadVoiceReference"
          @export-package="exportVoicePackage"
          @import-package="importVoicePackage"
        />
      <div v-else-if="petSettingsTab === 'observation'" class="settings-section-block"><h2>屏幕观察</h2><div class="settings-list"><div class="settings-item"><span><strong>视觉路线</strong><small>本地不上传画面；云端会发送选定范围</small></span><div class="settings-segment"><button type="button" :class="{ active: companionStatus.pet.settings.screen_vision_route === 'local' }" @click="companionStatus.pet.settings.screen_vision_route = 'local'">本地</button><button type="button" :class="{ active: companionStatus.pet.settings.screen_vision_route === 'cloud' }" @click="companionStatus.pet.settings.screen_vision_route = 'cloud'">云端</button></div></div><label v-if="companionStatus.pet.settings.screen_vision_route === 'cloud'" class="settings-item"><span><strong>云端视觉模型</strong><small>只列出支持图片输入的模型</small></span><select v-model="companionStatus.pet.settings.screen_vision_model_id"><option v-for="item in (companionStatus.screen_analysis?.vision_model_options || [])" :key="item.id" :value="item.id">{{ item.label }}</option></select></label><div class="settings-item"><span><strong>系统声音观察</strong><small>随视觉观察同时开启和停止；音频只在内存中转写</small></span><b class="settings-readonly">{{ companionStatus.screen.running ? '观察中' : '未启动' }}</b></div><label class="settings-item"><span><strong>由 Mio 判断是否回应</strong><small>游戏对白、选项和剧情推进会更积极；普通桌面仍保持安静</small></span><span class="switch-control"><input v-model="companionStatus.pet.settings.screen_ai_enabled" type="checkbox" /><i /></span></label><label class="settings-item"><span><strong>分析间隔</strong><small>越短越实时，也会增加模型调用</small></span><select v-model.number="companionStatus.pet.settings.screen_analysis_interval_seconds"><option :value="5">5秒</option><option :value="10">10秒</option><option :value="15">15秒</option><option :value="30">30秒</option></select></label><label class="settings-item"><span><strong>回应冷却</strong><small>一次开口后至少等待多久</small></span><select v-model.number="companionStatus.pet.settings.screen_voice_cooldown_seconds"><option :value="5">5秒</option><option :value="10">10秒</option><option :value="15">15秒</option><option :value="30">30秒</option></select></label><label class="settings-item"><span><strong>今日实扣上限</strong><small>达到上限后停止云端观察请求</small></span><span class="settings-number"><input v-model.number="companionStatus.pet.settings.screen_daily_cost_limit_yuan" type="number" min="0.1" max="1000" step="0.1" /><b>元</b></span></label></div><p class="settings-note">当前观察：{{ companionStatus.screen.running ? '运行中' : '未启动' }} · 游戏窗口会自动提高对白变化灵敏度 · 今日实扣 ¥{{ Number(companionStatus.screen_analysis?.budget?.daily?.confirmed_cost_yuan || 0).toFixed(4) }}</p></div>
        <div v-else class="settings-section-block">
          <h2>桌面行为</h2>
          <div class="settings-list">
            <label class="settings-item">
              <span><strong>桌宠对话模型</strong><small>只控制桌宠独立对话，不影响主应用和 QQ</small></span>
              <select v-model="companionStatus.pet.settings.pet_chat_model_id" @change="handlePetModelChange"><option value="auto">自动选择</option><option v-for="model in modelOptions" :key="model.id" :value="model.id">{{ model.display_name || model.model }}</option></select>
            </label>
            <label class="settings-item">
              <span><strong>桌宠思考方式</strong><small>只显示当前桌宠模型原生支持的档位</small></span>
              <select v-model="companionStatus.pet.settings.pet_chat_reasoning_level"><option v-for="item in petReasoningOptions" :key="item.id" :value="item.id">{{ item.label }}</option></select>
            </label>
            <label class="settings-item">
              <span><strong>电话识别模型</strong><small>自动使用固定语料实测最准确的模型，也可切换国内中文模型复验</small></span>
              <select v-model="companionStatus.pet.settings.pet_call_asr_engine"><option value="auto">自动（实测推荐）</option><option value="whisper">Faster-Whisper</option><option value="sensevoice">SenseVoice 中文</option><option value="paraformer">Paraformer 中文</option></select>
            </label>
            <label class="settings-item">
              <span><strong>电话识别语言</strong><small>中文通话固定按中文识别，避免短句被误判成其他语言</small></span>
              <select v-model="companionStatus.pet.settings.pet_call_input_language"><option value="zh">中文</option><option value="ja">日语</option></select>
            </label>
            <label class="settings-item">
              <span><strong>电话断句等待</strong><small>说完后静音多久开始回应，越短越快</small></span>
              <select v-model.number="companionStatus.pet.settings.pet_call_silence_ms"><option :value="450">0.45秒</option><option :value="650">0.65秒</option><option :value="900">0.9秒</option><option :value="1200">1.2秒</option></select>
            </label>
            <label class="settings-item">
              <span><strong>麦克风灵敏度</strong><small>环境嘈杂时调低灵敏度可减少误触发</small></span>
              <select v-model.number="companionStatus.pet.settings.pet_call_voice_threshold"><option :value="0.012">高</option><option :value="0.018">标准</option><option :value="0.028">低</option><option :value="0.04">很低</option></select>
            </label>
            <label class="settings-item"><span><strong>始终置顶</strong><small>保持桌宠显示在普通窗口上方</small></span><span class="switch-control"><input v-model="companionStatus.pet.settings.live2d_always_on_top" type="checkbox" /><i /></span></label>
            <label class="settings-item"><span><strong>锁定时鼠标穿透</strong><small>固定后可以点击桌宠背后的内容</small></span><span class="switch-control"><input v-model="companionStatus.pet.settings.live2d_click_through_locked" type="checkbox" /><i /></span></label>
            <label class="settings-item"><span><strong>说话气泡</strong><small>语音播放时显示中文打字机气泡</small></span><span class="switch-control"><input v-model="companionStatus.pet.settings.live2d_speech_bubble_enabled" type="checkbox" /><i /></span></label>
            <label class="settings-item"><span><strong>视线跟随鼠标</strong><small>Live2D形象可根据指针位置调整视线</small></span><span class="switch-control"><input v-model="companionStatus.pet.settings.live2d_follow_cursor" type="checkbox" /><i /></span></label>
            <label class="settings-item"><span><strong>待机动作</strong><small>空闲时播放模型自带动作</small></span><span class="switch-control"><input v-model="companionStatus.pet.settings.live2d_idle_motion" type="checkbox" /><i /></span></label>
            <label class="settings-item"><span><strong>气泡停留时间</strong><small>打字完成后继续显示多久</small></span><span class="settings-number"><input v-model.number="companionStatus.pet.settings.bubble_seconds" type="number" min="1" max="60" /><b>秒</b></span></label>
          </div>
        </div>
      </template><div v-else class="settings-empty-state">正在读取桌宠设置</div>
      <div class="settings-savebar"><span>启动、停止和试听立即执行；形象、语音、观察与行为统一保存</span><div><button type="button" :disabled="!isSettingsSectionDirty('pet')" @click="resetActiveSettings">取消</button><button class="primary-button" type="button" :disabled="!companionStatusReady || !isSettingsSectionDirty('pet') || Boolean(companionBusy)" @click="saveActiveSettings"><Check :size="15" />保存</button></div></div>
    </section>

    <section v-else-if="activeSettingsSection === 'data'" class="settings-content-section">
      <div class="settings-section-block privacy-control-block">
        <div class="settings-section-heading"><div><h2>隐私总控</h2><p>立即停止屏幕、系统声音、QQ、联网搜索、主动联系和自动模型任务</p></div><button :class="['privacy-pause-button', { paused: dataPrivacyState.privacy?.paused }]" type="button" :disabled="dataPrivacyBusy === 'privacy'" @click="togglePrivacyPause"><Power :size="15" />{{ dataPrivacyState.privacy?.transition === 'state_uncertain' ? '重新暂停' : dataPrivacyState.privacy?.transition === 'pause_incomplete' ? '重试暂停' : dataPrivacyState.privacy?.paused ? '恢复原设置' : '暂停敏感能力' }}</button></div>
        <p v-if="dataPrivacyState.privacy?.transition_error" class="privacy-transition-error">{{ dataPrivacyState.privacy.transition === 'state_uncertain' ? '当前状态无法确认：' : '上次操作未完整完成：' }}{{ dataPrivacyState.privacy.transition_error }}</p>
        <div v-if="dataPrivacyLoading" class="settings-empty-state">正在读取隐私状态</div>
        <div v-else class="privacy-capability-list"><div v-for="item in dataPrivacyState.privacy?.capabilities || []" :key="item.id"><i :class="{ enabled: item.enabled }" /><span><strong>{{ item.label }}</strong><small>{{ item.destination }}</small></span><b>{{ item.enabled ? '开启' : '关闭' }}</b></div></div>
      </div>
      <div class="settings-section-block"><div class="settings-section-heading"><div><h2>完整备份与恢复</h2><p>包含数据库、日记、记忆、角色、模型配置、头像、背景和桌宠设置；不包含 .env、日志和临时画面</p></div><span class="backup-heading-actions"><label><Download :size="15" />{{ dataPrivacyBusy === 'import' ? '正在导入' : '导入备份' }}<input type="file" accept=".zip,application/zip" :disabled="Boolean(dataPrivacyBusy)" @change="importDataBackup" /></label><button type="button" :disabled="Boolean(dataPrivacyBusy)" @click="createDataBackup"><Archive :size="15" />{{ dataPrivacyBusy === 'create' ? '正在备份' : '创建完整备份' }}</button></span></div>
        <div v-if="dataPrivacyLoading" class="settings-empty-state">正在检查备份</div>
        <div v-else-if="!dataPrivacyState.backups.length" class="settings-empty-state">还没有完整备份</div>
        <div v-else class="backup-list"><article v-for="item in dataPrivacyState.backups" :key="item.name" :class="{ invalid: !item.valid }"><div><strong>{{ item.name }}</strong><small>{{ item.created_at || '未知时间' }} · {{ (Number(item.size || 0) / 1048576).toFixed(2) }} MB · {{ item.valid ? '校验通过' : (item.error || '旧格式或损坏') }}</small></div><span v-if="item.valid"><a :href="`/api/backups/${encodeURIComponent(item.name)}/download`" download title="下载备份"><Download :size="15" /></a><button type="button" title="恢复这个备份" :disabled="Boolean(dataPrivacyBusy)" @click="restoreDataBackup(item.name)"><RotateCw :size="15" /></button></span></article></div>
      </div>
      <div class="settings-section-block"><h2>本地数据与版本</h2><div class="settings-list"><div class="settings-item"><span><strong>聊天、记忆与状态</strong><small>保存在同一个本地 SQLite 数据库中</small></span><b class="settings-readonly">本机</b></div><div class="settings-item"><span><strong>数据库结构</strong><small>迁移记录可追踪，升级前自动建立完整备份</small></span><b class="settings-readonly">v{{ dataPrivacyState.migrations?.current_version || 0 }} / v{{ dataPrivacyState.migrations?.latest_version || 0 }}</b></div><div class="settings-item"><span><strong>日记</strong><small>Markdown 可单独导出，不包含聊天原文和密钥</small></span><a class="settings-download-link" href="/diaries/export/all.zip" download><Download :size="15" />导出日记</a></div></div></div>
      <div class="settings-section-block"><h2>隐私边界</h2><div class="privacy-boundary-grid"><article><ShieldCheck :size="18" /><strong>本地处理</strong><p>SQLite、日记、记忆、桌宠形象和原始系统音频留在本机。系统音频片段转写后立即释放。</p></article><article><Wifi :size="18" /><strong>云端模型</strong><p>发送消息时，对话上下文与附件会交给当前供应商；云端视觉会发送你选择的屏幕或窗口画面。</p></article><article><KeyRound :size="18" /><strong>密钥</strong><p>供应商密钥不在设置列表回显，不会进入日记、完整备份或 Git。</p></article></div></div>
      <div class="settings-section-block danger-zone"><h2>危险操作</h2><p>恢复备份会覆盖当前数据，因此必须手动确认，且恢复前会自动创建回退备份。应用不提供无备份的一键清空。</p></div>
    </section>

    <section v-else class="settings-content-section">
      <div v-if="runtimeSettingsReady" class="settings-section-block"><h2>观察、联网与历史</h2><div class="settings-form-grid"><label><span>屏幕回应超时（秒）</span><input v-model.number="runtimeSettingsDraft.screen_reaction_timeout_seconds" type="number" min="5" max="120" /></label><label><span>联网正文上限</span><input v-model.number="runtimeSettingsDraft.web_page_max_chars" type="number" min="500" max="50000" step="500" /></label><label><span>屏幕历史保留天数</span><input v-model.number="runtimeSettingsDraft.screen_history_retention_days" type="number" min="1" max="365" /></label><label><span>屏幕历史最大行数</span><input v-model.number="runtimeSettingsDraft.screen_history_max_rows" type="number" min="1000" max="200000" step="1000" /></label></div></div>
      <template v-if="runtimeSettingsReady"><div class="settings-section-block"><h2>模型运行</h2><div class="settings-form-grid"><label><span>单次连接超时（秒）</span><input v-model.number="runtimeSettingsDraft.openai_timeout_seconds" type="number" min="5" max="300" /></label><label><span>单次请求最大时限（秒）</span><input v-model.number="runtimeSettingsDraft.openai_request_deadline_seconds" type="number" min="10" max="600" /></label><label><span>聊天温度</span><input v-model.number="runtimeSettingsDraft.chat_temperature" type="number" min="0" max="2" step="0.05" /></label><label><span>行动规划温度</span><input v-model.number="runtimeSettingsDraft.action_planner_temperature" type="number" min="0" max="2" step="0.05" /></label><label><span>说明书最大字数</span><input v-model.number="runtimeSettingsDraft.manual_max_chars" type="number" min="1000" max="50000" step="500" /></label><label><span>时区</span><input v-model.trim="runtimeSettingsDraft.timezone" type="text" /></label></div></div><div class="settings-section-block"><h2>构建与运行身份</h2><div class="settings-list runtime-identity-list"><div class="settings-item"><span><strong>构建</strong><small>{{ runtimeIdentity.app_version || '开发模式' }} · {{ runtimeIdentity.build_id || '尚未生成' }}</small></span><b :class="['connection-label', { online: runtimeIdentity.status === 'ok' }]">{{ runtimeIdentity.status === 'ok' ? '一致' : '需检查' }}</b></div><div class="settings-item"><span><strong>程序</strong><small>{{ runtimeIdentity.exe_path || '尚未报告' }}</small></span><b class="settings-readonly runtime-identity-value">{{ runtimeIdentity.source_mode ? '源码运行' : '正式程序' }}</b></div><div class="settings-item"><span><strong>业务运行根</strong><small>{{ runtimeIdentity.runtime_root || '尚未报告' }}</small></span><b class="settings-readonly runtime-identity-value">运行数据</b></div><div class="settings-item"><span><strong>桌面状态根</strong><small>{{ runtimeIdentity.state_root || '尚未报告' }}</small></span><b class="settings-readonly runtime-identity-value">日志与WebView</b></div><div class="settings-item"><span><strong>数据库</strong><small>{{ runtimeIdentity.database_path || '尚未报告' }}</small></span><b class="settings-readonly runtime-identity-value">SQLite</b></div></div><p v-for="warning in runtimeIdentity.warnings || []" :key="warning" class="runtime-identity-warning">{{ warning }}</p></div><div class="settings-section-block"><div class="settings-section-heading"><div><h2>窗口拓扑</h2><p>用于确认主应用、桌宠和备用窗口是否重复创建</p></div><b class="settings-readonly">活动 {{ companionStatus.window_topology?.active_count || 0 }} · 可见 {{ companionStatus.window_topology?.visible_count || 0 }}</b></div><div v-if="companionStatus.window_topology?.windows?.length" class="settings-list runtime-identity-list"><div v-for="item in companionStatus.window_topology.windows" :key="item.window_id" class="settings-item"><span><strong>{{ item.window_id }}</strong><small>{{ item.source }} · {{ item.runtime }} · PID {{ item.pid || '未知' }} · {{ item.action }}</small></span><b :class="['connection-label', { online: item.visible }]">{{ item.visible ? (item.focused ? '前台' : '可见') : '隐藏' }}</b></div></div><div v-else class="settings-empty-state">还没有窗口身份上报</div></div><div class="settings-section-block"><h2>服务状态</h2><div class="settings-list"><div class="settings-item"><span><strong>Mio后端</strong><small>主应用本地API与调度服务</small></span><b class="connection-label online">在线</b></div><div class="settings-item"><span><strong>QQ通道</strong><small>{{ qqStatus.diagnostic_message || '等待诊断' }}</small></span><b :class="['connection-label', { online: qqConnected }]">{{ qqStatusLabel }}</b></div><div class="settings-item"><span><strong>音色服务</strong><small>GPT-SoVITS本地合成服务</small></span><b :class="['connection-label', { online: companionStatus.voice_runtime?.service_running }]">{{ companionStatus.voice_runtime?.service_running ? '在线' : '未启动' }}</b></div><div class="settings-item"><span><strong>屏幕观察</strong><small>独立捕获进程与视觉分析</small></span><b :class="['connection-label', { online: companionStatus.screen?.running }]">{{ companionStatus.screen?.running ? '运行中' : '未启动' }}</b></div></div></div><div class="settings-section-block"><h2>运行组件路径</h2><div class="settings-form-grid path-settings-grid"><label><span>NapCat目录</span><input v-model.trim="runtimeSettingsDraft.napcat_dir" type="text" /></label><label><span>NapCat WebUI</span><input v-model.trim="runtimeSettingsDraft.napcat_webui_url" type="url" /></label><label><span>Mio音色目录</span><input v-model.trim="runtimeSettingsDraft.voice_training_dir" type="text" /></label><label><span>本地视觉目录</span><input v-model.trim="runtimeSettingsDraft.local_vision_dir" type="text" /></label></div></div><div class="settings-section-block"><h2>日志与诊断</h2><div class="settings-list"><div class="settings-item"><span><strong>桌面日志</strong><small>自动轮转，单文件达到2MB后保留最近3份</small></span><b class="settings-readonly">已启用</b></div><div class="settings-item"><span><strong>服务重启</strong><small>QQ、音色与观察服务可在对应分类中单独重启，避免影响其他功能</small></span><b class="settings-readonly">分服务控制</b></div><div class="settings-item"><span><strong>恢复默认设置</strong><small>当前不提供全局一键恢复，避免覆盖人格、路径和私人配置</small></span><b class="settings-readonly">需逐项修改</b></div></div></div></template><div v-else class="settings-empty-state">正在读取高级设置</div>
      <div class="settings-savebar"><span>路径和部分运行参数可能需要重启应用后完全生效</span><div><button type="button" :disabled="!isSettingsSectionDirty('advanced')" @click="resetActiveSettings">取消</button><button class="primary-button" type="button" :disabled="!isSettingsSectionDirty('advanced') || runtimeSettingsBusy" @click="saveActiveSettings"><Check :size="15" />保存</button></div></div>
    </section>
  </section>
</template>
