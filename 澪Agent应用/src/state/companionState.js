import { ref } from 'vue'

// Per-window state: factories never start I/O and never share mutable defaults.
export function createCompanionState() {
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
        indextts2_url: 'http://127.0.0.1:11996',
        indextts2_ref_audio: '',
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
  const companionStatusError = ref('')
  return {
    companionBusy,
    petChatDraft,
    petChatSending,
    petChatImages,
    petCallActive,
    petCallState,
    petCallError,
    petCallSettings,
    companionAvatarNonce,
    observationMode,
    screenScope,
    observationInterval,
    gameWindows,
    selectedGameHwnd,
    gameWindowsLoading,
    companionStatus,
    companionStatusReady,
    companionStatusError,
  }
}

// Native media resources stay outside Vue proxies and remain owned by this call.
export function createPetCallRuntime() {
  return {
    petCallStream: null,
    petCallAudioContext: null,
    petCallSource: null,
    petCallProcessor: null,
    petCallMonitorTimer: null,
    petCallFrames: [],
    petCallPreRoll: [],
    petCallSpeechStartedAt: 0,
    petCallSpeechCandidateStartedAt: 0,
    petCallLastVoiceAt: 0,
    petCallTurnPending: false,
    petCallInterruptSent: false,
    petCallBargeInStartedAt: 0,
    petCallSessionId: '',
    petCallNextTurnId: 1,
    petCallResponseId: '',
    petCallAwaitingVoiceSince: 0,
  }
}
