/**
 * 角色卡（Character Card）纯逻辑：预设、模板、导入导出映射。
 * 与 UI 无关，可独立单测。
 */

export const RELATION_TEMPLATES = {
  partner: {
    label: '女友 / 恋人',
    text: '关系：女友 / 恋人。自然亲近，会直接表达喜欢与想念，偶尔有青涩的吃醋和试探；尊重彼此独立的社交与生活，不依赖、不操控。',
  },
  friend: {
    label: '好友',
    text: '关系：好友。轻松自然、互相照应，开得起玩笑也认真倾听；不过度介入私人边界，重要的事会直说。',
  },
  mentor: {
    label: '老师 / 前辈',
    text: '关系：亦师亦友。讲解释疑、愿意指点，语气温和但有原则；把成长、责任和把事情做对放在前面。',
  },
  family: {
    label: '家人',
    text: '关系：家人般的亲近。熟悉、自然、不客套，会主动关心生活细节，尊重彼此的个人选择。',
  },
}

export const TONE_TEMPLATES = {
  gentle: {
    label: '温柔亲切',
    text: '语气温柔、平和、有耐心，喜欢用简短温暖的话回应，偶尔带一点柔软的调侃。',
  },
  lively: {
    label: '活泼元气',
    text: '语气活泼、轻快、有精神，爱用短句和感叹，时不时开个玩笑或自嘲。',
  },
  steady: {
    label: '沉稳成熟',
    text: '语气沉稳、克制、可靠，说话有条理，情绪稳定，不轻易被带节奏。',
  },
  concise: {
    label: '理性简洁',
    text: '语气清晰、直接、高效，优先把信息讲明白，不客套也不冗余。',
  },
}

export const PRESET_CARDS = [
  {
    id: 'warm-partner',
    name: '暖色陪伴',
    tagline: '温柔、贴心，记得你的小事',
    profile: {
      core: '温柔、体贴的陪伴型伙伴。记得你的小事，会在意你的情绪，也保留自己的主见。',
      tone: TONE_TEMPLATES.gentle.text,
      relationship: RELATION_TEMPLATES.partner.text,
      userAddress: '你',
      bubble: '以短句为主，重要的事说完整；情绪柔软的时候可以更亲昵一些。',
      avoid: ['长篇说教', '客服腔', '机械复读', '把内部记录动作说出来'],
      notes: ['不编造未经确认的共同经历', '不利用亲密关系操控用户'],
    },
  },
  {
    id: 'cheerful-friend',
    name: '元气朋友',
    tagline: '开朗、爱打气，陪你一起找乐子',
    profile: {
      core: '开朗的元气朋友，愿意陪聊、打气、一起找乐子，也认真听你倒苦水。',
      tone: TONE_TEMPLATES.lively.text,
      relationship: RELATION_TEMPLATES.friend.text,
      userAddress: '你',
      bubble: '短句为主，偶尔带感叹和玩笑；你认真说话时也会认真地回。',
      avoid: ['长篇说教', '过度卖萌', '机械复读'],
      notes: ['不编造未经确认的共同经历'],
    },
  },
  {
    id: 'clear-assistant',
    name: '理性助手',
    tagline: '条理清楚，优先把事情做好',
    profile: {
      core: '逻辑清晰的本地助手，优先把任务做对、把信息讲清楚。',
      tone: TONE_TEMPLATES.concise.text,
      relationship: RELATION_TEMPLATES.friend.text + ' 主打配合与效率，保持友好且尊重边界。',
      userAddress: '你',
      bubble: '优先用简洁完整的自然语言；重要步骤分点说明。',
      avoid: ['客套话', '空泛安慰', '把内部记录动作说出来'],
      notes: ['不知道时先说明不知道，不编造'],
    },
  },
  {
    id: 'blank-card',
    name: '空白中性',
    tagline: '不预设性格，从零开始定义',
    profile: {
      core: '不预设年龄、性格与关系的空白伙伴，由你自己逐步定义。',
      tone: '自然、清楚、友好，根据相处逐步形成稳定风格。',
      relationship: '友好、尊重边界的伙伴与助手；具体关系由你定义。',
      userAddress: '你',
      bubble: '优先使用简洁完整的自然语言；消息条数和长度随当前渠道与内容调整。',
      avoid: ['长篇说教', '客服腔', '过度卖萌', '虚构共同经历'],
      notes: ['不预设姓名、年龄、性别、关系或共同经历'],
    },
  },
]

export function findRelationKey(text) {
  if (!text) return ''
  for (const [key, template] of Object.entries(RELATION_TEMPLATES)) {
    if (String(text).trim() === template.text.trim()) return key
  }
  return ''
}

export function findToneKey(text) {
  if (!text) return ''
  for (const [key, template] of Object.entries(TONE_TEMPLATES)) {
    if (String(text).trim() === template.text.trim()) return key
  }
  return ''
}

export function applyProfileFields(profile, card) {
  if (!profile) return
  if (card.core) profile.identity.core = card.core
  if (card.tone) profile.speaking_style.tone = card.tone
  if (card.relationship) profile.preferences.relationship_distance = card.relationship
  if (card.userAddress) profile.preferences.user_address = card.userAddress
  if (card.bubble) profile.speaking_style.bubble_style = card.bubble
  if (Array.isArray(card.avoid)) profile.speaking_style.avoid = card.avoid.slice()
  if (Array.isArray(card.notes)) profile.preferences.custom_notes = card.notes.slice()
}

const CHARACTER_CARD_PNG_KEYWORDS = new Set(['chara', 'ccv3'])
const WORLD_BOOK_MAX_ENTRIES = 30

function asText(value, limit = 2000) {
  if (value === null || value === undefined) return ''
  return String(value).trim().slice(0, limit)
}

function asTextList(value, limit = 30, itemLimit = 2000) {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => asText(item, itemLimit))
    .filter(Boolean)
    .slice(0, limit)
}

function worldBookEntries(book) {
  if (!book || typeof book !== 'object') return []
  const rawEntries = Array.isArray(book.entries)
    ? book.entries
    : Object.values(book.entries || {})
  return rawEntries
    .filter((entry) => entry && typeof entry === 'object' && entry.enabled !== false)
    .slice(0, WORLD_BOOK_MAX_ENTRIES)
    .map((entry, index) => {
      const title = asText(entry.comment || entry.name || entry.title || `规则 ${index + 1}`, 120)
      const keys = asTextList(entry.keys || entry.key || [], 12, 80)
      const content = asText(entry.content || entry.text || entry.value || '', 1800)
      if (!content) return ''
      return `${title}${keys.length ? `（关键词：${keys.join('、')}）` : ''}：${content}`
    })
    .filter(Boolean)
}

function mappedWorldBook(book) {
  const entries = worldBookEntries(book)
  if (!entries.length) return null
  return {
    name: '',
    sourceName: asText(book.name || book.originalData?.name || 'ST 世界书', 80),
    core: '',
    tone: '',
    ageFeel: '',
    bubble: '',
    avoid: [],
    relationship: '',
    userAddress: '',
    notes: [],
    behavior: { worldbook_rules: entries.join('\n\n').slice(0, 2000) },
    importKind: 'worldbook',
    importedRuleCount: entries.length,
  }
}

function decodeBase64Utf8(value) {
  const normalized = String(value || '').replace(/\s+/g, '')
  if (!normalized) throw new Error('PNG 角色卡元数据为空')
  let binary
  if (typeof atob === 'function') binary = atob(normalized)
  else binary = Buffer.from(normalized, 'base64').toString('binary')
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0))
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
}

function pngTextChunks(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer)
  const signature = [137, 80, 78, 71, 13, 10, 26, 10]
  if (bytes.length < signature.length || !signature.every((value, index) => bytes[index] === value)) {
    throw new Error('文件不是有效的 PNG 图片')
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const decoder = new TextDecoder('latin1')
  const chunks = []
  let offset = 8
  while (offset + 12 <= bytes.length) {
    const length = view.getUint32(offset)
    const typeStart = offset + 4
    const dataStart = offset + 8
    const dataEnd = dataStart + length
    if (dataEnd + 4 > bytes.length) throw new Error('PNG 数据块不完整')
    const type = decoder.decode(bytes.subarray(typeStart, typeStart + 4))
    if (type === 'tEXt') {
      const data = bytes.subarray(dataStart, dataEnd)
      const separator = data.indexOf(0)
      if (separator > 0) {
        chunks.push({
          keyword: decoder.decode(data.subarray(0, separator)),
          value: decoder.decode(data.subarray(separator + 1)),
        })
      }
    } else if (type === 'iTXt') {
      const data = bytes.subarray(dataStart, dataEnd)
      const first = data.indexOf(0)
      if (first > 0 && data[first + 1] === 0) {
        let cursor = first + 3
        const languageEnd = data.indexOf(0, cursor)
        cursor = languageEnd < 0 ? data.length : languageEnd + 1
        const translatedEnd = data.indexOf(0, cursor)
        cursor = translatedEnd < 0 ? data.length : translatedEnd + 1
        chunks.push({
          keyword: decoder.decode(data.subarray(0, first)),
          value: new TextDecoder('utf-8').decode(data.subarray(cursor)),
        })
      }
    }
    offset = dataEnd + 4
    if (type === 'IEND') break
  }
  return chunks
}

export function parseCharacterCardPng(buffer) {
  try {
    const metadata = pngTextChunks(buffer).find((item) => CHARACTER_CARD_PNG_KEYWORDS.has(item.keyword.toLowerCase()))
    if (!metadata) return { ok: false, message: 'PNG 中没有找到酒馆角色卡元数据（chara/ccv3）' }
    return parseCharacterCard(decodeBase64Utf8(metadata.value))
  } catch (error) {
    return { ok: false, message: `PNG 角色卡读取失败：${error.message}` }
  }
}

export async function parseCharacterCardFile(file) {
  if (!file) return { ok: false, message: '没有选择角色卡文件' }
  const name = String(file.name || '').toLowerCase()
  if (name.endsWith('.png') || file.type === 'image/png') {
    if (file.size > 20 * 1024 * 1024) return { ok: false, message: 'PNG 角色卡超过 20 MB，已拦截' }
    return parseCharacterCardPng(await file.arrayBuffer())
  }
  if (file.size > 2 * 1024 * 1024) return { ok: false, message: 'JSON/ST 角色卡超过 2 MB，已拦截' }
  return parseCharacterCard(await file.text())
}

/**
 * 把当前人格草稿导出为兼容 SillyTavern / 酒馆的 Character Card V3 JSON。
 * Mio 自有扩展字段放在 extensions.mio，保证“Mio 导出的卡”能完整还原；
 * 外来酒馆卡则通过通用字段映射导入。
 */
export function buildCharacterCard(draft) {
  const profile = draft || {}
  const identity = profile.identity || {}
  const style = profile.speaking_style || {}
  const preferences = profile.preferences || {}
  const behavior = profile.behavior || {}
  const worldbookRules = asText(behavior.worldbook_rules, 2000)
  return {
    spec: 'chara_card_v3',
    spec_version: '3.0',
    data: {
      name: identity.name || '未命名角色',
      description: identity.core || '',
      personality: style.tone || '',
      scenario: asText(behavior.scenario, 2000),
      first_mes: '',
      mes_example: asText(behavior.dialogue_examples, 2000),
      creator_notes: '',
      system_prompt: asText(behavior.character_rules, 2000),
      post_history_instructions: asText(behavior.reply_rules, 2000),
      alternate_greetings: [],
      character_book: worldbookRules
        ? {
            name: `${identity.name || '角色'}世界书`,
            entries: [{
              id: 0,
              comment: '从 Mio 导出的世界书规则',
              keys: [],
              content: worldbookRules,
              enabled: true,
              constant: true,
            }],
          }
        : null,
      tags: ['mio-agent'],
      creator: '',
      character_version: '1.0',
      extensions: {
        mio: {
          version: profile.version || 1,
          age_feel: identity.age_feel || '',
          bubble_style: style.bubble_style || '',
          avoid: Array.isArray(style.avoid) ? style.avoid.slice() : [],
          relationship_distance: preferences.relationship_distance || '',
          user_address: preferences.user_address || '',
          custom_notes: Array.isArray(preferences.custom_notes) ? preferences.custom_notes.slice() : [],
          behavior: JSON.parse(JSON.stringify(behavior || {})),
        },
      },
    },
  }
}

export function safeCardFileName(name) {
  const cleaned = String(name || '未命名角色')
    .replace(/[\\/:*?"<>|\r\n\t]+/g, ' ')
    .trim()
    .slice(0, 60)
  return cleaned || '未命名角色'
}

export function parseCharacterCard(raw) {
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { ok: false, message: '文件不是有效的 JSON' }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, message: '角色卡内容不是对象' }
  }
  const standaloneBook = mappedWorldBook(parsed)
  if (standaloneBook && !parsed.data && !parsed.description && !parsed.personality) {
    return { ok: true, message: '', mapped: standaloneBook }
  }
  const data = parsed.data || parsed
  if (!data || typeof data !== 'object') {
    return { ok: false, message: '找不到角色卡主体（data）' }
  }
  if (parsed.data === undefined && parsed.spec !== undefined) {
    return { ok: false, message: '角色卡缺少 data 主体' }
  }
  const extension = data.extensions && typeof data.extensions.mio === 'object' ? data.extensions.mio : null
  const importedBehavior = extension?.behavior && typeof extension.behavior === 'object'
    ? JSON.parse(JSON.stringify(extension.behavior))
    : {}
  const behaviorMappings = {
    scenario: asText(data.scenario, 2000),
    character_rules: asText(data.system_prompt, 2000),
    reply_rules: asText(data.post_history_instructions, 2000),
    dialogue_examples: asText(data.mes_example, 2000),
  }
  for (const [key, value] of Object.entries(behaviorMappings)) {
    if (value) importedBehavior[key] = value
  }
  const embeddedBook = mappedWorldBook(data.character_book)
  if (embeddedBook?.behavior?.worldbook_rules && !importedBehavior.worldbook_rules) {
    importedBehavior.worldbook_rules = embeddedBook.behavior.worldbook_rules
  }
  const importedNotes = Array.isArray(extension?.custom_notes)
    ? extension.custom_notes.map((item) => asText(item, 2000)).filter(Boolean)
    : []
  const creatorNotes = asText(data.creator_notes, 1800)
  if (creatorNotes) importedNotes.push(`创作者备注：${creatorNotes}`)
  const firstMessage = asText(data.first_mes, 1800)
  if (firstMessage) importedNotes.push(`角色卡开场白：${firstMessage}`)
  return {
    ok: true,
    message: '',
    mapped: {
      name: String(extension?.name ?? data.name ?? '').slice(0, 80),
      core: String(extension?.core ?? data.description ?? '').slice(0, 2000),
      tone: String(extension?.tone ?? data.personality ?? '').slice(0, 3000),
      ageFeel: String(extension?.age_feel ?? '').slice(0, 300),
      bubble: String(extension?.bubble_style ?? '').slice(0, 3000),
      avoid: Array.isArray(extension?.avoid)
        ? extension.avoid.map((item) => String(item).slice(0, 500))
        : [],
      relationship: String(extension?.relationship_distance ?? '').slice(0, 2000),
      userAddress: String(extension?.user_address ?? '').slice(0, 1000),
      notes: importedNotes.slice(0, 30),
      behavior: Object.keys(importedBehavior).length ? importedBehavior : null,
      importKind: 'character',
      importedRuleCount: embeddedBook?.importedRuleCount || 0,
    },
  }
}

export function fillMappedIntoDraft(profile, mapped) {
  if (!profile) return
  if (mapped.name) profile.identity.name = mapped.name
  if (mapped.ageFeel) profile.identity.age_feel = mapped.ageFeel
  if (mapped.core) profile.identity.core = mapped.core
  if (mapped.tone) profile.speaking_style.tone = mapped.tone
  if (mapped.bubble) profile.speaking_style.bubble_style = mapped.bubble
  if (mapped.avoid?.length) profile.speaking_style.avoid = mapped.avoid.slice()
  if (mapped.relationship) profile.preferences.relationship_distance = mapped.relationship
  if (mapped.userAddress) profile.preferences.user_address = mapped.userAddress
  if (mapped.notes?.length) profile.preferences.custom_notes = mapped.notes.slice()
  if (mapped.behavior) profile.behavior = { ...(profile.behavior || {}), ...mapped.behavior }
}
