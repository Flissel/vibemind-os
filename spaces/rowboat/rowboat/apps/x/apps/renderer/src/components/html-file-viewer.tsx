import { useFileViewerSource } from './file-viewer-source'
import { LegacyHtmlFileViewer } from './html-file-viewer-legacy'
import { useEffect, useMemo, useRef, useState } from 'react'
import { konzept } from '@x/shared'
import { LOCAL_RUNTIME } from '@/lib/feature-flags'
import {
  canAcceptRequest,
  canConfirm,
  eventTime,
  guestAvailable,
  guestElementProps,
  guestMountTimeoutMs,
  guestNoticeText,
  guestSrc,
  konzeptHinweise,
  linkOpenRequest,
  nextPendingRequest,
  noteOpenRequest,
  reloadDebounceMs,
  workspaceEventTouches,
  type Armed,
  type Engaged,
  type PendingRequest,
} from '@/lib/konzept-flaeche'
import { AlertCircleIcon, FileTextIcon, Loader2Icon } from 'lucide-react'

const MAX_SIZE_BYTES = 5 * 1024 * 1024
/** An existence check that has not settled after this long releases the bridge; its late result is discarded. */
const EXISTENCE_CHECK_TIMEOUT_MS = 5000

type ViewerState =
  | { kind: 'loading' }
  | { kind: 'loaded' }
  | { kind: 'empty' }
  | { kind: 'tooLarge'; sizeMB: number }
  | { kind: 'error'; message: string }

interface HtmlFileViewerProps {
  path: string
  /** False while the persistent viewer cache keeps this viewer mounted but hidden. */
  active?: boolean
}

/**
 * The Flaeche (header, hints, reload, message bridge, confirmation strip) applies only to HTML under
 * knowledge/ in the desktop app. Everything else (web build, Spaces documents, local previews,
 * background-task reports) keeps the view as it was before: LegacyHtmlFileViewer, untouched.
 */
export function HtmlFileViewer({ path, active = true }: HtmlFileViewerProps) {
  const source = useFileViewerSource()
  if (!LOCAL_RUNTIME || !source.workspace || !konzept.isKnowledgeHtmlPath(path)) return <LegacyHtmlFileViewer path={path} />
  return <FlaecheViewer path={path} active={active} />
}

function FlaecheViewer({ path, active }: { path: string; active: boolean }) {
  const source = useFileViewerSource()
  const [state, setState] = useState<ViewerState>({ kind: 'loading' })
  // Reload = remount the guest (new key + new ?v=n). Never change src or call loadURL/reload on a live guest:
  // the main process treats every navigation other than the attached document as an attack and kills the guest.
  const [version, setVersion] = useState(0)
  // Guest lifecycle, each tied to the mount it happened in (`path#version`), so a remount starts clean:
  // which mount has loaded, and which mount was ended or refused by the main process.
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const [gone, setGone] = useState<{ key: string; kind: 'gone' | 'timeout' } | null>(null)
  const [stale, setStale] = useState(false)
  const [fileText, setFileText] = useState<string | null>(null)
  const [fileSize, setFileSize] = useState(0)
  const [pending, setPending] = useState<PendingRequest | null>(null)
  const [armed, setArmed] = useState<Armed | null>(null)
  const [engaged, setEngaged] = useState<Engaged | null>(null)
  // Mirrors of state for the message listener (no stale closures), a request id counter, the token of a
  // running existence check (null = none), the Date.now of the last "Abbrechen" (null = none since the last
  // reload / path change), and a generation that changes when the frame is remounted or hidden.
  const pendingRef = useRef<PendingRequest | null>(null)
  pendingRef.current = pending
  const requestIdRef = useRef(0)
  const checkInFlightRef = useRef<object | null>(null)
  const cancelledAtRef = useRef<number | null>(null)
  const generationRef = useRef(0)
  const guestRef = useRef<HTMLElement | null>(null)
  const lastPathRef = useRef<string | null>(null)
  const guestOk = guestAvailable(window.featureFlags?.konzeptGast, LOCAL_RUNTIME)
  const guestKey = `${path}#${version}`
  const guestUrl = useMemo(() => (guestOk ? guestSrc(path, version) : null), [guestOk, path, version])
  const goneKind = gone?.key === guestKey ? gone.kind : null
  const guestLoaded = loadedKey === guestKey
  const guestMounted = state.kind === 'loaded' && guestUrl !== null && goneKind === null

  useEffect(() => {
    let cancelled = false
    // A reload of the same file keeps the current view (no spinner flash); a new path starts fresh.
    const isReload = lastPathRef.current === path
    lastPathRef.current = path
    if (!isReload) {
      generationRef.current += 1
      cancelledAtRef.current = null
      setPending(null)
      setState({ kind: 'loading' })
      setGone(null)
      setLoadedKey(null)
      setVersion(0)
      setFileText(null)
    }

    ;(async () => {
      try {
        const stat = await source.stat({ path })
        if (cancelled) return
        if (stat.kind !== 'file') {
          setState({ kind: 'error', message: 'Selected path is not a file.' })
          return
        }
        setFileSize(stat.size)
        if (stat.size > MAX_SIZE_BYTES) {
          setState({ kind: 'tooLarge', sizeMB: stat.size / (1024 * 1024) })
          return
        }
        if (stat.size === 0) {
          setState({ kind: 'empty' })
          return
        }
        // Title and hints come from the file text, read here once per (re)load -- never from inside the frame.
        // (The read API has no length limit, so a reload re-reads up to 5 MB.)
        try {
          const doc = await source.read({ path, encoding: 'utf8' })
          if (cancelled) return
          setFileText(doc.data)
        } catch {
          if (cancelled) return
          setFileText(null)
        }
        setState({ kind: 'loaded' })
      } catch (err) {
        if (cancelled) return
        const message = err instanceof Error ? err.message : String(err)
        setState({ kind: 'error', message })
      }
    })()

    return () => {
      cancelled = true
    }
  }, [path, source, version])

  // Changes to this file (workspace watcher, or the agent's write via rowboat:html-touched) are
  // bundled over reloadDebounceMs. A hidden (cached) viewer only marks itself stale and reloads
  // when it is shown again.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    const schedule = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        timer = null
        setStale(true)
      }, reloadDebounceMs)
    }
    const onTouched = (e: Event) => {
      const detail = e instanceof CustomEvent ? (e.detail as unknown) : null
      if (typeof detail === 'object' && detail !== null && (detail as { path?: unknown }).path === path) schedule()
    }
    window.addEventListener('rowboat:html-touched', onTouched)
    let stop: (() => void) | undefined
    if (source.subscribe) {
      stop = source.subscribe(schedule)
    } else if (typeof window.ipc?.on === 'function') {
      stop = window.ipc.on('workspace:didChange', (event) => {
        if (workspaceEventTouches(event, path)) schedule()
      })
    }
    return () => {
      if (timer) clearTimeout(timer)
      window.removeEventListener('rowboat:html-touched', onTouched)
      stop?.()
    }
  }, [path, source])

  useEffect(() => {
    if (!stale || !active) return
    setStale(false)
    generationRef.current += 1
    cancelledAtRef.current = null
    setPending((p) => nextPendingRequest(p, { type: 'reset' }))
    setVersion((v) => v + 1)
  }, [stale, active])

  // A hidden viewer shows no confirmation strip and has no listener (below).
  useEffect(() => {
    if (!active) {
      generationRef.current += 1
      setPending((p) => nextPendingRequest(p, { type: 'reset' }))
    }
  }, [active])

  // Lifecycle of this mount's guest element (React's synthetic events do not cover <webview> events).
  // `dom-ready` / `did-stop-loading` = loaded. `close` / `render-process-gone` / `destroyed` = the main
  // process ended or refused the guest: the element is removed (guestMounted turns false), a pending
  // confirmation is reset, and a notice with "Neu laden" appears. A refused attach never fires dom-ready,
  // so 5 s without a load event count as "cannot be shown". Each viewer handles only its own element.
  useEffect(() => {
    const el = guestRef.current
    if (!guestMounted || !el) return
    let ready = false
    const onLoaded = () => {
      ready = true
      setLoadedKey(guestKey)
    }
    // Every removal path (event or timeout) discards a pending confirmation and late existence checks.
    const end = (kind: 'gone' | 'timeout') => {
      generationRef.current += 1
      setPending((p) => nextPendingRequest(p, { type: 'reset' }))
      setGone({ key: guestKey, kind })
    }
    const onGone = () => end('gone')
    const timer = setTimeout(() => {
      if (!ready) end('timeout')
    }, guestMountTimeoutMs)
    el.addEventListener('dom-ready', onLoaded)
    el.addEventListener('did-stop-loading', onLoaded)
    el.addEventListener('close', onGone)
    el.addEventListener('render-process-gone', onGone)
    el.addEventListener('destroyed', onGone)
    return () => {
      clearTimeout(timer)
      el.removeEventListener('dom-ready', onLoaded)
      el.removeEventListener('did-stop-loading', onLoaded)
      el.removeEventListener('close', onGone)
      el.removeEventListener('render-process-gone', onGone)
      el.removeEventListener('destroyed', onGone)
    }
  }, [guestMounted, guestKey])

  // Requests from the page arrive as `ipc-message` events of the guest element (channel 'konzept', exactly
  // one argument), forwarded by the guest preload and validated again here. Neither a web link nor a note
  // opens on a message: both only show the confirmation in the header row, and only a click on its "Öffnen"
  // button opens them. At most one request at a time: while one is pending (younger than 10 s) or an
  // existence check runs, further messages are ignored, not queued; for 10 s after "Abbrechen" every
  // request is ignored. One listener per visible viewer; a hidden viewer has none.
  useEffect(() => {
    const el = guestRef.current
    if (!active || !guestMounted || !el) return
    const onMessage = (e: Event) => {
      const ev = e as Event & { channel?: unknown; args?: unknown }
      if (ev.channel !== konzept.KONZEPT_GAST_KANAL) return
      if (!Array.isArray(ev.args) || ev.args.length !== 1) return
      const msg = konzept.forwardableGuestMessage(ev.args[0])
      if (!msg) return
      const link = linkOpenRequest(msg)
      const note = link ? null : noteOpenRequest(msg)
      if (!link && !note) return
      if (!canAcceptRequest(pendingRef.current, checkInFlightRef.current !== null, Date.now(), cancelledAtRef.current)) return
      if (link) {
        const id = ++requestIdRef.current
        setPending((p) => nextPendingRequest(p, { type: 'request', id, kind: 'link', value: link, now: Date.now() }))
        return
      }
      if (!note) return
      const generation = generationRef.current
      // The flag is always released: on settle, on a synchronous throw (turned into a rejection), or after
      // EXISTENCE_CHECK_TIMEOUT_MS -- then the generation is bumped so a late result is discarded. The token
      // keeps a late settle from releasing a newer check.
      const check = {}
      checkInFlightRef.current = check
      const release = () => {
        if (checkInFlightRef.current === check) checkInFlightRef.current = null
      }
      const timeout = setTimeout(() => {
        if (checkInFlightRef.current !== check) return
        generationRef.current += 1
        release()
      }, EXISTENCE_CHECK_TIMEOUT_MS)
      Promise.resolve()
        .then(() => source.stat({ path: note }))
        .then((st) => {
          // Discard if the guest was remounted/gone, the viewer hidden or the check timed out meanwhile.
          if (generation !== generationRef.current || st.kind !== 'file') return
          const id = ++requestIdRef.current
          setPending((p) => nextPendingRequest(p, { type: 'request', id, kind: 'note', value: note, now: Date.now() }))
        })
        .catch(() => {})
        .finally(() => {
          clearTimeout(timeout)
          release()
        })
    }
    el.addEventListener('ipc-message', onMessage)
    return () => el.removeEventListener('ipc-message', onMessage)
  }, [active, guestMounted, guestKey, source])

  // The lock of "Öffnen" starts when the confirmation row of THIS request has actually been painted
  // (double requestAnimationFrame after the commit), measured with performance.now(). A page that
  // floods the parent with messages can delay the paint but not shorten the lock.
  const pendingId = pending?.id
  useEffect(() => {
    if (pendingId === undefined) return
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        setArmed({ id: pendingId, armedAt: performance.now() })
      })
    })
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
    }
  }, [pendingId])
  // After the lock, "Öffnen" also needs the user to steer to it (pointer enters it, or keyboard focus with
  // :focus-visible). Every such moment is recorded; canConfirm only counts one after armedAt + confirmDelayMs,
  // so a cursor already resting where the button appeared must leave and come back. Recording it re-renders,
  // which is what turns the button active (no timer needed). `at` is the event's own time (eventTime), so a
  // stalled main thread cannot move an entry made during the lock behind it.
  const engage = (id: number, at: number) => setEngaged({ id, at })

  const title = useMemo(() => {
    const name = path.split('/').pop() ?? path
    return (fileText ? konzept.htmlTitle(fileText) : null) || name
  }, [fileText, path])
  const hinweise = useMemo(() => (fileText ? konzeptHinweise(fileText, fileSize) : []), [fileText, fileSize])
  const pendingHost = useMemo(() => {
    if (pending?.kind !== 'link') return ''
    try {
      return new URL(pending.value).host
    } catch {
      return ''
    }
  }, [pending])
  // Visual state only; the click handler checks again at click time.
  const canOpen = canConfirm(pending, armed, engaged, performance.now())

  if (state.kind === 'error') {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
        <AlertCircleIcon className="size-6 text-destructive" />
        <p className="text-sm font-medium text-foreground">Could not load preview</p>
        <p className="max-w-md text-xs">{state.message}</p>
        <p className="text-xs opacity-60">{path}</p>
      </div>
    )
  }

  if (state.kind === 'empty') {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <FileTextIcon className="size-6" />
        <p className="text-sm">This file is empty</p>
      </div>
    )
  }

  if (state.kind === 'tooLarge') {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-3 px-6 text-center text-muted-foreground">
        <FileTextIcon className="size-6" />
        <p className="text-sm font-medium text-foreground">File too large to preview</p>
        <p className="text-xs">
          {state.sizeMB.toFixed(1)} MB — preview limit is {(MAX_SIZE_BYTES / (1024 * 1024)).toFixed(0)} MB.
        </p>
        {/* No "Open in system" (R50): it would open knowledge HTML outside every protection, like R39. */}
      </div>
    )
  }

  // Served via `app://workspace/<rel-path>`. For knowledge HTML the main process adds the
  // no-network rule (CSP incl. `sandbox allow-scripts`), so files next to the page do not load
  // either: a concept is one file with inline styles, scripts and data: images.
  return (
    <div className="flex h-full w-full flex-col">
      {/* Fixed-height row: the normal header, or (while a request is pending) the confirmation. The
          height never changes, so the page does not move, and the buttons never sit over page pixels. */}
      {pending ? (
        <div key={pending.id} className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-muted px-3 text-xs text-foreground">
          {/* Only the question: never a host or note name here (it could be clipped into a spoof). The full
              host and address / path are in the detail area below. */}
          <p className="min-w-0 flex-1 overflow-hidden whitespace-nowrap">
            <span className="font-medium">{pending.kind === 'link' ? 'Link öffnen?' : 'Notiz öffnen?'}</span>{' '}
            <span>{pending.kind === 'link' ? '(Adresse unten)' : '(Pfad unten)'}</span>
          </p>
          <button
            type="button"
            // Not `disabled`: a disabled button can neither take keyboard focus nor reliably report the pointer
            // entering it. It is inactive (aria-disabled, greyed, click ignored) until canConfirm allows it.
            aria-disabled={!canOpen}
            title={canOpen ? undefined : 'Zum Öffnen den Mauszeiger neu auf den Knopf bewegen'}
            onPointerEnter={(e) => engage(pending.id, eventTime(e.timeStamp, performance.now()))}
            onFocus={(e) => {
              const at = eventTime(e.timeStamp, performance.now())
              let byKeyboard = false
              try {
                byKeyboard = e.currentTarget.matches(':focus-visible')
              } catch {
                byKeyboard = false
              }
              if (byKeyboard) engage(pending.id, at)
            }}
            onClick={(e) => {
              // Judged at the click's own time, which must lie after the engagement.
              if (!canConfirm(pending, armed, engaged, eventTime(e.timeStamp, performance.now()))) return
              const { kind, value } = pending
              setPending((p) => nextPendingRequest(p, { type: 'open' }))
              if (kind === 'link') {
                // Same path the rest of the renderer uses: the main window-open handler sends it to the system browser.
                window.open(value, '_blank', 'noopener,noreferrer')
              } else {
                window.dispatchEvent(new CustomEvent('rowboat:open-note', { detail: { path: value } }))
              }
            }}
            className={
              canOpen
                ? 'shrink-0 rounded-md border border-border bg-background px-2.5 py-1 font-medium hover:bg-accent'
                : 'shrink-0 cursor-not-allowed rounded-md border border-border bg-background px-2.5 py-1 font-medium opacity-50'
            }
          >
            Öffnen
          </button>
          <button
            type="button"
            onClick={() => {
              // A looping page would re-post at once and keep the header in confirmation mode: quiet for 10 s.
              cancelledAtRef.current = Date.now()
              // A note check started before the cancel must not raise a strip afterwards.
              generationRef.current += 1
              setPending((p) => nextPendingRequest(p, { type: 'cancel' }))
            }}
            className="shrink-0 rounded-md border border-border bg-background px-2.5 py-1 font-medium hover:bg-accent"
          >
            Abbrechen
          </button>
        </div>
      ) : (
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-background px-3">
          <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{title}</p>
          {/* No "open in new window": that path (shell.openPath) shows the file in the system browser,
              outside every protection (R39). */}
          <button
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent('rowboat:show-in-graph', { detail: { path } }))}
            className="shrink-0 rounded-md border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground hover:bg-accent"
          >
            Im Graphen zeigen
          </button>
        </div>
      )}
      {hinweise.length > 0 && (
        <ul className="shrink-0 space-y-0.5 border-b border-border bg-amber-50 px-3 py-1.5 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {hinweise.map((h, i) => (
            <li key={i}>{h}</li>
          ))}
        </ul>
      )}
      <div className="relative min-h-0 flex-1">
        {pending && (
          // Detail overlay at the top of the frame: full address / path, NO clickable control (the
          // buttons live in the header row). It does not take part in the layout, so the page does not move.
          <div className="absolute inset-x-0 top-0 z-10 border-b border-border bg-muted px-3 py-2 text-xs text-foreground shadow-md">
            {pending.kind === 'link' && pendingHost && <p className="break-all text-sm font-semibold">{pendingHost}</p>}
            <p className="max-h-24 overflow-y-auto break-all text-muted-foreground">{pending.value}</p>
          </div>
        )}
        {guestUrl === null ? (
          <GuestNotice text={guestNoticeText('missing')} />
        ) : goneKind !== null ? (
          <GuestNotice
            text={guestNoticeText(goneKind)}
            onReload={() => {
              generationRef.current += 1
              cancelledAtRef.current = null
              setPending((p) => nextPendingRequest(p, { type: 'reset' }))
              setVersion((v) => v + 1)
            }}
          />
        ) : (
          <>
            {guestMounted && (
              // Address and partition only, as strings: the main process forces rights, guest script and session.
              // Explicit flex sizing so the guest fills the pane.
              <webview
                key={guestKey}
                ref={guestRef}
                {...guestElementProps(guestUrl)}
                style={{ display: 'flex', width: '100%', height: '100%', background: '#fff' }}
              />
            )}
            {(state.kind === 'loading' || !guestLoaded) && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-background text-muted-foreground">
                <Loader2Icon className="size-6 animate-spin" />
                <p className="text-sm">Rendering preview…</p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

/** Hinweis statt Seite; mit `onReload` zusätzlich der Knopf "Neu laden" (neues Einhängen mit neuem `?v=n`). */
function GuestNotice({ text, onReload }: { text: string; onReload?: () => void }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-background px-6 text-center text-muted-foreground">
      <AlertCircleIcon className="size-6" />
      <p className="text-sm font-medium text-foreground">{text}</p>
      {onReload && (
        <button
          type="button"
          onClick={onReload}
          className="rounded-md border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground hover:bg-accent"
        >
          Neu laden
        </button>
      )}
    </div>
  )
}
