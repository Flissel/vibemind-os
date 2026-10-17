// VibeMind-Einbettung (Spec vibemind-os 2026-10-17-rowboat-einbettung): wohin jeder Push-Kanal
// im Einbettungsmodus geht. Ein neuer Push-Kanal in ipc.ts laesst embed-channels.test.ts scheitern,
// bis er hier eingeordnet ist.
//
// mirrored – an alle Fenster gesendet (broadcastToWindows, spaces:events, power:caffeinateChanged);
//            geht zusaetzlich an jede Einbettungs-Verbindung. turns:events: durable Ereignisse hier,
//            die Text-Deltas kommen absenderbezogen ueber turns:subscribe.
// sender   – absenderbezogen (event.sender.send); erreicht die Einbettung ueber ihren eigenen Absender.
// window   – gehoert zu Rowboats eigenem Fenster (Menue, Quick-Ask, PTT, Deeplinks, Meeting-Popup,
//            Updater, Browser-Bereich, Video-Popout) und geht nie an die Einbettung.

export const EMBED_MIRRORED_PUSH = [
  'workspace:didChange', 'runs:events', 'codeRun:events', 'sessions:events', 'turns:events',
  'services:events', 'live-note-agent:events', 'bg-task-agent:events', 'todo:events',
  'home:threadsChanged', 'oauth:didConnect', 'chatgpt:statusChanged', 'codeSession:status',
  'terminal:data', 'terminal:exit', 'power:caffeinateChanged', 'channels:status',
  'composio:didConnect', 'knowledge:didCommit', 'credits:didActivate', 'spaces:events',
] as const

export const EMBED_SENDER_PUSH = ['codeMode:engineProgress', 'apps:progress', 'voice:tts-chunk'] as const

export const EMBED_WINDOW_PUSH = [
  'app:openUrl', 'app:takeMeetingNotes', 'app:toggleMeetingNotes', 'updater:status',
  'menu:command', 'menu:toggleSidebar', 'shortcuts:switcherKey', 'meeting:externalCallEnded',
  'voice:ptt-key', 'quick-ask:submit', 'quick-ask:mode', 'quick-ask:tuck', 'quick-ask:cursor',
  'quick-ask:dragging', 'quick-ask:open-chat', 'quick-ask:chat-context', 'quick-ask:select-chat',
  'quick-ask:new-chat', 'quick-ask:shortcut-changed', 'theme:changed', 'meetingDetect:payload',
  'video:popout-state', 'video:popout-levels', 'video:popout-action', 'screen-pointer:state',
  'browser:didUpdateState', 'browser:httpAuthRequest', 'browser:httpAuthResolved',
  'browser:displayMediaRequest', 'browser:displayMediaResolved',
] as const

/** Aufrufe, die ein eigenes Rowboat-Fenster brauchen und eingebettet `embed_unsupported` liefern. */
export const EMBED_UNSUPPORTED_INVOKE_PREFIXES = ['browser:', 'video:'] as const

const mirrored: ReadonlySet<string> = new Set(EMBED_MIRRORED_PUSH)

export function isEmbedMirrored(channel: string): boolean {
  return mirrored.has(channel)
}

export function isEmbedUnsupportedInvoke(channel: string): boolean {
  return EMBED_UNSUPPORTED_INVOKE_PREFIXES.some((prefix) => channel.startsWith(prefix))
}
