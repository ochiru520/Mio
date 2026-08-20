<script setup>
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import AppDialog from './components/AppDialog.vue'
import OnboardingPage from './components/OnboardingPage.vue'
// Load large work surfaces on first use so the initial desktop bundle stays
// small and the cold-start UI can become interactive sooner.
const ChatPage = defineAsyncComponent(() => import('./components/ChatPage.vue'))
const CompanionPage = defineAsyncComponent(() => import('./components/CompanionPage.vue'))
const HomePage = defineAsyncComponent(() => import('./components/HomePage.vue'))
const RecordsPage = defineAsyncComponent(() => import('./components/RecordsPage.vue'))
const SettingsPage = defineAsyncComponent(() => import('./components/SettingsPage.vue'))
const TasksPage = defineAsyncComponent(() => import('./components/TasksPage.vue'))
import { buildTurnVoicePayload, voiceLanguageLabel } from './chatVoice.js'
import { ACTIVE_VIEW_HEARTBEAT_MS, buildActiveViewReport } from './activeViewState.js'
import { bargeInThreshold, bytesToBase64, encodePcmWav } from './petCallAudio.js'
import { apiRequest } from './services/api.js'
import { focusModal, restoreModalFocus, trapModalFocus } from './modalFocus.js'
import * as chatApi from './services/chatApi.js'
import * as autonomyApi from './services/autonomyApi.js'
import * as diaryApi from './services/diaryApi.js'
import * as memoryApi from './services/memoryApi.js'
import * as observationApi from './services/observationApi.js'
import * as qqApi from './services/qqApi.js'
import * as settingsApi from './services/settingsApi.js'
import * as selfStateApi from './services/selfStateApi.js'
import * as voiceApi from './services/voiceApi.js'
import {
  completeOnboarding as completeOnboardingRequest,
  loadOnboardingEnvironment,
  loadOnboardingStatus,
} from './services/onboardingApi.js'
import {
  createCompleteBackup,
  importCompleteBackup,
  loadDataPrivacy,
  restoreCompleteBackup,
  setPrivacyPaused,
} from './services/dataPrivacyApi.js'
import {
  Activity,
  Archive,
  ArrowLeft,
  BookOpen,
  Bot,
  CalendarDays,
  Check,
  ChartNoAxesCombined,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Copy,
  Download,
  Feather,
  FileText,
  FilePenLine,
  FolderOpen,
  Gamepad2,
  Heart,
  History,
  ImagePlus,
  LogIn,
  MessageSquareText,
  Maximize2,
  Minus,
  Monitor,
  MoreHorizontal,
  Paperclip,
  Pin,
  PinOff,
  Phone,
  PhoneOff,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Play,
  Plus,
  Power,
  RefreshCw,
  RotateCw,
  Search,
  Send,
  Settings,
  Sparkles,
  Square,
  SquarePen,
  Target,
  ListChecks,
  Trash2,
  Volume2,
  Wifi,
  WifiOff,
  Wrench,
  X,
} from '@lucide/vue'

const THEME_OPTIONS = [
  { id: 'mist', label: '柔雾', description: '淡粉与雾灰', colors: ['#f2f0f1', '#c7849c', '#4f927b'] },
  { id: 'clear', label: '清晨', description: '清蓝与浅灰', colors: ['#edf4f5', '#5b8c98', '#d6a26f'] },
  { id: 'rose', label: '蔷薇', description: '柔粉与葡萄灰', colors: ['#f4edef', '#a66f82', '#658e83'] },
  { id: 'forest', label: '森雨', description: '苔绿与暖白', colors: ['#edf1ed', '#668978', '#b48468'] },
  { id: 'sakura', label: '晚樱', description: '樱粉与暮蓝', colors: ['#f3eef1', '#b7798f', '#687f9b'] },
]
const VALID_THEME_IDS = new Set(THEME_OPTIONS.map((item) => item.id))

const DEFAULT_APP_PREFERENCES = {
  theme: 'mist',
  font_size: 'medium',
  default_open_page: 'home',
  left_sidebar_visible: true,
  right_sidebar_visible: true,
  left_sidebar_hover_expand: true,
  right_sidebar_hover_expand: true,
  remember_sidebar_state: true,
  light_animations: true,
  focus_mode: false,
  display_mode: 'full',
  visibility: {
    global_statusbar: true,
    status_mood: true,
    status_relationship: true,
    status_date: true,
    chat_statusbar: true,
    message_time: true,
    response_latency: true,
    message_cost: true,
    message_model: true,
    message_tokens: true,
    message_copy: true,
    message_play: true,
    memory_status: true,
    ai_analysis: true,
    developer_details: false,
    settings_character: true,
  },
  home_widgets: {
    daily_thirty: true,
    mood: true,
    diary: true,
    memory: true,
    agent_status: true,
    screen_status: true,
    api_info: false,
    token_stats: false,
  },
}

const DISPLAY_MODE_PRESETS = {
  companion: {
    response_latency: false,
    message_cost: false,
    message_model: false,
    message_tokens: false,
    memory_status: false,
    ai_analysis: false,
    developer_details: false,
  },
  full: {
    response_latency: true,
    message_cost: true,
    message_model: true,
    message_tokens: true,
    memory_status: true,
    ai_analysis: true,
    developer_details: false,
  },
  developer: {
    response_latency: true,
    message_cost: true,
    message_model: true,
    message_tokens: true,
    memory_status: true,
    ai_analysis: true,
    developer_details: true,
  },
}

const displayModeOptions = [
  { id: 'companion', label: '陪伴', description: '只保留关系、状态和对话本身' },
  { id: 'full', label: '完整', description: '展示费用、模型、记忆和分析依据' },
  { id: 'developer', label: '开发者', description: '补充 Token、请求 ID 和运行细节' },
]

const visibilityOptions = [
  { id: 'global_statusbar', label: '顶部 Mio 状态', description: '显示头像、心情、关系和日期' },
  { id: 'status_mood', label: '顶部心情', description: '在顶部状态中显示今天的心情' },
  { id: 'status_relationship', label: '顶部关系', description: '在顶部状态中显示关系状态' },
  { id: 'status_date', label: '顶部日期', description: '在顶部状态中显示逻辑日期' },
  { id: 'chat_statusbar', label: '聊天状态条', description: '显示陪伴、记忆和 Agent 状态' },
  { id: 'message_time', label: '消息时间', description: '在每轮对话结束后显示真实时间' },
  { id: 'response_latency', label: '响应耗时', description: '显示模型真实返回的总耗时' },
  { id: 'message_cost', label: '回复费用', description: '只在 Mio 的回复后显示实扣或待确认费用' },
  { id: 'message_model', label: '模型信息', description: '显示本轮实际使用的模型与思考方式' },
  { id: 'message_tokens', label: 'Token 信息', description: '显示本轮输入、缓存、推理与输出 Token' },
  { id: 'message_copy', label: '复制按钮', description: '悬停回复时显示复制按钮' },
  { id: 'message_play', label: '播放按钮', description: '悬停 Mio 回复时显示语音播放按钮' },
  { id: 'memory_status', label: '记忆连接', description: '显示当前接入的长期记忆数量' },
  { id: 'ai_analysis', label: '分析依据', description: '允许展开 Mio 的观察和状态判断依据' },
  { id: 'developer_details', label: '开发细节', description: '显示请求 ID、来源和完整技术字段' },
  { id: 'settings_character', label: '设置页 Mio 形象', description: '宽屏设置页右侧显示 Mio 的全身形象' },
]

const homeWidgetOptions = [
  { id: 'daily_thirty', label: '今日成长', description: '今天是否完成自我提升' },
  { id: 'mood', label: '心情分析', description: '今天的情绪和评分' },
  { id: 'diary', label: '今日日记', description: '今天是否已经整理' },
  { id: 'memory', label: '最近记忆', description: '当前长期记忆数量' },
  { id: 'agent_status', label: 'Agent 状态', description: '空闲、思考或执行中' },
  { id: 'screen_status', label: '屏幕状态', description: '桌宠和屏幕观察是否运行' },
  { id: 'api_info', label: 'API 信息', description: '当前模型与连接方式' },
  { id: 'token_stats', label: 'Token 统计', description: '当前上下文使用量' },
]

const profileBehaviorLabels = {
  initiative: '主动联系',
  diary: '日记行为',
  web_search: '联网原则',
  time_awareness: '时间意识',
  daily_thirty_awareness: '每日三十判断',
  autonomous_actions: '主动执行',
  pending_threads: '待跟进话题',
  curiosity: '好奇心',
  mood_quirks: '小情绪',
  scenario: '角色场景',
  character_rules: '角色规则与系统提示',
  reply_rules: '回复后置规则',
  dialogue_examples: '示例对话',
  worldbook_rules: '世界书规则',
}

function readAppPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem('mio_app_preferences') || '{}')
    const source = saved && typeof saved === 'object' ? saved : {}
    const merged = {
      ...DEFAULT_APP_PREFERENCES,
      ...source,
      visibility: { ...DEFAULT_APP_PREFERENCES.visibility, ...(source.visibility || {}) },
      home_widgets: { ...DEFAULT_APP_PREFERENCES.home_widgets, ...(source.home_widgets || {}) },
    }
    if (!VALID_THEME_IDS.has(merged.theme)) merged.theme = 'mist'
    if (!['home', 'chat', 'diaries', 'memory', 'tasks', 'companion'].includes(merged.default_open_page)) {
      merged.default_open_page = 'home'
    }
    if (!Object.prototype.hasOwnProperty.call(DISPLAY_MODE_PRESETS, merged.display_mode)) merged.display_mode = 'full'
    if (!Object.prototype.hasOwnProperty.call(saved || {}, 'left_sidebar_visible')) {
      merged.left_sidebar_visible = localStorage.getItem('mio_left_sidebar_visible') !== 'false'
    }
    if (!Object.prototype.hasOwnProperty.call(saved || {}, 'right_sidebar_visible')) {
      merged.right_sidebar_visible = localStorage.getItem('mio_right_sidebar_visible') !== 'false'
    }
    return merged
  } catch {
    return {
      ...DEFAULT_APP_PREFERENCES,
      visibility: { ...DEFAULT_APP_PREFERENCES.visibility },
      home_widgets: { ...DEFAULT_APP_PREFERENCES.home_widgets },
    }
  }
}

function cloneAppPreferences(source) {
  return {
    ...DEFAULT_APP_PREFERENCES,
    ...(source || {}),
    visibility: { ...DEFAULT_APP_PREFERENCES.visibility, ...(source?.visibility || {}) },
    home_widgets: { ...DEFAULT_APP_PREFERENCES.home_widgets, ...(source?.home_widgets || {}) },
  }
}

function applyStoredAppPreferences() {
  const next = cloneAppPreferences(readAppPreferences())
  savedAppPreferences.value = next
  appPreferencesDraft.value = cloneAppPreferences(next)
  leftSidebarVisible.value = next.focus_mode ? false : next.left_sidebar_visible !== false
  rightSidebarVisible.value = next.focus_mode ? false : next.right_sidebar_visible !== false
}

function handleAppPreferencesStorage(event) {
  if (event.key === 'mio_app_preferences') applyStoredAppPreferences()
}

const initialAppPreferences = readAppPreferences()
const validInitialViews = new Set(['home', 'chat', 'diaries', 'memory', 'tasks', 'companion'])
const activeView = ref(validInitialViews.has(initialAppPreferences.default_open_page) ? initialAppPreferences.default_open_page : 'home')
const settingsReturnView = ref(activeView.value)
const activeCompanionSection = ref('pet-panel')
const settingsSectionFromUrl = new URLSearchParams(window.location.search).get('settings-section')
const activeSettingsSection = ref(settingsSectionFromUrl || localStorage.getItem('mio_settings_section') || 'general')
const settingsSearch = ref('')
const settingsFeedback = ref({ section: '', type: '', message: '' })
const savedAppPreferences = ref(cloneAppPreferences(initialAppPreferences))
const appPreferencesDraft = ref(cloneAppPreferences(savedAppPreferences.value))
const chatSettingsDraft = ref({ model_id: '', reasoning_level: '', voice_language: 'auto' })
const savedChatSettings = ref('')
const savedStartupGreeting = ref(true)
const desktopPreferencesDraft = ref({ close_to_background: true, background_notifications: true, windows_startup: false })
const savedDesktopPreferences = ref('')
const desktopPreferencesReady = ref(false)
const desktopPreferencesBusy = ref(false)
const qqStartupEnabled = ref(false)
const savedQqStartupEnabled = ref(false)
const savedGroupChatSettings = ref('')
const savedCompanionSettings = ref({ pet: '', voice: '', observation: '' })
const runtimeSettingsDraft = ref({})
const savedRuntimeSettings = ref('')
const runtimeSettingsReady = ref(false)
const runtimeSettingsBusy = ref(false)
const webSearchTestQuery = ref('帮我查一下 DeepSeek 最新消息')
const webSearchTestBusy = ref(false)
const webSearchTestResult = ref(null)
const mioProfileDraft = ref(null)
const mioProfileNotesDraft = ref('')
const mioProfileAvoidDraft = ref('')
const savedMioProfile = ref('')
const mioProfileReady = ref(false)
const mioProfileBusy = ref(false)
const profileAvatarNonce = ref(Date.now())
const profileAvatarCustom = ref(false)
const userAvatarNonce = ref(Date.now())
const userAvatarCustom = ref(false)
const chatBackgroundNonce = ref(Date.now())
const chatBackgroundCustom = ref(false)
const standalonePetChat = window.location.hash === '#pet-chat-window'
const leftSidebarVisible = ref(savedAppPreferences.value.left_sidebar_visible !== false)
const rightSidebarVisible = ref(savedAppPreferences.value.right_sidebar_visible !== false)
const leftSidebarPinned = ref(localStorage.getItem('mio_left_sidebar_pinned') === 'true')
const rightSidebarPinned = ref(localStorage.getItem('mio_right_sidebar_pinned') === 'true')
const leftSidebarHovered = ref(false)
const rightSidebarHovered = ref(false)
const bootstrap = ref(null)
const providerPresets = computed(() => bootstrap.value?.provider_presets || [])
const onboardingBusy = ref(false)
const onboardingError = ref('')
const onboardingEnvironment = ref(null)
const onboardingEnvironmentBusy = ref(false)
const dataPrivacyState = ref({ backups: [], privacy: { paused: false, capabilities: [] }, migrations: {} })
const dataPrivacyLoading = ref(false)
const dataPrivacyBusy = ref('')
const appDialog = reactive({
  open: false,
  mode: 'confirm',
  title: '请确认',
  message: '',
  value: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
  multiline: false,
  resolve: null,
})

function settleAppDialog(result) {
  const resolve = appDialog.resolve
  appDialog.open = false
  appDialog.resolve = null
  if (resolve) resolve(result)
}

function showAppConfirm({ title = '请确认', message = '', confirmText = '确定', cancelText = '取消', danger = false } = {}) {
  return new Promise((resolve) => {
    Object.assign(appDialog, {
      open: true,
      mode: 'confirm',
      title,
      message,
      value: '',
      confirmText,
      cancelText,
      danger,
      multiline: false,
      resolve,
    })
  })
}

function showAppPrompt({ title, message = '', value = '', confirmText = '保存', multiline = false } = {}) {
  return new Promise((resolve) => {
    Object.assign(appDialog, {
      open: true,
      mode: 'prompt',
      title,
      message,
      value,
      confirmText,
      cancelText: '取消',
      danger: false,
      multiline,
      resolve,
    })
  })
}

function confirmAppDialog() {
  settleAppDialog(appDialog.mode === 'prompt' ? appDialog.value : true)
}

function cancelAppDialog() {
  settleAppDialog(appDialog.mode === 'prompt' ? null : false)
}

const messages = ref([])
const conversations = ref([])
const selectedConversationId = ref(localStorage.getItem('mio_conversation_id') || '')
const diaries = ref([])
const selectedDiary = ref(null)
const diarySearch = ref('')
const statsData = ref({ summary: {}, calendar: [], mood_trend: [], year: 0, month: 0, logical_date: '' })
const statsLoading = ref(false)
const statsLoaded = ref(false)
const agentTasks = ref([])
const tasksLoading = ref(false)
const tasksLoaded = ref(false)
const taskBusy = ref('')
const taskStatusFilter = ref('all')
const taskCenterTab = ref('actions')
const autonomyData = ref({
  policy: {
    paused: false,
    autonomy_level: 'suggest',
    quiet_start_hour: 22,
    quiet_end_hour: 8,
    minimum_interval_minutes: 120,
    daily_behavior_limit: 3,
    daily_budget_yuan: 0.05,
    capability_overrides: {},
  },
  goals: [],
  events: [],
  behaviors: [],
  usage: {},
})
const autonomyLoading = ref(false)
const autonomyLoaded = ref(false)
const autonomyBusy = ref('')
const newAutonomyGoalTitle = ref('')
const newAutonomyGoalCapability = ref('follow_up_reminder')
const draft = ref('')
const sending = ref(false)
const loading = ref(true)
const errorMessage = ref('')
const chatScroll = ref(null)
const fileInput = ref(null)
const modelPicker = ref(null)
const tokenUsageDialog = ref(null)
const providerDialog = ref(null)
if (localStorage.getItem('mio_auto_router_version') !== '1') {
  localStorage.setItem('mio_model_id', 'auto')
  localStorage.setItem('mio_reasoning_level', 'auto')
  localStorage.setItem('mio_auto_router_version', '1')
}
const reasoningLevel = ref(localStorage.getItem('mio_reasoning_level') || 'auto')
const selectedModel = ref(localStorage.getItem('mio_model_id') || 'auto')
const chatVoiceLanguage = ref('auto')
const showModelMenu = ref(false)
const modelMenuSection = ref('root')
const contextUsage = ref({ used_chars: 0, max_chars: 18000, percent: 0, has_summary: false })
const attachments = ref([])
const isFileDragging = ref(false)
const qqBusy = ref('')
const qqQrImageUrl = ref('')
const qqQrLoading = ref(false)
const qqQrError = ref('')
const qqAccountDraft = ref('')
const qqTestTargetDraft = ref('')
const qqSetupResult = ref(null)
const startupGreetingEnabled = ref(true)
const groupChatSettings = ref({
  enabled: false,
  group_ids: [],
  mention_required: true,
  context_message_count: 0,
  active_group_count: 0,
})
const groupIdsDraft = ref('')
const companionBusy = ref('')
const petChatDraft = ref('')
const petChatSending = ref(false)
const petChatImages = ref([])
const petCallActive = ref(false)
const petCallState = ref('idle')
const petCallError = ref('')
const petCallSettings = ref({
  pet_call_asr_engine: 'auto',
  pet_call_input_language: 'zh',
  pet_call_silence_ms: 650,
  pet_call_voice_threshold: 0.018,
  pet_call_min_speech_ms: 280,
  pet_call_max_turn_seconds: 18,
})
let petCallStream = null
let petCallAudioContext = null
let petCallSource = null
let petCallProcessor = null
let petCallMonitorTimer = null
let petCallFrames = []
let petCallPreRoll = []
let petCallSpeechStartedAt = 0
let petCallSpeechCandidateStartedAt = 0
let petCallLastVoiceAt = 0
let petCallTurnPending = false
let petCallInterruptSent = false
let petCallBargeInStartedAt = 0
let petCallSessionId = ''
let petCallNextTurnId = 1
let petCallResponseId = ''
let petCallAwaitingVoiceSince = 0
const companionAvatarNonce = ref(Date.now())
const observationMode = ref('game')
const screenScope = ref('primary')
const observationInterval = ref(1000)
const gameWindows = ref([])
const selectedGameHwnd = ref('')
const gameWindowsLoading = ref(false)
const companionStatus = ref({
  pet: {
    running: false,
    avatar_available: false,
    sprite_states: [],
    sprite_set_ready: false,
    sprite_expected_count: 6,
    activity: { state: 'idle', label: '安静待机', emotion: 'neutral', emotion_label: '自然' },
    settings: {
      voice_enabled: true,
      voice_startup_enabled: false,
      voice_idle_timeout_seconds: 180,
      voice_engine: 'gpt_sovits',
      local_voice_runtime: 'genie',
      default_voice_profile_id: 'mio',
      voice_profiles: {
        mio: {
          name: 'Mio 专属音色',
          engine: 'gpt_sovits',
          gpt_sovits_ref_audio: '',
          gpt_sovits_prompt_text: '',
          gpt_sovits_prompt_language: 'ja',
          gpt_sovits_text_language: 'auto',
          gpt_sovits_translate_to_japanese: false,
          gpt_sovits_gpt_weights: '',
          gpt_sovits_sovits_weights: '',
          use_emotion_references: true,
        },
      },
      chat_model_id: 'auto',
      chat_reasoning_level: 'auto',
      pet_chat_model_id: 'auto',
      pet_chat_reasoning_level: 'auto',
      pet_call_asr_engine: 'auto',
      pet_call_input_language: 'zh',
      speech_translation_model_id: 'deepseek-v4-flash',
      pet_call_silence_ms: 650,
      pet_call_voice_threshold: 0.018,
      pet_call_min_speech_ms: 280,
      pet_call_max_turn_seconds: 18,
      voice_volume: 85,
      voice_streaming_enabled: true,
      pet_speech_language: 'zh',
      speak_proactive: false,
      speak_screen_observations: false,
      speak_game_observations: true,
      qq_voice_mode: 'adaptive',
      gpt_sovits_url: 'http://127.0.0.1:9880',
      local_voice_runtime: 'genie',
      gpt_sovits_ref_audio: '',
      gpt_sovits_prompt_text: '',
      gpt_sovits_prompt_language: 'ja',
      gpt_sovits_text_language: 'auto',
      gpt_sovits_translate_to_japanese: false,
      gpt_sovits_gpt_weights: '',
      gpt_sovits_sovits_weights: '',
      screen_ai_enabled: true,
      screen_audio_enabled: true,
      screen_audio_model: 'base',
      screen_audio_language: 'auto',
      screen_audio_chunk_seconds: 5,
      screen_vision_route: 'local',
      screen_vision_model_id: 'auto-fast',
      screen_direct_voice_enabled: true,
      screen_change_threshold: 4,
      screen_analysis_interval_seconds: 5,
      screen_request_timeout_seconds: 25,
      screen_voice_cooldown_seconds: 5,
      screen_minimum_importance: 0.62,
      screen_daily_cost_limit_yuan: 5,
      bubble_seconds: 9,
      pet_size_percent: 150,
      live2d_click_through_locked: false,
      live2d_speech_bubble_enabled: true,
      live2d_keep_visible: false,
    },
  },
  screen: { running: false, preview_available: false, change_percent: 0, title: '主屏幕', error: '', capture_backend: 'imagegrab', capture_backend_error: '', interval_ms: 1000, screen_scope: 'primary' },
  screen_analysis: {
    enabled: true,
    capture_only: false,
    vision_available: false,
    vision_model_options: [],
    selected_vision_model_id: '',
    in_progress: false,
    last_analyzed_at: '',
    last_reply: '',
    last_error: '',
    last_model: '',
    last_cost_yuan: null,
    change_threshold: 4,
    minimum_interval_seconds: 30,
    budget: {
      paused: false,
      paused_reason: '',
      session_request_count: 0,
      session_cost_yuan: 0,
      session_unknown_cost_count: 0,
      daily: { request_count: 0, total_cost_yuan: 0, unknown_cost_count: 0 },
      daily_cost_limit_yuan: 5,
    },
    pipeline_timings: {},
  },
  voice_runtime: { engine: 'gpt_sovits', engine_label: 'GPT-SoVITS', service_running: false, service_loading: false, managed_running: false, reference_ready: false, reference_name: '', prompt_ready: false, emotion_reference_ready: false, emotion_reference_count: 0, active_weights: { gpt: '', sovits: '' }, last_error: '', weights: { gpt: [], sovits: [] } },
  voice_training: {
    source_ready: false,
    environment_ready: false,
    pretrained_ready: false,
    trained_ready: false,
    model_count: 0,
    expected_model_count: 9,
    material_count: 0,
    stage: '',
    message: '',
  },
})
const companionStatusReady = ref(false)
const diaryBusy = ref('')
const memoryTab = ref('notebook')
const memoryData = ref({
  threads: [],
  summaries: [],
  structured: [],
  structured_candidates: [],
  structured_history: [],
  follow_up_results: [],
  profile: null,
  runtime_summary: null,
})
const runtimeSummaryDraft = ref('')
const newThreadContent = ref('')
const newThreadFollowUp = ref('')
const newProfileNote = ref('')
const newConversationSummary = ref('')
const newStructuredMemory = ref({ layer: 'L0', category: 'preference', memory_key: '', content: '' })
const memoryBusy = ref('')
const dailyReviews = ref([])
const weeklyReviews = ref([])
const monthlyReviews = ref([])
const selectedDailyDate = ref('')
const selectedWeeklyStart = ref('')
const selectedMonthlyMonth = ref('')
const memoryLoading = ref(false)
const memoryLoaded = ref(false)
const reviewBusy = ref('')
const copiedTurnId = ref('')
const speakingPartId = ref('')
const voiceLoadingPartId = ref('')
const modelTestBusy = ref('')
const modelTestStatus = ref({})
const showProviderPanel = ref(false)
const showTokenUsagePanel = ref(false)
const tokenUsageLoading = ref(false)
const tokenUsageData = ref({ today: {}, total: {}, days: [], logical_day_boundary_hour: 4 })
const providerBusy = ref(false)
const providerDiscoveryBusy = ref(false)
const providerDiscoveryWarning = ref('')
const providerDiscoveryMeta = ref(null)
const discoveredModels = ref([])
const providerForm = ref({
  preset_id: '',
  provider_kind: 'relay',
  provider_protocol: 'openai',
  default_api_mode: 'auto',
  provider_name: '',
  model: '',
  base_url: '',
  api_key: '',
  supports_vision: false,
  cached_input_price_cny_per_million: 0,
  input_price_cny_per_million: 0,
  output_price_cny_per_million: 0,
})
let pollTimer = null
let dashboardPollTimer = null
let qqStatusPollTimer = null
let companionPollTimer = null
let activeViewHeartbeatTimer = null
let fileDragDepth = 0
let activeMessageAudio = null
let activeMessageAudioUrl = ''
let voiceAbortController = null
let chatAbortController = null
let activeChatCancelPayload = null
let companionStatusLoading = false
let messageRefreshInFlight = false
let conversationLoadVersion = 0
let qqQrLoadVersion = 0
let tokenUsageFocusBeforeOpen = null
let providerFocusBeforeOpen = null

watch(showTokenUsagePanel, async (open) => {
  if (open) {
    tokenUsageFocusBeforeOpen = document.activeElement
    await nextTick()
    focusModal(tokenUsageDialog.value)
    return
  }
  const target = tokenUsageFocusBeforeOpen
  tokenUsageFocusBeforeOpen = null
  await nextTick()
  restoreModalFocus(target)
})

watch(showProviderPanel, async (open) => {
  if (open) {
    providerFocusBeforeOpen = document.activeElement
    await nextTick()
    focusModal(providerDialog.value)
    return
  }
  const target = providerFocusBeforeOpen
  providerFocusBeforeOpen = null
  await nextTick()
  restoreModalFocus(target)
})

function closeTokenUsagePanel() {
  showTokenUsagePanel.value = false
}

function closeProviderPanel() {
  showProviderPanel.value = false
}

function onTokenUsageDialogKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeTokenUsagePanel()
    return
  }
  trapModalFocus(event, tokenUsageDialog.value)
}

function onProviderDialogKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeProviderPanel()
    return
  }
  trapModalFocus(event, providerDialog.value)
}

const navGroups = [{
  label: 'Mio 的空间',
  items: [
    { id: 'home', label: '首页', icon: Sparkles },
    { id: 'chat', label: '对话', icon: MessageSquareText },
    { id: 'diaries', label: '日记', icon: BookOpen },
    { id: 'memory', label: '记忆', icon: Archive },
    { id: 'tasks', label: '任务', icon: ListChecks },
    { id: 'companion', label: '桌宠', icon: Gamepad2 },
    { id: 'settings', label: '设置', icon: Settings },
  ],
}]
const navItems = [
  ...navGroups.flatMap((group) => group.items),
  { id: 'stats', label: '统计', icon: ChartNoAxesCombined },
]
const settingsNavigation = [{
  label: '设置',
  items: [
    { id: 'general', label: '基础与启动', description: '启动、后台与主动消息', icon: Settings, keywords: '启动 后台 问候 主动 通知 默认页面 更新' },
    { id: 'appearance', label: '外观与界面', description: '主题、字体、侧栏与消息信息', icon: Feather, keywords: '主题 字体 侧栏 动画 专注 时间 模型 Token 费用' },
    { id: 'profile', label: '人格与关系', description: '身份、关系、表达与行为边界', icon: Heart, keywords: '人格 身份 称呼 亲密 性格 语气 情绪 行为 属性 角色卡' },
    { id: 'conversation', label: '对话与记忆', description: '上下文、联网、附件与长期记忆', icon: MessageSquareText, keywords: '对话 记忆 上下文 压缩 联网 附件 图片 PDF Word OCR' },
    { id: 'diary', label: '日记与成长', description: '自动日记、周记与成长判断', icon: CalendarDays, keywords: '日记 周记 月记 成长 每日三十 确认 备份' },
    { id: 'models', label: '模型与 API', description: '模型、供应商与自动选择', icon: Bot, keywords: '模型 API 供应商 密钥 价格 思考 自动选择' },
    { id: 'dependencies', label: '环境与模型', description: '检查本机依赖并一键安装', icon: Wrench, keywords: '环境 依赖 模型 安装 下载 GPT-SoVITS NapCat Ollama 视觉 whisper 语音 检查' },
    { id: 'qq', label: 'QQ', description: '登录、NapCat、OneBot与群聊', icon: Wifi, keywords: 'QQ NapCat OneBot 登录 二维码 私聊 群聊 语音' },
    { id: 'pet', label: '桌宠', description: '形象、语音、观察与行为', icon: Gamepad2, keywords: '桌宠 Live2D 语音 屏幕 观察 置顶 穿透 气泡 音色' },
    { id: 'data', label: '数据与隐私', description: '本地数据、导出与隐私边界', icon: Archive, keywords: '数据 隐私 导出 清理 数据库 日记 记忆' },
    { id: 'advanced', label: '高级设置', description: '服务、路径、日志与诊断', icon: Activity, keywords: '高级 服务 端口 日志 路径 诊断 恢复默认' },
  ],
}]
const recordViews = ['diaries', 'stats', 'memory']
const taskStatusFilters = [
  { id: 'all', label: '全部' },
  { id: 'running', label: '执行中' },
  { id: 'needs_confirmation', label: '待确认' },
  { id: 'executed', label: '已完成' },
  { id: 'failed', label: '异常' },
]
const autonomyLevelOptions = [
  { id: 'observe', label: '只观察' },
  { id: 'suggest', label: '主动建议' },
  { id: 'auto_low_risk', label: '低风险自动' },
  { id: 'confirm_high_risk', label: '高风险确认' },
]
const autonomyCapabilityOptions = [
  { id: 'follow_up_reminder', label: '到期跟进' },
  { id: 'daily_state', label: '今日状态' },
  { id: 'service_health', label: '服务健康' },
  { id: 'application_activity', label: '应用活动' },
  { id: 'task_result', label: '任务结果' },
  { id: 'proactive_checkin', label: '主动联系' },
  { id: 'night_close', label: '夜间收尾' },
  { id: 'screen_event', label: '重要屏幕变化' },
]
const autonomyOverrideOptions = [
  { id: '', label: '继承全局' },
  ...autonomyLevelOptions,
  { id: 'disabled', label: '关闭' },
]
const avatarUrl = computed(() => `/api/settings/avatar?v=${profileAvatarNonce.value}`)
const userAvatarUrl = computed(() => `/api/settings/user-avatar?v=${userAvatarNonce.value}`)
const chatBackgroundUrl = computed(() => `/api/settings/chat-background?v=${chatBackgroundNonce.value}`)
const chatBackgroundStyle = computed(() => chatBackgroundCustom.value
  ? { '--chat-background-image': `url("${chatBackgroundUrl.value}")` }
  : {})
const mioDisplayName = computed(() => {
  const name = mioProfileDraft.value?.identity?.name
    || memoryData.value?.profile?.identity?.name
    || 'Mio'
  return String(name).trim() || 'Mio'
})
const companionAvatarUrl = computed(() => {
  const hasIdleSprite = companionStatus.value.pet?.sprite_states?.includes('idle')
  const path = hasIdleSprite ? '/api/companion/sprite/idle' : '/api/companion/avatar'
  return `${path}?v=${companionAvatarNonce.value}`
})

const fallbackReasoningOptions = [
  { id: 'default', label: '模型默认', description: '由模型自行决定' },
]

const currentModel = computed(() => bootstrap.value?.model?.display_name || '正在读取')
const modelOptions = computed(() => bootstrap.value?.models || [])
const runtimeIdentity = computed(() => bootstrap.value?.runtime_identity || {})
const hiddenProviderNames = computed(() => bootstrap.value?.hidden_model_providers || [])
const modelGroups = computed(() => {
  const groups = new Map()
  for (const model of modelOptions.value) {
    const providerId = model.provider_id || `legacy:${model.provider_name || '其他供应商'}`
    if (!groups.has(providerId)) {
      groups.set(providerId, {
        provider_id: providerId,
        provider: model.provider_name || '其他供应商',
        models: [],
      })
    }
    groups.get(providerId).models.push(model)
  }
  return [...groups.values()]
})
const activeModel = computed(() => {
  if (selectedModel.value === 'auto') {
    return modelOptions.value.find((item) => item.id === bootstrap.value?.model?.id) || bootstrap.value?.model || null
  }
  return modelOptions.value.find((item) => item.id === selectedModel.value) || bootstrap.value?.model || null
})
const isAutoRouting = computed(() => selectedModel.value === 'auto')
const activeModelSupportsVision = computed(() => isAutoRouting.value
  ? modelOptions.value.some((item) => item.supports_vision)
  : Boolean(activeModel.value?.supports_vision))
const activeReasoningOptions = computed(() => {
  if (isAutoRouting.value) {
    return [{ id: 'auto', label: '自动', description: '根据当前话题难度选择模型原生思考方式' }]
  }
  return activeModel.value?.reasoning_options?.length
    ? activeModel.value.reasoning_options
    : fallbackReasoningOptions
})
const settingsReasoningOptions = computed(() => {
  if (chatSettingsDraft.value.model_id === 'auto') {
    return [{ id: 'auto', label: '自动', description: '根据话题难度选择模型原生思考方式' }]
  }
  const profile = modelOptions.value.find((item) => item.id === chatSettingsDraft.value.model_id)
  return profile?.reasoning_options?.length ? profile.reasoning_options : fallbackReasoningOptions
})
const petReasoningOptions = computed(() => {
  const modelId = companionStatus.value.pet?.settings?.pet_chat_model_id || 'auto'
  if (modelId === 'auto') {
    return [{ id: 'auto', label: '自动', description: '根据桌宠对话难度选择模型原生思考方式' }]
  }
  const profile = modelOptions.value.find((item) => item.id === modelId)
  return profile?.reasoning_options?.length ? profile.reasoning_options : fallbackReasoningOptions
})
const filteredSettingsNavigation = computed(() => {
  const query = settingsSearch.value.trim().toLowerCase()
  if (!query) return settingsNavigation
  return settingsNavigation
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => `${item.label} ${item.description} ${item.keywords}`.toLowerCase().includes(query)),
    }))
    .filter((group) => group.items.length)
})
const activeSettingsItem = computed(() => settingsNavigation
  .flatMap((group) => group.items)
  .find((item) => item.id === activeSettingsSection.value) || settingsNavigation[0].items[0])
const appThemeClass = computed(() => {
  const previewTheme = activeView.value === 'settings' && activeSettingsSection.value === 'appearance'
    ? appPreferencesDraft.value.theme
    : savedAppPreferences.value.theme
  return `theme-${VALID_THEME_IDS.has(previewTheme) ? previewTheme : 'mist'}`
})
const displayVisibility = computed(() => savedAppPreferences.value.visibility || DEFAULT_APP_PREFERENCES.visibility)
const homeWidgetPreferences = computed(() => savedAppPreferences.value.home_widgets || DEFAULT_APP_PREFERENCES.home_widgets)
const displayMode = computed(() => savedAppPreferences.value.display_mode || 'full')
const structuredMemoryCount = computed(() => (
  (memoryData.value?.structured?.length || 0) + (memoryData.value?.summaries?.length || 0)
))
const agentRuntimeLabel = computed(() => {
  if (sending.value) return '正在思考'
  if (agentTasks.value.some((item) => ['queued', 'running'].includes(item.status))) return '正在执行'
  return '空闲'
})

const companionSettingKeys = {
  pet: [
    'bubble_seconds', 'pet_size_percent', 'pet_renderer', 'live2d_model_id', 'live2d_scale',
    'live2d_vertical_offset', 'live2d_follow_cursor', 'live2d_idle_motion', 'live2d_click_motion',
    'live2d_smart_passthrough', 'live2d_click_through_locked', 'live2d_speech_bubble_enabled',
    'live2d_keep_visible',
    'live2d_always_on_top', 'live2d_disable_gpu', 'live2d_motion_slots', 'live2d_expression_slots',
    'pet_chat_model_id', 'pet_chat_reasoning_level',
    'pet_call_asr_engine', 'pet_call_input_language', 'speech_translation_model_id', 'pet_call_silence_ms', 'pet_call_voice_threshold',
    'pet_call_min_speech_ms', 'pet_call_max_turn_seconds',
    'voice_enabled', 'voice_startup_enabled', 'voice_idle_timeout_seconds', 'voice_engine', 'local_voice_runtime', 'voice_volume', 'voice_streaming_enabled', 'speak_proactive',
    'default_voice_profile_id', 'voice_profiles',
    'pet_speech_language', 'qq_voice_mode', 'gpt_sovits_url', 'gpt_sovits_text_language',
    'gpt_sovits_translate_to_japanese', 'screen_ai_enabled', 'screen_audio_enabled',
    'screen_audio_model', 'screen_audio_language', 'screen_audio_chunk_seconds', 'screen_vision_route',
    'screen_vision_model_id', 'screen_direct_voice_enabled', 'screen_change_threshold',
    'screen_analysis_interval_seconds', 'screen_request_timeout_seconds', 'screen_voice_cooldown_seconds',
    'screen_minimum_importance', 'screen_daily_cost_limit_yuan', 'speak_screen_observations',
    'speak_game_observations',
  ],
  voice: [
    'voice_enabled', 'voice_startup_enabled', 'voice_idle_timeout_seconds', 'voice_engine', 'local_voice_runtime', 'voice_volume', 'voice_streaming_enabled', 'speak_proactive',
    'default_voice_profile_id', 'voice_profiles',
    'pet_speech_language', 'speech_translation_model_id',
    'qq_voice_mode', 'gpt_sovits_url', 'gpt_sovits_text_language', 'gpt_sovits_translate_to_japanese',
    'cloud_tts_api_key', 'cloud_tts_app_id', 'cloud_tts_speaker', 'cloud_tts_speech_rate',
  ],
  observation: [
    'screen_ai_enabled', 'screen_audio_enabled', 'screen_audio_model', 'screen_audio_language',
    'screen_audio_chunk_seconds', 'screen_vision_route', 'screen_vision_model_id', 'screen_direct_voice_enabled',
    'screen_change_threshold', 'screen_analysis_interval_seconds', 'screen_request_timeout_seconds',
    'screen_voice_cooldown_seconds',
    'screen_minimum_importance', 'screen_daily_cost_limit_yuan', 'speak_screen_observations',
    'speak_game_observations',
  ],
}

const runtimeSettingKeysBySection = {
  general: [
    'qq_proactive_enabled', 'qq_proactive_min_idle_minutes', 'qq_proactive_max_idle_minutes',
    'qq_proactive_day_start_hour', 'qq_proactive_day_end_hour', 'qq_proactive_check_seconds',
  ],
  conversation: [
    'chat_history_limit', 'chat_raw_history_limit', 'chat_context_max_chars', 'chat_recent_keep_messages',
    'memory_context_days', 'memory_context_max_chars', 'memory_context_messages_per_day',
    'chat_follow_up_capture_seconds', 'chat_follow_up_max_capture_count',
    'agent_attachment_max_count', 'agent_text_attachment_max_chars', 'agent_document_attachment_max_bytes',
    'agent_pdf_max_pages', 'agent_document_vision_max_pages', 'photo_archive_enabled',
    'web_search_enabled', 'web_search_max_results', 'web_search_timeout_seconds', 'web_page_max_chars',
  ],
  diary: [
    'daily_diary_auto_enabled', 'daily_diary_check_seconds', 'weekly_review_enabled',
    'weekly_review_hour', 'weekly_review_notify_qq', 'weekly_review_check_seconds',
    'monthly_review_enabled', 'monthly_review_hour', 'monthly_review_notify_qq', 'monthly_review_check_seconds',
    'backup_enabled', 'backup_keep_count', 'backup_check_seconds', 'day_boundary_hour',
    'night_close_enabled', 'night_close_start_hour', 'night_close_end_hour', 'night_close_min_quiet_minutes',
  ],
  qq: [
    'qq_bot_enabled', 'qq_allowed_user_ids', 'qq_image_enabled', 'qq_image_max_count',
    'qq_image_max_bytes', 'qq_image_detail', 'qq_image_send_to_model',
    'qq_message_debounce_seconds', 'qq_message_incomplete_debounce_seconds',
    'qq_delivery_ack_timeout_seconds', 'qq_delivery_max_retries',
    'qq_reply_initial_delay_seconds', 'qq_reply_delay_seconds',
  ],
  advanced: [
    'openai_timeout_seconds', 'openai_request_deadline_seconds', 'screen_reaction_timeout_seconds', 'screen_history_retention_days',
    'screen_history_max_rows', 'manual_max_chars', 'chat_temperature', 'action_planner_temperature',
    'timezone', 'napcat_dir', 'napcat_webui_url', 'voice_training_dir', 'local_vision_dir',
  ],
}

const privateRuntimePathKeys = [
  'persona_prompt_path',
  'runtime_summary_path',
  'personal_manual_path',
  'talent_manual_path',
]

function serializeSettings(value) {
  return JSON.stringify(value ?? null)
}

function rangeInputStyle(value, minimum, maximum) {
  const min = Number(minimum)
  const max = Number(maximum)
  const current = Number(value)
  const percent = max > min ? Math.max(0, Math.min(100, ((current - min) / (max - min)) * 100)) : 0
  return { '--range-progress': `${percent}%` }
}

function companionSettingsSnapshot(section, source = companionStatus.value.pet?.settings || {}) {
  return serializeSettings(Object.fromEntries((companionSettingKeys[section] || []).map((key) => [key, source[key]])))
}

function runtimeSettingsSnapshot(section, source = runtimeSettingsDraft.value) {
  return serializeSettings(Object.fromEntries(
    (runtimeSettingKeysBySection[section] || []).map((key) => [key, source?.[key]]),
  ))
}

function savedRuntimeSettingsSource() {
  try { return JSON.parse(savedRuntimeSettings.value || '{}') || {} } catch (_) { return {} }
}

function normalizedGroupChatSnapshot() {
  const groupIds = groupIdsDraft.value
    .replaceAll('，', ',')
    .replaceAll('；', ',')
    .split(/[;,]/)
    .map((item) => item.trim())
    .filter(Boolean)
  return serializeSettings({
    enabled: Boolean(groupChatSettings.value.enabled),
    mention_required: Boolean(groupChatSettings.value.mention_required),
    group_ids: [...new Set(groupIds)].sort(),
  })
}

function isSettingsSectionDirty(section) {
  if (section === 'general') {
    return startupGreetingEnabled.value !== savedStartupGreeting.value
      || (desktopPreferencesReady.value && serializeSettings(desktopPreferencesDraft.value) !== savedDesktopPreferences.value)
      || appPreferencesDraft.value.default_open_page !== savedAppPreferences.value.default_open_page
      || runtimeSettingsSnapshot('general') !== runtimeSettingsSnapshot('general', savedRuntimeSettingsSource())
  }
  if (section === 'appearance') {
    const keys = ['theme', 'font_size', 'left_sidebar_visible', 'right_sidebar_visible', 'left_sidebar_hover_expand', 'right_sidebar_hover_expand', 'remember_sidebar_state', 'light_animations', 'focus_mode']
    return serializeSettings({ values: Object.fromEntries(keys.map((key) => [key, appPreferencesDraft.value[key]])), visibility: appPreferencesDraft.value.visibility })
      !== serializeSettings({ values: Object.fromEntries(keys.map((key) => [key, savedAppPreferences.value[key]])), visibility: savedAppPreferences.value.visibility })
  }
  if (section === 'conversation') return serializeSettings(chatSettingsDraft.value) !== savedChatSettings.value
    || runtimeSettingsSnapshot('conversation') !== runtimeSettingsSnapshot('conversation', savedRuntimeSettingsSource())
  if (section === 'diary') return runtimeSettingsReady.value
    && runtimeSettingsSnapshot('diary') !== runtimeSettingsSnapshot('diary', savedRuntimeSettingsSource())
  if (section === 'profile') return mioProfileReady.value && serializeSettings({ profile: mioProfileDraft.value, notes: mioProfileNotesDraft.value, avoid: mioProfileAvoidDraft.value }) !== savedMioProfile.value
  if (section === 'qq') return normalizedGroupChatSnapshot() !== savedGroupChatSettings.value
    || qqStartupEnabled.value !== savedQqStartupEnabled.value
    || runtimeSettingsSnapshot('qq') !== runtimeSettingsSnapshot('qq', savedRuntimeSettingsSource())
  if (section === 'advanced') return runtimeSettingsReady.value
    && runtimeSettingsSnapshot('advanced') !== runtimeSettingsSnapshot('advanced', savedRuntimeSettingsSource())
  if (section === 'pet') {
    if (!companionStatusReady.value) return false
    return companionSettingsSnapshot(section) !== savedCompanionSettings.value[section]
  }
  return false
}
const compactActiveModelLabel = computed(() => isAutoRouting.value
  ? '自动'
  : compactModelLabel(activeModel.value || currentModel.value))
const compactCurrentReasoningLabel = computed(() => isAutoRouting.value
  ? '自动'
  : compactReasoningLabel(reasoningLevel.value))
const contextPercent = computed(() => Math.max(0, Math.min(100, Number(contextUsage.value?.percent || 0))))
const contextRingStyle = computed(() => ({ '--context-percent': `${contextPercent.value}%` }))
const contextUsageLabel = computed(() => {
  const used = Number(contextUsage.value?.used_chars || 0).toLocaleString('zh-CN')
  const limit = Number(contextUsage.value?.max_chars || 18000).toLocaleString('zh-CN')
  return `当前上下文：${used} / ${limit} 字（${contextPercent.value.toFixed(1)}%）`
})
const selectedConversation = computed(() => conversations.value.find((item) => item.id === selectedConversationId.value) || null)
const chatSubtitle = computed(() => {
  if (selectedConversation.value?.kind === 'qq') return `${logicalDate.value} · 与QQ共享上下文`
  if (selectedConversation.value?.kind === 'pet') return `${logicalDate.value} · 桌宠独立上下文 · 自动语音回复`
  return `${logicalDate.value} · 独立桌面对话`
})
const qqStatus = computed(() => bootstrap.value?.qq || {})
const qqConnected = computed(() => Boolean(bootstrap.value?.qq?.connected))
const qqStatusLabel = computed(() => {
  if (qqConnected.value) return '在线'
  if (qqStatus.value.diagnostic_code === 'account_mismatch') return '账号不一致'
  if (qqStatus.value.diagnostic_code === 'login_required') return 'QQ未登录'
  if (qqStatus.value.diagnostic_code === 'onebot_disconnected') return '消息通道未连接'
  if (qqStatus.value.websocket_connected) return '仅桥接'
  if (qqStatus.value.webui_reachable) return 'NapCat已启动'
  return '离线'
})
const qqStatusCopy = computed(() => {
  if (qqConnected.value) return 'QQ消息正在同步到当前会话。'
  const runtime = qqStatus.value.proactive_runtime || {}
  const nextPrompt = runtime.next_prompt_at ? `主动消息计划：${formatShortTime(runtime.next_prompt_at)}` : ''
  const reason = runtime.app_active ? 'QQ离线不影响应用内主动消息。' : '应用关闭时会暂停主动消息，重新打开后立即检查。'
  return [qqStatus.value.diagnostic_message || 'QQ通道未连接，桌面聊天不受影响。', nextPrompt, reason].filter(Boolean).join(' ')
})
const qqDiagnosticItems = computed(() => [
  { label: '控制脚本', ok: Boolean(qqStatus.value.control_scripts_ready) },
  { label: 'NapCat文件', ok: Boolean(qqStatus.value.napcat_executable_exists) },
  { label: 'NapCat进程', ok: Boolean(qqStatus.value.napcat_process_running) },
  { label: '机器人QQ', ok: Boolean(qqStatus.value.qq_process_running) },
  { label: 'WebUI配置', ok: Boolean(qqStatus.value.webui_config_ready) },
  { label: 'NapCat在线', ok: Boolean(qqStatus.value.webui_reachable) },
  { label: 'QQ登录', ok: Boolean(qqStatus.value.logged_in) },
  { label: 'OneBot连接', ok: Boolean(qqStatus.value.websocket_connected) },
])
const logicalDate = computed(() => bootstrap.value?.logical_date || '')
const attachmentLimits = computed(() => ({
  maxCount: Number(bootstrap.value?.attachment_limits?.max_count || 5),
  imageMaxBytes: Number(bootstrap.value?.attachment_limits?.image_max_bytes || 8 * 1024 * 1024),
  documentMaxBytes: Number(bootstrap.value?.attachment_limits?.document_max_bytes || 20 * 1024 * 1024),
  textMaxBytes: Number(bootstrap.value?.attachment_limits?.text_max_bytes || 512 * 1024),
  textMaxChars: Number(bootstrap.value?.attachment_limits?.text_max_chars || 200000),
}))
const todayState = computed(() => bootstrap.value?.today_state || {})
const timeGreeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 4) return '夜深了'
  if (hour < 11) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  if (hour < 23) return '晚上好'
  return '夜深了'
})
const statusMoodLabel = computed(() => String(todayState.value.mood || '安静陪伴').trim())
function compactProfileText(value, fallback, maxLength) {
  const firstPhrase = String(value || '').trim().split(/[；;，,。\n]/)[0]?.trim() || fallback
  return firstPhrase.length > maxLength ? `${firstPhrase.slice(0, maxLength)}…` : firstPhrase
}
const preferredUserAddress = computed(() => compactProfileText(
  memoryData.value?.profile?.preferences?.user_address,
  '你',
  8,
))
const relationshipDistanceLabel = computed(() => {
  const source = String(memoryData.value?.profile?.preferences?.relationship_distance || '').trim()
  const knownRelations = ['青梅竹马', '小女友', '女朋友', '恋人', '搭档', '朋友', '家人']
  const matched = knownRelations.filter((label) => source.includes(label))
  return [...new Set(matched)].slice(0, 2).join(' · ') || compactProfileText(source, '自然亲近', 12)
})
const activeViewSubtitle = computed(() => ({
  home: '回到我们今天的生活',
  chat: chatSubtitle.value,
  diaries: '把今天认真地留住',
  stats: '看看最近走过的路',
  memory: 'Mio 记得的事情都在这里',
  tasks: '一起把想法变成行动',
  companion: '让 Mio 来到你的桌面',
  settings: activeSettingsItem.value.description,
}[activeView.value] || '今天也陪你一起生活'))
const stateHistory = computed(() => bootstrap.value?.state_history || [])
const autoDiaryStatus = computed(() => bootstrap.value?.auto_diary || {})
const stateAnalyzeBusy = ref(false)
const moodScore = computed(() => Math.max(0, Math.min(5, Number(todayState.value.mood_score || 0))))
const moodTrend = computed(() => {
  if (!logicalDate.value) return []
  const states = new Map(stateHistory.value.map((item) => [item.date, item]))
  const current = new Date(`${logicalDate.value}T00:00:00Z`)
  return Array.from({ length: 7 }, (_, index) => {
    const target = new Date(current)
    target.setUTCDate(current.getUTCDate() - (6 - index))
    const date = target.toISOString().slice(0, 10)
    const state = states.get(date) || {}
    const score = Math.max(0, Math.min(5, Number(state.mood_score || 0)))
    return {
      date,
      label: `${target.getUTCMonth() + 1}/${target.getUTCDate()}`,
      score,
      mood: state.mood || '',
      isToday: date === logicalDate.value,
    }
  })
})
const todayStateDetails = computed(() => [
  { key: 'events', label: '今日主线', value: todayState.value.key_events, tone: 'main' },
  { key: 'adjustment', label: '可以调整', value: todayState.value.avoidance_signals, tone: 'warning' },
  { key: 'action', label: '下一步', value: todayState.value.next_min_action, tone: 'action' },
].filter((item) => String(item.value || '').trim()))
const totalTokenUsage = computed(() => Number(
  bootstrap.value?.total_token_usage
  || messages.value.reduce((total, item) => total + Number(item.prompt_tokens || 0) + Number(item.completion_tokens || 0), 0),
))
const todayTokenUsage = computed(() => Number(
  bootstrap.value?.today_token_usage?.total_tokens
  || tokenUsageData.value.today?.total_tokens
  || 0,
))
const latestAssistantNotice = computed(() => {
  const message = [...messages.value].reverse().find((item) => item.role === 'assistant' && String(item.content || '').trim())
  if (!message) return null
  return {
    content: cleanDisplayContent(message.content),
    created_at: message.created_at,
  }
})
const autoDiaryStatusLabel = computed(() => {
  const status = autoDiaryStatus.value
  if (!status.enabled) return '自动日记已关闭'
  const date = String(status.target_date || '').slice(5) || '上一记录日'
  return {
    generated: `${date} 已自动补生成`,
    already_exists: `${date} 已完成自动检查`,
    no_content: `${date} 没有可生成的内容`,
    skipped: `${date} 已跳过重复生成`,
    error: `${date} 自动生成失败`,
    not_checked: '等待首次自动检查',
  }[status.result] || `${date} 自动检查完成`
})
const statsSummary = computed(() => statsData.value.summary || {})
const statsMonthLabel = computed(() => statsData.value.year && statsData.value.month
  ? `${statsData.value.year} 年 ${statsData.value.month} 月`
  : '正在读取')
const statsCalendarCells = computed(() => {
  const year = Number(statsData.value.year || 0)
  const month = Number(statsData.value.month || 0)
  if (!year || !month) return []
  const entryMap = new Map((statsData.value.calendar || []).map((item) => [item.date, item]))
  const firstDay = new Date(Date.UTC(year, month - 1, 1))
  const offset = (firstDay.getUTCDay() + 6) % 7
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate()
  const cells = Array.from({ length: offset }, (_, index) => ({ key: `blank-${index}`, blank: true }))
  for (let day = 1; day <= daysInMonth; day += 1) {
    const date = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    const entry = entryMap.get(date)
    cells.push({
      key: date,
      blank: false,
      day,
      date,
      title: entry?.title || '',
      status: date > statsData.value.logical_date ? 'future' : (entry?.daily_thirty_status || 'unknown'),
      hasDiary: Boolean(entry),
      isToday: date === statsData.value.logical_date,
    })
  }
  return cells
})
const statsDistribution = computed(() => {
  const byStatus = statsSummary.value.by_status || {}
  const config = [
    { key: 'done', label: '完成', color: '#388166' },
    { key: 'partial', label: '部分完成', color: '#b17a32' },
    { key: 'missed', label: '未完成', color: '#b24b4b' },
    { key: 'unknown', label: '未记录', color: '#94a0a9' },
  ]
  const total = config.reduce((sum, item) => sum + Number(byStatus[item.key] || 0), 0)
  let offset = 0
  return config.map((item) => {
    const value = Number(byStatus[item.key] || 0)
    const percent = total ? (value / total) * 100 : 0
    const segment = { ...item, value, percent, offset, total }
    offset += percent
    return segment
  })
})
const statsMoodSeries = computed(() => {
  const logical = statsData.value.logical_date
  if (!logical) return []
  const byDate = new Map((statsData.value.mood_trend || []).map((item) => [item.date, item]))
  const current = new Date(`${logical}T00:00:00Z`)
  return Array.from({ length: 30 }, (_, index) => {
    const target = new Date(current)
    target.setUTCDate(current.getUTCDate() - (29 - index))
    const date = target.toISOString().slice(0, 10)
    const item = byDate.get(date) || {}
    const score = Math.max(0, Math.min(5, Number(item.mood_score || 0)))
    return {
      date,
      mood: item.mood || '',
      score,
      x: 42 + (index / 29) * 638,
      y: score ? 18 + ((5 - score) / 4) * 150 : 0,
    }
  })
})
const statsMoodPoints = computed(() => statsMoodSeries.value.filter((item) => item.score))
const statsMoodPath = computed(() => statsMoodPoints.value
  .map((item, index) => `${index ? 'L' : 'M'}${item.x.toFixed(1)} ${item.y.toFixed(1)}`)
  .join(' '))
const visibleMessages = computed(() => messages.value
  .map((item) => ({ ...item, content: cleanDisplayContent(item.content) }))
  .filter((item) => item.content || item.attachments?.length))
const messageTurns = computed(() => {
  const turns = []
  for (const message of visibleMessages.value) {
    const previous = turns.at(-1)
    const currentTime = new Date(message.created_at).getTime()
    const previousTime = new Date(previous?.created_at).getTime()
    const isNearbyLegacyMessage = !message.request_id
      && !previous?.request_id
      && message.source === previous?.source
      && Number.isFinite(currentTime)
      && Number.isFinite(previousTime)
      && currentTime - previousTime >= 0
      && currentTime - previousTime <= 60_000
    const sameAssistantTurn = message.role === 'assistant'
      && previous?.role === 'assistant'
      && (
        (message.request_id && message.request_id === previous.request_id)
        || isNearbyLegacyMessage
      )
    if (sameAssistantTurn) {
      previous.parts.push(message)
      previous.created_at = message.created_at
      previous.model_id = message.model_id || previous.model_id
      previous.reasoning_level = message.reasoning_level || previous.reasoning_level
      previous.prompt_tokens = message.prompt_tokens || previous.prompt_tokens
      previous.cached_prompt_tokens = message.cached_prompt_tokens || previous.cached_prompt_tokens
      previous.completion_tokens = message.completion_tokens || previous.completion_tokens
      previous.reasoning_tokens = message.reasoning_tokens || previous.reasoning_tokens
      if (
        message.request_cost_yuan !== null
        && message.request_cost_yuan !== undefined
        && message.request_cost_source !== 'shared_request'
      ) {
        previous.request_cost_yuan = message.request_cost_yuan
      }
      if (message.request_cost_source && message.request_cost_source !== 'shared_request') {
        previous.request_cost_source = message.request_cost_source
      }
      continue
    }
    turns.push({ ...message, parts: [message] })
  }
  return turns
})
const filteredAgentTasks = computed(() => {
  if (taskStatusFilter.value === 'all') return agentTasks.value
  if (taskStatusFilter.value === 'running') {
    return agentTasks.value.filter((item) => ['queued', 'running'].includes(item.status))
  }
  if (taskStatusFilter.value === 'failed') {
    return agentTasks.value.filter((item) => ['failed', 'skipped', 'cancelled'].includes(item.status))
  }
  return agentTasks.value.filter((item) => item.status === taskStatusFilter.value)
})
const taskSummary = computed(() => ({
  total: agentTasks.value.length,
  running: agentTasks.value.filter((item) => ['queued', 'running'].includes(item.status)).length,
  pending: agentTasks.value.filter((item) => item.status === 'needs_confirmation').length,
  completed: agentTasks.value.filter((item) => item.status === 'executed').length,
  failed: agentTasks.value.filter((item) => ['failed', 'skipped', 'cancelled'].includes(item.status)).length,
}))
const dailyReviewItems = computed(() => {
  const reviewMap = new Map(dailyReviews.value.map((item) => [item.date, item]))
  const items = diaries.value.map((diary) => ({
    date: diary.date,
    title: diary.title,
    review: reviewMap.get(diary.date) || null,
  }))
  for (const review of dailyReviews.value) {
    if (!items.some((item) => item.date === review.date)) {
      items.push({ date: review.date, title: `${review.date} 的回顾`, review })
    }
  }
  return items.sort((a, b) => b.date.localeCompare(a.date))
})
const selectedDailyReview = computed(() => dailyReviews.value.find((item) => item.date === selectedDailyDate.value) || null)
const weeklyReviewItems = computed(() => {
  const items = [...weeklyReviews.value]
  const currentWeek = weekStartFor(logicalDate.value)
  if (currentWeek && !items.some((item) => item.week_start === currentWeek)) {
    items.unshift({ week_start: currentWeek, week_end: weekEndFor(currentWeek), markdown_content: '' })
  }
  return items.sort((a, b) => b.week_start.localeCompare(a.week_start))
})
const selectedWeeklyReview = computed(() => weeklyReviewItems.value.find((item) => item.week_start === selectedWeeklyStart.value) || null)
const monthlyReviewItems = computed(() => {
  const items = new Map(monthlyReviews.value.map((item) => [item.month, { ...item }]))
  for (const diary of diaries.value) {
    const month = String(diary?.date || '').slice(0, 7)
    if (!month) continue
    const item = items.get(month) || {
      month,
      month_start: `${month}-01`,
      month_end: monthEndFor(month),
      markdown_content: '',
    }
    item.diary_count = Number(item.diary_count || 0) + 1
    items.set(month, item)
  }
  const currentMonth = String(logicalDate.value || '').slice(0, 7)
  if (currentMonth && !items.has(currentMonth)) {
    items.set(currentMonth, {
      month: currentMonth,
      month_start: `${currentMonth}-01`,
      month_end: monthEndFor(currentMonth),
      markdown_content: '',
      diary_count: 0,
    })
  }
  return [...items.values()].sort((a, b) => b.month.localeCompare(a.month))
})
const selectedMonthlyReview = computed(() => monthlyReviewItems.value.find((item) => item.month === selectedMonthlyMonth.value) || null)
const profileRows = computed(() => {
  const profile = memoryData.value.profile
  if (!profile) return []
  return [
    ['人格核心', profile.identity?.core],
    ['说话方式', profile.speaking_style?.tone],
    ['气泡习惯', profile.speaking_style?.bubble_style],
    ['对你的称呼', profile.preferences?.user_address],
    ['相处距离', profile.preferences?.relationship_distance],
  ].filter(([, value]) => value)
})

function formatRealTime(value) {
  if (!value) return '时间未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date).replaceAll('/', '-')
}

function formatShortTime(value) {
  if (!value) return '时间未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value).replace('T', ' ').slice(0, 16)
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function formatSidebarTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value).slice(5, 10)
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
  }).format(date)
}

function weekStartFor(value) {
  if (!value) return ''
  const date = new Date(`${value}T12:00:00`)
  if (Number.isNaN(date.getTime())) return ''
  const day = date.getDay() || 7
  date.setDate(date.getDate() - day + 1)
  return date.toISOString().slice(0, 10)
}

function weekEndFor(value) {
  if (!value) return ''
  const date = new Date(`${value}T12:00:00`)
  if (Number.isNaN(date.getTime())) return ''
  date.setDate(date.getDate() + 6)
  return date.toISOString().slice(0, 10)
}

function monthEndFor(value) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(value || ''))
  if (!match) return ''
  const year = Number(match[1])
  const month = Number(match[2])
  if (month < 1 || month > 12) return ''
  const lastDay = new Date(year, month, 0).getDate()
  return `${match[1]}-${match[2]}-${String(lastDay).padStart(2, '0')}`
}

function requestCostDetails(message) {
  const parts = Array.isArray(message?.parts) && message.parts.length ? message.parts : [message]
  const pricedPart = parts.find((part) => (
    part?.request_cost_yuan !== null
    && part?.request_cost_yuan !== undefined
    && !['shared_request', 'shared_multimodal_request'].includes(part?.request_cost_source)
  ))
  const fallbackPart = parts.find((part) => (
    part?.request_cost_yuan !== null
    && part?.request_cost_yuan !== undefined
    && Number(part.request_cost_yuan) > 0
  ))
  const selected = pricedPart || fallbackPart || message
  return {
    value: selected?.request_cost_yuan,
    source: selected?.request_cost_source || '',
  }
}

function formatCost(message) {
  const cost = requestCostDetails(message).value
  if (cost === null || cost === undefined) return '费用未识别'
  return `¥${Number(cost).toFixed(6)}`
}

function costSourceLabel(source) {
  return {
    provider_reported: '接口实扣',
    provider_reconciliation_pending: '实扣待确认',
    provider_partial: '部分实扣',
    official_estimate: '官方价格估算',
    provider_estimate: '站点价格估算',
    configured_estimate: '自定义价格估算',
    local_fallback: '本地回复',
  }[source] || ''
}

function pricingSourceLabel(source) {
  return {
    official_catalog: '官方价格',
    provider_catalog: '站点公开价格',
    manual: '手动价格',
  }[source] || '价格待配置'
}

function sourceLabel(source) {
  if (source === 'qq') return 'QQ'
  if (source === 'desktop') return '应用'
  if (source === 'desktop_pet' || source === 'desktop_pet_wake') return '桌宠'
  if (source === 'proactive') return '主动'
  if (source === 'startup') return '启动'
  if (source === 'screen') return '屏幕'
  if (source === 'game') return '游戏'
  if (source === 'system') return '系统'
  return '网页'
}

function reasoningLabel(level) {
  for (const model of modelOptions.value) {
    const match = model.reasoning_options?.find((item) => item.id === level)
    if (match) return match.label
  }
  return {
    fast: '低',
    standard: '中',
    deep: '高',
    off: '关闭思考',
    thinking: '开启思考',
    max: '最大',
  }[level] || '模型默认'
}

function compactModelLabel(model) {
  const displayName = typeof model === 'string' ? model : model?.display_name || model?.model || ''
  return String(displayName || '自动')
    .replace(/^DeepSeek\s*/i, '')
    .replace(/^GPT-/i, '')
    .replace(/\s*·\s*/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function messageModelLabel(turn) {
  const profile = modelOptions.value.find((item) => item.id === turn?.model_id)
  return profile?.display_name || turn?.provider_model || turn?.model_id || compactActiveModelLabel.value
}

function compactReasoningLabel(level) {
  if (level === 'thinking') return '开'
  if (level === 'off') return '关'
  return reasoningLabel(level)
}

function ensureReasoningForActiveModel() {
  if (isAutoRouting.value) {
    reasoningLevel.value = 'auto'
    persistReasoning()
    return
  }
  const optionIds = new Set(activeReasoningOptions.value.map((item) => item.id))
  const legacy = { fast: 'low', standard: 'medium', deep: 'high' }
  const migrated = legacy[reasoningLevel.value]
  if (migrated && optionIds.has(migrated)) reasoningLevel.value = migrated
  if (!optionIds.has(reasoningLevel.value)) {
    reasoningLevel.value = activeModel.value?.default_reasoning_level || activeReasoningOptions.value[0]?.id || 'default'
  }
  persistReasoning()
}

function turnId(turn) {
  return String(turn.request_id || turn.id)
}

const toolReceiptLabels = {
  get_self_state: '读取自我状态',
  list_capabilities: '读取能力清单',
  get_active_view: '读取当前页面',
  get_service_health: '检查服务状态',
  explain_last_route: '读取最近路由',
  get_today_state: '读取今日状态',
  search_web: '联网查证',
  search_memory: '检索记忆',
  get_diary: '读取日记',
  add_diary_material: '添加日记素材',
  set_daily_thirty: '更新每日三十',
  set_daily_mood: '更新今日情绪',
  update_today_state: '更新今日状态',
  remember_thread: '创建待跟进事项',
  resolve_thread: '完成待跟进事项',
  remember_memory: '保存记忆',
  edit_today_diary: '修改今日日记',
  generate_today_diary: '生成今日日记',
  update_profile: '更新 Mio 属性',
}

function turnToolReceipts(turn) {
  const receipts = Array.isArray(turn?.tool_receipts)
    ? turn.tool_receipts
    : turn?.parts?.find((part) => Array.isArray(part.tool_receipts))?.tool_receipts
  return Array.isArray(receipts) ? receipts : []
}

function toolReceiptLabel(receipt) {
  return toolReceiptLabels[receipt?.tool_name] || receipt?.tool_name || '工具'
}

function toolReceiptStatus(receipt) {
  const status = String(receipt?.status || '')
  if (status === 'needs_confirmation') {
    const taskId = Number(receipt?.action_id || receipt?.result?.task_id || 0)
    return taskId ? `等待确认 #${taskId}` : '等待确认'
  }
  return {
    completed: receipt?.replayed ? '已验证（重放）' : '已完成',
    executed: '已完成',
    failed: '执行失败',
    timed_out: '执行超时',
    cancelled: '已取消',
    skipped: '已跳过',
  }[status] || status || '状态未知'
}

function toolReceiptTitle(receipt) {
  if (receipt?.error) return String(receipt.error)
  try {
    return JSON.stringify(receipt?.result || {}).slice(0, 400)
  } catch {
    return ''
  }
}

function modelRequestError(error) {
  const message = String(error?.message || error)
  const detail = error?.detail || {}
  if (detail.http_status === 401 || /HTTP 401|Invalid token|API Key/i.test(message)) {
    const provider = detail.provider_name || activeModel.value?.provider_name || '当前供应商'
    const model = detail.provider_model || activeModel.value?.display_name || selectedModel.value
    return `「${provider}」拒绝了模型「${model}」使用的 API Key。请到设置中测试连接，并用有效密钥更新这个供应商。`
  }
  return message
}

function cleanDisplayContent(content) {
  const text = String(content || '').replace(/\*\*/g, '').trim()
  if (/^["“”']?\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?\]["“”']?$/.test(text)) return ''
  return text
    .split('\n')
    .filter((line) => !/^\s*[\[【](?:内部消息时间|本轮消息时间|当前消息时间)/.test(line))
    .filter((line) => !/^\s*\[(?:图片\s*\d+\s*张|文件：.+)\]\s*$/.test(line))
    .join('\n')
    .trim()
}

function statusLabel(status) {
  return {
    done: '已完成',
    partial: '进行中',
    missed: '未完成',
    unknown: '未判定',
  }[status] || '未判定'
}

function renderedMarkdown(markdown) {
  return DOMPurify.sanitize(marked.parse(markdown || ''))
}

async function request(url, options = {}) {
  return apiRequest(url, options)
}

async function completeOnboarding(payload) {
  onboardingBusy.value = true
  onboardingError.value = ''
  try {
    await completeOnboardingRequest(payload)
    window.location.reload()
  } catch (error) {
    onboardingError.value = error.message
  } finally {
    onboardingBusy.value = false
  }
}

async function refreshOnboardingEnvironment() {
  onboardingEnvironmentBusy.value = true
  onboardingError.value = ''
  try {
    onboardingEnvironment.value = await loadOnboardingEnvironment()
  } catch (error) {
    onboardingEnvironment.value = null
    onboardingError.value = error.message
  } finally {
    onboardingEnvironmentBusy.value = false
  }
}

async function refreshDataPrivacy({ quiet = false } = {}) {
  if (!quiet) dataPrivacyLoading.value = true
  try {
    dataPrivacyState.value = await loadDataPrivacy()
  } catch (error) {
    settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
  } finally {
    dataPrivacyLoading.value = false
  }
}

async function createDataBackup() {
  dataPrivacyBusy.value = 'create'
  try {
    await createCompleteBackup()
    await refreshDataPrivacy({ quiet: true })
    settingsFeedback.value = { section: 'data', type: 'success', message: '完整备份已经创建并校验通过' }
  } catch (error) {
    settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
  } finally {
    dataPrivacyBusy.value = ''
  }
}

async function importDataBackup(event) {
  const file = event?.target?.files?.[0]
  if (!file) return
  dataPrivacyBusy.value = 'import'
  try {
    await importCompleteBackup(file)
    await refreshDataPrivacy({ quiet: true })
    settingsFeedback.value = { section: 'data', type: 'success', message: '完整备份已导入并校验通过，可以选择恢复' }
  } catch (error) {
    settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
  } finally {
    event.target.value = ''
    dataPrivacyBusy.value = ''
  }
}

async function restoreDataBackup(name) {
  const confirmed = await showAppConfirm({
    title: '恢复本地数据',
    message: `恢复“${name}”会覆盖当前本地数据。恢复前会自动创建回退备份。`,
    confirmText: '继续恢复',
    danger: true,
  })
  if (!confirmed) return
  dataPrivacyBusy.value = name
  try {
    const result = await restoreCompleteBackup(name)
    settingsFeedback.value = { section: 'data', type: 'success', message: `恢复完成。已保留回退备份 ${result.rollback_backup}，请重启应用后继续使用` }
    await refreshDataPrivacy({ quiet: true })
  } catch (error) {
    settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
  } finally {
    dataPrivacyBusy.value = ''
  }
}

async function togglePrivacyPause() {
  dataPrivacyBusy.value = 'privacy'
  try {
    const privacy = dataPrivacyState.value.privacy || {}
    const shouldPause = !privacy.paused || ['pause_incomplete', 'state_uncertain'].includes(privacy.transition)
    dataPrivacyState.value = {
      ...dataPrivacyState.value,
      privacy: await setPrivacyPaused(shouldPause),
    }
    settingsFeedback.value = {
      section: 'data',
      type: 'success',
      message: dataPrivacyState.value.privacy.paused ? '敏感能力已全部暂停' : '已恢复暂停前的能力设置',
    }
  } catch (error) {
    settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
    await refreshDataPrivacy({ quiet: true }).catch(() => {})
  } finally {
    dataPrivacyBusy.value = ''
  }
}

async function refreshContextUsage() {
  const conversationId = selectedConversationId.value
  if (!conversationId) return
  try {
    const usage = await request(`/api/agent/context-usage?conversation_id=${encodeURIComponent(conversationId)}`)
    if (selectedConversationId.value === conversationId) contextUsage.value = usage
  } catch {
    // Context usage is supplementary and must not block chatting.
  }
}

async function refreshDayDashboard() {
  try {
    const data = await diaryApi.loadDiaryDashboard()
    bootstrap.value = { ...(bootstrap.value || {}), ...data }
    if (!diarySearch.value.trim()) {
      const merged = new Map(diaries.value.map((item) => [item.date, item]))
      for (const diary of data.diaries || []) merged.set(diary.date, diary)
      diaries.value = [...merged.values()].sort((a, b) => b.date.localeCompare(a.date))
    }
  } catch {
    // Dashboard polling must not interrupt chatting.
  }
}

async function openTokenUsagePanel() {
  showTokenUsagePanel.value = true
  tokenUsageLoading.value = true
  try {
    tokenUsageData.value = await request('/api/agent/token-usage?days=30')
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    tokenUsageLoading.value = false
  }
}

function formatTokenCount(value) {
  return Number(value || 0).toLocaleString('zh-CN')
}

async function analyzeTodayState() {
  if (stateAnalyzeBusy.value) return
  stateAnalyzeBusy.value = true
  errorMessage.value = ''
  try {
    await request('/api/state/analyze-today', { method: 'POST', body: '{}' })
    await refreshDayDashboard()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    stateAnalyzeBusy.value = false
  }
}

async function refreshQqStatus() {
  try {
    const qq = await qqApi.loadQqStatus()
    bootstrap.value = { ...(bootstrap.value || {}), qq }
    if (qq.logged_in) {
      clearQqQrImage()
    } else if (qq.qrcode_available && !qqQrImageUrl.value && !qqQrLoading.value) {
      void loadQqQrCode()
    }
  } catch {
    // A transient NapCat status failure must not interrupt desktop chatting.
  }
}

async function loadBootstrap({ quiet = false } = {}) {
  if (!quiet) loading.value = true
  try {
    const data = await chatApi.loadBootstrap()
    bootstrap.value = data
    if (!qqAccountDraft.value && data.qq?.napcat_account) qqAccountDraft.value = String(data.qq.napcat_account)
    if (!qqTestTargetDraft.value && data.settings?.qq_allowed_user_ids) {
      qqTestTargetDraft.value = String(data.settings.qq_allowed_user_ids).split(',')[0].trim()
    }
    if (data.qq?.logged_in) {
      clearQqQrImage()
    } else if (data.qq?.qrcode_available && !qqQrImageUrl.value && !qqQrLoading.value) {
      void loadQqQrCode()
    }
    if (data.qq?.group_chat) {
      const preserveGroupDraft = activeView.value === 'settings'
        && activeSettingsSection.value === 'qq'
        && isSettingsSectionDirty('qq')
      if (!preserveGroupDraft) {
        groupChatSettings.value = { ...groupChatSettings.value, ...data.qq.group_chat }
        groupIdsDraft.value = (data.qq.group_chat.group_ids || []).join(', ')
        savedGroupChatSettings.value = normalizedGroupChatSnapshot()
      }
    }
    conversations.value = data.conversations || []
    const availableModelIds = new Set(['auto', ...(data.models || []).map((item) => item.id)])
    if (!availableModelIds.has(selectedModel.value)) selectedModel.value = 'auto'
    ensureReasoningForActiveModel()
    if (!(activeView.value === 'settings' && activeSettingsSection.value === 'conversation' && isSettingsSectionDirty('conversation'))) {
      chatSettingsDraft.value = {
        model_id: selectedModel.value,
        reasoning_level: reasoningLevel.value,
        voice_language: chatVoiceLanguage.value,
      }
      savedChatSettings.value = serializeSettings(chatSettingsDraft.value)
    }
    const availableConversationIds = new Set(conversations.value.map((item) => item.id))
    if (!availableConversationIds.has(selectedConversationId.value)) {
      selectedConversationId.value = data.conversation_id
      localStorage.setItem('mio_conversation_id', selectedConversationId.value)
    }
    if (selectedConversationId.value === data.conversation_id) {
      messages.value = data.messages || []
      contextUsage.value = data.context_usage || contextUsage.value
    } else {
      const [selectedMessages, selectedUsage] = await Promise.all([
        request(`/api/agent/messages?limit=120&conversation_id=${encodeURIComponent(selectedConversationId.value)}`),
        request(`/api/agent/context-usage?conversation_id=${encodeURIComponent(selectedConversationId.value)}`),
      ])
      messages.value = selectedMessages
      contextUsage.value = selectedUsage
    }
    diaries.value = data.diaries || []
    if (!selectedDiary.value && diaries.value.length) {
      selectedDiary.value = diaries.value[0]
    }
    errorMessage.value = ''
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    loading.value = false
  }
  await settleChatScrollToBottom()
}

async function loadStartupGreetingSetting() {
  try {
    const data = await request('/api/companion/startup-greeting')
    startupGreetingEnabled.value = Boolean(data.enabled)
    savedStartupGreeting.value = startupGreetingEnabled.value
  } catch {
    // 旧后端没有此设置时保留默认开启状态。
  }
}

async function loadDesktopPreferences({ quiet = false } = {}) {
  const getter = window.pywebview?.api?.get_desktop_preferences
  if (!getter) return
  desktopPreferencesBusy.value = true
  try {
    const result = await getter()
    if (!result?.ok) throw new Error(result?.error || '桌面设置读取失败')
    desktopPreferencesDraft.value = {
      close_to_background: Boolean(result.close_to_background),
      background_notifications: Boolean(result.background_notifications),
      windows_startup: Boolean(result.windows_startup),
    }
    savedDesktopPreferences.value = serializeSettings(desktopPreferencesDraft.value)
    desktopPreferencesReady.value = true
  } catch (error) {
    if (!quiet) errorMessage.value = `桌面设置读取失败：${error.message}`
  } finally {
    desktopPreferencesBusy.value = false
  }
}

async function saveDesktopPreferences() {
  const setter = window.pywebview?.api?.set_desktop_preferences
  if (!setter || !desktopPreferencesReady.value) return true
  desktopPreferencesBusy.value = true
  try {
    const result = await setter({ ...desktopPreferencesDraft.value })
    if (!result?.ok) throw new Error(result?.error || '桌面设置保存失败')
    desktopPreferencesDraft.value = {
      close_to_background: Boolean(result.close_to_background),
      background_notifications: Boolean(result.background_notifications),
      windows_startup: Boolean(result.windows_startup),
    }
    savedDesktopPreferences.value = serializeSettings(desktopPreferencesDraft.value)
    return true
  } catch (error) {
    errorMessage.value = `桌面设置保存失败：${error.message}`
    showSettingsFeedback('general', 'error', '桌面设置保存失败')
    return false
  } finally {
    desktopPreferencesBusy.value = false
  }
}

async function loadQqStartupSetting() {
  try {
    const data = await request('/api/companion/qq-startup')
    qqStartupEnabled.value = Boolean(data.enabled)
    savedQqStartupEnabled.value = qqStartupEnabled.value
  } catch {
    // 旧后端没有此设置时保留默认关闭状态。
  }
}

async function saveStartupGreetingSetting() {
  try {
    const greeting = await request('/api/companion/startup-greeting', {
      method: 'PATCH',
      body: JSON.stringify({ enabled: startupGreetingEnabled.value }),
    })
    startupGreetingEnabled.value = Boolean(greeting.enabled)
    savedStartupGreeting.value = startupGreetingEnabled.value
    return true
  } catch (error) {
    errorMessage.value = `启动打招呼设置保存失败：${error.message}`
    showSettingsFeedback('general', 'error', '启动打招呼设置保存失败')
    return false
  }
}

async function saveQqStartupSetting() {
  try {
    const qqStartup = await request('/api/companion/qq-startup', {
      method: 'PATCH',
      body: JSON.stringify({ enabled: qqStartupEnabled.value }),
    })
    qqStartupEnabled.value = Boolean(qqStartup.enabled)
    savedQqStartupEnabled.value = qqStartupEnabled.value
    return true
  } catch (error) {
    errorMessage.value = `QQ 随 Mio 启动设置保存失败：${error.message}`
    showSettingsFeedback('qq', 'error', 'QQ 随 Mio 启动设置保存失败')
    return false
  }
}

async function refreshMessages() {
  const conversationId = selectedConversationId.value
  if (!conversationId || messageRefreshInFlight) return
  messageRefreshInFlight = true
  try {
    const data = await request(`/api/agent/messages?limit=120&conversation_id=${encodeURIComponent(conversationId)}`)
    if (selectedConversationId.value !== conversationId) return
    const previousLastId = messages.value.at(-1)?.id
    messages.value = data
    if (data.at(-1)?.id !== previousLastId) {
      await Promise.all([scrollToBottom(), refreshContextUsage()])
    }
  } catch {
    // Polling failures are reflected by the next bootstrap/status action.
  } finally {
    messageRefreshInFlight = false
  }
}

async function requestStartupGreeting() {
  if (!selectedConversationId.value) return
  try {
    const result = await request('/api/agent/startup-greeting', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: selectedConversationId.value }),
    })
    if (result.sent) {
      await Promise.all([refreshMessages(), refreshConversations()])
    }
  } catch {
    // A startup greeting should never prevent the application from opening.
  }
}

function createClientRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `desktop-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`
}

async function sendMessage() {
  const content = draft.value.trim()
  if ((!content && !attachments.value.length) || sending.value) return
  const conversationId = selectedConversationId.value
  const clientRequestId = createClientRequestId()
  const outgoingAttachments = attachments.value.map((item) => ({
    kind: item.kind,
    name: item.name,
    mime_type: item.mime_type,
    data_url: item.data_url || '',
    text: item.text || '',
    size: item.size,
    ephemeral: Boolean(item.ephemeral),
  }))
  const localAttachments = attachments.value.map((item) => ({
    kind: item.kind,
    name: item.name,
    mime_type: item.mime_type,
    size: item.size,
    url: item.preview_url || '',
  }))
  draft.value = ''
  attachments.value = []
  sending.value = true
  errorMessage.value = ''
  messages.value.push({
    id: `local-${clientRequestId}`,
    request_id: clientRequestId,
    role: 'user',
    content: content || '发送了附件',
    attachments: localAttachments,
    source: 'desktop',
    created_at: new Date().toISOString(),
    request_cost_yuan: 0,
  })
  await scrollToBottom()
  try {
    const isDesktopPetConversation = conversationId === 'desktop_pet'
    const endpoint = isDesktopPetConversation && !outgoingAttachments.length
      ? '/api/companion/chat'
      : '/api/agent/chat'
    const payload = isDesktopPetConversation && !outgoingAttachments.length
      ? {
          message: content,
          reasoning_level: reasoningLevel.value,
          model_id: selectedModel.value,
          client_request_id: clientRequestId,
        }
      : {
          message: content,
          reasoning_level: reasoningLevel.value,
          model_id: selectedModel.value,
          conversation_id: conversationId,
          attachments: outgoingAttachments,
          client_request_id: clientRequestId,
        }
    const controller = new AbortController()
    chatAbortController = controller
    activeChatCancelPayload = {
      endpoint,
      body: isDesktopPetConversation
        ? { client_request_id: clientRequestId }
        : { conversation_id: conversationId, client_request_id: clientRequestId },
    }
    const result = await request(endpoint, {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
    if (result.context_usage && selectedConversationId.value === conversationId) {
      contextUsage.value = result.context_usage
    }
    await refreshMessages()
    if (result.request_id && selectedConversationId.value === conversationId) {
      messages.value = messages.value.map((message) => (
        message.request_id === result.request_id
          ? {
              ...message,
              first_token_latency_ms: result.first_token_latency_ms,
              total_latency_ms: result.total_latency_ms,
              agent_run_id: result.agent_run_id || message.agent_run_id || '',
              agent_run_status: result.agent_run_status || message.agent_run_status || '',
              tool_receipts: result.tool_receipts || message.tool_receipts || [],
            }
          : message
      ))
    }
    await refreshConversations()
  } catch (error) {
    let cancellationError = ''
    if (error?.code === 'request_timeout' && activeChatCancelPayload) {
      try {
        await requestChatCancellation(activeChatCancelPayload)
      } catch (cancelError) {
        cancellationError = cancelError.message
      }
    }
    errorMessage.value = error?.code === 'request_cancelled'
      ? '已停止这次回复'
      : `${modelRequestError(error)}${cancellationError ? `；后端取消确认失败：${cancellationError}` : ''}`
    await refreshMessages()
    await refreshConversations()
  } finally {
    chatAbortController = null
    activeChatCancelPayload = null
    sending.value = false
  }
}

async function requestChatCancellation(cancelPayload) {
  return apiRequest(`${cancelPayload.endpoint}/cancel`, {
    method: 'POST',
    body: JSON.stringify(cancelPayload.body),
    deadlineClass: 'mutation',
  })
}

async function cancelActiveChat() {
  if (!sending.value || !chatAbortController || !activeChatCancelPayload) return
  const cancelPayload = activeChatCancelPayload
  chatAbortController.abort('user_cancelled')
  try {
    await requestChatCancellation(cancelPayload)
  } catch (error) {
    if (error?.code !== 'request_cancelled') errorMessage.value = `停止回复失败：${error.message}`
  }
}

async function refreshConversations() {
  try {
    conversations.value = await chatApi.listConversations()
  } catch {
    // The message view remains usable if only the sidebar refresh fails.
  }
}

async function selectConversation(conversationId) {
  if (!conversationId || conversationId === selectedConversationId.value) {
    activeView.value = 'chat'
    await settleChatScrollToBottom()
    return
  }
  activeView.value = 'chat'
  loading.value = true
  errorMessage.value = ''
  selectedConversationId.value = conversationId
  const loadVersion = ++conversationLoadVersion
  localStorage.setItem('mio_conversation_id', conversationId)
  try {
    const [selectedMessages, selectedUsage] = await Promise.all([
      request(`/api/agent/messages?limit=120&conversation_id=${encodeURIComponent(conversationId)}`),
      request(`/api/agent/context-usage?conversation_id=${encodeURIComponent(conversationId)}`),
    ])
    if (loadVersion !== conversationLoadVersion || selectedConversationId.value !== conversationId) return
    messages.value = selectedMessages
    contextUsage.value = selectedUsage
  } catch (error) {
    if (loadVersion === conversationLoadVersion && selectedConversationId.value === conversationId) {
      errorMessage.value = error.message
    }
  } finally {
    if (loadVersion === conversationLoadVersion) loading.value = false
  }
  if (loadVersion !== conversationLoadVersion || selectedConversationId.value !== conversationId) return
  await settleChatScrollToBottom()
}

async function createNewConversation() {
  activeView.value = 'chat'
  errorMessage.value = ''
  try {
    const conversation = await chatApi.createConversation('新对话')
    conversations.value = [conversation, ...conversations.value]
    selectedConversationId.value = conversation.id
    localStorage.setItem('mio_conversation_id', conversation.id)
    messages.value = []
    contextUsage.value = { used_chars: 0, max_chars: contextUsage.value.max_chars || 18000, percent: 0, has_summary: false }
    await nextTick()
    document.querySelector('.composer-input')?.focus()
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function renameConversation(conversation) {
  const title = await showAppPrompt({
    title: '重命名对话',
    message: '输入一个便于识别的对话名称',
    value: conversation.title || '新对话',
  })
  if (title === null) return
  const normalized = title.replace(/\s+/g, ' ').trim()
  if (!normalized || normalized === conversation.title) return
  errorMessage.value = ''
  try {
    const updated = await request(`/api/agent/conversations/${encodeURIComponent(conversation.id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ title: normalized }),
    })
    conversations.value = conversations.value.map((item) => (
      item.id === conversation.id ? { ...item, ...updated } : item
    ))
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function deleteConversation(conversation) {
  const confirmed = await showAppConfirm({
    title: `删除“${conversation.title}”？`,
    message: '该窗口的聊天记录会被永久删除，已经生成的日记不会受影响。',
    confirmText: '删除对话',
    danger: true,
  })
  if (!confirmed) return
  errorMessage.value = ''
  try {
    await request(`/api/agent/conversations/${encodeURIComponent(conversation.id)}`, {
      method: 'DELETE',
    })
    const remaining = conversations.value.filter((item) => item.id !== conversation.id)
    conversations.value = remaining
    if (selectedConversationId.value === conversation.id) {
      const nextConversation = remaining.find((item) => item.kind === 'qq') || remaining[0]
      selectedConversationId.value = ''
      if (nextConversation) {
        await selectConversation(nextConversation.id)
      } else {
        messages.value = []
        localStorage.removeItem('mio_conversation_id')
      }
    }
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function copyTurn(turn) {
  const content = turn.parts.map((part) => cleanDisplayContent(part.content)).filter(Boolean).join('\n')
  if (!content) return
  try {
    await navigator.clipboard.writeText(content)
  } catch {
    const helper = document.createElement('textarea')
    helper.value = content
    helper.style.position = 'fixed'
    helper.style.opacity = '0'
    document.body.appendChild(helper)
    helper.select()
    document.execCommand('copy')
    helper.remove()
  }
  copiedTurnId.value = turnId(turn)
  window.setTimeout(() => {
    if (copiedTurnId.value === turnId(turn)) copiedTurnId.value = ''
  }, 1600)
}

function stopMessageVoice() {
  voiceAbortController?.abort()
  voiceAbortController = null
  if (activeMessageAudio) {
    activeMessageAudio.pause()
    activeMessageAudio.src = ''
    activeMessageAudio = null
  }
  if (activeMessageAudioUrl) {
    URL.revokeObjectURL(activeMessageAudioUrl)
    activeMessageAudioUrl = ''
  }
  speakingPartId.value = ''
  voiceLoadingPartId.value = ''
}

async function playMessageVoice(turn) {
  const playbackId = turnId(turn)
  if (speakingPartId.value === playbackId || voiceLoadingPartId.value === playbackId) {
    stopMessageVoice()
    return
  }
  const payload = buildTurnVoicePayload(
    turn,
    cleanDisplayContent,
    chatVoiceLanguage.value,
  )
  if (!payload.text) return
  stopMessageVoice()
  errorMessage.value = ''
  voiceLoadingPartId.value = playbackId
  voiceAbortController = new AbortController()
  try {
    const audioBlob = await apiRequest('/api/companion/voice/audio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: voiceAbortController.signal,
      deadlineClass: 'media',
      responseType: 'blob',
    })
    activeMessageAudioUrl = URL.createObjectURL(audioBlob)
    activeMessageAudio = new Audio(activeMessageAudioUrl)
    activeMessageAudio.addEventListener('ended', stopMessageVoice, { once: true })
    activeMessageAudio.addEventListener('error', stopMessageVoice, { once: true })
    voiceLoadingPartId.value = ''
    speakingPartId.value = playbackId
    await activeMessageAudio.play()
  } catch (error) {
    if (error?.code !== 'request_cancelled') errorMessage.value = `语音播放失败：${error.message}`
    stopMessageVoice()
  }
}

function turnVoiceLanguageLabel(turn) {
  const payload = buildTurnVoicePayload(
    turn,
    cleanDisplayContent,
    chatVoiceLanguage.value,
  )
  return voiceLanguageLabel(payload.language)
}

function handleComposerKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    sendMessage()
  }
}

async function scrollToBottom() {
  await nextTick()
  if (chatScroll.value) chatScroll.value.scrollTop = chatScroll.value.scrollHeight
}

async function settleChatScrollToBottom() {
  await nextTick()
  await new Promise((resolve) => {
    window.requestAnimationFrame(() => window.requestAnimationFrame(resolve))
  })
  await scrollToBottom()
  window.setTimeout(scrollToBottom, 100)
  window.setTimeout(scrollToBottom, 300)
}

function persistReasoning() {
  localStorage.setItem('mio_reasoning_level', reasoningLevel.value)
}

function persistSelectedModel() {
  localStorage.setItem('mio_model_id', selectedModel.value)
}

function toggleModelMenu() {
  showModelMenu.value = !showModelMenu.value
  modelMenuSection.value = 'root'
}

function closeModelMenu() {
  showModelMenu.value = false
  modelMenuSection.value = 'root'
}

async function loadSharedChatSettings() {
  try {
    const saved = await voiceApi.loadVoiceSettings()
    chatVoiceLanguage.value = saved.voice_language || 'auto'
    chatSettingsDraft.value.voice_language = chatVoiceLanguage.value
    savedChatSettings.value = serializeSettings(chatSettingsDraft.value)
    // 以后端保存的思考档位为准，避免本机残留旧值（如 high）每次启动覆盖回去
    if (saved.reasoning_level) {
      reasoningLevel.value = saved.reasoning_level
      persistReasoning()
    }
    if (companionStatus.value.pet?.settings) {
      companionStatus.value.pet.settings.gpt_sovits_text_language = chatVoiceLanguage.value
    }
  } catch {
    // 旧后端没有读取接口时，继续使用跟随原文。
  }
}

async function syncSharedChatSettings(voiceLanguage = chatVoiceLanguage.value) {
  try {
    const saved = await request('/api/companion/chat-settings', {
      method: 'PATCH',
      body: JSON.stringify({
        model_id: selectedModel.value,
        reasoning_level: reasoningLevel.value,
        voice_language: voiceLanguage || 'auto',
      }),
    })
    chatVoiceLanguage.value = saved.voice_language || 'auto'
    if (saved.voice_language && companionStatus.value.pet?.settings) {
      companionStatus.value.pet.settings.gpt_sovits_text_language = saved.voice_language
    }
    return saved
  } catch (error) {
    errorMessage.value = `QQ设置同步失败：${error.message}`
    return null
  }
}

function chooseModel(modelId) {
  selectedModel.value = modelId
  persistSelectedModel()
  ensureReasoningForActiveModel()
  persistReasoning()
  void syncSharedChatSettings()
  modelMenuSection.value = 'root'
}

function chooseReasoning(level) {
  reasoningLevel.value = level
  persistReasoning()
  void syncSharedChatSettings()
  modelMenuSection.value = 'root'
}

function handleDocumentPointerDown(event) {
  if (showModelMenu.value && !modelPicker.value?.contains(event.target)) closeModelMenu()
}

function handleDocumentKeydown(event) {
  if (event.key === 'Escape') closeModelMenu()
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error(`无法读取文件：${file.name}`))
    reader.readAsDataURL(file)
  })
}

function isTextAttachment(file) {
  const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || ''
  return file.type.startsWith('text/') || [
    '.txt', '.md', '.markdown', '.json', '.jsonl', '.log',
    '.py', '.js', '.ts', '.vue', '.html', '.css', '.xml', '.yaml', '.yml',
  ].includes(extension)
}

function fileExtension(file) {
  return file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || ''
}

function isImageAttachment(file) {
  return file.type.startsWith('image/') || [
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.dib', '.tif', '.tiff',
    '.avif', '.heic', '.heif', '.svg',
  ].includes(fileExtension(file))
}

function isDocumentAttachment(file) {
  const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || ''
  return [
    '.pdf', '.docx', '.csv', '.tsv', '.xlsx',
    '.mp4', '.mkv', '.mov', '.webm', '.avi',
  ].includes(extension)
}

async function addSelectedFiles(selectedFiles) {
  if (!selectedFiles.length) return
  const limits = attachmentLimits.value
  if (attachments.value.length + selectedFiles.length > limits.maxCount) {
    errorMessage.value = `每次最多添加 ${limits.maxCount} 个附件。`
    return
  }

  try {
    for (const file of selectedFiles) {
      if (isImageAttachment(file)) {
        if (file.size > limits.imageMaxBytes) throw new Error(`图片超过 ${(limits.imageMaxBytes / 1024 / 1024).toFixed(0)}MB：${file.name}`)
        const dataUrl = await readFileAsDataUrl(file)
        attachments.value.push({
          id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}-${file.name}`,
          kind: 'image',
          name: file.name,
          mime_type: file.type || 'application/octet-stream',
          size: file.size,
          data_url: dataUrl,
          preview_url: dataUrl,
        })
        continue
      }
      if (isDocumentAttachment(file)) {
        if (file.size > limits.documentMaxBytes) throw new Error(`文档超过 ${(limits.documentMaxBytes / 1024 / 1024).toFixed(0)}MB：${file.name}`)
        const dataUrl = await readFileAsDataUrl(file)
        attachments.value.push({
          id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}-${file.name}`,
          kind: 'document',
          name: file.name,
          mime_type: file.type || 'application/octet-stream',
          size: file.size,
          data_url: dataUrl,
        })
        continue
      }
      if (isTextAttachment(file)) {
        if (file.size > limits.textMaxBytes) throw new Error(`文本文件超过 ${(limits.textMaxBytes / 1024).toFixed(0)}KB：${file.name}`)
        const text = await file.text()
        if (text.length > limits.textMaxChars) throw new Error(`文本文件超过 ${limits.textMaxChars.toLocaleString('zh-CN')} 字：${file.name}`)
        attachments.value.push({
          id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}-${file.name}`,
          kind: 'text',
          name: file.name,
          mime_type: file.type || 'text/plain',
          size: file.size,
          text,
        })
        continue
      }
      if (file.size > limits.documentMaxBytes) throw new Error(`文件超过 ${(limits.documentMaxBytes / 1024 / 1024).toFixed(0)}MB：${file.name}`)
      attachments.value.push({
        id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}-${file.name}`,
        kind: 'file',
        name: file.name,
        mime_type: file.type || 'application/octet-stream',
        size: file.size,
        data_url: await readFileAsDataUrl(file),
      })
    }
    errorMessage.value = ''
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function handleFileSelection(event) {
  const selectedFiles = [...(event.target.files || [])]
  event.target.value = ''
  await addSelectedFiles(selectedFiles)
}

async function handleComposerPaste(event) {
  const clipboardFiles = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile())
    .filter(Boolean)
  if (!clipboardFiles.length) return
  event.preventDefault()
  await addSelectedFiles(clipboardFiles)
}

function dragContainsFiles(event) {
  return [...(event.dataTransfer?.types || [])].includes('Files')
}

function handleFileDragEnter(event) {
  if (!dragContainsFiles(event)) return
  event.preventDefault()
  fileDragDepth += 1
  isFileDragging.value = true
}

function handleFileDragOver(event) {
  if (!dragContainsFiles(event)) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'
}

function handleFileDragLeave(event) {
  if (!isFileDragging.value) return
  fileDragDepth = Math.max(0, fileDragDepth - 1)
  if (!fileDragDepth) isFileDragging.value = false
}

async function handleFileDrop(event) {
  if (!dragContainsFiles(event)) return
  event.preventDefault()
  fileDragDepth = 0
  isFileDragging.value = false
  await addSelectedFiles([...(event.dataTransfer?.files || [])])
}

function removeAttachment(id) {
  attachments.value = attachments.value.filter((item) => item.id !== id)
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function turnAttachments(turn) {
  return turn.parts.flatMap((part) => part.attachments || [])
}

function resetProviderForm() {
  providerForm.value = {
    preset_id: '',
    provider_kind: 'relay',
    provider_protocol: 'openai',
    default_api_mode: 'auto',
    provider_name: '',
    model: '',
    base_url: '',
    api_key: '',
    supports_vision: false,
    cached_input_price_cny_per_million: 0,
    input_price_cny_per_million: 0,
    output_price_cny_per_million: 0,
  }
  discoveredModels.value = []
  providerDiscoveryWarning.value = ''
  providerDiscoveryMeta.value = null
}

function selectProviderKind(kind) {
  providerForm.value.provider_kind = kind
  providerForm.value.preset_id = ''
  providerForm.value.provider_protocol = kind === 'official' ? 'openai' : 'openai'
  providerForm.value.default_api_mode = 'auto'
  providerForm.value.base_url = kind === 'official' ? 'https://api.openai.com/v1' : ''
  if (kind === 'official') providerForm.value.provider_name = 'OpenAI 官方'
  discoveredModels.value = []
  providerDiscoveryWarning.value = ''
}

function selectProviderPreset(presetId) {
  const preset = providerPresets.value.find((item) => item.id === presetId)
  if (!preset) return
  providerForm.value.preset_id = preset.id
  providerForm.value.provider_kind = preset.kind || 'official'
  providerForm.value.provider_protocol = preset.protocol || 'openai'
  providerForm.value.default_api_mode = preset.default_api_mode || 'auto'
  providerForm.value.provider_name = preset.name || ''
  providerForm.value.base_url = preset.base_url || ''
  discoveredModels.value = []
  providerDiscoveryWarning.value = preset.note || ''
  providerDiscoveryMeta.value = null
}

function selectOfficialProvider(protocol) {
  providerForm.value.provider_kind = 'official'
  providerForm.value.provider_protocol = protocol
  providerForm.value.base_url = protocol === 'deepseek'
    ? 'https://api.deepseek.com/v1'
    : 'https://api.openai.com/v1'
  providerForm.value.provider_name = protocol === 'deepseek' ? 'DeepSeek 官方' : 'OpenAI 官方'
  discoveredModels.value = []
  providerDiscoveryWarning.value = ''
}

async function openProviderPanel() {
  activeView.value = 'settings'
  activeSettingsSection.value = 'models'
  localStorage.setItem('mio_settings_section', 'models')
  resetProviderForm()
  showProviderPanel.value = true
}

async function saveProvider() {
  if (providerBusy.value) return
  providerBusy.value = true
  errorMessage.value = ''
  try {
    const selected = discoveredModels.value.filter((item) => item.selected)
    if (discoveredModels.value.length && !selected.length && !providerForm.value.model.trim()) {
      throw new Error('至少选择一个模型版本。')
    }
    const requestedModels = selected.length ? selected : [{
      model: providerForm.value.model,
      display_name: providerForm.value.model,
      family_name: providerForm.value.model,
      variant_name: '',
      cached_input_price_cny_per_million: providerForm.value.cached_input_price_cny_per_million,
      input_price_cny_per_million: providerForm.value.input_price_cny_per_million,
      output_price_cny_per_million: providerForm.value.output_price_cny_per_million,
    }]
    const created = await request('/api/agent/providers', {
      method: 'POST',
      body: JSON.stringify({
        provider_name: providerForm.value.provider_name,
        provider_kind: providerForm.value.provider_kind,
        provider_protocol: providerForm.value.provider_protocol,
        default_api_mode: providerForm.value.default_api_mode,
        preset_id: providerForm.value.preset_id,
        auth_scheme: providerDiscoveryMeta.value?.auth_scheme || 'auto',
        base_url: providerDiscoveryMeta.value?.resolved_api_base_url || providerForm.value.base_url,
        api_key: providerForm.value.api_key,
        models: requestedModels.map((item) => ({
          display_name: item.display_name || item.model,
          family_name: item.family_name || item.model,
          variant_name: item.variant_name || '',
          model: item.model,
          supports_vision: Boolean(item.supports_vision ?? providerForm.value.supports_vision),
          cached_input_price_cny_per_million: item.cached_input_price_cny_per_million || 0,
          input_price_cny_per_million: item.input_price_cny_per_million || 0,
          output_price_cny_per_million: item.output_price_cny_per_million || 0,
          pricing_source: item.pricing_source || '',
          api_mode: item.api_mode || '',
        })),
      }),
    })
    const lastModel = created.models?.at(-1)
    if (!lastModel) throw new Error('供应商没有保存任何模型。')
    await loadBootstrap({ quiet: true })
    selectedModel.value = lastModel.id
    persistSelectedModel()
    showProviderPanel.value = false
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    providerBusy.value = false
  }
}

async function discoverProviderModels() {
  if (providerDiscoveryBusy.value) return
  providerDiscoveryBusy.value = true
  errorMessage.value = ''
  providerDiscoveryWarning.value = ''
  providerDiscoveryMeta.value = null
  try {
    const data = await request('/api/agent/models/discover', {
      method: 'POST',
      body: JSON.stringify({
        base_url: providerForm.value.base_url,
        api_key: providerForm.value.api_key,
        provider_kind: providerForm.value.provider_kind,
        provider_protocol: providerForm.value.provider_protocol,
        default_api_mode: providerForm.value.default_api_mode,
        preset_id: providerForm.value.preset_id,
      }),
    })
    discoveredModels.value = (data.models || []).map((model) => ({
      ...model,
      selected: model.api_supported !== false && /[-_](flash|pro|sol|luna)$/i.test(model.model),
    }))
    providerDiscoveryWarning.value = data.warning || ''
    providerDiscoveryMeta.value = {
      resolved_api_base_url: data.resolved_api_base_url || data.resolved_base_url || providerForm.value.base_url,
      models_endpoint: data.models_endpoint || '',
      auth_scheme: data.auth_scheme || 'bearer',
      default_api_mode: data.default_api_mode || providerForm.value.default_api_mode,
      attempts: data.attempts || [],
    }
    if (!discoveredModels.value.length) throw new Error('这个供应商没有返回可用模型。')
  } catch (error) {
    providerDiscoveryMeta.value = null
    const manualHint = '仍可在下方手动填写模型 ID；实际聊天需要有效的 API Key。'
    providerDiscoveryWarning.value = `${error.message} ${manualHint}`
  } finally {
    providerDiscoveryBusy.value = false
  }
}

async function testWebSearch() {
  if (webSearchTestBusy.value) return
  webSearchTestBusy.value = true
  webSearchTestResult.value = null
  try {
    webSearchTestResult.value = await settingsApi.testWebSearch(webSearchTestQuery.value)
  } catch (error) {
    webSearchTestResult.value = { ok: false, message: error.message, sources: [], attempts: [] }
  } finally {
    webSearchTestBusy.value = false
  }
}

async function deleteProvider(model) {
  if (!model.is_custom || providerBusy.value) return
  providerBusy.value = true
  errorMessage.value = ''
  try {
    await request(`/api/agent/models/${encodeURIComponent(model.id)}`, { method: 'DELETE' })
    if (selectedModel.value === model.id) selectedModel.value = 'auto'
    persistSelectedModel()
    await loadBootstrap({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    providerBusy.value = false
  }
}

async function deleteProviderGroup(group) {
  if (providerBusy.value || !group?.provider_id) return
  const hasBuiltInModels = group.models.some((model) => !model.is_custom)
  const confirmed = await showAppConfirm({
    title: `删除供应商“${group.provider}”？`,
    message: hasBuiltInModels
      ? `将隐藏这个内置供应商及其 ${group.models.length} 个模型，并清理失效引用。之后可以在这里恢复。`
      : `将删除这个供应商及其 ${group.models.length} 个本机模型配置。此操作不能撤销。`,
    confirmText: '删除供应商',
    danger: true,
  })
  if (!confirmed) return
  providerBusy.value = true
  errorMessage.value = ''
  try {
    const result = await request(`/api/agent/providers/${encodeURIComponent(group.provider_id)}`, { method: 'DELETE' })
    const deletedIds = new Set(result.deleted_model_ids || group.models.map((model) => model.id))
    if (deletedIds.has(selectedModel.value)) selectedModel.value = 'auto'
    persistSelectedModel()
    if (deletedIds.has(chatSettingsDraft.value.model_id)) chatSettingsDraft.value.model_id = 'auto'
    await loadBootstrap({ quiet: true })
    showSettingsFeedback('models', 'success', `已删除供应商“${group.provider}”`)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    providerBusy.value = false
  }
}

async function restoreProvider(provider) {
  if (providerBusy.value || !provider?.provider_id) return
  providerBusy.value = true
  errorMessage.value = ''
  try {
    await request(`/api/agent/providers/${encodeURIComponent(provider.provider_id)}/restore`, {
      method: 'POST',
      body: '{}',
    })
    await loadBootstrap({ quiet: true })
    showSettingsFeedback('models', 'success', `已恢复内置供应商“${provider.display_name}”`)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    providerBusy.value = false
  }
}

async function testModel(model) {
  if (modelTestBusy.value) return
  modelTestBusy.value = model.id
  modelTestStatus.value = { ...modelTestStatus.value, [model.id]: { state: 'testing', text: '测试中' } }
  try {
    const result = await request(`/api/agent/models/${encodeURIComponent(model.id)}/test`, {
      method: 'POST',
      body: '{}',
    })
    modelTestStatus.value = { ...modelTestStatus.value, [model.id]: { state: 'success', text: result.message } }
  } catch (error) {
    modelTestStatus.value = { ...modelTestStatus.value, [model.id]: { state: 'error', text: modelRequestError(error) } }
  } finally {
    modelTestBusy.value = ''
  }
}

async function loadStats(year = 0, month = 0) {
  if (statsLoading.value) return
  statsLoading.value = true
  try {
    statsData.value = await request(`/api/agent/stats?year=${year}&month=${month}`)
    statsLoaded.value = true
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    statsLoading.value = false
  }
}

async function loadAgentTasks({ quiet = false } = {}) {
  if (tasksLoading.value) return
  tasksLoading.value = true
  try {
    agentTasks.value = await request('/api/agent/tasks?limit=200')
    tasksLoaded.value = true
  } catch (error) {
    if (!quiet) errorMessage.value = error.message
  } finally {
    tasksLoading.value = false
  }
  await loadAutonomy({ quiet: true })
}

async function loadAutonomy({ quiet = false } = {}) {
  if (autonomyLoading.value) return
  autonomyLoading.value = true
  try {
    autonomyData.value = await autonomyApi.loadAutonomy(200)
    autonomyLoaded.value = true
  } catch (error) {
    if (!quiet) errorMessage.value = error.message
  } finally {
    autonomyLoading.value = false
  }
}

async function saveAutonomyPolicy() {
  if (autonomyBusy.value) return
  autonomyBusy.value = 'policy'
  try {
    const policy = autonomyData.value.policy || {}
    autonomyData.value.policy = await autonomyApi.updateAutonomyPolicy({
      paused: Boolean(policy.paused),
      autonomy_level: policy.autonomy_level,
      quiet_start_hour: Number(policy.quiet_start_hour),
      quiet_end_hour: Number(policy.quiet_end_hour),
      minimum_interval_minutes: Number(policy.minimum_interval_minutes),
      daily_behavior_limit: Number(policy.daily_behavior_limit),
      daily_budget_yuan: Number(policy.daily_budget_yuan),
      capability_overrides: policy.capability_overrides || {},
    })
  } catch (error) {
    errorMessage.value = error.message
    await loadAutonomy({ quiet: true })
  } finally {
    autonomyBusy.value = ''
  }
}

function setAutonomyCapabilityOverride(capabilityId, mode) {
  const overrides = { ...(autonomyData.value.policy?.capability_overrides || {}) }
  if (mode) overrides[capabilityId] = mode
  else delete overrides[capabilityId]
  autonomyData.value.policy.capability_overrides = overrides
}

async function createAutonomyGoal() {
  const title = newAutonomyGoalTitle.value.trim()
  if (!title || autonomyBusy.value) return
  autonomyBusy.value = 'create-goal'
  try {
    await autonomyApi.createAutonomyGoal({
      title,
      description: '由用户在 Agent 执行中心创建。',
      conversation_id: selectedConversationId.value,
      autonomy_level: '',
      capabilities: [newAutonomyGoalCapability.value],
      due_at: '',
    })
    newAutonomyGoalTitle.value = ''
    await loadAutonomy({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    autonomyBusy.value = ''
  }
}

async function updateAutonomyGoal(goal, status) {
  if (autonomyBusy.value) return
  autonomyBusy.value = `goal-${goal.id}`
  try {
    const updated = await autonomyApi.updateAutonomyGoalStatus(goal.id, status)
    autonomyData.value.goals = autonomyData.value.goals.map((item) => item.id === updated.id ? updated : item)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    autonomyBusy.value = ''
  }
}

async function approveAutonomyBehavior(behavior) {
  if (autonomyBusy.value) return
  autonomyBusy.value = `behavior-${behavior.id}`
  try {
    await autonomyApi.approveAutonomyBehavior(behavior.id)
    await Promise.all([
      loadAutonomy(),
      refreshMessages(),
    ])
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    autonomyBusy.value = ''
  }
}

async function cancelAutonomyBehavior(behavior) {
  if (autonomyBusy.value) return
  autonomyBusy.value = `behavior-${behavior.id}`
  try {
    await autonomyApi.cancelAutonomyBehavior(behavior.id)
    await loadAutonomy()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    autonomyBusy.value = ''
  }
}

function autonomyStatusLabel(status) {
  return {
    active: '进行中',
    paused: '已暂停',
    completed: '已完成',
    cancelled: '已取消',
    pending: '等待评估',
    claimed: '评估中',
    processed: '已处理',
    ignored: '未行动',
    waiting_confirmation: '等待确认',
    failed: '失败',
    planned: '待执行',
    awaiting_confirmation: '等待确认',
    delivered: '已送达',
    delivery_unknown: '送达未知',
    suppressed: '已阻止',
  }[status] || status || '未知状态'
}

function autonomyDeliveryLabel(behavior) {
  return {
    app_and_qq: '应用与 QQ',
    app_only: '仅应用',
    app_qq_unknown: '应用已送达，QQ 未知',
    app_delivered: '应用已送达',
    not_attempted: '尚未投递',
  }[behavior?.delivery_status] || behavior?.delivery_status || '尚未投递'
}

function taskStatusLabel(status) {
  return {
    queued: '等待执行',
    running: '正在执行',
    needs_confirmation: '等待确认',
    executed: '执行完成',
    failed: '执行失败',
    skipped: '已跳过',
    cancelled: '已取消',
  }[status] || status || '未知状态'
}

function taskPayloadSummary(task) {
  const payload = task?.payload || {}
  return payload.content
    || payload.instruction
    || payload.reason
    || payload.mood
    || payload.daily_thirty_reason
    || 'Mio 根据对话判断并执行的本地动作'
}

async function approveAgentTask(task) {
  if (taskBusy.value) return
  taskBusy.value = `approve-${task.id}`
  try {
    const updated = await request(`/api/agent/tasks/${task.id}/approve`, { method: 'POST', body: '{}' })
    agentTasks.value = agentTasks.value.map((item) => item.id === updated.id ? updated : item)
    if (task.conversation_id === selectedConversationId.value) await refreshMessages()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    taskBusy.value = ''
  }
}

async function cancelAgentTask(task) {
  if (taskBusy.value) return
  taskBusy.value = `cancel-${task.id}`
  try {
    const updated = await request(`/api/agent/tasks/${task.id}/cancel`, { method: 'POST', body: '{}' })
    agentTasks.value = agentTasks.value.map((item) => item.id === updated.id ? updated : item)
    if (task.conversation_id === selectedConversationId.value) await refreshMessages()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    taskBusy.value = ''
  }
}

function changeStatsMonth(delta) {
  const current = new Date(Date.UTC(statsData.value.year, statsData.value.month - 1 + delta, 1))
  loadStats(current.getUTCFullYear(), current.getUTCMonth() + 1)
}

function openStatsDiary(cell) {
  if (cell.hasDiary) openDiary(cell.date)
}

async function loadDiaries() {
  try {
    const loadedDiaries = await request(`/api/diaries?q=${encodeURIComponent(diarySearch.value.trim())}`)
    diaries.value = loadedDiaries
    if (!loadedDiaries.length) {
      selectedDiary.value = null
    } else if (!selectedDiary.value || !loadedDiaries.some((item) => item.date === selectedDiary.value.date)) {
      selectedDiary.value = await request(`/api/diaries/${loadedDiaries[0].date}`)
    }
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function openDiary(date) {
  activeView.value = 'diaries'
  try {
    selectedDiary.value = await request(`/api/diaries/${date}`)
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function generateTodayDiary() {
  diaryBusy.value = 'generate'
  try {
    await diaryApi.generateTodayDiary()
    await loadDiaries()
    await refreshDayDashboard()
    if (logicalDate.value) await openDiary(logicalDate.value)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    diaryBusy.value = ''
  }
}

async function confirmDiary() {
  if (!selectedDiary.value) return
  diaryBusy.value = 'confirm'
  try {
    await request(`/api/diaries/${selectedDiary.value.date}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ confirmed: true }),
    })
    await openDiary(selectedDiary.value.date)
    await loadDiaries()
    await refreshDayDashboard()
    if (statsLoaded.value) await loadStats(statsData.value.year, statsData.value.month)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    diaryBusy.value = ''
  }
}

async function updateDiary(date, payload) {
  diaryBusy.value = 'edit'
  try {
    await request(`/api/diaries/${date}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
    await loadDiaries()
    await openDiary(date)
    await refreshDayDashboard()
    if (statsLoaded.value) await loadStats(statsData.value.year, statsData.value.month)
    return true
  } catch (error) {
    errorMessage.value = error.message
    return false
  } finally {
    diaryBusy.value = ''
  }
}

async function loadMemoryHub({ quiet = false } = {}) {
  if (!quiet) memoryLoading.value = true
  try {
    const [memory, reviews, weekly, monthly] = await Promise.all([
      memoryApi.loadMemory(),
      diaryApi.listDailyReviews(),
      diaryApi.listWeeklyReviews(),
      diaryApi.listMonthlyReviews(),
    ])
    memoryData.value = memory
    runtimeSummaryDraft.value = memory.runtime_summary?.content || ''
    dailyReviews.value = reviews
    weeklyReviews.value = weekly
    monthlyReviews.value = monthly
    if (!selectedDailyDate.value) selectedDailyDate.value = dailyReviewItems.value[0]?.date || logicalDate.value
    if (!selectedWeeklyStart.value) selectedWeeklyStart.value = weeklyReviewItems.value[0]?.week_start || weekStartFor(logicalDate.value)
    if (!selectedMonthlyMonth.value) selectedMonthlyMonth.value = monthlyReviewItems.value[0]?.month || logicalDate.value.slice(0, 7)
    memoryLoaded.value = true
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryLoading.value = false
  }
}

async function resolveThread(threadId) {
  try {
    await request(`/api/threads/${threadId}/resolve`, { method: 'POST', body: '{}' })
    memoryData.value.threads = memoryData.value.threads.filter((item) => item.id !== threadId)
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function recordFollowUpResult(thread, payload) {
  if (!thread?.id || memoryBusy.value) return false
  memoryBusy.value = `thread-result-${thread.id}`
  try {
    await request(`/api/threads/${thread.id}/result`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    await loadMemoryHub({ quiet: true })
    return true
  } catch (error) {
    errorMessage.value = error.message
    return false
  } finally {
    memoryBusy.value = ''
  }
}

async function saveRuntimeSummary() {
  if (!runtimeSummaryDraft.value.trim() || memoryBusy.value) return
  memoryBusy.value = 'runtime'
  try {
    await request('/api/memory/runtime-summary', {
      method: 'PUT',
      body: JSON.stringify({ content: runtimeSummaryDraft.value }),
    })
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

function memoryLayerLabel(layer) {
  return {
    L0: '核心记忆',
    L1: '近期状态',
    L2: '长期经历',
  }[layer] || layer
}

function memoryCategoryLabel(category) {
  return {
    identity: '身份事实',
    preference: '稳定偏好',
    relationship: '相处边界',
    current_state: '当前状态',
    plan: '计划',
    project: '项目',
    experience: '经历',
    person: '人物',
    other: '其他',
  }[category] || category
}

async function addStructuredMemory() {
  if (!newStructuredMemory.value.content.trim() || memoryBusy.value) return
  memoryBusy.value = 'structured-new'
  try {
    await request('/api/memory/items', {
      method: 'POST',
      body: JSON.stringify({
        ...newStructuredMemory.value,
        confidence: 1,
        conversation_id: selectedConversationId.value || 'default',
      }),
    })
    newStructuredMemory.value = { layer: 'L0', category: 'preference', memory_key: '', content: '' }
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function archiveStructuredMemory(memory) {
  if (memoryBusy.value) return
  const confirmed = await showAppConfirm({ title: '停用这条记忆？', message: '历史证据仍会保留。', confirmText: '停用记忆' })
  if (!confirmed) return
  memoryBusy.value = `structured-${memory.id}`
  try {
    await request(`/api/memory/items/${memory.id}`, { method: 'DELETE' })
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function restoreStructuredMemory(memory) {
  if (memoryBusy.value) return false
  const confirmed = await showAppConfirm({
    title: '恢复这个记忆版本？',
    message: '当前同类记忆会保留在历史中，这个版本会重新用于后续对话。',
    confirmText: '恢复版本',
  })
  if (!confirmed) return false
  memoryBusy.value = `restore-${memory.id}`
  try {
    await request(`/api/memory/items/${memory.id}/restore`, { method: 'POST', body: '{}' })
    await loadMemoryHub({ quiet: true })
    return true
  } catch (error) {
    errorMessage.value = error.message
    return false
  } finally {
    memoryBusy.value = ''
  }
}

async function confirmMemoryCandidate(memory) {
  if (memoryBusy.value) return
  memoryBusy.value = `candidate-${memory.id}`
  try {
    await request(`/api/memory/items/${memory.id}/confirm`, { method: 'POST', body: '{}' })
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function rejectMemoryCandidate(memory) {
  if (memoryBusy.value) return
  memoryBusy.value = `candidate-${memory.id}`
  try {
    await request(`/api/memory/items/${memory.id}/reject`, { method: 'POST', body: '{}' })
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function editStructuredMemory(memory) {
  const content = await showAppPrompt({
    title: '修改记忆内容',
    message: '修改后会更新 Mio 在后续对话中使用的内容',
    value: memory.content || '',
    multiline: true,
  })
  if (content === null || !content.trim() || content.trim() === memory.content || memoryBusy.value) return
  memoryBusy.value = `structured-${memory.id}`
  try {
    await request('/api/memory/items', {
      method: 'POST',
      body: JSON.stringify({
        layer: memory.layer || 'L0',
        category: memory.category || 'other',
        memory_key: memory.memory_key || `${memory.category || 'memory'}-${memory.id}`,
        content: content.trim(),
        confidence: 1,
        conversation_id: selectedConversationId.value || 'default',
      }),
    })
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function addMemoryThread() {
  if (!newThreadContent.value.trim() || memoryBusy.value) return
  memoryBusy.value = 'thread-new'
  try {
    await request('/api/threads', {
      method: 'POST',
      body: JSON.stringify({
        content: newThreadContent.value,
        conversation_id: selectedConversationId.value || 'default',
        follow_up_after: newThreadFollowUp.value,
      }),
    })
    newThreadContent.value = ''
    newThreadFollowUp.value = ''
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function saveMemoryThread(thread) {
  if (!thread.content?.trim() || memoryBusy.value) return
  memoryBusy.value = `thread-${thread.id}`
  try {
    await request(`/api/threads/${thread.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        content: thread.content,
        conversation_id: thread.conversation_id,
        follow_up_after: thread.follow_up_after || '',
      }),
    })
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function deleteMemoryThread(threadId) {
  if (memoryBusy.value) return
  const confirmed = await showAppConfirm({ title: '删除待跟进记忆？', message: '删除后不会再根据这条内容主动跟进。', confirmText: '删除', danger: true })
  if (!confirmed) return
  memoryBusy.value = `thread-${threadId}`
  try {
    await request(`/api/threads/${threadId}`, { method: 'DELETE' })
    memoryData.value.threads = memoryData.value.threads.filter((item) => item.id !== threadId)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function saveConversationSummary(summary) {
  if (!summary.content?.trim() || memoryBusy.value) return
  memoryBusy.value = `summary-${summary.conversation_id}`
  try {
    await request('/api/memory/conversation-summary', {
      method: 'PUT',
      body: JSON.stringify({ conversation_id: summary.conversation_id, content: summary.content }),
    })
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function addConversationSummary() {
  if (!newConversationSummary.value.trim() || memoryBusy.value) return
  const conversationId = selectedConversationId.value || 'default'
  memoryBusy.value = 'summary-new'
  try {
    await request('/api/memory/conversation-summary', {
      method: 'PUT',
      body: JSON.stringify({ conversation_id: conversationId, content: newConversationSummary.value }),
    })
    newConversationSummary.value = ''
    await loadMemoryHub({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function deleteConversationSummary(summary) {
  if (memoryBusy.value) return
  const confirmed = await showAppConfirm({ title: '删除这份长期印象？', message: '之后会在上下文再次压缩时重新生成。', confirmText: '删除', danger: true })
  if (!confirmed) return
  memoryBusy.value = `summary-${summary.conversation_id}`
  try {
    await request(`/api/memory/conversation-summary/${encodeURIComponent(summary.conversation_id)}`, { method: 'DELETE' })
    memoryData.value.summaries = memoryData.value.summaries.filter((item) => item.conversation_id !== summary.conversation_id)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function addProfileNote() {
  if (!newProfileNote.value.trim() || memoryBusy.value) return
  memoryBusy.value = 'note-new'
  try {
    const result = await request('/api/memory/profile-notes', {
      method: 'POST',
      body: JSON.stringify({ content: newProfileNote.value }),
    })
    memoryData.value.profile = result.profile
    newProfileNote.value = ''
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function saveProfileNote(index, note) {
  if (!String(note || '').trim() || memoryBusy.value) return
  memoryBusy.value = `note-${index}`
  try {
    const result = await request(`/api/memory/profile-notes/${index}`, {
      method: 'PUT',
      body: JSON.stringify({ content: note }),
    })
    memoryData.value.profile = result.profile
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function deleteProfileNote(index) {
  if (memoryBusy.value) return
  const confirmed = await showAppConfirm({ title: '删除这条 Mio 属性记忆？', message: '删除后，Mio 不会再把它作为固定属性使用。', confirmText: '删除', danger: true })
  if (!confirmed) return
  memoryBusy.value = `note-${index}`
  try {
    const result = await request(`/api/memory/profile-notes/${index}`, { method: 'DELETE' })
    memoryData.value.profile = result.profile
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    memoryBusy.value = ''
  }
}

async function openDailyReview(date) {
  activeView.value = 'memory'
  memoryTab.value = 'daily'
  selectedDailyDate.value = date
  if (!memoryLoaded.value) await loadMemoryHub()
}

async function generateDailyReview(date = selectedDailyDate.value || logicalDate.value) {
  if (!date || reviewBusy.value) return
  selectedDailyDate.value = date
  reviewBusy.value = 'daily'
  errorMessage.value = ''
  try {
    await request(`/api/reviews/${date}/generate`, { method: 'POST', body: '{}' })
    dailyReviews.value = await request('/api/reviews')
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    reviewBusy.value = ''
  }
}

async function generateWeekly(start = selectedWeeklyStart.value || weekStartFor(logicalDate.value)) {
  if (!start || reviewBusy.value) return
  selectedWeeklyStart.value = start
  reviewBusy.value = 'weekly'
  errorMessage.value = ''
  try {
    await request(`/api/weekly/${start}/generate`, { method: 'POST', body: '{}' })
    weeklyReviews.value = await request('/api/weekly')
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    reviewBusy.value = ''
  }
}

async function generateMonthly(month = selectedMonthlyMonth.value || logicalDate.value.slice(0, 7)) {
  if (!month || reviewBusy.value) return
  selectedMonthlyMonth.value = month
  reviewBusy.value = 'monthly'
  errorMessage.value = ''
  try {
    await request(`/api/monthly/${month}/generate`, { method: 'POST', body: '{}' })
    monthlyReviews.value = await request('/api/monthly')
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    reviewBusy.value = ''
  }
}

function applyQqStatus(status) {
  bootstrap.value = {
    ...(bootstrap.value || {}),
    qq: { ...(bootstrap.value?.qq || {}), ...(status || {}) },
  }
}

function qqStartupMessage(status) {
  if (status?.account_ready && status?.websocket_connected) return `OneBot 已连接，机器人 QQ ${status.connected_account || ''} 可以使用`
  if (status?.diagnostic_code === 'account_mismatch') return status.diagnostic_message
  if (status?.webui_reachable && status?.account_ready) return '机器人 QQ 已登录，正在等待 OneBot 连接'
  if (status?.webui_reachable) return 'NapCat WebUI 已就绪，正在准备登录二维码'
  if (status?.qq_process_running) return '机器人 QQ 已启动，正在等待 NapCat WebUI'
  if (status?.napcat_process_running) return 'NapCat 启动器已运行，正在等待机器人 QQ'
  return '启动命令已发送，正在等待 NapCat 进程'
}

async function waitForQqStartup({ attempts = 60, delayMs = 750 } = {}) {
  let lastStatus = null
  let lastRequestError = null
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      lastStatus = await request('/api/agent/qq/status')
      lastRequestError = null
      applyQqStatus(lastStatus)
      qqSetupResult.value = {
        ...(qqSetupResult.value || {}),
        stage: 'starting',
        message: qqStartupMessage(lastStatus),
        diagnostic: lastStatus.diagnostic_message || '',
      }
      if (lastStatus.webui_reachable || (lastStatus.websocket_connected && lastStatus.account_ready)) return lastStatus
    } catch (error) {
      lastRequestError = error
      qqSetupResult.value = {
        ...(qqSetupResult.value || {}),
        stage: 'starting',
        message: 'NapCat 启动状态暂时读取失败，正在重试',
        diagnostic: error.message || '',
      }
    }
    if (attempt < attempts - 1) {
      await new Promise((resolve) => window.setTimeout(resolve, delayMs))
    }
  }
  const diagnostic = lastStatus?.diagnostic_message
    || lastRequestError?.message
    || 'NapCat、机器人 QQ 和 WebUI 都没有就绪'
  throw new Error(`NapCat 启动未完成：${diagnostic}。请确认电脑已安装并登录过 NT QQ，再点击“重启NapCat”`)
}

async function controlQq(action) {
  qqBusy.value = action
  errorMessage.value = ''
  try {
    const result = await request(`/api/agent/qq/${action}`, { method: 'POST', body: '{}' })
    if (['start', 'restart'].includes(action)) {
      const status = await waitForQqStartup()
      if (!status.account_ready) {
        await request('/api/agent/qq/login', { method: 'POST', body: '{}' })
        await loadQqQrCode({ attempts: 30 })
      }
    }
    if (action === 'login') await loadQqQrCode({ attempts: 30 })
    if (action === 'stop') clearQqQrImage()
    await loadBootstrap({ quiet: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    qqBusy.value = ''
  }
}

async function setupQqChannel() {
  const account = qqAccountDraft.value.trim()
  if (!/^\d{5,12}$/.test(account)) {
    showSettingsFeedback('qq', 'error', '机器人 QQ 号必须是 5 到 12 位数字')
    return false
  }
  qqBusy.value = 'setup'
  qqSetupResult.value = null
  errorMessage.value = ''
  try {
    let result = await request('/api/agent/qq/setup', {
      method: 'POST',
      body: JSON.stringify({ account, target_user_id: qqTestTargetDraft.value.trim() }),
    })
    if (result.stage === 'installing') {
      showSettingsFeedback('qq', 'success', 'NapCat 正在安装，完成后会自动继续配置')
      for (let attempt = 0; attempt < 1350; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000))
        const progress = await request('/api/dependencies/napcat/status')
        if (progress.error) throw new Error(progress.error)
        if (progress.done && !progress.installing) break
        if (attempt === 1349) throw new Error('NapCat 安装等待超过 45 分钟，请检查安装窗口')
      }
      result = await request('/api/agent/qq/setup', {
        method: 'POST',
        body: JSON.stringify({ account, target_user_id: qqTestTargetDraft.value.trim() }),
      })
    }
    qqSetupResult.value = result
    const status = await waitForQqStartup()
    if (!status.account_ready) {
      if (!result.force_qr_login) {
        await request('/api/agent/qq/login', { method: 'POST', body: '{}' })
      }
      const qrcodeLoaded = await loadQqQrCode({ attempts: 30 })
      if (!qrcodeLoaded) throw new Error(qqQrError.value || 'NapCat 已启动，但登录二维码还没有准备好')
    }
    await loadBootstrap({ quiet: true })
    showSettingsFeedback(
      'qq',
      'success',
      status.account_ready ? `NapCat 已启动，机器人 QQ ${status.connected_account || account} 已登录` : 'NapCat 已启动并写入 OneBot，请用手机 QQ 扫码',
    )
    return true
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('qq', 'error', error.message || 'QQ 通道配置失败')
    return false
  } finally {
    qqBusy.value = ''
  }
}

async function testQqDelivery() {
  const account = qqAccountDraft.value.trim()
  const target = qqTestTargetDraft.value.trim()
  if (!/^\d{5,12}$/.test(account) || !/^\d{5,12}$/.test(target)) {
    showSettingsFeedback('qq', 'error', '请填写机器人 QQ 和接收测试消息的 QQ 号')
    return false
  }
  qqBusy.value = 'test-delivery'
  try {
    const result = await request('/api/agent/qq/test-delivery', {
      method: 'POST',
      body: JSON.stringify({ account, target_user_id: target }),
    })
    qqSetupResult.value = result
    if (!result.delivery_confirmed) throw new Error(result.diagnostic || 'NapCat 没有确认发送')
    showSettingsFeedback('qq', 'success', `NapCat 已确认发送${result.message_id ? `，消息 ID ${result.message_id}` : ''}`)
    return true
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('qq', 'error', error.message || '测试消息发送失败')
    return false
  } finally {
    qqBusy.value = ''
  }
}

function clearQqQrImage() {
  qqQrLoadVersion += 1
  if (qqQrImageUrl.value) URL.revokeObjectURL(qqQrImageUrl.value)
  qqQrImageUrl.value = ''
  qqQrLoading.value = false
  qqQrError.value = ''
}

async function loadQqQrCode({ attempts = 16 } = {}) {
  const loadVersion = ++qqQrLoadVersion
  if (qqQrImageUrl.value) URL.revokeObjectURL(qqQrImageUrl.value)
  qqQrImageUrl.value = ''
  qqQrLoading.value = true
  qqQrError.value = ''
  let lastError = '二维码暂时还没准备好'

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(`/api/agent/qq/qrcode?ts=${Date.now()}`, {
        headers: { Accept: 'image/png,image/*' },
        cache: 'no-store',
      })
      if (response.ok) {
        const blob = await response.blob()
        if (!blob.size || !blob.type.startsWith('image/')) throw new Error('二维码图片数据无效')
        const imageUrl = URL.createObjectURL(blob)
        if (loadVersion !== qqQrLoadVersion) {
          URL.revokeObjectURL(imageUrl)
          return false
        }
        qqQrImageUrl.value = imageUrl
        qqQrLoading.value = false
        return true
      }
      try {
        const payload = await response.json()
        lastError = payload.detail || lastError
      } catch {
        lastError = `二维码读取失败：HTTP ${response.status}`
      }
      if (response.status !== 404) break
    } catch (error) {
      lastError = error.message || lastError
    }
    if (attempt < attempts - 1) await new Promise((resolve) => window.setTimeout(resolve, 500))
  }

  if (loadVersion === qqQrLoadVersion) {
    qqQrLoading.value = false
    qqQrError.value = `${lastError}，请重新获取`
  }
  return false
}

async function saveGroupChatSettings() {
  if (qqBusy.value) return
  qqBusy.value = 'group-settings'
  errorMessage.value = ''
  try {
    const groupIds = groupIdsDraft.value
      .replaceAll('，', ',')
      .replaceAll('；', ',')
      .split(/[;,]/)
      .map((item) => item.trim())
      .filter(Boolean)
    const result = await request('/api/agent/qq/group-settings', {
      method: 'PATCH',
      body: JSON.stringify({
        enabled: groupChatSettings.value.enabled,
        group_ids: [...new Set(groupIds)],
        mention_required: groupChatSettings.value.mention_required,
      }),
    })
    groupChatSettings.value = { ...groupChatSettings.value, ...result.group_chat }
    groupIdsDraft.value = (result.group_chat.group_ids || []).join(', ')
    savedGroupChatSettings.value = normalizedGroupChatSnapshot()
    bootstrap.value = {
      ...(bootstrap.value || {}),
      qq: { ...(bootstrap.value?.qq || {}), group_chat: result.group_chat },
    }
    showSettingsFeedback('qq', 'success', 'QQ群聊设置已保存')
    return true
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('qq', 'error', 'QQ群聊设置保存失败')
    return false
  } finally {
    qqBusy.value = ''
  }
}

async function clearGroupChatContext() {
  if (qqBusy.value) return
  qqBusy.value = 'group-context'
  errorMessage.value = ''
  try {
    const result = await request('/api/agent/qq/group-context/clear', { method: 'POST', body: '{}' })
    groupChatSettings.value = { ...groupChatSettings.value, ...result.group_chat }
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    qqBusy.value = ''
  }
}

async function loadCompanionStatus({ quiet = false, preserveSettings = false } = {}) {
  if (companionStatusLoading) return
  companionStatusLoading = true
  try {
    const editingSettings = companionStatus.value.pet?.settings
    const previousSpriteVersion = companionStatus.value.pet?.sprite_version || ''
    const nextStatus = await observationApi.loadCompanionStatus()
    if (preserveSettings && editingSettings && nextStatus.pet) {
      nextStatus.pet.settings = editingSettings
    } else if (nextStatus.pet?.settings && nextStatus.voice_runtime?.active_weights) {
      nextStatus.pet.settings.gpt_sovits_gpt_weights ||= nextStatus.voice_runtime.active_weights.gpt || ''
      nextStatus.pet.settings.gpt_sovits_sovits_weights ||= nextStatus.voice_runtime.active_weights.sovits || ''
    }
    nextStatus.screen = nextStatus.screen || nextStatus.window || nextStatus.game || {}
     companionStatus.value = nextStatus
    companionStatusReady.value = true
    if (!preserveSettings) {
      for (const section of Object.keys(companionSettingKeys)) {
        savedCompanionSettings.value[section] = companionSettingsSnapshot(section, nextStatus.pet?.settings || {})
      }
      if (companionStatus.value.screen?.screen_scope) screenScope.value = companionStatus.value.screen.screen_scope
      if (companionStatus.value.screen?.interval_ms) observationInterval.value = companionStatus.value.screen.interval_ms
      observationMode.value = companionStatus.value.screen?.mode === 'window' ? 'game' : 'screen'
      if (companionStatus.value.screen?.hwnd) selectedGameHwnd.value = String(companionStatus.value.screen.hwnd)
    }
    if (String(nextStatus.pet?.sprite_version || '') !== String(previousSpriteVersion)) {
      companionAvatarNonce.value = Date.now()
    }
  } catch (error) {
    if (!quiet) errorMessage.value = error.message
  } finally {
    companionStatusLoading = false
  }
}

async function openScreenPreviewWindow() {
  errorMessage.value = ''
  try {
    if (window.pywebview?.api?.open_screen_preview) {
      const result = await window.pywebview.api.open_screen_preview()
      if (result?.ok === false) throw new Error(result.error || '独立预览窗口启动失败')
      return
    }
    window.open(`/api/companion/screen/preview?t=${Date.now()}`, '_blank', 'noopener,noreferrer')
  } catch (error) {
    errorMessage.value = `打开独立预览失败：${error.message}`
  }
}

function handlePetChatKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    if (window.mioPetChat?.hide) window.mioPetChat.hide()
    else window.pywebview?.api?.hide_pet_chat_window?.()
    return
  }
  if (event.key === 'Backspace' && !petChatDraft.value && petChatImages.value.length) {
    petChatImages.value = petChatImages.value.slice(0, -1)
    return
  }
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    sendPetChat()
  }
}

function beginPetChatWindowDrag(event) {
  if (event.button !== 0) return
  if (window.mioPetChat?.isElectron) return
  event.preventDefault()
  window.pywebview?.api?.window_drag?.()
}

const petCallStateLabel = computed(() => ({
  idle: '语音聊天',
  connecting: '正在接通',
  listening: '正在听',
  thinking: 'Mio 在想',
  waiting_voice: '准备声音',
  speaking: 'Mio 在说',
  error: '通话异常',
}[petCallState.value] || '语音聊天'))

function resetPetCallCapture() {
  petCallFrames = []
  petCallSpeechStartedAt = 0
  petCallSpeechCandidateStartedAt = 0
  petCallLastVoiceAt = 0
  petCallInterruptSent = false
  petCallBargeInStartedAt = 0
}

async function submitPetCallTurn() {
  if (!petCallFrames.length || petCallTurnPending || !petCallAudioContext || !petCallSessionId) return
  const callSessionId = petCallSessionId
  const turnId = petCallNextTurnId
  const frameCount = petCallFrames.reduce((total, frame) => total + frame.length, 0)
  const samples = new Float32Array(frameCount)
  let offset = 0
  for (const frame of petCallFrames) {
    samples.set(frame, offset)
    offset += frame.length
  }
  resetPetCallCapture()
  petCallTurnPending = true
  petCallState.value = 'thinking'
  try {
    const result = await request('/api/companion/call/turn', {
      method: 'POST',
      body: JSON.stringify({
        wav_base64: bytesToBase64(encodePcmWav(samples, petCallAudioContext.sampleRate)),
        language: petCallSettings.value.pet_call_input_language || 'zh',
        call_session_id: callSessionId,
        turn_id: turnId,
      }),
    })
    if (!petCallActive.value || petCallSessionId !== callSessionId) return
    petCallNextTurnId = Math.max(petCallNextTurnId, Number(result.turn_id || turnId) + 1)
    if (result.heard) {
      petCallResponseId = String(result.response_id || '')
      petCallAwaitingVoiceSince = performance.now()
      petCallState.value = 'waiting_voice'
    } else {
      petCallResponseId = ''
      petCallAwaitingVoiceSince = 0
      petCallState.value = 'listening'
    }
  } catch (error) {
    if (!petCallActive.value || petCallSessionId !== callSessionId) return
    petCallError.value = error.message
    petCallState.value = 'error'
  } finally {
    if (petCallSessionId === callSessionId) petCallTurnPending = false
  }
}

function processPetCallAudio(event) {
  if (!petCallActive.value) return
  const frame = new Float32Array(event.inputBuffer.getChannelData(0))
  let energy = 0
  for (const sample of frame) energy += sample * sample
  const rms = Math.sqrt(energy / Math.max(1, frame.length))
  const now = performance.now()
  const settings = petCallSettings.value
  const voiced = rms >= Number(settings.pet_call_voice_threshold || 0.018)
  const bargeInVoiced = rms >= bargeInThreshold(settings.pet_call_voice_threshold)
  const frameMs = frame.length / petCallAudioContext.sampleRate * 1000
  const maxPreRollFrames = Math.max(2, Math.ceil(240 / frameMs))

  if (!petCallSpeechStartedAt) {
    if (['thinking', 'waiting_voice', 'connecting'].includes(petCallState.value)) {
      petCallPreRoll = []
      petCallSpeechCandidateStartedAt = 0
      petCallBargeInStartedAt = 0
      return
    }
    if (petCallState.value === 'speaking') {
      if (!bargeInVoiced) {
        petCallPreRoll = []
        petCallBargeInStartedAt = 0
        return
      }
      petCallPreRoll.push(frame)
      if (petCallPreRoll.length > maxPreRollFrames) petCallPreRoll.shift()
      petCallBargeInStartedAt ||= now
      if (now - petCallBargeInStartedAt < 360) return
    } else if (!voiced || petCallTurnPending) {
      petCallPreRoll.push(frame)
      if (petCallPreRoll.length > maxPreRollFrames) petCallPreRoll.shift()
      petCallSpeechCandidateStartedAt = 0
      petCallBargeInStartedAt = 0
      return
    } else {
      petCallPreRoll.push(frame)
      if (petCallPreRoll.length > maxPreRollFrames) petCallPreRoll.shift()
      petCallSpeechCandidateStartedAt ||= now
      if (now - petCallSpeechCandidateStartedAt < 160) return
    }
    petCallSpeechStartedAt = now
    petCallLastVoiceAt = now
    petCallFrames = [...petCallPreRoll]
    petCallPreRoll = []
    if (petCallState.value === 'speaking' && !petCallInterruptSent) {
      petCallInterruptSent = true
      void request('/api/companion/call/interrupt', {
        method: 'POST',
        body: JSON.stringify({ call_session_id: petCallSessionId, response_id: petCallResponseId }),
      }).catch(() => {})
      petCallState.value = 'listening'
    }
  }
  petCallFrames.push(frame)
  if (voiced) petCallLastVoiceAt = now
  const speechMs = now - petCallSpeechStartedAt
  const silenceMs = now - petCallLastVoiceAt
  const reachedSilence = silenceMs >= Number(settings.pet_call_silence_ms || 650)
  const reachedMaximum = speechMs >= Number(settings.pet_call_max_turn_seconds || 18) * 1000
  if ((reachedSilence && speechMs >= Number(settings.pet_call_min_speech_ms || 280)) || reachedMaximum) {
    void submitPetCallTurn()
  }
}

async function startPetCall() {
  if (petCallActive.value) return
  petCallError.value = ''
  petCallState.value = 'connecting'
  try {
    const result = await request('/api/companion/call/start', { method: 'POST' })
    petCallSessionId = String(result.call_session_id || '')
    petCallNextTurnId = Number(result.next_turn_id || 1)
    petCallResponseId = ''
    petCallAwaitingVoiceSince = 0
    if (!petCallSessionId) throw new Error('电话服务没有返回会话 ID')
    petCallSettings.value = { ...petCallSettings.value, ...(result.settings || {}) }
    petCallStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
        sampleRate: { ideal: 16000 },
      },
    })
    petCallAudioContext = new AudioContext({ latencyHint: 'interactive' })
    await petCallAudioContext.resume()
    petCallSource = petCallAudioContext.createMediaStreamSource(petCallStream)
    petCallProcessor = petCallAudioContext.createScriptProcessor(2048, 1, 1)
    petCallProcessor.onaudioprocess = processPetCallAudio
    petCallSource.connect(petCallProcessor)
    petCallProcessor.connect(petCallAudioContext.destination)
    const microphoneTrack = petCallStream.getAudioTracks()[0]
    const microphoneSettings = microphoneTrack?.getSettings?.() || {}
    void request('/api/companion/call/device', {
      method: 'POST',
      body: JSON.stringify({
        call_session_id: petCallSessionId,
        device_id: String(microphoneSettings.deviceId || ''),
        label: String(microphoneTrack?.label || ''),
        sample_rate: Number(microphoneSettings.sampleRate || petCallAudioContext.sampleRate || 0),
        channel_count: Number(microphoneSettings.channelCount || 1),
        echo_cancellation: typeof microphoneSettings.echoCancellation === 'boolean' ? microphoneSettings.echoCancellation : null,
        noise_suppression: typeof microphoneSettings.noiseSuppression === 'boolean' ? microphoneSettings.noiseSuppression : null,
        auto_gain_control: typeof microphoneSettings.autoGainControl === 'boolean' ? microphoneSettings.autoGainControl : null,
      }),
    }).catch(() => {})
    resetPetCallCapture()
    petCallActive.value = true
    petCallState.value = 'listening'
    petCallMonitorTimer = window.setInterval(async () => {
      if (!petCallActive.value || !petCallSessionId) return
      try {
        const status = await request('/api/companion/call/status')
        if (String(status.call_session_id || '') !== petCallSessionId) return
        if (!status.active) {
          await stopPetCall({ notifyServer: false, preserveError: true })
          return
        }
        const startedId = String(status.voice_started?.response_id || '')
        const endedId = String(status.voice_ended?.response_id || '')
        if (petCallResponseId && startedId === petCallResponseId && endedId !== petCallResponseId) {
          petCallState.value = 'speaking'
          petCallAwaitingVoiceSince = 0
        } else if (petCallResponseId && endedId === petCallResponseId) {
          if (String(status.voice_ended?.reason || '') === 'error') {
            petCallError.value = 'Mio 的声音播放失败'
            petCallState.value = 'error'
          } else {
            petCallState.value = 'listening'
          }
          petCallResponseId = ''
          petCallAwaitingVoiceSince = 0
        } else if (
          petCallState.value === 'waiting_voice'
          && petCallAwaitingVoiceSince
          && performance.now() - petCallAwaitingVoiceSince > 30000
        ) {
          petCallError.value = 'Mio 已经生成回复，但 30 秒内没有收到真实首音'
          petCallState.value = 'error'
        }
      } catch (_) {}
    }, 250)
  } catch (error) {
    petCallError.value = error.message
    petCallState.value = 'error'
    await stopPetCall({ notifyServer: true, preserveError: true })
  }
}

async function stopPetCall({ notifyServer = true, preserveError = false } = {}) {
  const wasActive = petCallActive.value || petCallState.value === 'connecting'
  const callSessionId = petCallSessionId
  const responseId = petCallResponseId
  petCallActive.value = false
  petCallSessionId = ''
  petCallResponseId = ''
  petCallAwaitingVoiceSince = 0
  resetPetCallCapture()
  if (petCallMonitorTimer) window.clearInterval(petCallMonitorTimer)
  petCallMonitorTimer = null
  if (petCallProcessor) {
    petCallProcessor.onaudioprocess = null
    try { petCallProcessor.disconnect() } catch (_) {}
  }
  if (petCallSource) {
    try { petCallSource.disconnect() } catch (_) {}
  }
  for (const track of petCallStream?.getTracks?.() || []) track.stop()
  if (petCallAudioContext) await petCallAudioContext.close().catch(() => {})
  petCallProcessor = null
  petCallSource = null
  petCallStream = null
  petCallAudioContext = null
  petCallTurnPending = false
  if (!preserveError) petCallState.value = 'idle'
  if (notifyServer && wasActive && callSessionId) {
    await request('/api/companion/call/stop', {
      method: 'POST',
      body: JSON.stringify({ call_session_id: callSessionId, response_id: responseId }),
    }).catch(() => {})
  }
}

async function togglePetCall() {
  if (petCallActive.value) await stopPetCall()
  else await startPetCall()
}

async function sendPetChat() {
  const content = petChatDraft.value.trim()
  if ((!content && !petChatImages.value.length) || petChatSending.value) return
  const images = petChatImages.value.map(({ name, data_url }) => ({ name, data_url }))
  const clientRequestId = createClientRequestId()
  petChatDraft.value = ''
  petChatImages.value = []
  petChatSending.value = true
  errorMessage.value = ''
  try {
    const result = await request('/api/companion/chat', {
      method: 'POST',
      body: JSON.stringify({ message: content, images, client_request_id: clientRequestId }),
    })
    if (result.voice_attempted && !result.spoken && !result.voice_delegated) {
      errorMessage.value = `桌宠已经回复，但语音没有播放：${result.voice_error || '未知原因'}`
    }
  } catch (error) {
    petChatDraft.value = content
    petChatImages.value = images.map((item, index) => ({ ...item, id: `retry-${Date.now()}-${index}` }))
    errorMessage.value = `桌宠 Mio 暂时没有回复：${error.message}`
  } finally {
    petChatSending.value = false
  }
}

async function handlePetChatPaste(event) {
  const files = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
    .map((item) => item.getAsFile())
    .filter(Boolean)
  if (!files.length) return
  event.preventDefault()
  try {
    for (const file of files.slice(0, Math.max(0, 5 - petChatImages.value.length))) {
      if (file.size > attachmentLimits.value.imageMaxBytes) {
        throw new Error(`图片超过 ${(attachmentLimits.value.imageMaxBytes / 1024 / 1024).toFixed(0)}MB`)
      }
      petChatImages.value.push({
        id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}`,
        name: file.name || '粘贴的图片.png',
        data_url: await readFileAsDataUrl(file),
      })
    }
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function saveCompanionSize() {
  if (companionBusy.value === 'size') return
  companionBusy.value = 'size'
  errorMessage.value = ''
  try {
    companionStatus.value = await request('/api/companion/size', {
      method: 'PATCH',
      body: JSON.stringify({ percent: Number(companionStatus.value.pet.settings.pet_size_percent) }),
    })
  } catch (error) {
    errorMessage.value = `桌宠大小调整失败：${error.message}`
  } finally {
    companionBusy.value = ''
  }
}

async function controlCompanion(action) {
  if (companionBusy.value) return
  companionBusy.value = action
  errorMessage.value = ''
  try {
    companionStatus.value = await request(`/api/companion/${action}`, { method: 'POST', body: '{}' })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    companionBusy.value = ''
  }
}

async function previewLive2DMotion(group) {
  if (!group || companionBusy.value) return
  companionBusy.value = 'motion-preview'
  errorMessage.value = ''
  try {
    await request('/api/companion/live2d/motion/preview', {
      method: 'POST',
      body: JSON.stringify({ group }),
    })
  } catch (error) {
    errorMessage.value = `动作预览失败：${error.message}`
  } finally {
    companionBusy.value = ''
  }
}

async function previewLive2DExpression(expression) {
  if (!expression || companionBusy.value) return
  companionBusy.value = 'expression-preview'
  errorMessage.value = ''
  try {
    await request('/api/companion/live2d/expression/preview', {
      method: 'POST',
      body: JSON.stringify({ expression }),
    })
  } catch (error) {
    errorMessage.value = `表情预览失败：${error.message}`
  } finally {
    companionBusy.value = ''
  }
}

async function saveCompanionSettings(section = '') {
  if (typeof section !== 'string') section = ''
  if (companionBusy.value || !companionStatusReady.value) return
  companionBusy.value = 'settings'
  errorMessage.value = ''
  try {
    const settings = companionStatus.value.pet.settings
    if (section === 'observation') {
      settings.screen_audio_enabled = Boolean(companionStatus.value.screen?.running)
    }
    let previousPetSettings = {}
    if (section === 'pet' && savedCompanionSettings.value.pet) {
      try { previousPetSettings = JSON.parse(savedCompanionSettings.value.pet) || {} } catch (_) {}
    }
    const petWasRunning = section === 'pet' && Boolean(companionStatus.value.pet.running)
    const payload = section && companionSettingKeys[section]
      ? Object.fromEntries(companionSettingKeys[section].map((key) => [key, settings[key]]))
      : settings
    companionStatus.value = await request('/api/companion/settings', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    const rendererChanged = section === 'pet'
      && previousPetSettings.pet_renderer !== settings.pet_renderer
    const runtimeChanged = section === 'pet'
      && previousPetSettings.live2d_disable_gpu !== settings.live2d_disable_gpu
    if (petWasRunning && (rendererChanged || runtimeChanged)) {
      companionStatus.value = await request('/api/companion/restart', { method: 'POST', body: '{}' })
    }
    const sections = section ? [section] : Object.keys(companionSettingKeys)
    if (section === 'voice') sections.push('pet')
    if (section === 'pet') sections.push('voice')
    for (const item of sections) {
      savedCompanionSettings.value[item] = companionSettingsSnapshot(item)
    }
    if (section) {
      const message = section === 'pet' && (rendererChanged || runtimeChanged) ? '桌宠运行方式已更新' : `${activeSettingsItem.value.label}设置已保存`
      showSettingsFeedback(section, 'success', message)
    }
    return true
  } catch (error) {
    errorMessage.value = error.message
    if (section) showSettingsFeedback(section, 'error', `${activeSettingsItem.value.label}设置保存失败`)
    return false
  } finally {
    companionBusy.value = ''
  }
}

async function testCompanionVoice() {
  companionBusy.value = 'voice'
  errorMessage.value = ''
  try {
    await request('/api/companion/voice/test', { method: 'POST', body: '{}' })
    await loadCompanionStatus({ quiet: true, preserveSettings: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    companionBusy.value = ''
  }
}

async function testCompanionVoiceProfile() {
  const saved = await saveCompanionSettings('voice')
  if (!saved) return
  await testCompanionVoice()
}

async function uploadVoiceReference(event, profileId) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (file.size > 40 * 1024 * 1024) {
    showSettingsFeedback('pet', 'error', '参考音频不能超过 40 MB')
    return
  }
  const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || ''
  const allowedExtensions = new Set(['.wav', '.mp3', '.flac', '.m4a', '.ogg', '.aac', '.wma'])
  if (!file.type.startsWith('audio/') && !allowedExtensions.has(extension)) {
    showSettingsFeedback('pet', 'error', '请选择 WAV、MP3、FLAC 等音频文件')
    return
  }
  const saved = await saveCompanionSettings('voice')
  if (!saved) return
  companionBusy.value = 'voice-reference'
  errorMessage.value = ''
  try {
    companionStatus.value = await request('/api/companion/voice/reference', {
      method: 'POST',
      body: JSON.stringify({
        name: file.name,
        data_url: await readFileAsDataUrl(file),
        profile_id: profileId,
      }),
    })
    savedCompanionSettings.value.voice = companionSettingsSnapshot('voice')
    savedCompanionSettings.value.pet = companionSettingsSnapshot('pet')
    showSettingsFeedback('pet', 'success', '参考音频已保存到当前音色')
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('pet', 'error', `参考音频上传失败：${error.message}`)
  } finally {
    companionBusy.value = ''
  }
}

async function exportVoicePackage(profileId) {
  if (!profileId) return
  companionBusy.value = 'voice-export'
  errorMessage.value = ''
  try {
    const response = await fetch('/api/companion/voice/profiles/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId }),
    })
    if (!response.ok) {
      let detail = '导出失败'
      try {
        detail = (await response.json()).detail || detail
      } catch {
        // Keep the fallback message.
      }
      throw new Error(detail)
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `音色包-${profileId}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('pet', 'error', `音色包导出失败：${error.message}`)
  } finally {
    companionBusy.value = ''
  }
}

async function importVoicePackage(event) {
  const nativeImporter = window.pywebview?.api?.import_voice_package
  const file = event?.target?.files?.[0]
  if (event?.target) event.target.value = ''
  if (!nativeImporter && !file) return
  const saved = await saveCompanionSettings('voice')
  if (!saved) return
  companionBusy.value = 'voice-import'
  errorMessage.value = ''
  try {
    if (nativeImporter) {
      let result = await nativeImporter()
      if (result?.canceled) return
      if (result?.ok === false) throw new Error(result.error || '导入失败')
      if (result?.started) {
        const statusReader = window.pywebview?.api?.voice_package_import_status
        if (!statusReader) throw new Error('桌面导入状态接口不可用，请重启应用后重试')
        showSettingsFeedback('pet', 'success', `正在后台导入「${result.filename || '音色包'}」，主应用可以继续保持打开`)
        while (true) {
          await new Promise((resolve) => window.setTimeout(resolve, 500))
          const status = await statusReader(result.job_id)
          if (status?.state === 'completed') {
            result = status
            break
          }
          const percent = Number.isFinite(Number(status?.percent)) ? ` ${Math.max(0, Math.min(99, Number(status.percent)))}%` : ''
          showSettingsFeedback('pet', 'success', `${status?.message || '正在后台导入音色包'}${percent}；主应用不会因此关闭`)
        }
        if (result?.ok === false) throw new Error(result.error || '独立导入任务失败')
      }
      await loadCompanionStatus({ quiet: false })
      const imported = result?.imported || {}
      const engineLabel = imported.engine === 'so_vits_svc' ? 'So-VITS-SVC 第三方音色' : 'Mio 音色包'
      const readyMessage = imported.runtime === 'missing'
        ? `${engineLabel}「${imported.name || '未命名'}」已安全保存；本机缺少第三方音色运行环境，暂未设为默认`
        : `${engineLabel}「${imported.name || '未命名'}」已通过模型加载验证、设为默认，可直接试听`
      showSettingsFeedback('pet', imported.runtime === 'missing' ? 'error' : 'success', readyMessage)
    } else {
      const response = await fetch('/api/companion/voice/profiles/import-package', {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      })
      if (!response.ok) {
        let detail = '导入失败'
        try {
          detail = (await response.json()).detail || detail
        } catch {
          // Keep the fallback message.
        }
        throw new Error(detail)
      }
      companionStatus.value = await response.json()
    }
    savedCompanionSettings.value.voice = companionSettingsSnapshot('voice')
    savedCompanionSettings.value.pet = companionSettingsSnapshot('pet')
    if (!nativeImporter) showSettingsFeedback('pet', 'success', '音色包已导入并设为默认，可直接试听')
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('pet', 'error', `音色包导入失败：${error.message}`)
  } finally {
    companionBusy.value = ''
  }
}

async function controlVoiceRuntime(action) {
  if (companionBusy.value) return
  companionBusy.value = `voice-runtime-${action}`
  errorMessage.value = ''
  try {
    companionStatus.value.voice_runtime = await request(`/api/companion/voice/runtime/${action}`, {
      method: 'POST',
      body: '{}',
    })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    companionBusy.value = ''
  }
}

async function controlLocalVision(action) {
  if (companionBusy.value) return
  companionBusy.value = `local-vision-${action}`
  errorMessage.value = ''
  try {
    const localVision = await request(`/api/companion/local-vision/runtime/${action}`, {
      method: 'POST',
      body: '{}',
    })
    companionStatus.value.screen_analysis = {
      ...(companionStatus.value.screen_analysis || {}),
      local_vision: localVision,
    }
    await loadCompanionStatus({ quiet: true, preserveSettings: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    companionBusy.value = ''
  }
}

async function pullLocalVisionModel() {
  if (companionBusy.value) return
  companionBusy.value = 'local-vision-pull'
  errorMessage.value = ''
  try {
    const localVision = await request('/api/companion/local-vision/model/pull', {
      method: 'POST',
      body: '{}',
    })
    companionStatus.value.screen_analysis = {
      ...(companionStatus.value.screen_analysis || {}),
      local_vision: localVision,
    }
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    companionBusy.value = ''
  }
}

async function controlVoiceTraining(action) {
  if (companionBusy.value) return
  companionBusy.value = `voice-training-${action}`
  errorMessage.value = ''
  try {
    companionStatus.value.voice_training = await request(`/api/companion/voice/training/${action}`, {
      method: 'POST',
      body: '{}',
    })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    companionBusy.value = ''
  }
}

async function importLive2DModel() {
  if (companionBusy.value) return
  companionBusy.value = 'live2d-import'
  errorMessage.value = ''
  try {
    let result
    if (window.pywebview?.api?.import_live2d_model) {
      result = await window.pywebview.api.import_live2d_model()
      if (result?.canceled) return
      if (result?.ok === false) throw new Error(result.error || 'Live2D 模型导入失败')
    } else {
      const sourcePath = await showAppPrompt({
        title: '导入 Live2D',
        message: '请输入包含 .model3.json 的模型目录完整路径',
        confirmText: '导入',
      })
      if (!sourcePath) return
      result = await request('/api/companion/live2d/models/import', {
        method: 'POST',
        body: JSON.stringify({ source_path: sourcePath }),
      })
    }
    await loadCompanionStatus({ quiet: false })
    showSettingsFeedback('pet', 'success', `已导入 ${result?.model?.name || 'Live2D 模型'}`)
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('pet', 'error', `Live2D 模型导入失败：${error.message}`)
  } finally {
    companionBusy.value = ''
  }
}

async function deleteLive2DModel(model) {
  if (companionBusy.value || !model?.id) return
  const confirmed = await showAppConfirm({
    title: `删除“${model.name || model.id}”？`,
    message: '模型文件会从本机形象目录移除。',
    confirmText: '删除形象',
    danger: true,
  })
  if (!confirmed) return
  companionBusy.value = 'live2d-delete'
  errorMessage.value = ''
  try {
    await request(`/api/companion/live2d/models/${encodeURIComponent(model.id)}`, { method: 'DELETE' })
    await loadCompanionStatus({ quiet: false })
    showSettingsFeedback('pet', 'success', '自定义 Live2D 模型已删除')
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('pet', 'error', `Live2D 模型删除失败：${error.message}`)
  } finally {
    companionBusy.value = ''
  }
}

async function replaceLive2DPreview(model, event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !model?.id) return
  if (!isImageAttachment(file)) {
    errorMessage.value = '请选择图片文件。'
    return
  }
  companionBusy.value = 'live2d-preview'
  errorMessage.value = ''
  try {
    const dataUrl = await readFileAsDataUrl(file)
    await request(`/api/companion/live2d/models/${encodeURIComponent(model.id)}/preview`, {
      method: 'POST',
      body: JSON.stringify({ data_url: dataUrl }),
    })
    companionAvatarNonce.value = Date.now()
    await loadCompanionStatus({ quiet: false })
    showSettingsFeedback('pet', 'success', `已更新 ${model.name || 'Live2D 模型'} 的封面`)
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('pet', 'error', `封面更新失败：${error.message}`)
  } finally {
    companionBusy.value = ''
  }
}

async function uploadCompanionSpriteSheet(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!isImageAttachment(file)) {
    errorMessage.value = '请选择图片文件。'
    return
  }
  companionBusy.value = 'spritesheet'
  errorMessage.value = ''
  try {
    const dataUrl = await readFileAsDataUrl(file)
    companionStatus.value = await request('/api/companion/spritesheet', {
      method: 'POST',
      body: JSON.stringify({ data_url: dataUrl }),
    })
    companionAvatarNonce.value = Date.now()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    companionBusy.value = ''
  }
}

async function loadGameWindows({ quiet = false } = {}) {
  if (gameWindowsLoading.value) return
  gameWindowsLoading.value = true
  try {
    const windows = await observationApi.listWindows()
    gameWindows.value = Array.isArray(windows) ? windows : []
    const currentHwnd = String(companionStatus.value.screen?.hwnd || selectedGameHwnd.value || '')
    if (currentHwnd && gameWindows.value.some((item) => String(item.hwnd) === currentHwnd)) {
      selectedGameHwnd.value = currentHwnd
    } else if (gameWindows.value.length === 1) {
      selectedGameHwnd.value = String(gameWindows.value[0].hwnd)
    }
  } catch (error) {
    if (!quiet) errorMessage.value = error.message
  } finally {
    gameWindowsLoading.value = false
  }
}

async function controlObservation(action) {
  if (companionBusy.value) return
  companionBusy.value = `observation-${action}`
  errorMessage.value = ''
  try {
    if (action === 'stop') {
      companionStatus.value.screen = await request('/api/companion/screen/stop', { method: 'POST', body: '{}' })
      companionStatus.value.pet.settings.screen_audio_enabled = false
      return
    }

    if (observationMode.value === 'game') {
      if (!selectedGameHwnd.value) throw new Error('请选择要观察的游戏窗口。')
      companionStatus.value.screen = await request('/api/companion/game/select', {
        method: 'POST',
        body: JSON.stringify({ hwnd: Number(selectedGameHwnd.value) }),
      })
      companionStatus.value.screen = await request('/api/companion/game/start', {
        method: 'POST',
        body: JSON.stringify({ interval_ms: observationInterval.value }),
      })
    } else {
      companionStatus.value.screen = await request('/api/companion/screen/start', {
        method: 'POST',
        body: JSON.stringify({ interval_ms: observationInterval.value, scope: screenScope.value }),
      })
    }
    companionStatus.value.pet.settings.screen_audio_enabled = true
    await loadCompanionStatus({ quiet: true, preserveSettings: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    companionBusy.value = ''
  }
}

async function letMioSeeObservation() {
  if (companionBusy.value) return
  companionBusy.value = 'observation-see'
  errorMessage.value = ''
  try {
    let result
    if (observationMode.value === 'game') {
      if (!selectedGameHwnd.value) throw new Error('请选择要观察的游戏窗口。')
      if (
        companionStatus.value.screen?.mode !== 'window'
        || String(companionStatus.value.screen?.hwnd || '') !== String(selectedGameHwnd.value)
      ) {
        companionStatus.value.screen = await request('/api/companion/game/select', {
          method: 'POST',
          body: JSON.stringify({ hwnd: Number(selectedGameHwnd.value) }),
        })
      }
      result = await request('/api/companion/game/analyze', { method: 'POST', body: '{}' })
    } else {
      result = await request('/api/companion/screen/analyze', {
        method: 'POST',
        body: JSON.stringify({ interval_ms: observationInterval.value, scope: screenScope.value }),
      })
    }
    companionStatus.value.screen_analysis = result.analysis
    await loadCompanionStatus({ quiet: true, preserveSettings: true })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    companionBusy.value = ''
  }
}

function focusComposer() {
  activeView.value = 'chat'
  nextTick(() => document.querySelector('.composer-input')?.focus())
}

function openMemoryTab(tab) {
  activeView.value = 'memory'
  memoryTab.value = tab
}

function showSettingsFeedback(section, type, message) {
  settingsFeedback.value = { section, type, message }
  window.setTimeout(() => {
    if (settingsFeedback.value.section === section && settingsFeedback.value.message === message) {
      settingsFeedback.value = { section: '', type: '', message: '' }
    }
  }, 3200)
}

function handleSettingsModelChange() {
  const options = settingsReasoningOptions.value
  const optionIds = new Set(options.map((item) => item.id))
  if (!optionIds.has(chatSettingsDraft.value.reasoning_level)) {
    const profile = modelOptions.value.find((item) => item.id === chatSettingsDraft.value.model_id)
    chatSettingsDraft.value.reasoning_level = chatSettingsDraft.value.model_id === 'auto'
      ? 'auto'
      : (profile?.default_reasoning_level || options[0]?.id || 'default')
  }
}

function handlePetModelChange() {
  const settings = companionStatus.value.pet?.settings
  if (!settings) return
  const options = petReasoningOptions.value
  const optionIds = new Set(options.map((item) => item.id))
  if (!optionIds.has(settings.pet_chat_reasoning_level)) {
    const profile = modelOptions.value.find((item) => item.id === settings.pet_chat_model_id)
    settings.pet_chat_reasoning_level = settings.pet_chat_model_id === 'auto'
      ? 'auto'
      : (profile?.default_reasoning_level || options[0]?.id || 'default')
  }
}

function applyDisplayMode(mode) {
  const preset = DISPLAY_MODE_PRESETS[mode]
  if (!preset) return
  appPreferencesDraft.value = cloneAppPreferences({
    ...appPreferencesDraft.value,
    display_mode: mode,
    visibility: { ...appPreferencesDraft.value.visibility, ...preset },
  })
}

function saveAppearanceSettings(section = 'appearance') {
  const current = cloneAppPreferences(savedAppPreferences.value)
  const draft = cloneAppPreferences(appPreferencesDraft.value)
  const saved = section === 'general'
    ? cloneAppPreferences({ ...current, default_open_page: draft.default_open_page })
    : cloneAppPreferences({
        ...current,
        theme: draft.theme,
        font_size: draft.font_size,
        left_sidebar_visible: draft.left_sidebar_visible,
        right_sidebar_visible: draft.right_sidebar_visible,
        left_sidebar_hover_expand: draft.left_sidebar_hover_expand,
        right_sidebar_hover_expand: draft.right_sidebar_hover_expand,
        remember_sidebar_state: draft.remember_sidebar_state,
        light_animations: draft.light_animations,
        focus_mode: draft.focus_mode,
        visibility: draft.visibility,
      })
  savedAppPreferences.value = saved
  appPreferencesDraft.value = cloneAppPreferences(saved)
  localStorage.setItem('mio_app_preferences', JSON.stringify(saved))
  leftSidebarVisible.value = saved.focus_mode ? false : saved.left_sidebar_visible !== false
  rightSidebarVisible.value = saved.focus_mode ? false : saved.right_sidebar_visible !== false
  localStorage.setItem('mio_left_sidebar_visible', String(leftSidebarVisible.value))
  localStorage.setItem('mio_right_sidebar_visible', String(rightSidebarVisible.value))
  showSettingsFeedback(section, 'success', section === 'general' ? '基础设置已保存' : '外观与界面设置已保存并生效')
}

async function loadRuntimeSettings({ quiet = false } = {}) {
  if (runtimeSettingsBusy.value) return
  runtimeSettingsBusy.value = true
  try {
    const result = await settingsApi.loadRuntimeSettings()
    runtimeSettingsDraft.value = { ...(result.settings || {}) }
    savedRuntimeSettings.value = serializeSettings(runtimeSettingsDraft.value)
    runtimeSettingsReady.value = true
  } catch (error) {
    if (!quiet) errorMessage.value = error.message
  } finally {
    runtimeSettingsBusy.value = false
  }
}

async function saveRuntimeSettings() {
  if (runtimeSettingsBusy.value || !runtimeSettingsReady.value) return false
  runtimeSettingsBusy.value = true
  errorMessage.value = ''
  try {
    const payload = { ...runtimeSettingsDraft.value }
    for (const key of privateRuntimePathKeys) delete payload[key]
    const result = await request('/api/settings/runtime', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    runtimeSettingsDraft.value = { ...(result.settings || {}) }
    savedRuntimeSettings.value = serializeSettings(runtimeSettingsDraft.value)
    await loadBootstrap({ quiet: true })
    showSettingsFeedback(activeSettingsSection.value, 'success', `${activeSettingsItem.value.label}设置已保存并生效`)
    return true
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback(activeSettingsSection.value, 'error', `${activeSettingsItem.value.label}设置保存失败`)
    return false
  } finally {
    runtimeSettingsBusy.value = false
  }
}

async function loadMioProfileSettings({ quiet = false } = {}) {
  if (mioProfileBusy.value) return
  mioProfileBusy.value = true
  try {
    const result = await settingsApi.loadProfileSettings()
    mioProfileDraft.value = result.profile || null
    mioProfileNotesDraft.value = (result.profile?.preferences?.custom_notes || []).join('\n')
    mioProfileAvoidDraft.value = (result.profile?.speaking_style?.avoid || []).join('\n')
    savedMioProfile.value = serializeSettings({ profile: mioProfileDraft.value, notes: mioProfileNotesDraft.value, avoid: mioProfileAvoidDraft.value })
    mioProfileReady.value = Boolean(mioProfileDraft.value)
    profileAvatarCustom.value = Boolean(result.avatar?.custom)
    userAvatarCustom.value = Boolean(result.user_avatar?.custom)
    chatBackgroundCustom.value = Boolean(result.chat_background?.custom)
    profileAvatarNonce.value = Date.now()
    userAvatarNonce.value = Date.now()
    chatBackgroundNonce.value = Date.now()
  } catch (error) {
    if (!quiet) errorMessage.value = error.message
  } finally {
    mioProfileBusy.value = false
  }
}

async function saveMioProfileSettings() {
  if (mioProfileBusy.value || !mioProfileReady.value || !mioProfileDraft.value) return false
  mioProfileBusy.value = true
  errorMessage.value = ''
  try {
    const profile = JSON.parse(JSON.stringify(mioProfileDraft.value))
    profile.identity ||= {}
    profile.identity.name = String(profile.identity.name || '').trim()
    if (!profile.identity.name) throw new Error('显示名字不能为空')
    profile.preferences ||= {}
    profile.preferences.custom_notes = mioProfileNotesDraft.value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean)
    profile.speaking_style ||= {}
    profile.speaking_style.avoid = mioProfileAvoidDraft.value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean)
    const result = await request('/api/settings/profile', {
      method: 'PATCH',
      body: JSON.stringify({ profile }),
    })
    mioProfileDraft.value = result.profile
    mioProfileNotesDraft.value = (result.profile?.preferences?.custom_notes || []).join('\n')
    mioProfileAvoidDraft.value = (result.profile?.speaking_style?.avoid || []).join('\n')
    savedMioProfile.value = serializeSettings({ profile: mioProfileDraft.value, notes: mioProfileNotesDraft.value, avoid: mioProfileAvoidDraft.value })
    memoryData.value.profile = result.profile
    showSettingsFeedback('profile', 'success', '人格与属性已保存，下一轮对话开始生效')
    return true
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('profile', 'error', '人格与属性保存失败')
    return false
  } finally {
    mioProfileBusy.value = false
  }
}

async function uploadProfileAvatar(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!isImageAttachment(file)) {
    showSettingsFeedback('profile', 'error', '请选择图片文件')
    return
  }
  if (file.size > 12 * 1024 * 1024) {
    showSettingsFeedback('profile', 'error', '头像图片不能超过 12MB')
    return
  }
  mioProfileBusy.value = true
  errorMessage.value = ''
  try {
    const dataUrl = await readFileAsDataUrl(file)
    await request('/api/settings/avatar', {
      method: 'POST',
      body: JSON.stringify({ data_url: dataUrl }),
    })
    profileAvatarCustom.value = true
    profileAvatarNonce.value = Date.now()
    showSettingsFeedback('profile', 'success', '头像已更新')
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('profile', 'error', `头像更新失败：${error.message}`)
  } finally {
    mioProfileBusy.value = false
  }
}

async function resetProfileAvatar() {
  if (mioProfileBusy.value || !profileAvatarCustom.value) return
  mioProfileBusy.value = true
  errorMessage.value = ''
  try {
    await request('/api/settings/avatar', { method: 'DELETE' })
    profileAvatarCustom.value = false
    profileAvatarNonce.value = Date.now()
    showSettingsFeedback('profile', 'success', '已恢复默认头像')
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('profile', 'error', `恢复默认头像失败：${error.message}`)
  } finally {
    mioProfileBusy.value = false
  }
}

async function uploadAppearanceImage(event, kind) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!isImageAttachment(file)) {
    showSettingsFeedback('appearance', 'error', '请选择图片文件')
    return
  }
  if (file.size > 12 * 1024 * 1024) {
    showSettingsFeedback('appearance', 'error', '图片不能超过 12MB')
    return
  }
  const isBackground = kind === 'background'
  const endpoint = isBackground ? '/api/settings/chat-background' : '/api/settings/user-avatar'
  mioProfileBusy.value = true
  errorMessage.value = ''
  try {
    const dataUrl = await readFileAsDataUrl(file)
    await request(endpoint, { method: 'POST', body: JSON.stringify({ data_url: dataUrl }) })
    if (isBackground) {
      chatBackgroundCustom.value = true
      chatBackgroundNonce.value = Date.now()
    } else {
      userAvatarCustom.value = true
      userAvatarNonce.value = Date.now()
    }
    showSettingsFeedback('appearance', 'success', isBackground ? '对话背景已更新' : '用户头像已更新')
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('appearance', 'error', `${isBackground ? '对话背景' : '用户头像'}更新失败：${error.message}`)
  } finally {
    mioProfileBusy.value = false
  }
}

async function resetAppearanceImage(kind) {
  const isBackground = kind === 'background'
  const isCustom = isBackground ? chatBackgroundCustom.value : userAvatarCustom.value
  if (mioProfileBusy.value || !isCustom) return
  const endpoint = isBackground ? '/api/settings/chat-background' : '/api/settings/user-avatar'
  mioProfileBusy.value = true
  errorMessage.value = ''
  try {
    await request(endpoint, { method: 'DELETE' })
    if (isBackground) {
      chatBackgroundCustom.value = false
      chatBackgroundNonce.value = Date.now()
    } else {
      userAvatarCustom.value = false
      userAvatarNonce.value = Date.now()
    }
    showSettingsFeedback('appearance', 'success', isBackground ? '已恢复默认对话背景' : '已恢复默认用户头像')
  } catch (error) {
    errorMessage.value = error.message
    showSettingsFeedback('appearance', 'error', `恢复默认失败：${error.message}`)
  } finally {
    mioProfileBusy.value = false
  }
}

async function saveConversationSettings() {
  selectedModel.value = chatSettingsDraft.value.model_id || 'auto'
  reasoningLevel.value = chatSettingsDraft.value.reasoning_level || 'auto'
  ensureReasoningForActiveModel()
  persistSelectedModel()
  persistReasoning()
  const saved = await syncSharedChatSettings(chatSettingsDraft.value.voice_language)
  if (!saved) {
    showSettingsFeedback('conversation', 'error', '对话与语音设置保存失败')
    return false
  }
  chatSettingsDraft.value = {
    model_id: selectedModel.value,
    reasoning_level: reasoningLevel.value,
    voice_language: saved?.voice_language || chatSettingsDraft.value.voice_language || 'auto',
  }
  savedChatSettings.value = serializeSettings(chatSettingsDraft.value)
  showSettingsFeedback('conversation', 'success', '对话与模型设置已保存')
  return true
}

async function saveActiveSettings() {
  const section = activeSettingsSection.value
  if (section === 'general') {
    const desktopSaved = await saveDesktopPreferences()
    if (!desktopSaved) return false
    const greetingSaved = await saveStartupGreetingSetting()
    if (!greetingSaved) {
      showSettingsFeedback('general', 'error', '桌面设置已保存，但启动打招呼保存失败')
      return false
    }
    saveAppearanceSettings('general')
    const runtimeSaved = await saveRuntimeSettings()
    if (!runtimeSaved) {
      showSettingsFeedback('general', 'error', '桌面和启动设置已保存，但主动联系设置保存失败')
      return false
    }
    showSettingsFeedback('general', 'success', '基础与启动设置已全部保存')
    return true
  }
  if (section === 'profile') return saveMioProfileSettings()
  if (section === 'appearance') return saveAppearanceSettings()
  if (section === 'conversation') {
    const conversationSaved = await saveConversationSettings()
    if (!conversationSaved) return false
    return saveRuntimeSettings()
  }
  if (section === 'diary') return saveRuntimeSettings()
  if (section === 'qq') {
    const startupSaved = await saveQqStartupSetting()
    if (!startupSaved) return false
    const groupSaved = await saveGroupChatSettings()
    if (!groupSaved) {
      showSettingsFeedback('qq', 'error', 'QQ 启动设置已保存，但群聊设置保存失败')
      return false
    }
    const runtimeSaved = await saveRuntimeSettings()
    if (!runtimeSaved) {
      showSettingsFeedback('qq', 'error', 'QQ 启动和群聊设置已保存，但通道参数保存失败')
      return false
    }
    showSettingsFeedback('qq', 'success', 'QQ 设置已全部保存')
    return true
  }
  if (section === 'advanced') return saveRuntimeSettings()
  if (section === 'pet') return saveCompanionSettings(section)
  return false
}

async function resetActiveSettings() {
  const section = activeSettingsSection.value
  settingsFeedback.value = { section: '', type: '', message: '' }
  if (section === 'general' || section === 'appearance') {
    appPreferencesDraft.value = cloneAppPreferences(savedAppPreferences.value)
  }
  if (section === 'general') {
    startupGreetingEnabled.value = savedStartupGreeting.value
    if (desktopPreferencesReady.value) {
      try { desktopPreferencesDraft.value = JSON.parse(savedDesktopPreferences.value) } catch (_) {}
    }
  }
  if (section === 'profile') {
    await loadMioProfileSettings({ quiet: true })
    return
  }
  if (section === 'conversation') {
    try { chatSettingsDraft.value = JSON.parse(savedChatSettings.value || '{}') } catch (_) {}
  }
  if (['general', 'conversation', 'diary', 'qq', 'advanced'].includes(section)) {
    runtimeSettingsDraft.value = savedRuntimeSettingsSource()
  }
  if (section === 'qq') {
    qqStartupEnabled.value = savedQqStartupEnabled.value
    try {
      const saved = JSON.parse(savedGroupChatSettings.value || '{}')
      groupChatSettings.value.enabled = Boolean(saved.enabled)
      groupChatSettings.value.mention_required = Boolean(saved.mention_required)
      groupIdsDraft.value = (saved.group_ids || []).join(', ')
    } catch (_) {}
  }
  if (section === 'pet') await loadCompanionStatus({ quiet: true, preserveSettings: false })
}

async function openSettingsSection(sectionId) {
  const enteringSettings = activeView.value !== 'settings'
  if (!enteringSettings && sectionId === activeSettingsSection.value) return
  if (!enteringSettings && isSettingsSectionDirty(activeSettingsSection.value)) {
    const discard = await showAppConfirm({ title: '放弃未保存的修改？', message: '当前分类的修改尚未保存，切换后会丢失。', confirmText: '放弃并切换', danger: true })
    if (!discard) return
    await resetActiveSettings()
  }
  if (enteringSettings) {
    settingsReturnView.value = activeView.value
    activeView.value = 'settings'
  }
  activeSettingsSection.value = sectionId
  localStorage.setItem('mio_settings_section', sectionId)
  if (sectionId === 'profile' && !mioProfileReady.value) loadMioProfileSettings()
  if (sectionId === 'data') refreshDataPrivacy()
  if (!runtimeSettingsReady.value) loadRuntimeSettings()
}

async function navigatePrimaryView(view) {
  if (view === 'settings') {
    await openSettingsSection('general')
    return
  }
  activeView.value = view
  if (view === 'diaries') {
    const date = selectedDiary.value?.date || diaries.value[0]?.date
    if (date) await openDiary(date)
  }
}

async function returnToApp() {
  if (isSettingsSectionDirty(activeSettingsSection.value)) {
    const discard = await showAppConfirm({ title: '放弃未保存的修改？', message: '当前分类的修改尚未保存，返回后会丢失。', confirmText: '放弃并返回', danger: true })
    if (!discard) return
    await resetActiveSettings()
  }
  const returnView = validInitialViews.has(settingsReturnView.value) ? settingsReturnView.value : 'home'
  activeView.value = returnView
}

function openCompanionSection(sectionId) {
  activeView.value = 'companion'
  activeCompanionSection.value = sectionId
}

async function focusDesktopPetChat() {
  activeView.value = 'chat'
  leftSidebarVisible.value = true
  localStorage.setItem('mio_left_sidebar_visible', 'true')
  await refreshConversations()
  await selectConversation('desktop_pet')
  if (window.location.hash === '#desktop-pet-chat') {
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
  }
}

function toggleLeftSidebar() {
  leftSidebarVisible.value = !leftSidebarVisible.value
  localStorage.setItem('mio_left_sidebar_visible', String(leftSidebarVisible.value))
  savedAppPreferences.value = { ...savedAppPreferences.value, left_sidebar_visible: leftSidebarVisible.value }
  appPreferencesDraft.value = { ...appPreferencesDraft.value, left_sidebar_visible: leftSidebarVisible.value }
  localStorage.setItem('mio_app_preferences', JSON.stringify(savedAppPreferences.value))
}

function toggleRightSidebar() {
  rightSidebarVisible.value = !rightSidebarVisible.value
  localStorage.setItem('mio_right_sidebar_visible', String(rightSidebarVisible.value))
  savedAppPreferences.value = { ...savedAppPreferences.value, right_sidebar_visible: rightSidebarVisible.value }
  appPreferencesDraft.value = { ...appPreferencesDraft.value, right_sidebar_visible: rightSidebarVisible.value }
  localStorage.setItem('mio_app_preferences', JSON.stringify(savedAppPreferences.value))
}

function toggleLeftSidebarPinned() {
  leftSidebarPinned.value = !leftSidebarPinned.value
  localStorage.setItem('mio_left_sidebar_pinned', String(leftSidebarPinned.value))
}

function toggleRightSidebarPinned() {
  rightSidebarPinned.value = !rightSidebarPinned.value
  localStorage.setItem('mio_right_sidebar_pinned', String(rightSidebarPinned.value))
}

async function controlDesktopWindow(action) {
  const control = window.pywebview?.api?.window_control
  if (!control) return
  try {
    await control(action)
  } catch (error) {
    errorMessage.value = `窗口操作失败：${error.message}`
  }
}

function resizeDesktopWindow(direction, event) {
  if (event?.button !== undefined && event.button !== 0) return
  event?.preventDefault?.()
  window.pywebview?.api?.window_resize?.(direction)
}

provide('mio-settings-page', reactive({
  activeSettingsItem,
  activeSettingsSection,
  filteredSettingsNavigation,
  avatarUrl,
  chatBackgroundCustom,
  chatBackgroundUrl,
  appPreferencesDraft,
  themeOptions: THEME_OPTIONS,
  applyDisplayMode,
  chatSettingsDraft,
  clearGroupChatContext,
  companionAvatarUrl,
  companionBusy,
  companionStatus,
  companionStatusReady,
  contextUsageLabel,
  controlCompanion,
  controlQq,
  controlVoiceRuntime,
  desktopPreferencesBusy,
  desktopPreferencesDraft,
  desktopPreferencesReady,
  exportVoicePackage,
  dataPrivacyBusy,
  dataPrivacyLoading,
  dataPrivacyState,
  createDataBackup,
  importDataBackup,
  deleteProvider,
  deleteProviderGroup,
  displayModeOptions,
  groupChatSettings,
  groupIdsDraft,
  handleSettingsModelChange,
  handlePetModelChange,
  homeWidgetOptions,
  importLive2DModel,
  importVoicePackage,
  isSettingsSectionDirty,
  deleteLive2DModel,
  replaceLive2DPreview,
  modelGroups,
  hiddenProviderNames,
  modelOptions,
  petReasoningOptions,
  modelTestBusy,
  modelTestStatus,
  openProviderPanel,
  openSettingsSection,
  pricingSourceLabel,
  providerBusy,
  restoreProvider,
  runtimeIdentity,
  qqBusy,
  qqAccountDraft,
  qqConnected,
  qqDiagnosticItems,
  qqQrError,
  qqQrImageUrl,
  qqQrLoading,
  qqSetupResult,
  qqStartupEnabled,
  qqStatus,
  qqStatusCopy,
  qqStatusLabel,
  qqTestTargetDraft,
  runtimeSettingsBusy,
  runtimeSettingsDraft,
  runtimeSettingsReady,
  webSearchTestBusy,
  webSearchTestQuery,
  webSearchTestResult,
  testWebSearch,
  rangeInputStyle,
  resetActiveSettings,
  mioProfileBusy,
  mioProfileDraft,
  mioProfileAvoidDraft,
  mioProfileNotesDraft,
  mioProfileReady,
  mioDisplayName,
  profileAvatarCustom,
  profileBehaviorLabels,
  resetProfileAvatar,
  resetAppearanceImage,
  restoreDataBackup,
  saveActiveSettings,
  saveCompanionSettings,
  saveCompanionSize,
  selectedModel,
  settingsFeedback,
  settingsSearch,
  settingsReasoningOptions,
  startupGreetingEnabled,
  testCompanionVoice,
  testCompanionVoiceProfile,
  testModel,
  setupQqChannel,
  testQqDelivery,
  togglePrivacyPause,
  uploadCompanionSpriteSheet,
  uploadVoiceReference,
  uploadProfileAvatar,
  uploadAppearanceImage,
  userAvatarCustom,
  userAvatarUrl,
  visibilityOptions,
}))

provide('mio-companion-page', reactive({
  activeCompanionSection,
  companionAvatarUrl,
  companionBusy,
  companionStatus,
  controlCompanion,
  controlLocalVision,
  controlObservation,
  controlVoiceRuntime,
  controlVoiceTraining,
  formatRealTime,
  gameWindows,
  gameWindowsLoading,
  letMioSeeObservation,
  mioDisplayName,
  loadGameWindows,
  observationInterval,
  observationMode,
  openScreenPreviewWindow,
  openSettingsSection,
  rangeInputStyle,
  previewLive2DMotion,
  previewLive2DExpression,
  pullLocalVisionModel,
  saveCompanionSettings,
  saveCompanionSize,
  screenScope,
  selectedGameHwnd,
  testCompanionVoice,
  uploadCompanionSpriteSheet,
}))

provide('mio-chat-page', reactive({
  activeModelSupportsVision,
  activeReasoningOptions,
  analyzeTodayState,
  activeView,
  attachments,
  autoDiaryStatus,
  autoDiaryStatusLabel,
  avatarUrl,
  cancelActiveChat,
  chatBackgroundStyle,
  chooseModel,
  chooseReasoning,
  compactActiveModelLabel,
  messageModelLabel,
  compactCurrentReasoningLabel,
  conversations,
  createNewConversation,
  contextPercent,
  contextRingStyle,
  contextUsage,
  contextUsageLabel,
  controlQq,
  copiedTurnId,
  copyTurn,
  costSourceLabel,
  diaryBusy,
  diaries,
  diarySearch,
  displayMode,
  displayVisibility,
  draft,
  fileInput,
  formatCost,
  formatFileSize,
  formatRealTime,
  formatShortTime,
  generateTodayDiary,
  handleComposerKeydown,
  handleComposerPaste,
  handleFileDragEnter,
  handleFileDragLeave,
  handleFileDragOver,
  handleFileDrop,
  handleFileSelection,
  isAutoRouting,
  isFileDragging,
  loading,
  logicalDate,
  agentRuntimeLabel,
  messageTurns,
  mioDisplayName,
  modelGroups,
  openSettingsSection,
  modelMenuSection,
  modelPicker,
  moodScore,
  moodTrend,
  openDiary,
  playMessageVoice,
  qqBusy,
  qqConnected,
  qqStatusCopy,
  qqStatusLabel,
  reasoningLabel,
  reasoningLevel,
  removeAttachment,
  renameConversation,
  requestCostDetails,
  rightSidebarVisible,
  sendMessage,
  selectConversation,
  selectedConversationId,
  sending,
  showModelMenu,
  sourceLabel,
  speakingPartId,
  statusLabel,
  stateAnalyzeBusy,
  structuredMemoryCount,
  todayState,
  todayStateDetails,
  toggleModelMenu,
  turnAttachments,
  turnId,
  turnToolReceipts,
  toolReceiptLabel,
  toolReceiptStatus,
  toolReceiptTitle,
  turnVoiceLanguageLabel,
  visibleMessages,
  voiceLoadingPartId,
  userAvatarCustom,
  userAvatarUrl,
  chatScroll,
  deleteConversation,
  formatSidebarTime,
}))

provide('mio-records-page', reactive({
  activeView,
  addConversationSummary,
  addMemoryThread,
  addProfileNote,
  addStructuredMemory,
  approveAutonomyBehavior,
  approveAgentTask,
  autonomyBusy,
  autonomyCapabilityOptions,
  autonomyData,
  autonomyDeliveryLabel,
  autonomyLevelOptions,
  autonomyLoaded,
  autonomyLoading,
  autonomyOverrideOptions,
  autonomyStatusLabel,
  archiveStructuredMemory,
  confirmMemoryCandidate,
  cancelAgentTask,
  cancelAutonomyBehavior,
  changeStatsMonth,
  confirmDiary,
  dailyReviewItems,
  dailyReviews,
  deleteConversationSummary,
  deleteMemoryThread,
  deleteProfileNote,
  diaries,
  diaryBusy,
  filteredAgentTasks,
  formatRealTime,
  formatShortTime,
  generateDailyReview,
  generateTodayDiary,
  generateWeekly,
  generateMonthly,
  createAutonomyGoal,
  loadAutonomy,
  loadAgentTasks,
  loadDiaries,
  loadMemoryHub,
  loadStats,
  logicalDate,
  memoryBusy,
  memoryCategoryLabel,
  memoryData,
  mioDisplayName,
  memoryLayerLabel,
  memoryLoaded,
  memoryLoading,
  editStructuredMemory,
  memoryTab,
  newConversationSummary,
  newAutonomyGoalCapability,
  newAutonomyGoalTitle,
  newProfileNote,
  newStructuredMemory,
  newThreadContent,
  newThreadFollowUp,
  openDailyReview,
  openDiary,
  openStatsDiary,
  profileRows,
  renderedMarkdown,
  rejectMemoryCandidate,
  recordFollowUpResult,
  resolveThread,
  reviewBusy,
  runtimeSummaryDraft,
  saveConversationSummary,
  saveMemoryThread,
  saveProfileNote,
  saveRuntimeSummary,
  saveAutonomyPolicy,
  setAutonomyCapabilityOverride,
  selectedDailyDate,
  selectedDailyReview,
  selectedDiary,
  restoreStructuredMemory,
  selectedWeeklyReview,
  selectedWeeklyStart,
  selectedMonthlyReview,
  selectedMonthlyMonth,
  statsCalendarCells,
  statsData,
  statsDistribution,
  statsLoaded,
  statsLoading,
  statsMonthLabel,
  statsMoodPath,
  statsMoodPoints,
  statsMoodSeries,
  statsSummary,
  statusLabel,
  taskBusy,
  taskCenterTab,
  taskPayloadSummary,
  taskStatusFilter,
  taskStatusFilters,
  taskStatusLabel,
  taskSummary,
  tasksLoaded,
  tasksLoading,
  updateAutonomyGoal,
  weeklyReviewItems,
  weeklyReviews,
  monthlyReviewItems,
  monthlyReviews,
  updateDiary,
}))

function markAppReady() {
  // The launcher waits for this concrete DOM signal instead of treating the
  // WebView loaded event alone as proof that the current screen is interactive.
  document.documentElement.dataset.mioReady = 'true'
  window.dispatchEvent(new CustomEvent('mio:app-ready'))
  void request('/api/companion/app-ready', { method: 'POST' }).catch(() => {
    // UI readiness must not be blocked by optional local-voice warm-up.
  })
}

async function syncActiveViewState(
  view = activeView.value,
  section = activeSettingsSection.value,
  visible = document.visibilityState === 'visible',
) {
  if (standalonePetChat) return
  try {
    await selfStateApi.reportActiveView(buildActiveViewReport(view, section, visible))
  } catch {
    // SelfSnapshot reporting is diagnostic and must never block the primary UI.
  }
}

function handleVisibilityChange() {
  void syncActiveViewState()
}

onMounted(async () => {
  if (standalonePetChat) {
    document.documentElement.classList.add('standalone-pet-chat-document')
    document.body.classList.add('standalone-pet-chat-document')
    window.addEventListener('mio:pet-chat-hidden', handlePetChatHidden)
    void setPetChatWindowState(true)
    return
  }
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  document.addEventListener('keydown', handleDocumentKeydown)
  document.addEventListener('visibilitychange', handleVisibilityChange)
  window.addEventListener('storage', handleAppPreferencesStorage)
  window.addEventListener('mio:open-pet-chat', focusDesktopPetChat)
  try {
    const onboarding = await loadOnboardingStatus()
    if (onboarding?.completed === false) {
      bootstrap.value = { onboarding, models: [] }
      loading.value = false
      await refreshOnboardingEnvironment()
      await syncActiveViewState('onboarding')
      markAppReady()
      return
    }
  } catch (error) {
    errorMessage.value = error.message
  }
  await loadBootstrap()
  await syncActiveViewState()
  markAppReady()
  await loadSharedChatSettings()
  void loadMioProfileSettings({ quiet: true })
  await loadStartupGreetingSetting()
  await loadDesktopPreferences({ quiet: true })
  await loadQqStartupSetting()
  await syncSharedChatSettings()
  if (activeView.value === 'home') {
    void loadMemoryHub()
    void loadCompanionStatus({ quiet: true })
  }
  void requestStartupGreeting()
  if (window.location.hash === '#desktop-pet-chat') focusDesktopPetChat()
  pollTimer = window.setInterval(refreshMessages, 5000)
  dashboardPollTimer = window.setInterval(refreshDayDashboard, 15000)
  qqStatusPollTimer = window.setInterval(refreshQqStatus, 10000)
  activeViewHeartbeatTimer = window.setInterval(() => {
    void syncActiveViewState()
  }, ACTIVE_VIEW_HEARTBEAT_MS)
  companionPollTimer = window.setInterval(() => {
    if (activeView.value === 'companion') loadCompanionStatus({ quiet: true, preserveSettings: true })
  }, 3000)
})

watch(activeView, async (view) => {
  void syncActiveViewState(view)
  closeModelMenu()
  if (view !== 'settings') showProviderPanel.value = false
  if (view === 'home') {
    if (!memoryLoaded.value) loadMemoryHub()
    await loadCompanionStatus({ quiet: true })
  }
  if (view === 'memory' && !memoryLoaded.value) loadMemoryHub()
  if (view === 'stats' && !statsLoaded.value) loadStats()
  if (view === 'tasks') loadAgentTasks({ quiet: tasksLoaded.value })
  if (view === 'companion' || view === 'settings') {
    await loadCompanionStatus({ quiet: true })
    if (view === 'settings' && activeSettingsSection.value === 'advanced' && !runtimeSettingsReady.value) {
      await loadRuntimeSettings({ quiet: true })
    }
    if (view === 'settings' && activeSettingsSection.value === 'profile' && !mioProfileReady.value) {
      await loadMioProfileSettings({ quiet: true })
    }
    if (view === 'settings' && activeSettingsSection.value === 'data') {
      await refreshDataPrivacy({ quiet: dataPrivacyState.value.backups.length > 0 })
    }
    if (view === 'companion') await loadGameWindows({ quiet: true })
  }
  if (view === 'chat') {
    await settleChatScrollToBottom()
  }
})

watch(activeSettingsSection, (section) => {
  if (activeView.value === 'settings') void syncActiveViewState('settings', section)
})

onBeforeUnmount(() => {
  document.documentElement.classList.remove('standalone-pet-chat-document')
  document.body.classList.remove('standalone-pet-chat-document')
  if (standalonePetChat) void setPetChatWindowState(false)
  void stopPetCall()
  window.removeEventListener('mio:pet-chat-hidden', handlePetChatHidden)
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  document.removeEventListener('keydown', handleDocumentKeydown)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  window.removeEventListener('storage', handleAppPreferencesStorage)
  window.removeEventListener('mio:open-pet-chat', focusDesktopPetChat)
  if (pollTimer) window.clearInterval(pollTimer)
  if (dashboardPollTimer) window.clearInterval(dashboardPollTimer)
  if (qqStatusPollTimer) window.clearInterval(qqStatusPollTimer)
  if (companionPollTimer) window.clearInterval(companionPollTimer)
  if (activeViewHeartbeatTimer) window.clearInterval(activeViewHeartbeatTimer)
  clearQqQrImage()
  stopMessageVoice()
  void syncActiveViewState(activeView.value, activeSettingsSection.value, false)
})

function handlePetChatHidden() {
  void setPetChatWindowState(false)
  void stopPetCall()
}

async function setPetChatWindowState(open) {
  try {
    await request('/api/companion/chat-window/state', {
      method: 'POST',
      body: JSON.stringify({ open: Boolean(open) }),
    })
  } catch (_) {}
}
</script>

<template>
  <main v-if="standalonePetChat" :class="['standalone-pet-chat', appThemeClass]">
    <div class="pet-chat-composer standalone-pet-chat-composer">
      <span class="standalone-pet-chat-drag" title="拖动对话框" aria-hidden="true" @mousedown="beginPetChatWindowDrag"></span>
      <textarea
        v-model="petChatDraft"
        rows="1"
        :placeholder="errorMessage || (petChatImages.length ? `已粘贴 ${petChatImages.length} 张图片` : `和${mioDisplayName}说话`)"
        @keydown="handlePetChatKeydown"
        @paste="handlePetChatPaste"
      />
      <button
        :class="['pet-call-button', { active: petCallActive }]"
        type="button"
        :title="petCallError || petCallStateLabel"
        @click="togglePetCall"
      >
        <PhoneOff v-if="petCallActive" :size="16" />
        <Phone v-else :size="16" />
      </button>
      <button type="button" title="发送" :disabled="(!petChatDraft.trim() && !petChatImages.length) || petChatSending" @click="sendPetChat">
        <RefreshCw v-if="petChatSending" class="spin" :size="16" />
        <Send v-else :size="16" />
      </button>
    </div>
  </main>

  <OnboardingPage
    v-else-if="bootstrap && bootstrap.onboarding?.completed === false"
    :onboarding="bootstrap.onboarding"
    :environment="onboardingEnvironment"
    :environment-busy="onboardingEnvironmentBusy"
    :busy="onboardingBusy"
    :error="onboardingError"
    @complete="completeOnboarding"
    @refresh-environment="refreshOnboardingEnvironment"
  />

  <div v-else :class="['app-shell', 'integrated-shell', appThemeClass, `font-${savedAppPreferences.font_size || 'medium'}`, `active-view-${activeView}`, `display-mode-${displayMode}`, { 'left-sidebar-hidden': !leftSidebarVisible, 'right-sidebar-hidden': !rightSidebarVisible, 'reduce-motion': savedAppPreferences.light_animations === false, 'focus-mode': savedAppPreferences.focus_mode }]">
    <span v-for="direction in ['top', 'right', 'bottom', 'left', 'top-left', 'top-right', 'bottom-right', 'bottom-left']" :key="direction" :class="['window-resize-handle', direction]" aria-hidden="true" @pointerdown="resizeDesktopWindow(direction, $event)"></span>
    <header class="window-titlebar integrated-titlebar">
      <button v-if="activeView !== 'settings'" class="shell-toggle" type="button" :title="leftSidebarVisible ? '隐藏左侧栏' : '显示左侧栏'" @click="toggleLeftSidebar">
        <component :is="leftSidebarVisible ? PanelLeftClose : PanelLeftOpen" :size="17" />
      </button>
      <div class="window-drag-zone pywebview-drag-region" @dblclick="controlDesktopWindow('maximize')">
        <strong>{{ activeView === 'settings' ? '设置' : navItems.find((item) => item.id === activeView)?.label }}</strong>
      </div>
      <button v-if="activeView !== 'settings'" class="shell-toggle" type="button" :title="rightSidebarVisible ? '隐藏右侧栏' : '显示右侧栏'" @click="toggleRightSidebar">
        <component :is="rightSidebarVisible ? PanelRightClose : PanelRightOpen" :size="17" />
      </button>
      <div class="window-controls" aria-label="窗口控制">
        <button type="button" title="最小化" aria-label="最小化" @click="controlDesktopWindow('minimize')"><Minus :size="15" /></button>
        <button type="button" title="最大化或还原" aria-label="最大化或还原" @click="controlDesktopWindow('maximize')"><Maximize2 :size="13" /></button>
        <button class="close" type="button" title="关闭到后台" aria-label="关闭到后台" @click="controlDesktopWindow('close')"><X :size="15" /></button>
      </div>
    </header>

    <aside
      v-if="leftSidebarVisible"
      :class="['sidebar', 'integrated-sidebar', { expanded: leftSidebarPinned || (savedAppPreferences.left_sidebar_hover_expand && leftSidebarHovered), pinned: leftSidebarPinned }]"
      @mouseenter="leftSidebarHovered = true"
      @mouseleave="leftSidebarHovered = false"
    >
      <div class="brand-block">
        <img class="brand-avatar" :src="avatarUrl" :alt="mioDisplayName" />
        <div><strong>{{ mioDisplayName }}</strong><span>私人空间</span></div>
        <button class="sidebar-pin" type="button" :title="leftSidebarPinned ? '取消固定展开' : '固定展开'" @click="toggleLeftSidebarPinned">
          <component :is="leftSidebarPinned ? PinOff : Pin" :size="14" />
        </button>
      </div>
      <nav class="main-nav" aria-label="主要导航">
        <button v-for="item in navGroups[0].items" :key="item.id" type="button" :class="['nav-item', { active: activeView === item.id }]" :title="item.label" @click="navigatePrimaryView(item.id)">
          <component :is="item.icon" :size="18" /><span>{{ item.label }}</span>
        </button>
      </nav>
      <div class="sidebar-footer pet-launcher">
        <button type="button" :title="companionStatus.pet?.running ? '停止桌宠' : '启动桌宠'" :disabled="Boolean(companionBusy)" @click="controlCompanion(companionStatus.pet?.running ? 'stop' : 'start')">
          <component :is="companionStatus.pet?.running ? Power : Play" :size="17" />
          <span><strong>桌宠</strong><small>{{ companionStatus.pet?.running ? '运行中' : '未启动' }}</small></span>
          <i :class="{ online: companionStatus.pet?.running }" />
        </button>
      </div>
    </aside>

    <main :class="['workspace', 'integrated-workspace', { 'has-error': errorMessage, 'view-settings': activeView === 'settings' }]">
      <div v-if="errorMessage" class="error-banner"><span>{{ errorMessage }}</span><button type="button" title="关闭" @click="errorMessage = ''"><X :size="16" /></button></div>
      <HomePage v-if="activeView === 'home'" :logical-date="logicalDate" :today-state="todayState" :today-state-details="todayStateDetails" :diaries="diaries" :memory-data="memoryData" :display-name="mioDisplayName" :user-address="preferredUserAddress" :companion-running="Boolean(companionStatus.pet?.running)" @navigate="activeView = $event" @open-diary="openDiary" />
      <ChatPage v-else-if="activeView === 'chat'" />
      <RecordsPage v-else-if="['diaries', 'stats', 'memory'].includes(activeView)" />
      <TasksPage v-else-if="activeView === 'tasks'" />
      <section v-else-if="activeView === 'settings'" class="settings-window-shell embedded-settings-shell">
        <header class="settings-window-topbar">
          <button type="button" @click="returnToApp"><ArrowLeft :size="16" />返回应用</button>
          <strong>设置</strong>
        </header>
        <div class="settings-window-layout">
          <aside class="settings-window-navigation">
            <label class="settings-window-search"><Search :size="15" /><input v-model="settingsSearch" type="search" placeholder="搜索设置" /></label>
            <nav aria-label="设置分类">
              <template v-for="group in filteredSettingsNavigation" :key="group.label">
                <span class="settings-window-group-label">{{ group.label }}</span>
                <button v-for="item in group.items" :key="item.id" :class="{ active: activeSettingsSection === item.id }" type="button" :title="item.label" :aria-label="item.label" @click="openSettingsSection(item.id)">
                  <component :is="item.icon" :size="16" /><span>{{ item.label }}</span><i v-if="isSettingsSectionDirty(item.id)" />
                </button>
              </template>
            </nav>
          </aside>
          <main class="settings-window-content"><SettingsPage /></main>
        </div>
      </section>
      <CompanionPage v-else />
    </main>

    <aside
      v-if="rightSidebarVisible"
      :class="['integrated-right-rail', { expanded: rightSidebarPinned || (savedAppPreferences.right_sidebar_hover_expand && rightSidebarHovered), pinned: rightSidebarPinned }]"
      @mouseenter="rightSidebarHovered = true"
      @mouseleave="rightSidebarHovered = false"
    >
      <header class="rail-mood">
        <div><Heart :size="18" /><span><small>现在的心情</small><strong>{{ statusMoodLabel }}</strong></span></div>
        <button type="button" :title="rightSidebarPinned ? '取消固定展开' : '固定展开'" @click="toggleRightSidebarPinned"><component :is="rightSidebarPinned ? PinOff : Pin" :size="14" /></button>
      </header>
      <section class="rail-relationship"><Sparkles :size="17" /><span><small>关系</small><strong>{{ relationshipDistanceLabel }}</strong></span></section>
      <section class="rail-growth">
        <div><Target :size="17" /><span><small>今日成长</small><strong>{{ statusLabel(todayState.daily_thirty_status) }}</strong></span></div>
        <p>{{ todayState.daily_thirty_reason || '还在了解今天的进展' }}</p>
      </section>
      <section class="rail-state-list">
        <div v-for="item in todayStateDetails" :key="item.key"><span>{{ item.label }}</span><p>{{ item.value }}</p></div>
      </section>
      <section class="rail-services">
        <button type="button" title="QQ通道" @click="openSettingsSection('qq')"><component :is="qqConnected ? Wifi : WifiOff" :size="17" /><span><strong>QQ</strong><small>{{ qqConnected ? '在线' : '离线' }}</small></span><i :class="{ online: qqConnected }" /></button>
        <button type="button" title="语音服务" @click="openSettingsSection('pet')"><Volume2 :size="17" /><span><strong>语音</strong><small>{{ companionStatus.voice_runtime?.service_running ? '在线' : '未启动' }}</small></span><i :class="{ online: companionStatus.voice_runtime?.service_running }" /></button>
        <button type="button" title="桌宠状态" @click="activeView = 'companion'"><Gamepad2 :size="17" /><span><strong>桌宠</strong><small>{{ companionStatus.pet?.running ? '运行中' : '未启动' }}</small></span><i :class="{ online: companionStatus.pet?.running }" /></button>
        <button type="button" title="屏幕观察" @click="openCompanionSection('screen-panel')"><Monitor :size="17" /><span><strong>观察</strong><small>{{ companionStatus.screen?.running ? (companionStatus.screen.title || '进行中') : '未启动' }}</small></span><i :class="{ online: companionStatus.screen?.running }" /></button>
      </section>
      <button class="rail-token" type="button" title="查看 Token 统计" @click="openTokenUsagePanel"><Activity :size="17" /><span><small>今日 Token</small><strong>{{ todayTokenUsage.toLocaleString('zh-CN') }}</strong></span></button>
      <section v-if="latestAssistantNotice" class="rail-notice"><MessageSquareText :size="17" /><span><small>最近消息 · {{ formatShortTime(latestAssistantNotice.created_at) }}</small><p>{{ latestAssistantNotice.content }}</p></span></section>
    </aside>

    <AppDialog
      v-model="appDialog.value"
      :open="appDialog.open"
      :mode="appDialog.mode"
      :title="appDialog.title"
      :message="appDialog.message"
      :confirm-text="appDialog.confirmText"
      :cancel-text="appDialog.cancelText"
      :danger="appDialog.danger"
      :multiline="appDialog.multiline"
      @confirm="confirmAppDialog"
      @cancel="cancelAppDialog"
    />

    <Teleport to="body">
      <div v-if="showTokenUsagePanel" class="modal-backdrop" @click.self="closeTokenUsagePanel">
        <section ref="tokenUsageDialog" class="token-usage-modal" role="dialog" aria-modal="true" aria-label="Token 统计" tabindex="-1" @keydown="onTokenUsageDialogKeydown">
        <header><div><h2>Token 统计</h2><p>按凌晨 {{ tokenUsageData.logical_day_boundary_hour || 4 }} 点划分每天的使用量</p></div><button class="icon-button" type="button" title="关闭" data-modal-initial-focus @click="closeTokenUsagePanel"><X :size="18" /></button></header>
        <div v-if="tokenUsageLoading" class="token-usage-loading">正在读取使用记录</div>
        <template v-else>
          <div class="token-summary-grid">
            <article><span>今日合计</span><strong>{{ formatTokenCount(tokenUsageData.today?.total_tokens) }}</strong><small>对话 {{ formatTokenCount(tokenUsageData.today?.chat_tokens) }} · 观察 {{ formatTokenCount(tokenUsageData.today?.screen_tokens) }}</small></article>
            <article><span>累计合计</span><strong>{{ formatTokenCount(tokenUsageData.total?.total_tokens || totalTokenUsage) }}</strong><small>对话 {{ formatTokenCount(tokenUsageData.total?.chat_tokens) }} · 观察 {{ formatTokenCount(tokenUsageData.total?.screen_tokens) }}</small></article>
          </div>
          <div class="token-breakdown">
            <span><small>今日输入</small><b>{{ formatTokenCount(tokenUsageData.today?.prompt_tokens) }}</b></span>
            <span><small>今日输出</small><b>{{ formatTokenCount(tokenUsageData.today?.completion_tokens) }}</b></span>
            <span><small>今日推理</small><b>{{ formatTokenCount(tokenUsageData.today?.reasoning_tokens) }}</b></span>
            <span><small>缓存输入</small><b>{{ formatTokenCount(tokenUsageData.today?.cached_prompt_tokens) }}</b></span>
          </div>
          <div class="token-day-list">
            <header><span>日期</span><span>对话</span><span>观察</span><span>合计</span></header>
            <div v-for="day in tokenUsageData.days" :key="day.date"><time>{{ day.date }}</time><span>{{ formatTokenCount(day.chat_tokens) }}</span><span>{{ formatTokenCount(day.screen_tokens) }}</span><strong>{{ formatTokenCount(day.total_tokens) }}</strong></div>
          </div>
        </template>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="showProviderPanel && activeView === 'settings' && activeSettingsSection === 'models'" class="modal-backdrop" @click.self="closeProviderPanel">
        <section ref="providerDialog" class="provider-modal" role="dialog" aria-modal="true" aria-label="供应商设置" tabindex="-1" @keydown="onProviderDialogKeydown">
        <header><div><h2>新增API供应商</h2><p>连接一次供应商，再选择要使用的具体模型版本。</p></div><button class="icon-button" type="button" title="关闭" @click="closeProviderPanel"><X :size="18" /></button></header>
        <div class="provider-kind-picker">
          <button type="button" :class="{ active: providerForm.provider_kind === 'official' }" @click="selectProviderKind('official')">官方 API</button>
          <button type="button" :class="{ active: providerForm.provider_kind === 'relay' }" @click="selectProviderKind('relay')">中转站</button>
        </div>
        <div v-if="providerForm.provider_kind === 'official'" class="provider-official-picker">
          <select :value="providerForm.preset_id" @change="selectProviderPreset($event.target.value)">
            <option value="">选择常用供应商</option>
            <option v-for="preset in providerPresets" :key="preset.id" :value="preset.id">{{ preset.name }}</option>
          </select>
        </div>
        <label>供应商名称<input v-model.trim="providerForm.provider_name" :disabled="Boolean(providerForm.preset_id)" autofocus placeholder="例如 OpenAI 中转站" /></label>
        <label>API地址<input v-model.trim="providerForm.base_url" :disabled="Boolean(providerForm.preset_id)" placeholder="https://example.com/v1" /></label>
        <label>API Key<input v-model="providerForm.api_key" type="password" autocomplete="off" placeholder="sk-..." /></label>
        <label>接口模式<select v-model="providerForm.default_api_mode"><option value="auto">自动识别</option><option value="responses">Responses API（Codex）</option><option value="chat_completions">Chat Completions</option></select></label>
        <div class="provider-discovery-bar">
          <span>{{ providerForm.provider_kind === 'official' ? '按官方模型接口获取' : 'OpenAI 兼容 / New API 中转站' }}</span>
          <button type="button" :disabled="providerDiscoveryBusy" @click="discoverProviderModels">
            <Download :size="15" />{{ providerDiscoveryBusy ? '正在获取' : '获取模型列表' }}
          </button>
        </div>
        <p v-if="providerDiscoveryWarning" class="provider-discovery-warning">{{ providerDiscoveryWarning }}</p>
        <div v-if="providerDiscoveryMeta" class="provider-discovery-result">
          <strong>连接成功</strong>
          <span>API 根地址：{{ providerDiscoveryMeta.resolved_api_base_url }}</span>
          <span>模型目录：{{ providerDiscoveryMeta.models_endpoint }}</span>
          <span>鉴权：{{ providerDiscoveryMeta.auth_scheme }}</span>
        </div>
        <div v-if="discoveredModels.length" class="discovered-model-list">
          <div v-for="model in discoveredModels" :key="model.model" class="discovered-model">
            <input v-model="model.selected" type="checkbox" :disabled="model.api_supported === false" />
            <span><strong>{{ model.display_name }}</strong><small>{{ model.model }}{{ model.api_supported === false ? ' · /messages 暂不支持' : '' }}</small></span>
            <em>{{ pricingSourceLabel(model.pricing_source) }}</em>
            <div v-if="model.selected" class="discovered-pricing">
              <label>缓存<input v-model.number="model.cached_input_price_cny_per_million" type="number" min="0" step="0.001" /></label>
              <label>输入<input v-model.number="model.input_price_cny_per_million" type="number" min="0" step="0.01" /></label>
              <label>输出<input v-model.number="model.output_price_cny_per_million" type="number" min="0" step="0.01" /></label>
              <small>元 / 百万 Token；接口返回实扣时会自动优先使用</small>
            </div>
          </div>
        </div>
        <label>模型ID<input v-model.trim="providerForm.model" placeholder="不选择上方列表时，可在这里手动填写模型ID" /></label>
        <template v-if="!discoveredModels.some((item) => item.selected)">
          <div class="form-grid price-grid">
            <label>缓存价格（元/百万Token）<input v-model.number="providerForm.cached_input_price_cny_per_million" type="number" min="0" step="0.001" /></label>
            <label>输入价格（元/百万Token）<input v-model.number="providerForm.input_price_cny_per_million" type="number" min="0" step="0.01" /></label>
            <label>输出价格（元/百万Token）<input v-model.number="providerForm.output_price_cny_per_million" type="number" min="0" step="0.01" /></label>
          </div>
        </template>
        <div class="provider-privacy-note">
          API Key 只保存在本机 D 盘数据目录，不会在页面中回显。
        </div>
        <label class="checkbox-field"><input v-model="providerForm.supports_vision" type="checkbox" />支持图片识别</label>
        <footer>
          <button type="button" @click="closeProviderPanel">取消</button>
          <button class="primary-button" type="button" :disabled="providerBusy" @click="saveProvider">{{ providerBusy ? '正在保存' : `保存${discoveredModels.filter((item) => item.selected).length || 1}个模型` }}</button>
        </footer>
        </section>
      </div>
    </Teleport>
  </div>
</template>
