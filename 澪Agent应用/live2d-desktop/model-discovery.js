const fs = require('fs')
const path = require('path')
const crypto = require('crypto')

const RENDER_CACHE_VERSION = 1

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/, ''))
}

function relativeFiles(root, files) {
  return files.map((file) => path.relative(root, file).split(path.sep).join('/'))
}

const MOTION_SLOT_PATTERNS = {
  idle: [/^idle$/i, /idle|wait|stand|breath/i],
  touch: [/^tapbody$/i, /tap|touch|click|body/i],
  think: [/think|ponder|question|wonder|confus/i],
  speak: [/speak|talk|voice|mouth|chat/i],
  observe: [/observe|watch|look|search|scan/i],
  cheerful: [/happy|joy|cheer|smile|laugh|win|victory|success/i],
  concerned: [/sad|worry|concern|trouble|down|lose|defeat/i],
  alert: [/alert|angry|serious|surprise|shock|danger/i],
  attention: [/attention|curious|notice|question|look/i],
  shy: [/shy|blush|embarrass/i],
}

function assignMotionSlots(motionNames) {
  const match = (patterns) => motionNames.find((name) => patterns.some((pattern) => pattern.test(name))) || ''
  const idle = match(MOTION_SLOT_PATTERNS.idle) || motionNames[0] || ''
  const touch = match(MOTION_SLOT_PATTERNS.touch)
    || motionNames.find((name) => name !== idle)
    || idle
  const motionSlots = {
    idle,
    touch,
    think: match(MOTION_SLOT_PATTERNS.think) || idle,
    speak: match(MOTION_SLOT_PATTERNS.speak) || idle,
    observe: match(MOTION_SLOT_PATTERNS.observe) || idle,
    cheerful: match(MOTION_SLOT_PATTERNS.cheerful) || touch || idle,
    concerned: match(MOTION_SLOT_PATTERNS.concerned) || idle,
    alert: match(MOTION_SLOT_PATTERNS.alert) || touch || idle,
    attention: match(MOTION_SLOT_PATTERNS.attention) || idle,
    shy: match(MOTION_SLOT_PATTERNS.shy) || idle,
  }
  const assigned = new Set(Object.values(motionSlots).filter(Boolean))
  return {
    motionSlots,
    unassignedMotions: motionNames.filter((name) => !assigned.has(name)),
  }
}

function modelCapabilities(sourceRoot, sourceModel, allFiles) {
  const document = readJson(sourceModel)
  const references = document.FileReferences || {}
  const motionEntries = references.Motions && typeof references.Motions === 'object'
    ? Object.entries(references.Motions)
    : []
  const motions = motionEntries.map(([name, entries]) => ({
    name: String(name),
    count: Array.isArray(entries) ? entries.length : 0,
    files: Array.isArray(entries)
      ? entries.map((item) => String(item?.File || '')).filter(Boolean)
      : [],
  }))
  const expressions = Array.isArray(references.Expressions)
    ? references.Expressions.map((entry, index) => ({
        name: String(entry?.Name || `Expression${index + 1}`),
        file: String(entry?.File || ''),
      })).filter((entry) => entry.file)
    : []
  const groups = Array.isArray(document.Groups) ? document.Groups : []
  const parameterGroup = (name) => groups
    .filter((group) => String(group?.Name || '').toLowerCase() === name.toLowerCase())
    .flatMap((group) => Array.isArray(group?.Ids) ? group.Ids : [])
    .map(String)
  const motionNames = motions.map((entry) => entry.name)
  const { motionSlots, unassignedMotions } = assignMotionSlots(motionNames)
  const idleGroup = motionSlots.idle
  const tapGroup = motionSlots.touch
  const licenseFiles = relativeFiles(
    sourceRoot,
    allFiles.filter((file) => /(^|[\\/])(licen[sc]e|copying|notice)(\.|$|[-_])/i.test(file)).slice(0, 20),
  )
  return {
    format: String(document.Version || 'Cubism 4'),
    motions,
    expressions,
    physics: Boolean(references.Physics),
    physicsFile: String(references.Physics || ''),
    pose: Boolean(references.Pose),
    poseFile: String(references.Pose || ''),
    displayInfo: Boolean(references.DisplayInfo),
    lipSyncParameters: parameterGroup('LipSync'),
    eyeBlinkParameters: parameterGroup('EyeBlink'),
    idleGroup,
    tapGroup,
    motionSlots,
    unassignedMotions,
    licenseFiles,
  }
}

function registerUnlistedExpressions(sourceRoot, sourceModel, allFiles) {
  const document = readJson(sourceModel)
  const references = document.FileReferences || (document.FileReferences = {})
  const expressions = Array.isArray(references.Expressions) ? [...references.Expressions] : []
  const modelRoot = path.dirname(sourceModel)
  const knownFiles = new Set(expressions.map((item) => String(item?.File || '').replaceAll('\\', '/').toLowerCase()))
  const knownNames = new Set(expressions.map((item) => String(item?.Name || '').toLowerCase()))
  let changed = false
  for (const file of allFiles.filter((item) => item.toLowerCase().endsWith('.exp3.json')).sort()) {
    const relative = path.relative(modelRoot, file).split(path.sep).join('/')
    if (relative.startsWith('../') || knownFiles.has(relative.toLowerCase())) continue
    const baseName = path.basename(file).replace(/\.exp3\.json$/i, '') || 'Expression'
    let name = baseName
    let suffix = 2
    while (knownNames.has(name.toLowerCase())) name = `${baseName}-${suffix++}`
    expressions.push({ Name: name, File: relative })
    knownFiles.add(relative.toLowerCase())
    knownNames.add(name.toLowerCase())
    changed = true
  }
  if (changed) {
    references.Expressions = expressions
    fs.writeFileSync(sourceModel, `${JSON.stringify(document, null, 2)}\n`, 'utf8')
  }
  return changed
}

function previewCandidate(sourceRoot, allFiles) {
  const suffixes = new Set(['.png', '.jpg', '.jpeg', '.webp'])
  const candidates = allFiles.flatMap((file) => {
    if (!suffixes.has(path.extname(file).toLowerCase())) return []
    const relative = path.relative(sourceRoot, file)
    const folders = path.dirname(relative).split(path.sep).map((item) => item.toLowerCase())
    if (folders.some((item) => item.includes('texture') || item.includes('贴图'))) return []
    const filename = path.basename(file).toLowerCase()
    const stem = path.basename(file, path.extname(file)).toLowerCase()
    const exact = /^preview\.(png|jpe?g|webp)$/.test(filename)
    const keyword = ['preview', 'cover', 'icon', 'avatar', 'thumbnail', '立绘', '头像', '封面']
      .some((word) => stem.includes(word))
    return [{ file, rank: exact ? 0 : keyword ? 1 : 2, size: fs.statSync(file).size, depth: relative.split(path.sep).length }]
  })
  candidates.sort((left, right) => left.rank - right.rank || right.size - left.size || left.depth - right.depth)
  return candidates[0]?.file || ''
}

function fileSignature(root, filePath) {
  const stat = fs.statSync(filePath)
  return {
    path: path.relative(root, filePath).split(path.sep).join('/'),
    size: stat.size,
    modifiedMs: Math.round(stat.mtimeMs),
  }
}

function renderOptimizedModel(modelRoot, sourceModel, nativeImage, maxTextureSize = 2048) {
  const resolvedRoot = path.resolve(modelRoot)
  const resolvedModel = path.resolve(sourceModel)
  if (!resolvedModel.startsWith(`${resolvedRoot}${path.sep}`)) throw new Error('model_outside_root')
  const document = readJson(resolvedModel)
  const references = document.FileReferences || {}
  const textures = Array.isArray(references.Textures) ? references.Textures.map(String) : []
  if (!textures.length) {
    return { modelPath: resolvedModel, optimized: false, optimizedTextureCount: 0, maxTextureSize }
  }

  const modelParent = path.dirname(resolvedModel)
  const texturePaths = textures.map((entry) => {
    const candidate = path.resolve(modelParent, entry)
    if (!candidate.startsWith(`${resolvedRoot}${path.sep}`) || !fs.statSync(candidate).isFile()) {
      throw new Error(`invalid_texture:${entry}`)
    }
    return candidate
  })
  const cacheKey = crypto
    .createHash('sha256')
    .update(path.relative(resolvedRoot, resolvedModel))
    .digest('hex')
    .slice(0, 12)
  const cacheRoot = path.join(resolvedRoot, '.mio-render-cache', cacheKey)
  const manifestPath = path.join(cacheRoot, 'manifest.json')
  const generatedModel = path.join(
    modelParent,
    `.${path.basename(resolvedModel, '.model3.json')}.mio-render-${maxTextureSize}.model3.json`,
  )
  const signature = {
    version: RENDER_CACHE_VERSION,
    maxTextureSize,
    model: fileSignature(resolvedRoot, resolvedModel),
    textures: texturePaths.map((filePath) => fileSignature(resolvedRoot, filePath)),
  }

  try {
    const manifest = readJson(manifestPath)
    const outputsReady = (manifest.outputTextures || []).every((entry) => (
      fs.existsSync(path.join(cacheRoot, String(entry)))
    ))
    const modelReady = !manifest.optimizedTextureCount || fs.existsSync(generatedModel)
    if (JSON.stringify(manifest.signature) === JSON.stringify(signature) && outputsReady && modelReady) {
      return {
        modelPath: manifest.optimizedTextureCount ? generatedModel : resolvedModel,
        optimized: Boolean(manifest.optimizedTextureCount),
        optimizedTextureCount: Number(manifest.optimizedTextureCount || 0),
        maxTextureSize,
        sourceTextureMaxSize: Number(manifest.sourceTextureMaxSize || 0),
      }
    }
  } catch (_) {}

  fs.mkdirSync(cacheRoot, { recursive: true })
  const optimizedTextures = [...textures]
  const outputTextures = []
  let optimizedTextureCount = 0
  let sourceTextureMaxSize = 0
  texturePaths.forEach((texturePath, index) => {
    const image = nativeImage.createFromPath(texturePath)
    if (!image || image.isEmpty()) throw new Error(`texture_decode_failed:${textures[index]}`)
    const size = image.getSize()
    const largestSide = Math.max(Number(size.width || 0), Number(size.height || 0))
    sourceTextureMaxSize = Math.max(sourceTextureMaxSize, largestSide)
    if (largestSide <= maxTextureSize) return
    const scale = maxTextureSize / largestSide
    const resized = image.resize({
      width: Math.max(1, Math.round(size.width * scale)),
      height: Math.max(1, Math.round(size.height * scale)),
      quality: 'best',
    })
    const outputName = `${index}.png`
    const outputPath = path.join(cacheRoot, outputName)
    fs.writeFileSync(outputPath, resized.toPNG())
    optimizedTextures[index] = path.relative(modelParent, outputPath).split(path.sep).join('/')
    outputTextures.push(outputName)
    optimizedTextureCount += 1
  })

  if (optimizedTextureCount) {
    references.Textures = optimizedTextures
    fs.writeFileSync(generatedModel, `${JSON.stringify(document, null, 2)}\n`, 'utf8')
  }
  fs.writeFileSync(manifestPath, `${JSON.stringify({
    signature,
    optimizedTextureCount,
    sourceTextureMaxSize,
    outputTextures,
  }, null, 2)}\n`, 'utf8')
  return {
    modelPath: optimizedTextureCount ? generatedModel : resolvedModel,
    optimized: Boolean(optimizedTextureCount),
    optimizedTextureCount,
    maxTextureSize,
    sourceTextureMaxSize,
  }
}

module.exports = {
  assignMotionSlots,
  modelCapabilities,
  previewCandidate,
  registerUnlistedExpressions,
  renderOptimizedModel,
}
