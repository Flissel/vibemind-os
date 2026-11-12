'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { AnmeldeSchutz } from '@/components/AnmeldeSchutz'
import { useIch } from '@/components/IchKontext'
import { Meldung } from '@/components/Meldung'
import { SchemaFeld } from '@/components/formular/SchemaFeld'
import { api } from '@/lib/api'
import { KERN, bausteinName } from '@/lib/begriffe'
import { darfAnlegen } from '@/lib/rechte'
import type { EventBasis, Feld, Firma, Kategorie, WirksamesSchema } from '@/lib/typen'
import { ausEingabe } from '@/lib/zeit'

const EINGABE =
  'w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-gray-900 focus:outline-none'
const BESCHRIFTUNG = 'mb-1 block text-sm font-medium text-gray-700'
const KNOPF =
  'rounded-lg bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-40'
const KNOPF_ZURUECK =
  'rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50'
const KARTE =
  'rounded-xl bg-white p-4 text-left shadow-md shadow-gray-300/25 outline outline-1 outline-neutral-200/40 transition-all duration-300 hover:scale-[1.01]'

type Schritt = 1 | 2 | 3
type Metadaten = Record<string, unknown>

function zusaetze(s: WirksamesSchema | undefined): string {
  if (!s) return ''
  const namen = s.bausteine.filter((b) => !KERN.includes(b)).map((b) => bausteinName(b, s.begriffe))
  return namen.length > 0 ? `+ ${namen.join(', ')}` : ''
}

/** Beginn + 2 h auf der Wandzeit (`YYYY-MM-DDTHH:mm`), ohne Zeitzonenrechnung. */
function plusZweiStunden(eingabe: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(eingabe)
  if (!m) return ''
  const t = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]) + 2, Number(m[5])))
  return t.toISOString().slice(0, 16)
}

function vorgaben(felder: Feld[]): Metadaten {
  const m: Metadaten = {}
  for (const f of felder) if (f.vorgabe !== null && f.vorgabe !== undefined) m[f.id] = f.vorgabe
  return m
}

function gefuellt(wert: unknown): boolean {
  return wert !== undefined && wert !== null && !(typeof wert === 'string' && wert.trim() === '')
}

function Anlegen() {
  const ich = useIch()
  const router = useRouter()
  const [schritt, setSchritt] = useState<Schritt>(1)
  const [kategorien, setKategorien] = useState<Kategorie[] | null>(null)
  const [schemata, setSchemata] = useState<Record<string, WirksamesSchema>>({})
  const [ladefehler, setLadefehler] = useState<unknown>(null)
  const [firmen, setFirmen] = useState<Firma[]>(
    ich.betreiber ? [] : ich.firmen.filter((f) => ich.rechte.event_anlegen.includes(f.id)),
  )
  const [kategorie, setKategorie] = useState('')
  const [firmaId, setFirmaId] = useState(
    !ich.betreiber && ich.rechte.event_anlegen.length === 1 ? (ich.rechte.event_anlegen[0] ?? '') : '',
  )
  const [name, setName] = useState('')
  const [beginn, setBeginn] = useState('')
  const [ende, setEnde] = useState('')
  const [endeGeaendert, setEndeGeaendert] = useState(false)
  const [ortAdresse, setOrtAdresse] = useState('')
  const [ortLink, setOrtLink] = useState('')
  const [beschreibung, setBeschreibung] = useState('')
  const [metadaten, setMetadaten] = useState<Metadaten>({})
  const [sendet, setSendet] = useState(false)
  const [sendefehler, setSendefehler] = useState<unknown>(null)

  useEffect(() => {
    let aktiv = true
    async function laden() {
      const liste = await api.holen<Kategorie[]>('/api/kategorien')
      // Ein fehlgeschlagenes Schema lässt nur die Zusätze der Karte weg.
      const ergebnisse = await Promise.allSettled(
        liste.map((k) => api.holen<WirksamesSchema>(`/api/kategorien/${k.id}/schema`)),
      )
      if (!aktiv) return
      const geladen: Record<string, WirksamesSchema> = {}
      ergebnisse.forEach((r, i) => {
        const k = liste[i]
        if (k && r.status === 'fulfilled') geladen[k.id] = r.value
      })
      setSchemata(geladen)
      setKategorien(liste)
    }
    laden().catch((e: unknown) => {
      if (aktiv) setLadefehler(e)
    })
    return () => {
      aktiv = false
    }
  }, [])

  useEffect(() => {
    if (!ich.betreiber) return
    let aktiv = true
    api
      .holen<Firma[]>('/api/firmen')
      .then((liste) => {
        if (!aktiv) return
        setFirmen(liste)
        if (liste.length === 1) setFirmaId(liste[0]?.id ?? '')
      })
      .catch((e: unknown) => {
        if (aktiv) setLadefehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [ich.betreiber])

  const schema: WirksamesSchema | undefined = schemata[kategorie]
  const pflicht = useMemo(() => new Set<string>(schema?.eckdaten_pflicht ?? []), [schema])
  const felder = schema?.felder ?? []
  const stern = (feldname: string) => (pflicht.has(feldname) ? ' *' : '')

  function waehle(id: string) {
    setKategorie(id)
    setMetadaten(vorgaben(schemata[id]?.felder ?? []))
    setSendefehler(null)
    setSchritt(2)
  }

  function beginnSetzen(wert: string) {
    setBeginn(wert)
    if (wert && !endeGeaendert) setEnde(plusZweiStunden(wert))
  }

  function eckdatenOk(): boolean {
    if (!firmaId || !name.trim() || !beginn || !ende) return false
    if (ende < beginn) return false
    if (pflicht.has('ort_adresse') && !ortAdresse.trim()) return false
    if (pflicht.has('ort_link') && !ortLink.trim()) return false
    if (pflicht.has('beschreibung') && !beschreibung.trim()) return false
    return felder.every((f) => !f.pflicht || f.typ === 'ja_nein' || gefuellt(metadaten[f.id]))
  }

  async function anlegen() {
    setSendefehler(null)
    setSendet(true)
    const meta: Metadaten = {}
    for (const f of felder) {
      const w = metadaten[f.id]
      if (f.typ === 'ja_nein') {
        if (w !== undefined || f.pflicht) meta[f.id] = w === true
      } else if (gefuellt(w)) {
        meta[f.id] = w
      }
    }
    try {
      const neu = await api.senden<EventBasis>('POST', '/api/events', {
        firma_id: firmaId,
        kategorie,
        name: name.trim(),
        beginn: ausEingabe(beginn),
        ende: ausEingabe(ende),
        ort_adresse: ortAdresse.trim() || undefined,
        ort_link: ortLink.trim() || undefined,
        beschreibung: beschreibung.trim() || undefined,
        metadaten: meta,
      })
      router.push(`/events/${neu.id}`)
    } catch (e: unknown) {
      setSendefehler(e)
      // Alle Eingaben (und damit jeder 422) liegen in Schritt 2; dort steht die Meldung.
      setSchritt(2)
    } finally {
      setSendet(false)
    }
  }

  const schrittTitel = ['Kategorie wählen', 'Eckdaten', 'Telegram-Gruppe verbinden (optional)']

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <h1 className="text-2xl font-bold text-gray-900">Neues Event</h1>
      <ol className="flex flex-wrap gap-4 text-sm font-medium text-gray-500">
        {schrittTitel.map((t, i) => (
          <li
            key={t}
            aria-current={schritt === i + 1 ? 'step' : undefined}
            className={schritt === i + 1 ? 'text-gray-900' : ''}
          >
            {i + 1}. {t}
          </li>
        ))}
      </ol>
      <Meldung fehler={ladefehler} />

      {schritt === 1 && kategorien && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 md:grid-cols-3">
          {kategorien.map((k) => (
            <button key={k.id} type="button" onClick={() => waehle(k.id)} className={KARTE}>
              <span className="line-clamp-1 block text-base font-bold text-gray-900">{k.name}</span>
              <span className="mt-1 block text-sm text-gray-500">{zusaetze(schemata[k.id])}</span>
            </button>
          ))}
        </div>
      )}

      {schritt === 2 && (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (!eckdatenOk()) return
            setSendefehler(null)
            setSchritt(3)
          }}
        >
          <p className="text-sm text-gray-500">Kategorie: {schema?.name}</p>
          <div>
            <label htmlFor="e-firma" className={BESCHRIFTUNG}>
              Firma *
            </label>
            {firmen.length === 1 ? (
              <p id="e-firma" className="text-sm text-gray-900">
                {firmen[0]?.name}
              </p>
            ) : (
              <select
                id="e-firma"
                value={firmaId}
                onChange={(e) => setFirmaId(e.target.value)}
                className={EINGABE}
              >
                <option value="">–</option>
                {firmen.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div>
            <label htmlFor="e-name" className={BESCHRIFTUNG}>
              Name *
            </label>
            <input id="e-name" value={name} onChange={(e) => setName(e.target.value)} className={EINGABE} />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="e-beginn" className={BESCHRIFTUNG}>
                Beginn *
              </label>
              <input
                id="e-beginn"
                type="datetime-local"
                value={beginn}
                onChange={(e) => beginnSetzen(e.target.value)}
                className={EINGABE}
              />
            </div>
            <div>
              <label htmlFor="e-ende" className={BESCHRIFTUNG}>
                Ende *
              </label>
              <input
                id="e-ende"
                type="datetime-local"
                value={ende}
                onChange={(e) => {
                  setEndeGeaendert(true)
                  setEnde(e.target.value)
                }}
                className={EINGABE}
              />
            </div>
          </div>
          {ende && beginn && ende < beginn && <Meldung text="Das Ende liegt vor dem Beginn." />}
          <div>
            <label htmlFor="e-ort" className={BESCHRIFTUNG}>
              Ort (Adresse){stern('ort_adresse')}
            </label>
            <input
              id="e-ort"
              value={ortAdresse}
              onChange={(e) => setOrtAdresse(e.target.value)}
              className={EINGABE}
            />
          </div>
          <div>
            <label htmlFor="e-link" className={BESCHRIFTUNG}>
              Online-Link{stern('ort_link')}
            </label>
            <input
              id="e-link"
              type="url"
              value={ortLink}
              onChange={(e) => setOrtLink(e.target.value)}
              className={EINGABE}
            />
          </div>
          <div>
            <label htmlFor="e-beschreibung" className={BESCHRIFTUNG}>
              Beschreibung{stern('beschreibung')}
            </label>
            <textarea
              id="e-beschreibung"
              rows={4}
              value={beschreibung}
              onChange={(e) => setBeschreibung(e.target.value)}
              className={EINGABE}
            />
          </div>
          {felder.map((f) => (
            <SchemaFeld
              key={f.id}
              feld={f}
              wert={metadaten[f.id]}
              aendern={(w) => setMetadaten((m) => ({ ...m, [f.id]: w }))}
            />
          ))}
          <Meldung fehler={sendefehler} />
          <div className="flex gap-3">
            <button type="button" className={KNOPF_ZURUECK} onClick={() => setSchritt(1)}>
              Zurück
            </button>
            <button type="submit" className={KNOPF} disabled={!eckdatenOk()}>
              Weiter
            </button>
          </div>
        </form>
      )}

      {schritt === 3 && (
        <div className="space-y-4">
          <h2 className="text-lg font-bold text-gray-900">Telegram-Gruppe verbinden (optional)</h2>
          <p className="text-sm text-gray-600">
            Kommt mit dem Bot-Ausbau. Du kannst die Gruppe später auf der Event-Seite verbinden.
          </p>
          <div className="flex gap-3">
            <button type="button" className={KNOPF_ZURUECK} onClick={() => setSchritt(2)}>
              Zurück
            </button>
            <button type="button" className={KNOPF} disabled={sendet} onClick={() => void anlegen()}>
              Event anlegen
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function Berechtigt() {
  const ich = useIch()
  if (!darfAnlegen(ich)) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-6">
        <Meldung text="Du darfst keine Events anlegen." />
      </div>
    )
  }
  return <Anlegen />
}

export default function EventNeu() {
  return (
    <AnmeldeSchutz>
      <Berechtigt />
    </AnmeldeSchutz>
  )
}
