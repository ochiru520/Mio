import test from 'node:test'
import assert from 'node:assert/strict'
import { messageCacheRateLabel, messageTokenUsageTitle } from './messageTokenUsage.js'

test('cache share uses input only and keeps cache and reasoning within their parent totals', () => {
  const title = messageTokenUsageTitle({
    prompt_tokens: 2000, cached_prompt_tokens: 1500, completion_tokens: 500, reasoning_tokens: 200,
  })
  assert.match(title, /输入：2,000 token（已包含缓存输入）/)
  assert.match(title, /上游报告缓存：1,500 token（占输入 75\.0%）/)
  assert.match(title, /输出：500 token（其中推理 200 token）/)
  assert.doesNotMatch(title, /当前上下文占用|未报告/)
})

test('zero or missing cache usage does not claim an actual cache miss', () => {
  for (const cached_prompt_tokens of [0, null, undefined]) {
    const title = messageTokenUsageTitle({ prompt_tokens: 1000, cached_prompt_tokens })
    assert.match(title, /上游缓存：未知/)
    assert.match(title, /供应商未报告，旧记录也无法补推/)
  }
})

test('visible cache rate uses input denominator, including cumulative daily totals', () => {
  assert.equal(messageCacheRateLabel({ prompt_tokens: 2000, cached_prompt_tokens: 1800, completion_tokens: 4000 }), '缓存 90.0%')
  // Weighted sum 1800/2000, not the mean of 100/100 and 1700/1900.
  assert.equal(messageCacheRateLabel({ prompt_tokens: 100 + 1900, cached_prompt_tokens: 100 + 1700 }), '缓存 90.0%')
  assert.equal(messageCacheRateLabel({ prompt_tokens: '1000', cached_prompt_tokens: '1' }), '缓存 0.1%')
})

test('zero, absent and impossible cache usage stays unconfirmed in visible labels', () => {
  for (const usage of [undefined, {}, { prompt_tokens: 1000 },
    { prompt_tokens: 1000, cached_prompt_tokens: 0 },
    { prompt_tokens: 0, cached_prompt_tokens: 100 },
    { prompt_tokens: 100, cached_prompt_tokens: 101 },
    { prompt_tokens: Infinity, cached_prompt_tokens: 100 },
    { prompt_tokens: 100, cached_prompt_tokens: -1 }]) {
    assert.equal(messageCacheRateLabel(usage), '缓存 未知')
  }
  assert.match(messageTokenUsageTitle({ prompt_tokens: 100, cached_prompt_tokens: 200 }), /占输入比例未知/)
})

test('Agent cumulative usage cannot be mistaken for the current context size', () => {
  const title = messageTokenUsageTitle({
    prompt_tokens: '12000', cached_prompt_tokens: '9000', completion_tokens: '2000',
  }, true)
  assert.match(title, /多次模型调用的累计用量，不等于当前上下文占用/)
  assert.match(title, /上游报告缓存：9,000 token（占输入 75\.0%）/)
})

test('missing or invalid input leaves the cache share unknown', () => {
  for (const prompt_tokens of [0, undefined, 'invalid', Infinity, -1]) {
    const title = messageTokenUsageTitle({ prompt_tokens })
    assert.match(title, /占输入比例未知/)
    assert.doesNotMatch(title, /NaN|Infinity|0\.0%/)
  }
})


test('confirmed zero and incomplete cache coverage remain distinct from unknown', () => {
  const zero = { prompt_tokens: 1000, cached_prompt_tokens: 0, cached_tokens_reporting: 'reported_zero' }
  assert.equal(messageCacheRateLabel(zero), '缓存 0.0%')
  assert.match(messageTokenUsageTitle(zero), /上游报告缓存：0 token（占输入 0\.0%）/)
  assert.equal(messageCacheRateLabel({ prompt_tokens: 10000, cached_prompt_tokens: 800, cache_hit_ratio: .8 }), '缓存 80.0%')
  assert.match(messageTokenUsageTitle({ cache_hit_ratio: .8, cache_reporting_coverage: .5, unknown_cost_calls: 2 }), /覆盖率：50.0%/)
  assert.match(messageTokenUsageTitle({ unknown_cost_calls: 2 }), /2 次费用未知/)
})
