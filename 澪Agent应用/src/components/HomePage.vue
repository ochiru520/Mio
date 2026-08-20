<script>
import {
  ArrowRight,
  BookOpen,
  CalendarDays,
  CheckCircle2,
  Circle,
  Heart,
  MessageSquareText,
  Sparkles,
  Wifi,
  WifiOff,
} from '@lucide/vue'

export default {
  name: 'HomePage',
  components: {
    ArrowRight,
    BookOpen,
    CalendarDays,
    CheckCircle2,
    Circle,
    Heart,
    MessageSquareText,
    Sparkles,
    Wifi,
    WifiOff,
  },
  props: {
    logicalDate: { type: String, default: '' },
    todayState: { type: Object, default: () => ({}) },
    todayStateDetails: { type: Array, default: () => [] },
    diaries: { type: Array, default: () => [] },
    memoryData: { type: Object, default: () => ({}) },
    displayName: { type: String, default: 'Mio' },
    userAddress: { type: String, default: '你' },
    qqConnected: { type: Boolean, default: false },
    qqStatusLabel: { type: String, default: '离线' },
    companionRunning: { type: Boolean, default: false },
    rightPanelVisible: { type: Boolean, default: true },
    homeWidgets: { type: Object, default: () => ({}) },
    agentStatus: { type: String, default: '空闲' },
    screenStatus: { type: String, default: '未启动' },
    currentModel: { type: String, default: '正在读取' },
    contextUsage: { type: Object, default: () => ({}) },
  },
  emits: ['navigate', 'open-diary'],
  computed: {
    readableDate() {
      if (!this.logicalDate) return '今天'
      const [year, month, day] = this.logicalDate.split('-').map(Number)
      const weekday = new Intl.DateTimeFormat('zh-CN', { weekday: 'long' })
        .format(new Date(year, month - 1, day))
      return `${month} 月 ${day} 日 · ${weekday}`
    },
    greeting() {
      const hour = new Date().getHours()
      if (hour < 4) return '夜深了'
      if (hour < 11) return '早上好'
      if (hour < 14) return '中午好'
      if (hour < 18) return '下午好'
      if (hour < 23) return '晚上好'
      return '夜深了'
    },
    periodLabel() {
      const hour = new Date().getHours()
      if (hour < 4) return '深夜'
      if (hour < 11) return '上午'
      if (hour < 14) return '中午'
      if (hour < 18) return '下午'
      return '晚上'
    },
    moodScore() {
      return Math.max(0, Math.min(5, Number(this.todayState.mood_score || 0)))
    },
    recentDiaries() {
      return (this.diaries || []).slice(0, 3)
    },
    recentMemories() {
      const structured = Array.isArray(this.memoryData?.structured) ? this.memoryData.structured : []
      const summaries = Array.isArray(this.memoryData?.summaries) ? this.memoryData.summaries : []
      const categoryLabels = {
        identity: '关于我',
        preference: '偏好与习惯',
        relationship: '重要关系',
        current_state: '近期状态',
        plan: '计划',
        project: '项目',
        experience: '经历',
        person: '重要的人',
        other: '其他',
      }
      return [...structured, ...summaries]
        .map((item) => ({
          id: item.id || `${item.date || ''}-${item.content || item.summary || ''}`,
          title: categoryLabels[item.category] || item.date || '最近记忆',
          content: item.content || item.summary || '',
        }))
        .filter((item) => item.content)
        .slice(0, 3)
    },
    memoryCount() {
      return (this.memoryData?.structured?.length || 0) + (this.memoryData?.summaries?.length || 0)
    },
    todayDiary() {
      return (this.diaries || []).find((item) => item.date === this.logicalDate)
    },
    ongoingItem() {
      const thread = (this.memoryData?.threads || []).find((item) => item.status !== 'resolved')
      if (thread) return { title: '上次还在继续的事', content: thread.content, target: 'memory' }
      const diary = this.recentDiaries[0]
      if (diary) return { title: diary.title || `${diary.date} 的日记`, content: `${diary.date} 留下的记录`, target: 'diaries' }
      return { title: '从今天开始', content: '还没有需要接着处理的事情', target: 'chat' }
    },
    attentionItems() {
      return [
        this.todayState.avoidance_signals && { label: '可以调整', value: this.todayState.avoidance_signals },
        this.todayState.next_min_action && { label: '下一步', value: this.todayState.next_min_action },
        { label: '今日成长', value: this.todayState.daily_thirty_reason || this.dailyThirtyLabel },
      ].filter(Boolean)
    },
    todayDynamics() {
      return [
        this.todayState.key_events && { label: '今日主线', value: this.todayState.key_events },
        this.todayState.mood && { label: '今日情绪', value: this.todayState.mood },
        this.todayDiary && { label: '日记', value: this.todayDiary.title || '今天的日记已经写下' },
      ].filter(Boolean)
    },
    recentRecords() {
      const diaries = this.recentDiaries.map((item) => ({ id: `diary-${item.date}`, label: item.date, title: this.diaryPreview(item), target: 'diaries', date: item.date }))
      const memories = this.recentMemories.map((item) => ({ id: `memory-${item.id}`, label: item.title, title: item.content, target: 'memory' }))
      return [...diaries, ...memories].slice(0, 5)
    },
    dailyThirtyLabel() {
      return {
        done: '今天已经完成',
        partial: '正在慢慢推进',
        missed: '今天还没完成',
        unknown: '还在观察',
      }[this.todayState.daily_thirty_status || 'unknown']
    },
    summaryWidgets() {
      const widgets = [
        {
          id: 'daily_thirty',
          label: '今日成长',
          value: this.dailyThirtyLabel,
          detail: this.todayState.daily_thirty_reason || `${this.displayName}会从今天的对话里继续了解`,
        },
        {
          id: 'mood',
          label: '心情分析',
          value: this.todayState.mood || '还在感受',
          detail: this.moodScore ? `${this.moodScore} / 5` : '等待更多生活片段',
        },
        {
          id: 'diary',
          label: '今日日记',
          value: this.todayDiary ? '已经写下' : '等待整理',
          detail: this.todayDiary?.title || '到了晚上，我们再一起回顾',
        },
        {
          id: 'memory',
          label: '最近记忆',
          value: `${this.memoryCount} 条`,
          detail: `重要的事情，${this.displayName}会认真记住`,
        },
        {
          id: 'agent_status',
          label: 'Agent 状态',
          value: this.agentStatus,
          detail: this.agentStatus === '空闲' ? '随时可以帮你做事' : '正在处理当前任务',
        },
        {
          id: 'screen_status',
          label: '屏幕状态',
          value: this.screenStatus,
          detail: this.companionRunning ? '桌宠已在桌面运行' : '桌宠当前未启动',
        },
        {
          id: 'api_info',
          label: '当前模型',
          value: this.currentModel,
          detail: this.qqConnected ? '应用与 QQ 通道均可使用' : '当前仅应用内对话可用',
        },
        {
          id: 'token_stats',
          label: '上下文',
          value: `${Number(this.contextUsage.percent || 0).toFixed(1)}%`,
          detail: `${Number(this.contextUsage.used_chars || 0).toLocaleString('zh-CN')} / ${Number(this.contextUsage.max_chars || 18000).toLocaleString('zh-CN')} 字`,
        },
      ]
      return widgets.filter((item) => this.homeWidgets[item.id] !== false)
    },
  },
  methods: {
    diaryPreview(item) {
      if (item?.title && item.title !== item.date) return item.title
      const firstEvent = String(item?.markdown_content || '')
        .split('\n')
        .map((line) => line.trim())
        .find((line) => line.startsWith('- '))
      return firstEvent?.slice(2) || `${item?.date || '最近'} 的日记`
    },
  },
}
</script>

<template>
  <div class="home-page integrated-home">
    <header class="home-today-header">
      <span>{{ readableDate }} · {{ periodLabel }}</span>
      <h1>{{ greeting }}，{{ userAddress }}</h1>
      <p>{{ todayState.key_events || '今天还没有形成清晰的主线，先从你正在做的事开始' }}</p>
    </header>
    <section class="home-focus-grid">
      <article class="home-focus-primary">
        <span>继续上次的事情</span><h2>{{ ongoingItem.title }}</h2><p>{{ ongoingItem.content }}</p>
        <button type="button" @click="$emit('navigate', ongoingItem.target)">继续 <ArrowRight :size="15" /></button>
      </article>
      <article class="home-attention-panel">
        <span>今天需要留意</span>
        <div v-for="item in attentionItems" :key="item.label"><strong>{{ item.label }}</strong><p>{{ item.value }}</p></div>
      </article>
    </section>
    <section class="home-activity-section">
      <header><div><Sparkles :size="17" /><h2>今天的动态</h2></div><button type="button" @click="$emit('navigate', 'chat')">打开对话</button></header>
      <div v-if="todayDynamics.length" class="home-activity-list"><article v-for="item in todayDynamics" :key="item.label"><span>{{ item.label }}</span><p>{{ item.value }}</p></article></div>
      <p v-else class="home-empty-line">今天的状态还在慢慢形成</p>
    </section>
    <section class="home-records-section">
      <header><div><BookOpen :size="17" /><h2>最近记录</h2></div></header>
      <div v-if="recentRecords.length" class="home-records-list">
        <button v-for="item in recentRecords" :key="item.id" type="button" @click="item.date ? $emit('open-diary', item.date) : $emit('navigate', item.target)"><span>{{ item.label }}</span><strong>{{ item.title }}</strong><ArrowRight :size="14" /></button>
      </div>
      <p v-else class="home-empty-line">还没有可以展示的记录</p>
    </section>
  </div>
</template>
