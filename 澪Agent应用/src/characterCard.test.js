import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  PRESET_CARDS,
  RELATION_TEMPLATES,
  TONE_TEMPLATES,
  applyProfileFields,
  buildCharacterCard,
  fillMappedIntoDraft,
  findRelationKey,
  findToneKey,
  parseCharacterCard,
  parseCharacterCardPng,
  safeCardFileName,
} from './characterCard.js'

function pngCard(card, keyword = 'chara') {
  const signature = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10])
  const encoded = Buffer.from(JSON.stringify(card), 'utf8').toString('base64')
  const data = Buffer.concat([Buffer.from(keyword, 'latin1'), Buffer.from([0]), Buffer.from(encoded, 'latin1')])
  const chunk = Buffer.alloc(12 + data.length)
  chunk.writeUInt32BE(data.length, 0)
  chunk.write('tEXt', 4, 4, 'latin1')
  data.copy(chunk, 8)
  chunk.writeUInt32BE(0, 8 + data.length)
  const iend = Buffer.from([0, 0, 0, 0, 73, 69, 78, 68, 0, 0, 0, 0])
  return Buffer.concat([Buffer.from(signature), chunk, iend])
}

function makeProfile(overrides = {}) {
  return {
    version: 1,
    identity: { name: '小霞', age_feel: '20 岁上下', core: '海边长大的女孩' },
    speaking_style: { tone: '温柔', bubble_style: '短句', avoid: ['说教'] },
    preferences: { user_address: '你', relationship_distance: '女友', custom_notes: ['不编造共同经历'] },
    behavior: { initiative: '默认关闭' },
    ...overrides,
  }
}

test('buildCharacterCard 生成酒馆 V3 结构，且澪扩展字段可完整还原', () => {
  const profile = makeProfile()
  profile.behavior = {
    ...profile.behavior,
    scenario: '雨夜咖啡馆。',
    character_rules: '保持角色身份。',
    reply_rules: '回复不超过三段。',
    dialogue_examples: '{{char}}: 欢迎回来。',
    worldbook_rules: '店名是晚灯。',
  }
  const card = buildCharacterCard(profile)
  assert.equal(card.spec, 'chara_card_v3')
  assert.equal(card.data.name, '小霞')
  assert.equal(card.data.description, '海边长大的女孩')
  assert.equal(card.data.personality, '温柔')
  assert.equal(card.data.scenario, '雨夜咖啡馆。')
  assert.equal(card.data.system_prompt, '保持角色身份。')
  assert.equal(card.data.post_history_instructions, '回复不超过三段。')
  assert.equal(card.data.mes_example, '{{char}}: 欢迎回来。')
  assert.equal(card.data.character_book.entries[0].content, '店名是晚灯。')
  assert.equal(card.data.extensions.mio.user_address, '你')
  assert.equal(card.data.extensions.mio.relationship_distance, '女友')
  assert.deepEqual(card.data.extensions.mio.avoid, ['说教'])
  assert.equal(card.data.extensions.mio.behavior.initiative, '默认关闭')
  assert.equal(card.data.extensions.mio.behavior.character_rules, '保持角色身份。')

  const parsed = parseCharacterCard(JSON.stringify(card))
  assert.equal(parsed.ok, true)
  assert.equal(parsed.mapped.name, '小霞')
  assert.equal(parsed.mapped.core, '海边长大的女孩')
  assert.equal(parsed.mapped.tone, '温柔')
  assert.equal(parsed.mapped.relationship, '女友')
  assert.equal(parsed.mapped.userAddress, '你')
  assert.equal(parsed.mapped.behavior.initiative, '默认关闭')
  assert.equal(parsed.mapped.behavior.scenario, '雨夜咖啡馆。')
  assert.equal(parsed.mapped.behavior.worldbook_rules, '店名是晚灯。')
})

test('外来酒馆卡映射到通用字段', () => {
  const foreign = {
    spec: 'chara_card_v3',
    spec_version: '3.0',
    data: { name: 'Alice', description: '开朗的咖啡师', personality: '爱开玩笑', first_mes: '嗨' },
  }
  const parsed = parseCharacterCard(JSON.stringify(foreign))
  assert.equal(parsed.ok, true)
  assert.equal(parsed.mapped.name, 'Alice')
  assert.equal(parsed.mapped.core, '开朗的咖啡师')
  assert.equal(parsed.mapped.tone, '爱开玩笑')
  assert.equal(parsed.mapped.relationship, '')
})

test('酒馆高级字段映射到行为与边界，不只填写一句话人设和语气', () => {
  const foreign = {
    spec: 'chara_card_v3',
    spec_version: '3.0',
    data: {
      name: 'Alice',
      description: '开朗的咖啡师',
      personality: '爱开玩笑',
      scenario: '用户走进雨夜咖啡馆。',
      system_prompt: '保持角色身份，不替用户做决定。',
      post_history_instructions: '回复控制在三段以内。',
      mes_example: '{{char}}: 今天想喝什么？',
      creator_notes: '适合日常陪伴。',
      first_mes: '欢迎回来。',
      character_book: {
        entries: [{ comment: '咖啡馆', keys: ['店名'], content: '店名是晚灯。', enabled: true }],
      },
    },
  }
  const parsed = parseCharacterCard(JSON.stringify(foreign))
  assert.equal(parsed.ok, true)
  assert.equal(parsed.mapped.behavior.scenario, '用户走进雨夜咖啡馆。')
  assert.equal(parsed.mapped.behavior.character_rules, '保持角色身份，不替用户做决定。')
  assert.equal(parsed.mapped.behavior.reply_rules, '回复控制在三段以内。')
  assert.match(parsed.mapped.behavior.worldbook_rules, /店名是晚灯/)
  assert.match(parsed.mapped.notes.join('\n'), /创作者备注/)
  assert.match(parsed.mapped.notes.join('\n'), /欢迎回来/)
})

test('PNG V2/V3 角色卡读取 chara 元数据', () => {
  const card = {
    spec: 'chara_card_v2',
    spec_version: '2.0',
    data: { name: '花火', description: '神秘少女', personality: '俏皮' },
  }
  const parsed = parseCharacterCardPng(pngCard(card))
  assert.equal(parsed.ok, true)
  assert.equal(parsed.mapped.name, '花火')
  assert.equal(parsed.mapped.core, '神秘少女')
})

test('ST 世界书可单独导入行为与边界', () => {
  const parsed = parseCharacterCard(JSON.stringify({
    name: '匹诺康尼世界书',
    entries: {
      0: { comment: '地点', key: ['匹诺康尼'], content: '一座梦境之城。', enabled: true },
      1: { comment: '禁用项', content: '不应导入。', enabled: false },
    },
  }))
  assert.equal(parsed.ok, true)
  assert.equal(parsed.mapped.importKind, 'worldbook')
  assert.equal(parsed.mapped.name, '')
  assert.equal(parsed.mapped.sourceName, '匹诺康尼世界书')
  assert.equal(parsed.mapped.importedRuleCount, 1)
  assert.match(parsed.mapped.behavior.worldbook_rules, /梦境之城/)
  assert.doesNotMatch(parsed.mapped.behavior.worldbook_rules, /不应导入/)
  const profile = makeProfile()
  fillMappedIntoDraft(profile, parsed.mapped)
  assert.equal(profile.identity.name, '小霞')
})

test('坏 JSON、非对象与缺 data 均被拒绝', () => {
  assert.equal(parseCharacterCard('{oops').ok, false)
  assert.equal(parseCharacterCard('[1,2]').ok, false)
  assert.equal(parseCharacterCard('42').ok, false)
  assert.equal(parseCharacterCard(JSON.stringify({ spec: 'x' })).ok, false)
})

test('applyProfileFields 只覆盖指定字段，不触碰名字', () => {
  const profile = makeProfile()
  applyProfileFields(profile, {
    core: '新的人设',
    tone: '新语气',
    relationship: '新的关系',
    userAddress: '你',
    bubble: '短句为主',
    avoid: ['机械复读'],
    notes: ['新的备注'],
  })
  assert.equal(profile.identity.core, '新的人设')
  assert.equal(profile.speaking_style.tone, '新语气')
  assert.equal(profile.preferences.relationship_distance, '新的关系')
  assert.equal(profile.preferences.user_address, '你')
  assert.deepEqual(profile.speaking_style.avoid, ['机械复读'])
  assert.equal(profile.identity.name, '小霞')
  assert.equal(profile.behavior.initiative, '默认关闭')
})

test('fillMappedIntoDraft 完整回填澪导出卡', () => {
  const profile = makeProfile()
  const card = buildCharacterCard(makeProfile({ identity: { name: '改名', age_feel: '25 岁', core: '新身份' } }))
  const parsed = parseCharacterCard(JSON.stringify(card))
  fillMappedIntoDraft(profile, parsed.mapped)
  assert.equal(profile.identity.name, '改名')
  assert.equal(profile.identity.age_feel, '25 岁')
  assert.equal(profile.identity.core, '新身份')
})

test('关系与语气模板键识别', () => {
  assert.equal(findRelationKey(''), '')
  assert.equal(findRelationKey('随便写的内容'), '')
  assert.equal(findRelationKey(RELATION_TEMPLATES.partner.text), 'partner')
  assert.equal(findToneKey(TONE_TEMPLATES.lively.text), 'lively')
  assert.equal(findToneKey('自定义语气'), '')
})

test('预设卡齐全且字段完整', () => {
  assert.equal(PRESET_CARDS.length, 4)
  for (const card of PRESET_CARDS) {
    assert.ok(card.id && card.name && card.tagline)
    assert.ok(card.profile.core && card.profile.tone && card.profile.relationship)
    assert.ok(Array.isArray(card.profile.avoid))
  }
})

test('safeCardFileName 清理非法字符并限长', () => {
  assert.equal(safeCardFileName('小/霞:测试?'), '小 霞 测试')
  assert.equal(safeCardFileName('   '), '未命名角色')
  assert.equal(safeCardFileName('').length > 0, true)
  assert.ok(safeCardFileName('x'.repeat(200)).length <= 60)
})
