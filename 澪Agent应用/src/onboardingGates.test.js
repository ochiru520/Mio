import assert from 'node:assert/strict'
import test from 'node:test'

import { canContinueOnboardingStep, onboardingCompletion } from './onboardingGates.js'

const base = {
  coreReady: true,
  environmentBusy: false,
  assistantName: '澪',
  userAddress: '你',
  modelVerified: false,
  providerBusy: false,
}

test('模型步骤可以跳过，不强制真实测试', () => {
  assert.equal(canContinueOnboardingStep({ ...base, step: 3 }), true)
  assert.equal(canContinueOnboardingStep({ ...base, step: 3, providerBusy: true }), false)
})

test('核心环境与称呼步骤保持各自门禁', () => {
  assert.equal(canContinueOnboardingStep({ ...base, step: 0, coreReady: false }), false)
  assert.equal(canContinueOnboardingStep({ ...base, step: 0 }), true)
  assert.equal(canContinueOnboardingStep({ ...base, step: 2, assistantName: ' ' }), false)
  assert.equal(canContinueOnboardingStep({ ...base, step: 2 }), true)
})

test('跳过完成使用默认称呼，严格规范敏感开关并记录跳过', () => {
  assert.deepEqual(onboardingCompletion({ assistant_name:'  ', user_address:'', web_search_enabled:'true', qq_enabled:true }, true), {
    assistant_name:'Mio', user_address:'你', web_search_enabled:false, proactive_enabled:false,
    daily_diary_auto_enabled:false, qq_enabled:true, tutorial_skipped:true,
  })
  assert.equal(onboardingCompletion({ assistant_name:' 小澪 ', user_address:' 朋友 ' }).assistant_name, '小澪')
  assert.equal(onboardingCompletion({}).tutorial_skipped, false)
})
