/** Coordinate local audio owners so music never masks a call or voice reply. */
const players = new Map()
const paused = new Map()
const owners = new Set()
const remoteOwners = new Map()
const senderId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
let channel = null, heartbeat = null, revision = 0, listenersInstalled = false
const ownerLeaseMs = 90000

function hasOwners() {
  return owners.size > 0 || [...remoteOwners.values()].some(item => item.owners.length)
}

function updatePlayback(wasOwned) {
  const owned = hasOwners()
  if (!wasOwned && owned) pauseMusic('', true)
  else if (wasOwned && !owned) resumeMusic()
}

function publishOwners() {
  try { channel?.postMessage({ type: 'owners', sender: senderId, revision: ++revision, owners: [...owners] }) } catch (_) {}
}

function ensureWindowChannel() {
  if (channel || typeof window === 'undefined' || typeof window.BroadcastChannel !== 'function') return
  try { channel = new window.BroadcastChannel('mio-audio-owners-v1') } catch (_) { return }
  channel.onmessage = event => {
    const value = event.data
    if (!value || value.sender === senderId) return
    if (value.type === 'hello') { publishOwners(); return }
    if (value.type !== 'owners' || typeof value.sender !== 'string' || !Number.isFinite(value.revision)
      || !Array.isArray(value.owners) || value.owners.length > 64 || !value.owners.every(owner => typeof owner === 'string')) return
    const previous = remoteOwners.get(value.sender)
    if (previous && value.revision <= previous.revision) return
    const wasOwned = hasOwners()
    remoteOwners.set(value.sender, { owners: value.owners, revision: value.revision, receivedAt: performance.now() })
    updatePlayback(wasOwned)
  }
  heartbeat = setInterval(() => {
    const wasOwned = hasOwners(), now = performance.now()
    for (const [sender, value] of remoteOwners) if (now - value.receivedAt > ownerLeaseMs) remoteOwners.delete(sender)
    updatePlayback(wasOwned)
    if (owners.size) publishOwners()
  }, 15000)
  if (!listenersInstalled) {
    listenersInstalled = true
    window.addEventListener('pagehide', () => {
      owners.clear(); publishOwners()
      clearInterval(heartbeat); heartbeat = null
      try { channel?.close() } catch (_) {}
      channel = null; remoteOwners.clear(); paused.clear()
    })
    window.addEventListener('pageshow', () => { if (players.size || owners.size) ensureWindowChannel() })
    window.addEventListener('focus', () => { if (owners.size) publishOwners() })
  }
  try { channel.postMessage({ type: 'hello', sender: senderId }) } catch (_) {}
  publishOwners()
}

function pauseMusic(except = '', remember = false) {
  for (const [id, audio] of players) {
    if (id === except || audio.paused) continue
    if (remember) paused.set(id, audio.currentTime)
    audio.pause()
  }
}

function resumeMusic() {
  for (const [id, time] of paused) {
    const audio = players.get(id)
    if (!audio) continue
    try { audio.currentTime = time } catch (_) {}
    void audio.play().catch(() => {})
  }
  paused.clear()
}

export function registerMusicPlayer(id, audio) {
  ensureWindowChannel()
  const key = String(id)
  players.set(key, audio)
  return () => {
    audio.pause()
    if (players.get(key) === audio) { players.delete(key); paused.delete(key) }
  }
}

export function musicStarted(id) {
  if (hasOwners()) {
    players.get(String(id))?.pause()
    return false
  }
  pauseMusic(String(id))
  return true
}

export function acquireAudioOwner(nextOwner) {
  const value = String(nextOwner || '')
  if (!value) return
  ensureWindowChannel()
  const wasOwned = hasOwners()
  owners.add(value)
  updatePlayback(wasOwned)
  publishOwners()
}

export function releaseAudioOwner(releasedOwner) {
  const wasOwned = hasOwners()
  if (!owners.delete(String(releasedOwner || ''))) return
  updatePlayback(wasOwned)
  publishOwners()
}

export function currentAudioOwner() {
  return [...owners].at(-1) || [...remoteOwners.values()].flatMap(value => value.owners).at(-1) || ''
}
