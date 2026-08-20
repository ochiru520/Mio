<script setup>
import { inject } from 'vue'
import {
  Activity,
  Check,
  CircleDollarSign,
  Clock3,
  Eye,
  ListChecks,
  MessageSquareText,
  Pause,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Target,
  X,
} from '@lucide/vue'

const context = inject('mio-records-page')
if (!context) throw new Error('任务页上下文未初始化')
</script>

<template>
  <section class="page-layout single-page tasks-page">
    <div class="tasks-shell">
      <header class="tasks-heading">
        <div>
          <ListChecks :size="22" />
          <div>
            <h2>Agent 执行中心</h2>
            <p>目标、权限、实际动作和投递结果都在这里留痕</p>
          </div>
        </div>
        <button class="icon-button" type="button" title="刷新执行中心" :disabled="context.tasksLoading || context.autonomyLoading" @click="context.loadAgentTasks()">
          <RefreshCw :class="{ spin: context.tasksLoading || context.autonomyLoading }" :size="16" />
        </button>
      </header>

      <div class="task-center-tabs" role="tablist" aria-label="Agent 执行中心视图">
        <button type="button" :class="{ active: context.taskCenterTab === 'actions' }" @click="context.taskCenterTab = 'actions'"><ListChecks :size="15" />执行记录</button>
        <button type="button" :class="{ active: context.taskCenterTab === 'goals' }" @click="context.taskCenterTab = 'goals'"><Target :size="15" />目标与权限</button>
        <button type="button" :class="{ active: context.taskCenterTab === 'autonomy' }" @click="context.taskCenterTab = 'autonomy'"><Activity :size="15" />主动记录</button>
      </div>

      <template v-if="context.taskCenterTab === 'actions'">
        <div class="task-filter-bar" role="tablist" aria-label="任务状态筛选">
          <button
            v-for="filter in context.taskStatusFilters"
            :key="filter.id"
            type="button"
            :class="{ active: context.taskStatusFilter === filter.id }"
            @click="context.taskStatusFilter = filter.id"
          >
            {{ filter.label }}
            <span v-if="filter.id === 'all'">{{ context.taskSummary.total }}</span>
            <span v-else-if="filter.id === 'running'">{{ context.taskSummary.running }}</span>
            <span v-else-if="filter.id === 'needs_confirmation'">{{ context.taskSummary.pending }}</span>
            <span v-else-if="filter.id === 'executed'">{{ context.taskSummary.completed }}</span>
            <span v-else>{{ context.taskSummary.failed }}</span>
          </button>
        </div>

        <div v-if="context.tasksLoading && !context.tasksLoaded" class="center-state tasks-center-state">
          <RefreshCw class="spin" :size="22" />
          <span>正在读取执行记录</span>
        </div>
        <div v-else-if="!context.filteredAgentTasks.length" class="tasks-empty">
          <ListChecks :size="28" />
          <strong>这里暂时没有任务</strong>
          <span>Mio 实际修改日记、状态、记忆或属性后，会在这里留下记录</span>
        </div>
        <div v-else class="task-list">
          <article v-for="task in context.filteredAgentTasks" :key="task.id" class="task-row">
            <div :class="['task-state-mark', `status-${task.status}`]">
              <RefreshCw v-if="['queued', 'running'].includes(task.status)" :class="{ spin: task.status === 'running' }" :size="16" />
              <Clock3 v-else-if="task.status === 'needs_confirmation'" :size="16" />
              <Check v-else-if="task.status === 'executed'" :size="16" />
              <X v-else :size="16" />
            </div>
            <div class="task-row-main">
              <header>
                <div><strong>{{ task.title }}</strong><span :class="`status-${task.status}`">{{ context.taskStatusLabel(task.status) }}</span></div>
                <time>{{ context.formatRealTime(task.finished_at || task.created_at) }}</time>
              </header>
              <p>{{ context.taskPayloadSummary(task) }}</p>
              <div class="task-meta">
                <span>对话：{{ task.conversation_id || '默认对话' }}</span>
                <span v-if="task.source_message_id">来源消息 #{{ task.source_message_id }}</span>
                <span>任务 #{{ task.id }}</span>
              </div>
              <div v-if="task.result" :class="['task-result', { error: ['failed', 'skipped', 'cancelled'].includes(task.status) }]">{{ task.result }}</div>
            </div>
            <div v-if="task.status === 'needs_confirmation'" class="task-actions">
              <button type="button" :disabled="Boolean(context.taskBusy)" @click="context.cancelAgentTask(task)">取消</button>
              <button class="primary" type="button" :disabled="Boolean(context.taskBusy)" @click="context.approveAgentTask(task)">
                <RefreshCw v-if="context.taskBusy === `approve-${task.id}`" class="spin" :size="14" />
                <Check v-else :size="14" />确认执行
              </button>
            </div>
          </article>
        </div>
      </template>

      <template v-else-if="context.taskCenterTab === 'goals'">
        <section class="autonomy-policy-panel">
          <header>
            <div><ShieldCheck :size="18" /><div><strong>自主权限</strong><span>{{ context.autonomyData.policy.paused ? '全部自主行为已暂停' : '只对活动目标评估事件' }}</span></div></div>
            <button :class="['autonomy-pause-button', { paused: context.autonomyData.policy.paused }]" type="button" :disabled="Boolean(context.autonomyBusy)" @click="context.autonomyData.policy.paused = !context.autonomyData.policy.paused; context.saveAutonomyPolicy()">
              <Play v-if="context.autonomyData.policy.paused" :size="14" />
              <Pause v-else :size="14" />
              {{ context.autonomyData.policy.paused ? '恢复' : '暂停' }}
            </button>
          </header>

          <div class="autonomy-level-control" role="radiogroup" aria-label="自主权限档位">
            <button v-for="level in context.autonomyLevelOptions" :key="level.id" type="button" role="radio" :aria-checked="context.autonomyData.policy.autonomy_level === level.id" :class="{ active: context.autonomyData.policy.autonomy_level === level.id }" @click="context.autonomyData.policy.autonomy_level = level.id">
              {{ level.label }}
            </button>
          </div>

          <div class="autonomy-policy-grid">
            <label><span>安静时段</span><div><input v-model.number="context.autonomyData.policy.quiet_start_hour" aria-label="安静时段开始" type="number" min="0" max="23" /><b>至</b><input v-model.number="context.autonomyData.policy.quiet_end_hour" aria-label="安静时段结束" type="number" min="0" max="23" /><b>时</b></div></label>
            <label><span>最小间隔</span><div><input v-model.number="context.autonomyData.policy.minimum_interval_minutes" type="number" min="1" max="1440" /><b>分钟</b></div></label>
            <label><span>每日次数</span><div><input v-model.number="context.autonomyData.policy.daily_behavior_limit" type="number" min="0" max="100" /><b>次</b></div></label>
            <label><span>每日预算</span><div><b>¥</b><input v-model.number="context.autonomyData.policy.daily_budget_yuan" type="number" min="0" max="100" step="0.01" /></div></label>
          </div>

          <div class="autonomy-capability-section">
            <div class="autonomy-capability-heading"><strong>能力覆盖</strong><span>只覆盖选中的能力，其余继承全局档位</span></div>
            <div class="autonomy-capability-grid">
              <label v-for="capability in context.autonomyCapabilityOptions" :key="capability.id" class="autonomy-capability-row">
                <span>{{ capability.label }}</span>
                <select :value="context.autonomyData.policy.capability_overrides[capability.id] || ''" @change="context.setAutonomyCapabilityOverride(capability.id, $event.target.value)">
                  <option v-for="option in context.autonomyOverrideOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
                </select>
              </label>
            </div>
          </div>
          <footer>
            <span><CircleDollarSign :size="13" />今日 {{ context.autonomyData.usage.behavior_count || 0 }} 次 · ¥{{ Number(context.autonomyData.usage.cost_yuan || 0).toFixed(4) }}</span>
            <button class="primary" type="button" :disabled="Boolean(context.autonomyBusy)" @click="context.saveAutonomyPolicy()"><Check :size="14" />保存权限</button>
          </footer>
        </section>

        <section class="autonomy-goals-section">
          <header><div><Target :size="18" /><div><strong>授权目标</strong><span>没有目标的事件只记录，不行动</span></div></div></header>
          <form class="autonomy-goal-form" @submit.prevent="context.createAutonomyGoal()">
            <input v-model="context.newAutonomyGoalTitle" type="text" maxlength="200" placeholder="添加一个希望 Mio 持续关注的目标" />
            <select v-model="context.newAutonomyGoalCapability">
              <option value="follow_up_reminder">到期跟进</option>
              <option value="daily_state">今日状态</option>
              <option value="service_health">服务健康</option>
              <option value="application_activity">应用活动</option>
              <option value="task_result">任务结果</option>
              <option value="proactive_checkin">主动联系</option>
              <option value="night_close">夜间收尾</option>
              <option value="screen_event">重要屏幕变化</option>
            </select>
            <button class="primary" type="submit" :disabled="!context.newAutonomyGoalTitle.trim() || Boolean(context.autonomyBusy)"><Plus :size="15" />添加</button>
          </form>
          <div v-if="!context.autonomyData.goals.length" class="tasks-empty compact"><Target :size="25" /><strong>还没有授权目标</strong><span>Mio 不会仅因为观察到事件就主动行动</span></div>
          <div v-else class="autonomy-list">
            <article v-for="goal in context.autonomyData.goals" :key="goal.id" class="autonomy-row">
              <div class="autonomy-row-icon"><Target :size="16" /></div>
              <div class="autonomy-row-main">
                <header><strong>{{ goal.title }}</strong><span :class="`status-${goal.status}`">{{ context.autonomyStatusLabel(goal.status) }}</span><time>{{ context.formatShortTime(goal.updated_at) }}</time></header>
                <p>{{ goal.description || '用户授权目标' }}</p>
                <div class="task-meta"><span>能力：{{ (goal.capabilities || []).join('、') || '未指定' }}</span><span v-if="goal.due_at">到期：{{ context.formatShortTime(goal.due_at) }}</span></div>
              </div>
              <div v-if="goal.status === 'active'" class="task-actions">
                <button type="button" :disabled="Boolean(context.autonomyBusy)" @click="context.updateAutonomyGoal(goal, 'paused')"><Pause :size="13" />暂停</button>
                <button type="button" :disabled="Boolean(context.autonomyBusy)" @click="context.updateAutonomyGoal(goal, 'completed')"><Check :size="13" />完成</button>
              </div>
              <div v-else-if="goal.status === 'paused'" class="task-actions"><button type="button" :disabled="Boolean(context.autonomyBusy)" @click="context.updateAutonomyGoal(goal, 'active')"><Play :size="13" />恢复</button></div>
            </article>
          </div>
        </section>
      </template>

      <template v-else>
        <div v-if="context.autonomyLoading && !context.autonomyLoaded" class="center-state tasks-center-state"><RefreshCw class="spin" :size="22" /><span>正在读取主动记录</span></div>
        <template v-else>
          <section class="autonomy-log-section">
            <header><div><MessageSquareText :size="18" /><div><strong>主动行为</strong><span>包含决策原因和最终投递状态</span></div></div></header>
            <div v-if="!context.autonomyData.behaviors.length" class="tasks-empty compact"><MessageSquareText :size="25" /><strong>还没有主动行为</strong><span>只有通过权限、时段、预算和相关性门禁后才会出现</span></div>
            <div v-else class="autonomy-list">
              <article v-for="behavior in context.autonomyData.behaviors" :key="behavior.id" class="autonomy-row">
                <div class="autonomy-row-icon"><MessageSquareText :size="16" /></div>
                <div class="autonomy-row-main">
                  <header><strong>{{ behavior.content || behavior.behavior_type }}</strong><span :class="`status-${behavior.status}`">{{ context.autonomyStatusLabel(behavior.status) }}</span><time>{{ context.formatShortTime(behavior.created_at) }}</time></header>
                  <p>{{ behavior.reason }}</p>
                  <div class="task-meta"><span>权限：{{ behavior.permission_mode }}</span><span>投递：{{ context.autonomyDeliveryLabel(behavior) }}</span><span>行为 #{{ behavior.id }}</span></div>
                </div>
                <div v-if="behavior.status === 'awaiting_confirmation'" class="task-actions">
                  <button type="button" :disabled="Boolean(context.autonomyBusy)" @click="context.cancelAutonomyBehavior(behavior)"><X :size="13" />取消</button>
                  <button class="primary" type="button" :disabled="Boolean(context.autonomyBusy)" @click="context.approveAutonomyBehavior(behavior)"><Check :size="13" />批准</button>
                </div>
              </article>
            </div>
          </section>

          <section class="autonomy-log-section">
            <header><div><Eye :size="18" /><div><strong>事件记录</strong><span>未授权、被暂停和已处理的事实都会保留原因</span></div></div></header>
            <div v-if="!context.autonomyData.events.length" class="tasks-empty compact"><Eye :size="25" /><strong>还没有事件</strong></div>
            <div v-else class="autonomy-list">
              <article v-for="event in context.autonomyData.events" :key="event.id" class="autonomy-row compact-row">
                <div class="autonomy-row-icon"><Activity :size="15" /></div>
                <div class="autonomy-row-main">
                  <header><strong>{{ event.event_type }}</strong><span :class="`status-${event.status}`">{{ context.autonomyStatusLabel(event.status) }}</span><time>{{ context.formatShortTime(event.occurred_at) }}</time></header>
                  <p>{{ event.decision_reason || event.error || `来源：${event.source || '系统'}` }}</p>
                  <div class="task-meta"><span>能力：{{ event.capability || '未分类' }}</span><span>目标 #{{ event.goal_id || '无' }}</span><span>事件 #{{ event.id }}</span></div>
                </div>
              </article>
            </div>
          </section>
        </template>
      </template>
    </div>
  </section>
</template>
