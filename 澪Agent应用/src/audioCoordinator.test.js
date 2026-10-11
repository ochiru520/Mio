import test from 'node:test'
import assert from 'node:assert/strict'
import { acquireAudioOwner, currentAudioOwner, musicStarted, registerMusicPlayer, releaseAudioOwner } from './audioCoordinator.js'

function audio() {
  return { paused: false, currentTime: 12, pause() { this.paused = true }, play() { this.paused = false; return Promise.resolve() } }
}

test('audio owner pauses music and restores its position after release', async () => {
  const player = audio()
  const cleanup = registerMusicPlayer('track', player)
  acquireAudioOwner('call')
  assert.equal(currentAudioOwner(), 'call')
  assert.equal(player.paused, true)
  player.currentTime = 0
  releaseAudioOwner('call')
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(currentAudioOwner(), '')
  assert.equal(player.currentTime, 12)
  assert.equal(player.paused, false)
  cleanup()
})

test('music cannot start while a voice owner is active', () => {
  const player = audio()
  const cleanup = registerMusicPlayer('blocked', player)
  acquireAudioOwner('voice')
  player.paused = false
  assert.equal(musicStarted('blocked'), false)
  assert.equal(player.paused, true)
  releaseAudioOwner('voice')
  cleanup()
})

test('changing tracks does not resurrect the old track after voice playback', () => {
  const first = audio(), second = audio()
  second.paused = true
  const cleanupFirst = registerMusicPlayer('first', first), cleanupSecond = registerMusicPlayer('second', second)
  second.paused = false
  musicStarted('second')
  assert.equal(first.paused, true)
  acquireAudioOwner('voice')
  releaseAudioOwner('voice')
  assert.equal(first.paused, true)
  assert.equal(second.paused, false)
  cleanupFirst(); cleanupSecond()
})

test('music waits until both call and message voice release their ownership', () => {
  const player = audio(), cleanup = registerMusicPlayer('overlap', player)
  acquireAudioOwner('call'); acquireAudioOwner('message')
  releaseAudioOwner('call')
  assert.equal(player.paused, true)
  assert.equal(currentAudioOwner(), 'message')
  releaseAudioOwner('message')
  assert.equal(player.paused, false)
  cleanup()
})
