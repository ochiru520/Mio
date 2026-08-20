const assert = require('assert')
const fs = require('fs')
const os = require('os')
const path = require('path')

const { modelCapabilities, renderOptimizedModel } = require('../model-discovery')

function walkFiles(root) {
  const output = []
  const pending = [root]
  while (pending.length) {
    const current = pending.pop()
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolute = path.join(current, entry.name)
      if (entry.isDirectory()) pending.push(absolute)
      else output.push(absolute)
    }
  }
  return output
}

const root = path.resolve(__dirname, '..', '..', 'public', 'live2d-pet', 'models', 'hiyori')
const modelPath = path.join(root, 'Hiyori.model3.json')
const capabilities = modelCapabilities(root, modelPath, walkFiles(root))

assert.strictEqual(capabilities.idleGroup, 'Idle')
assert.strictEqual(capabilities.tapGroup, 'TapBody')
assert.strictEqual(capabilities.motionSlots.idle, 'Idle')
assert.strictEqual(capabilities.motionSlots.touch, 'TapBody')
assert.strictEqual(capabilities.motionSlots.speak, 'Idle')
assert.deepStrictEqual(capabilities.unassignedMotions, [])
assert.strictEqual(capabilities.physics, true)
assert.strictEqual(capabilities.pose, true)
assert.deepStrictEqual(capabilities.motions.map((entry) => entry.name), ['Idle', 'TapBody'])
assert.ok(capabilities.lipSyncParameters.includes('ParamMouthOpenY'))
assert.ok(capabilities.eyeBlinkParameters.includes('ParamEyeLOpen'))

const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mio-live2d-optimization-'))
try {
  const texturePath = path.join(temporaryRoot, 'texture.png')
  const importedModelPath = path.join(temporaryRoot, 'sample.model3.json')
  fs.writeFileSync(texturePath, Buffer.from('original-texture'))
  fs.writeFileSync(importedModelPath, JSON.stringify({
    Version: 3,
    FileReferences: {
      Moc: 'sample.moc3',
      Textures: ['texture.png'],
      Physics: 'sample.physics3.json',
    },
  }), 'utf8')
  let decodeCount = 0
  const nativeImage = {
    createFromPath: (filePath) => {
      assert.strictEqual(filePath, texturePath)
      decodeCount += 1
      return {
        isEmpty: () => false,
        getSize: () => ({ width: 4096, height: 2048 }),
        resize: (options) => {
          assert.deepStrictEqual(options, { width: 2048, height: 1024, quality: 'best' })
          return { toPNG: () => Buffer.from('optimized-texture') }
        },
      }
    },
  }
  const first = renderOptimizedModel(temporaryRoot, importedModelPath, nativeImage, 2048)
  const second = renderOptimizedModel(temporaryRoot, importedModelPath, nativeImage, 2048)
  const generated = JSON.parse(fs.readFileSync(first.modelPath, 'utf8'))
  assert.strictEqual(first.optimized, true)
  assert.strictEqual(first.optimizedTextureCount, 1)
  assert.strictEqual(first.sourceTextureMaxSize, 4096)
  assert.strictEqual(first.modelPath, second.modelPath)
  assert.strictEqual(decodeCount, 1)
  assert.strictEqual(generated.FileReferences.Moc, 'sample.moc3')
  assert.strictEqual(generated.FileReferences.Physics, 'sample.physics3.json')
  assert.match(generated.FileReferences.Textures[0], /^\.mio-render-cache\//)
  assert.strictEqual(fs.readFileSync(texturePath, 'utf8'), 'original-texture')
} finally {
  const resolvedTemp = path.resolve(temporaryRoot)
  const allowedRoot = `${path.resolve(os.tmpdir())}${path.sep}`
  if (!resolvedTemp.startsWith(allowedRoot)) throw new Error('unsafe_test_cleanup_path')
  fs.rmSync(resolvedTemp, { recursive: true, force: true })
}

console.log(JSON.stringify({ ok: true, capabilities }, null, 2))
