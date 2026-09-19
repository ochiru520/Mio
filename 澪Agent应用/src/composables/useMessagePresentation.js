/** MessagePresentation operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import DOMPurify from 'dompurify'
import { creationCostLabel } from '../agentCreationState.js'
import { creationLoras } from '../agentCreationState.js'
import { creationProgressPercent } from '../agentCreationState.js'
import { creationPrompt } from '../agentCreationState.js'
import { creationSamplerLabel } from '../agentCreationState.js'
import { creationStatusLabel } from '../agentCreationState.js'
import { creationWorkflowSnapshot } from '../agentCreationState.js'
import { executionStatus } from '../agentTaskState.js'
import { findCreationJob } from '../agentCreationState.js'
import { formatCreationElapsed } from '../agentCreationState.js'
import { marked } from 'marked'

export function useMessagePresentation(deps) {
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
    for (const model of deps.modelOptions.value) {
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
    const profile = deps.modelOptions.value.find((item) => item.id === turn?.model_id)
    return profile?.display_name || turn?.provider_model || turn?.model_id || deps.compactActiveModelLabel.value
  }
  function compactReasoningLabel(level) {
    if (level === 'thinking') return '开'
    if (level === 'off') return '关'
    return reasoningLabel(level)
  }
  function turnId(turn) {
    return String(turn.request_id || turn.id)
  }
  function turnToolReceipts(turn) {
    const receipts = Array.isArray(turn?.tool_receipts)
      ? turn.tool_receipts
      : turn?.parts?.find((part) => Array.isArray(part.tool_receipts))?.tool_receipts
    return Array.isArray(receipts) ? receipts : []
  }
  function turnCreationJob(turn) {
    return findCreationJob(turnToolReceipts(turn))
  }
  function turnCreationPrompt(turn) {
    return creationPrompt(turnCreationJob(turn))
  }
  function turnCreationLoras(turn) {
    return creationLoras(turnCreationJob(turn))
  }
  function turnCreationSampler(turn) {
    return creationSamplerLabel(turnCreationJob(turn))
  }
  function turnCreationWorkflow(turn) {
    return creationWorkflowSnapshot(turnCreationJob(turn))
  }
  function agentExecutionStatus(turn) {
    return executionStatus(turnToolReceipts(turn), turnCreationJob(turn)) ?? creationStatusLabel(turnCreationJob(turn), turnToolReceipts(turn))
  }
  function agentExecutionProgress(turn) {
    return creationProgressPercent(turnCreationJob(turn))
  }
  function agentExecutionElapsed(turn) {
    return formatCreationElapsed(turnCreationJob(turn)?.elapsed_seconds)
  }
  function agentCreationCost(turn) {
    return creationCostLabel(turnCreationJob(turn))
  }
  function turnTokenCount(turn) {
    return Number(turn?.prompt_tokens || 0) + Number(turn?.completion_tokens || 0)
  }
  function toolReceiptLabel(receipt) {
    return deps.toolReceiptLabels[receipt?.tool_name] || receipt?.tool_name || '工具'
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
      const provider = detail.provider_name || deps.activeModel.value?.provider_name || '当前供应商'
      const model = detail.provider_model || deps.activeModel.value?.display_name || deps.selectedModel.value
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
  return { formatRealTime, formatShortTime, formatSidebarTime, weekStartFor, weekEndFor, monthEndFor, requestCostDetails, formatCost, costSourceLabel, pricingSourceLabel, sourceLabel, reasoningLabel, compactModelLabel, messageModelLabel, compactReasoningLabel, turnId, turnToolReceipts, turnCreationJob, turnCreationPrompt, turnCreationLoras, turnCreationSampler, turnCreationWorkflow, agentExecutionStatus, agentExecutionProgress, agentExecutionElapsed, agentCreationCost, turnTokenCount, toolReceiptLabel, toolReceiptStatus, toolReceiptTitle, modelRequestError, cleanDisplayContent, statusLabel, renderedMarkdown }
}
