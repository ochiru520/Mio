import { ref } from 'vue'

// Per-window state: factories never start I/O and never share mutable defaults.
export function createTasksState() {
  const agentTasks = ref([])
  const tasksLoading = ref(false)
  const tasksError = ref('')
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
  const autonomyError = ref('')
  const autonomyLoaded = ref(false)
  const autonomyBusy = ref('')
  const newAutonomyGoalTitle = ref('')
  const newAutonomyGoalCapability = ref('follow_up_reminder')
  return {
    agentTasks,
    tasksLoading,
    tasksError,
    tasksLoaded,
    taskBusy,
    taskStatusFilter,
    taskCenterTab,
    autonomyData,
    autonomyLoading,
    autonomyError,
    autonomyLoaded,
    autonomyBusy,
    newAutonomyGoalTitle,
    newAutonomyGoalCapability,
  }
}
