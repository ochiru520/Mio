import assert from 'node:assert/strict'
import test from 'node:test'

await import('../public/live2d-pet/expression-utils.js')

const { expressionNames, selectExpression } = globalThis.MioLive2DExpression

test('导入模型的大写 Name 表情字段可以被读取', () => {
  assert.deepEqual(
    expressionNames([{ Name: '7 害羞', File: '7.exp3.json' }, { name: 'Happy' }]),
    ['7 害羞', 'Happy'],
  )
})

test('回复情绪可以选择中文命名的导入表情', () => {
  const expressions = [
    { Name: '6 泪' },
    { Name: '7 害羞' },
    { Name: '8 生气' },
    { Name: '10 星星眼' },
  ]

  assert.equal(selectExpression(expressions, 'shy'), '7 害羞')
  assert.equal(selectExpression(expressions, 'serious'), '8 生气')
  assert.equal(selectExpression(expressions, 'cheerful'), '10 星星眼')
})

test('用户配置的表情槽位优先于自动匹配', () => {
  const expressions = [{ Name: '7 害羞' }, { Name: '9 爱心眼' }]

  assert.equal(selectExpression(expressions, 'shy', '9 爱心眼'), '9 爱心眼')
})
