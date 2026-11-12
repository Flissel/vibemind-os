'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Bestaetigen } from '@/components/Bestaetigen'
import { useIch } from '@/components/IchKontext'
import { Meldung } from '@/components/Meldung'
import { SchemaFeld } from '@/components/formular/SchemaFeld'
import { api } from '@/lib/api'
import { darfVerwalten } from '@/lib/rechte'
import type { EventDetail, EventStatus } from '@/lib/typen'
import { alsEingabe, ausEingabe } from '@/lib/zeit'

const EINGABE =
  'w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-gray-900 focus:outline-none'
const BESCHRIFTUNG = 'mb-1 block text-sm font-medium text-gray-700'
const KNOPF =
  'rounded-lg bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-40'
const KNOPF_RAND =
  'rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40'
const KNOPF_ROT =
  'rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-40'

type Eckdaten = 'name' | 'beginn' | 'ende' | 'ort_adresse' | 'ort_link' | 'beschreibung'
type Bearbeitung = Partial<Record<Eckdaten, string>>
type Bestaetigung = 'absagen' | 'archivieren' | 'loeschen'

const ALLTAG: { status: EventStatus; text: string }[] = [
  { status: 'geplant', text: 'Geplant' },
  { status: 'laeuft', text: 'Läuft' },
  { status: 'abgeschlossen', text: 'Abgeschlossen' },
]

function ausgangswerte(e: EventDetail): Record<Eckdaten, string> {
  return {
    name: e.name,
    beginn: alsEingabe(e.beginn),
    ende: alsEingabe(e.ende),
    ort_adresse: e.ort_adresse ?? '',
    ort_link: e.ort_link ?? '',
    beschreibung: e.beschreibung ?? '',
  }
}

function leer(wert: unknown): boolean {
  return wert === undefined || wert === null || (typeof wert === 'string' && wert === '')
}

interface Props {
  event: EventDetail
  onAktualisiert: (e: EventDetail) => void
}

export function UebersichtReiter({ event, onAktualisiert }: Props) {
  const ich = useIch()
  const router = useRouter()
  const verwalten = darfVerwalten(ich, event.firma_id)
  const [eckEdit, setEckEdit] = useState<Bearbeitung>({})
  const [metaEdit, setMetaEdit] = useState<Record<string, unknown>>({})
  const [laeuft, setLaeuft] = useState(false)
  const [fehler, setFehler] = useState<unknown>(null)
  const [erfolg, setErfolg] = useState('')
  const [dialog, setDialog] = useState<Bestaetigung | null>(null)

  const basis = ausgangswerte(event)
  const wert = (k: Eckdaten): string => eckEdit[k] ?? basis[k]

  // Nur geänderte Felder gehen in den PATCH; die Metadaten als Ganzes, sobald eines abweicht.
  const aenderung: Record<string, unknown> = {}
  for (const k of Object.keys(eckEdit) as Eckdaten[]) {
    const neu = wert(k)
    if (neu === basis[k]) continue
    if (k === 'beginn' || k === 'ende') {
      if (neu !== '') aenderung[k] = ausEingabe(neu)
    } else if (k === 'name') {
      aenderung[k] = neu.trim()
    } else {
      aenderung[k] = neu.trim() === '' ? null : neu.trim()
    }
  }
  const meta: Record<string, unknown> = { ...event.metadaten }
  let metaGeaendert = false
  for (const [k, w] of Object.entries(metaEdit)) {
    if (JSON.stringify(w ?? null) === JSON.stringify(meta[k] ?? null)) continue
    if (leer(w)) delete meta[k]
    else meta[k] = w
    metaGeaendert = true
  }
  if (metaGeaendert) aenderung.metadaten = meta

  const geaendert = Object.keys(aenderung).length > 0
  const pflichtOk = wert('name').trim() !== '' && wert('beginn') !== '' && wert('ende') !== ''
  const zeitOk = wert('ende') >= wert('beginn')

  async function speichern() {
    setFehler(null)
    setErfolg('')
    setLaeuft(true)
    try {
      const neu = await api.senden<EventDetail>('PATCH', `/api/events/${event.id}`, aenderung)
      onAktualisiert({ ...event, ...neu, schema: event.schema })
      setEckEdit({})
      setMetaEdit({})
      setErfolg('Gespeichert')
    } catch (e: unknown) {
      setFehler(e)
    } finally {
      setLaeuft(false)
    }
  }

  async function statusSetzen(status: EventStatus) {
    setDialog(null)
    setFehler(null)
    setErfolg('')
    setLaeuft(true)
    try {
      const neu = await api.senden<EventDetail>('POST', `/api/events/${event.id}/status`, { status })
      onAktualisiert({ ...event, ...neu, schema: event.schema })
    } catch (e: unknown) {
      setFehler(e)
    } finally {
      setLaeuft(false)
    }
  }

  async function loeschen() {
    setFehler(null)
    setLaeuft(true)
    try {
      await api.senden('DELETE', `/api/events/${event.id}`)
      router.push('/')
    } catch (e: unknown) {
      setDialog(null)
      setFehler(e)
      setLaeuft(false)
    }
  }

  // Aus abgesagt/archiviert führt jeder Wechsel über das Leitungsrecht (R3).
  const beendet = event.status === 'abgesagt' || event.status === 'archiviert'

  return (
    <div className="space-y-8">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (geaendert && pflichtOk && zeitOk) void speichern()
        }}
      >
        <h2 className="text-lg font-bold text-gray-900">Eckdaten</h2>
        <div>
          <label htmlFor="u-name" className={BESCHRIFTUNG}>
            Name *
          </label>
          <input
            id="u-name"
            value={wert('name')}
            onChange={(e) => setEckEdit((m) => ({ ...m, name: e.target.value }))}
            className={EINGABE}
          />
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="u-beginn" className={BESCHRIFTUNG}>
              Beginn *
            </label>
            <input
              id="u-beginn"
              type="datetime-local"
              value={wert('beginn')}
              onChange={(e) => setEckEdit((m) => ({ ...m, beginn: e.target.value }))}
              className={EINGABE}
            />
          </div>
          <div>
            <label htmlFor="u-ende" className={BESCHRIFTUNG}>
              Ende *
            </label>
            <input
              id="u-ende"
              type="datetime-local"
              value={wert('ende')}
              onChange={(e) => setEckEdit((m) => ({ ...m, ende: e.target.value }))}
              className={EINGABE}
            />
          </div>
        </div>
        {!zeitOk && <Meldung text="Das Ende liegt vor dem Beginn." />}
        <div>
          <label htmlFor="u-ort" className={BESCHRIFTUNG}>
            Ort (Adresse)
          </label>
          <input
            id="u-ort"
            value={wert('ort_adresse')}
            onChange={(e) => setEckEdit((m) => ({ ...m, ort_adresse: e.target.value }))}
            className={EINGABE}
          />
        </div>
        <div>
          <label htmlFor="u-link" className={BESCHRIFTUNG}>
            Online-Link
          </label>
          <input
            id="u-link"
            type="url"
            value={wert('ort_link')}
            onChange={(e) => setEckEdit((m) => ({ ...m, ort_link: e.target.value }))}
            className={EINGABE}
          />
        </div>
        <div>
          <label htmlFor="u-beschreibung" className={BESCHRIFTUNG}>
            Beschreibung
          </label>
          <textarea
            id="u-beschreibung"
            rows={4}
            value={wert('beschreibung')}
            onChange={(e) => setEckEdit((m) => ({ ...m, beschreibung: e.target.value }))}
            className={EINGABE}
          />
        </div>
        {event.schema.felder.map((f) => (
          <SchemaFeld
            key={f.id}
            feld={f}
            wert={f.id in metaEdit ? metaEdit[f.id] : event.metadaten[f.id]}
            aendern={(w) => setMetaEdit((m) => ({ ...m, [f.id]: w }))}
          />
        ))}
        <Meldung fehler={fehler} erfolg={erfolg} />
        <button type="submit" className={KNOPF} disabled={!geaendert || !pflichtOk || !zeitOk || laeuft}>
          Speichern
        </button>
      </form>

      <section className="space-y-3">
        <h2 className="text-lg font-bold text-gray-900">Status</h2>
        <div className="flex flex-wrap gap-2">
          {!beendet &&
            ALLTAG.map((s) => (
              <button
                key={s.status}
                type="button"
                className={KNOPF_RAND}
                disabled={laeuft || event.status === s.status}
                onClick={() => void statusSetzen(s.status)}
              >
                {s.text}
              </button>
            ))}
          {verwalten && !beendet && (
            <>
              <button type="button" className={KNOPF_RAND} disabled={laeuft} onClick={() => setDialog('absagen')}>
                Absagen
              </button>
              <button
                type="button"
                className={KNOPF_RAND}
                disabled={laeuft}
                onClick={() => setDialog('archivieren')}
              >
                Archivieren
              </button>
            </>
          )}
          {verwalten && beendet && (
            <button
              type="button"
              className={KNOPF_RAND}
              disabled={laeuft}
              onClick={() => void statusSetzen('geplant')}
            >
              Wieder aufnehmen
            </button>
          )}
        </div>
      </section>

      {verwalten && (
        <section className="space-y-3">
          <h2 className="text-lg font-bold text-gray-900">Gefahrenbereich</h2>
          <button type="button" className={KNOPF_ROT} disabled={laeuft} onClick={() => setDialog('loeschen')}>
            Event löschen
          </button>
        </section>
      )}

      {dialog === 'absagen' && (
        <Bestaetigen
          titel="Event absagen?"
          text="Das Event wird als abgesagt markiert. Du kannst es später wieder aufnehmen."
          aktion="Absagen bestätigen"
          gefaehrlich
          laeuft={laeuft}
          onBestaetigen={() => void statusSetzen('abgesagt')}
          onAbbrechen={() => setDialog(null)}
        />
      )}
      {dialog === 'archivieren' && (
        <Bestaetigen
          titel="Event archivieren?"
          text="Das Event wird archiviert. Du kannst es später wieder aufnehmen."
          aktion="Archivieren bestätigen"
          laeuft={laeuft}
          onBestaetigen={() => void statusSetzen('archiviert')}
          onAbbrechen={() => setDialog(null)}
        />
      )}
      {dialog === 'loeschen' && (
        <Bestaetigen
          titel="Event löschen?"
          text="Das Event und alle seine Daten werden unwiderruflich gelöscht."
          aktion="Endgültig löschen"
          namePruefen={event.name}
          gefaehrlich
          laeuft={laeuft}
          onBestaetigen={() => void loeschen()}
          onAbbrechen={() => setDialog(null)}
        />
      )}
    </div>
  )
}
