/** Tasks operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import * as autonomyApi from '../services/autonomyApi.js'

export function useTasks(deps) {
  async function loadAgentTasks({ quiet = false } = {}) {
    if (deps.tasksLoading.value) return
    deps.tasksLoading.value = true
    try {
      deps.agentTasks.value = await deps.request('/api/agent/tasks?limit=200')
      deps.tasksLoaded.value = true
    } catch (error) {
      if (!quiet) deps.errorMessage.value = error.message
    } finally {
      deps.tasksLoading.value = false
    }
    await loadAutonomy({ quiet: true })
  }
  async function loadAutonomy({ quiet = false } = {}) {
    if (deps.autonomyLoading.value) return
    deps.autonomyLoading.value = true
    try {
      deps.autonomyData.value = await autonomyApi.loadAutonomy(200)
      deps.autonomyLoaded.value = true
    } catch (error) {
      if (!quiet) deps.errorMessage.value = error.message
    } finally {
      deps.autonomyLoading.value = false
    }
  }
  async function saveAutonomyPolicy() {
    if (deps.autonomyBusy.value) return
    deps.autonomyBusy.value = 'policy'
    try {
      const policy = deps.autonomyData.value.policy || {}
      deps.autonomyData.value.policy = await autonomyApi.updateAutonomyPolicy({
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
      deps.errorMessage.value = error.message
      await loadAutonomy({ quiet: true })
    } finally {
      deps.autonomyBusy.value = ''
    }
  }
  function setAutonomyCapabilityOverride(capabilityId, mode) {
    const overrides = { ...(deps.autonomyData.value.policy?.capability_overrides || {}) }
    if (mode) overrides[capabilityId] = mode
    else delete overrides[capabilityId]
    deps.autonomyData.value.policy.capability_overrides = overrides
  }
  async function createAutonomyGoal() {
    const title = deps.newAutonomyGoalTitle.value.trim()
    if (!title || deps.autonomyBusy.value) return
    deps.autonomyBusy.value = 'create-goal'
    try {
      await autonomyApi.createAutonomyGoal({
        title,
        description: '由用户在 Agent 执行中心创建。',
        conversation_id: deps.selectedConversationId.value,
        autonomy_level: '',
        capabilities: [deps.newAutonomyGoalCapability.value],
        due_at: '',
      })
      deps.newAutonomyGoalTitle.value = ''
      await loadAutonomy({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.autonomyBusy.value = ''
    }
  }
  async function updateAutonomyGoal(goal, status) {
    if (deps.autonomyBusy.value) return
    deps.autonomyBusy.value = `goal-${goal.id}`
    try {
      const updated = await autonomyApi.updateAutonomyGoalStatus(goal.id, status)
      deps.autonomyData.value.goals = deps.autonomyData.value.goals.map((item) => item.id === updated.id ? updated : item)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.autonomyBusy.value = ''
    }
  }
  async function approveAutonomyBehavior(behavior) {
    if (deps.autonomyBusy.value) return
    deps.autonomyBusy.value = `behavior-${behavior.id}`
    try {
      await autonomyApi.approveAutonomyBehavior(behavior.id)
      await Promise.all([
        loadAutonomy(),
        deps.refreshMessages(),
      ])
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.autonomyBusy.value = ''
    }
  }
  async function cancelAutonomyBehavior(behavior) {
    if (deps.autonomyBusy.value) return
    deps.autonomyBusy.value = `behavior-${behavior.id}`
    try {
      await autonomyApi.cancelAutonomyBehavior(behavior.id)
      await loadAutonomy()
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.autonomyBusy.value = ''
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
    if (deps.taskBusy.value) return
    deps.taskBusy.value = `approve-${task.id}`
    try {
      const updated = await deps.request(`/api/agent/tasks/${task.id}/approve`, { method: 'POST', body: '{}' })
      deps.agentTasks.value = deps.agentTasks.value.map((item) => item.id === updated.id ? updated : item)
      if (task.conversation_id === deps.selectedConversationId.value) await deps.refreshMessages()
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.taskBusy.value = ''
    }
  }
  async function cancelAgentTask(task) {
    if (deps.taskBusy.value) return
    deps.taskBusy.value = `cancel-${task.id}`
    try {
      const updated = await deps.request(`/api/agent/tasks/${task.id}/cancel`, { method: 'POST', body: '{}' })
      deps.agentTasks.value = deps.agentTasks.value.map((item) => item.id === updated.id ? updated : item)
      if (task.conversation_id === deps.selectedConversationId.value) await deps.refreshMessages()
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.taskBusy.value = ''
    }
  }
  return { loadAgentTasks, loadAutonomy, saveAutonomyPolicy, setAutonomyCapabilityOverride, createAutonomyGoal, updateAutonomyGoal, approveAutonomyBehavior, cancelAutonomyBehavior, autonomyStatusLabel, autonomyDeliveryLabel, taskStatusLabel, taskPayloadSummary, approveAgentTask, cancelAgentTask }
}
