// VibeMind: Integrationen (OpenFang), Teilprojekt 2. Spec vibemind-os
// docs/superpowers/specs/2026-10-15-integrationen-rowboat-design.md
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { IntegrationCard } from './integration-card'
import { kartenModell, type Knopf } from './zustand'

type Status = Awaited<ReturnType<typeof window.ipc.invoke<'integrations:status'>>>
const SCHNELL_MS = 3_000
const LANGSAM_MS = 10_000
const SCHNELL_DAUER_MS = 120_000
const SCHLUESSEL_HINWEIS = 'Rowboat hat keinen gültigen OpenFang-Schlüssel – Rowboat über start_desktop.py neu starten'

type Rueckfrage = { art: 'abmelden' | 'entfernen'; id: string; name: string } | null
const KANAL = {
  anmelden: 'integrations:anmelden', abmelden: 'integrations:abmelden', schluessel: 'integrations:schluessel',
  neuVerbinden: 'integrations:neuVerbinden', entfernen: 'integrations:entfernen',
} as const

export function IntegrationsPage({ visible }: { visible: boolean }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [laufend, setLaufend] = useState<Set<string>>(new Set())
  const [rueckfrage, setRueckfrage] = useState<Rueckfrage>(null)
  // Erhoeht nach erfolgreicher Browser-Aktion: startet die Abfrage neu, damit die erste
  // schnelle Runde nicht hinter einem laufenden 10-s-Timer wartet.
  const [neustart, setNeustart] = useState(0)
  const schnellBis = useRef(0)
  const laufendRef = useRef<Set<string>>(new Set())

  const laden = useCallback(async () => {
    try { setStatus(await window.ipc.invoke('integrations:status', null)) }
    catch { setStatus({ verbindung: 'openfang_aus', installiert: [], verfuegbar: [] }) }
  }, [])

  useEffect(() => {
    if (!visible) return
    let aus = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const runde = async () => {
      await laden()
      if (aus) return
      timer = setTimeout(runde, Date.now() < schnellBis.current ? SCHNELL_MS : LANGSAM_MS)
    }
    void runde()
    return () => { aus = true; if (timer) clearTimeout(timer) }
  }, [visible, laden, neustart])

  const ausfuehren = useCallback(async (schluessel: string, f: () => Promise<{ ok: boolean; meldung?: string }>, browser: boolean) => {
    if (laufendRef.current.has(schluessel)) return
    laufendRef.current.add(schluessel)
    setLaufend(new Set(laufendRef.current))
    try {
      const r = await f()
      if (!r.ok) toast.error(r.meldung ?? 'Aktion fehlgeschlagen')
      if (r.ok && browser) {
        schnellBis.current = Date.now() + SCHNELL_DAUER_MS
        setNeustart((n) => n + 1) // Effekt laedt sofort neu und plant die naechste Runde in 3 s
      } else {
        await laden()
      }
    } catch {
      toast.error('Aktion fehlgeschlagen')
    } finally {
      laufendRef.current.delete(schluessel)
      setLaufend(new Set(laufendRef.current))
    }
  }, [laden])

  const knopf = (id: string, name: string, k: Knopf) => {
    if (k === 'abmelden' || k === 'entfernen') { setRueckfrage({ art: k, id, name }); return }
    void ausfuehren(id, () => window.ipc.invoke(KANAL[k], { id }), k === 'anmelden' || k === 'schluessel')
  }

  const verbindung = status?.verbindung
  const ausgegraut = verbindung !== 'ok'
  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-4xl px-8 py-10 flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold">Integrationen</h1>
          <p className="text-sm text-muted-foreground">Anbieter-Anbindungen über OpenFang – für alle Agenten, die sie freigeschaltet haben.</p>
        </div>
        {verbindung === 'openfang_aus' && <div role="alert" className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm">OpenFang läuft nicht (127.0.0.1:4200)</div>}
        {(verbindung === 'schluessel_fehlt' || verbindung === 'schluessel_falsch') && <div role="alert" className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">{SCHLUESSEL_HINWEIS}</div>}

        {verbindung === 'ok' && <>
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">Installiert</h2>
          {status?.installiert.length === 0 && <p className="text-sm text-muted-foreground">Noch keine Integration installiert.</p>}
          <div className="grid gap-3 sm:grid-cols-2">
            {status?.installiert.map((i) => (
              <IntegrationCard key={i.id} name={i.name} icon={i.icon} modell={kartenModell(i)} offeneFreigaben={i.offeneFreigaben}
                gesperrt={laufend.has(i.id)} ausgegraut={ausgegraut}
                onKnopf={(k) => knopf(i.id, i.name, k)}
                onFreigaben={() => void ausfuehren('freigaben', () => window.ipc.invoke('integrations:freigabenOeffnen', null), false)} />
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">Verfügbar</h2>
          {status?.verfuegbar.length === 0 && <p className="text-sm text-muted-foreground">Keine weiteren Vorlagen verfügbar.</p>}
          {status?.verfuegbar.map((v) => (
            <div key={v.id} className={`flex items-center gap-3 rounded-lg border border-border p-3 ${ausgegraut ? 'opacity-50' : ''}`}>
              <span aria-hidden>{v.icon}</span>
              <div className="flex-1"><div className="font-medium">{v.name}</div><div className="text-xs text-muted-foreground">{v.beschreibung}</div></div>
              <button type="button" aria-label={`${v.name} hinzufügen`} disabled={laufend.has(v.id) || ausgegraut}
                className="rounded-md border border-border px-3 py-1 text-sm hover:bg-accent disabled:opacity-50"
                onClick={() => void ausfuehren(v.id, () => window.ipc.invoke('integrations:add', { id: v.id }), true)}>Hinzufügen</button>
            </div>
          ))}
        </section>
        </>}
      </div>

      <AlertDialog open={rueckfrage !== null} onOpenChange={(o) => { if (!o) setRueckfrage(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{rueckfrage?.art === 'entfernen' ? `${rueckfrage?.name} entfernen?` : `${rueckfrage?.name} abmelden?`}</AlertDialogTitle>
            <AlertDialogDescription>
              {rueckfrage?.art === 'entfernen'
                ? 'Die Integration wird getrennt und deinstalliert. Agenten verlieren ihre Werkzeuge.'
                : 'Der Zugang wird widerrufen und aus dem Tresor gelöscht. Danach ist eine neue Anmeldung nötig.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Abbrechen</AlertDialogCancel>
            <AlertDialogAction onClick={() => {
              const r = rueckfrage
              setRueckfrage(null)
              if (r) void ausfuehren(r.id, () => window.ipc.invoke(KANAL[r.art], { id: r.id }), false)
            }}>{rueckfrage?.art === 'entfernen' ? 'Entfernen' : 'Abmelden'}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
