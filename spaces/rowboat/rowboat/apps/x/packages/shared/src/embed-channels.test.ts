import { describe, it, expect } from 'vitest'
import { ipcSchemas, isPushChannel } from './ipc.js'
import { PUSH_CHANNELS } from './push-channels.js'
import {
  EMBED_MIRRORED_PUSH, EMBED_SENDER_PUSH, EMBED_WINDOW_PUSH,
  isEmbedMirrored, isEmbedUnsupportedInvoke,
} from './embed-channels.js'

const allPush = Object.keys(ipcSchemas)
  .filter((c) => isPushChannel(c as keyof typeof ipcSchemas))
  .sort()

describe('Einbettung: Push-Kanaele', () => {
  it('jeder Push-Kanal steht in genau einer Gruppe (neuer Kanal = Test rot, bitte einordnen)', () => {
    const all = [...EMBED_MIRRORED_PUSH, ...EMBED_SENDER_PUSH, ...EMBED_WINDOW_PUSH]
    expect(new Set(all).size).toBe(all.length)
    expect([...all].sort()).toEqual(allPush)
  })
  it('alle Broadcast-Kanaele des rowboat-server erreichen die Einbettung', () => {
    for (const c of PUSH_CHANNELS) {
      expect([...EMBED_MIRRORED_PUSH, ...EMBED_SENDER_PUSH]).toContain(c)
    }
  })
  it('isEmbedMirrored nur fuer gespiegelte Kanaele', () => {
    expect(isEmbedMirrored('runs:events')).toBe(true)
    expect(isEmbedMirrored('spaces:events')).toBe(true)
    expect(isEmbedMirrored('voice:tts-chunk')).toBe(false)
    expect(isEmbedMirrored('quick-ask:submit')).toBe(false)
    expect(isEmbedMirrored('gibt-es-nicht')).toBe(false)
  })
  it('Browser-Bereich und Video-Popout sind eingebettet nicht verfuegbar', () => {
    expect(isEmbedUnsupportedInvoke('browser:setVisible')).toBe(true)
    expect(isEmbedUnsupportedInvoke('video:setPopout')).toBe(true)
    expect(isEmbedUnsupportedInvoke('workspace:readFile')).toBe(false)
    expect(isEmbedUnsupportedInvoke('app:getVersions')).toBe(false)
  })
})
