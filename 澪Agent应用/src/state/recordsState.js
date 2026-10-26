import { ref } from 'vue'

// Per-window state: factories never start I/O and never share mutable defaults.
export function createRecordsState() {
  const diaries = ref([])
  const selectedDiary = ref(null)
  const diarySearch = ref('')
  const statsData = ref({ summary: {}, calendar: [], mood_trend: [], year: 0, month: 0, logical_date: '' })
  const statsLoading = ref(false)
  const statsError = ref('')
  const statsLoaded = ref(false)
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
  const memoryError = ref('')
  const memoryLoaded = ref(false)
  const reviewBusy = ref('')
  return {
    diaries,
    selectedDiary,
    diarySearch,
    statsData,
    statsLoading,
    statsError,
    statsLoaded,
    diaryBusy,
    memoryTab,
    memoryData,
    runtimeSummaryDraft,
    newThreadContent,
    newThreadFollowUp,
    newProfileNote,
    newConversationSummary,
    newStructuredMemory,
    memoryBusy,
    dailyReviews,
    weeklyReviews,
    monthlyReviews,
    selectedDailyDate,
    selectedWeeklyStart,
    selectedMonthlyMonth,
    memoryLoading,
    memoryError,
    memoryLoaded,
    reviewBusy,
  }
}
