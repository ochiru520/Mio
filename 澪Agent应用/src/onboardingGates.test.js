import assert from 'node:assert/strict'
import test from 'node:test'

import { canContinueOnboardingStep } from './onboardingGates.js'

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
