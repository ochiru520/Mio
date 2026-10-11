import { createApp } from 'vue'
import App from './App.vue'

// 样式加载顺序固定，不依赖“后加载者胜出”来覆盖：
//   1. tokens  唯一令牌层（颜色、尺度）
//   2. base    重置、滚动条、表单、按钮、对话框
//   3. styles  历史基础样式（正在逐步迁移到令牌）
//   4. integrated 应用外壳的栅格与各页面布局
//   5. refinements 最后的针对性修正
import './styles/tokens.css'
import './styles/base.css'
import './styles.css'
import './styles/integrated.css'
import './styles/refinements.css'
import './styles/agent-settings.css'
import './styles/agent-surface.css'
import './styles/ui-polish.css'

createApp(App).mount('#app')
