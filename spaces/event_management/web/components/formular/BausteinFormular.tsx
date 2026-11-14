'use client'

import { useId, useState } from 'react'
import type { FormEvent } from 'react'
import { Meldung } from '@/components/Meldung'
import { api } from '@/lib/api'
import { spaltenName, wertText } from '@/lib/anzeige'
import { alsEuroEingabe, parseEuro } from '@/lib/euro'
import type { BausteinInfo, Kontakt, Zeile } from '@/lib/typen'
import { alsEingabe, ausEingabe } from '@/lib/zeit'

const NEUER_KONTAKT = '__neu__'
const EINGABE =
  'w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-gray-900 focus:outline-none'
const BESCHRIFTUNG = 'mb-1 block text-sm font-medium text-gray-700'
const EINGABEART: Record<string, string> = { ganzzahl: 'number', datumzeit: 'datetime-local', url: 'url' }

export function istCent(spalte: string): boolean {
  return spalte.endsWith('_cent')
}

/** Anzeige von `ref:<eintrag>`: das Feld `titel` oder `name` der Zielzeile. */
export function refName(zeile: Zeile | undefined): string {
  if (!zeile) return '–'
  const wert = zeile.titel ?? zeile.name
  return typeof wert === 'string' ? wert : '–'
}

interface Props {
  info: BausteinInfo
  firmaId: string
  /** Zu bearbeitende Zeile; ohne sie wird angelegt. */
  zeile?: Zeile
  kontakte: Kontakt[]
  /** Zeilen der Ref-Ziele je Eintrag (für `ref:<eintrag>`-Auswahlen). */
  refZeilen: Record<string, Zeile[]>
  onKontaktAngelegt: (kontakt: Kontakt) => void
  /** Wirft bei Fehlern; das Formular zeigt sie an. */
  onSpeichern: (daten: Record<string, unknown>) => Promise<void>
  onAbbrechen: () => void
}

function startwert(typ: string, spalte: string, wert: unknown): string {
  if (wert === null || wert === undefined) return ''
  if (typ === 'datumzeit') return typeof wert === 'string' ? alsEingabe(wert) : ''
  if (istCent(spalte) && typeof wert === 'number') return alsEuroEingabe(wert)
  return typeof wert === 'string' || typeof wert === 'number' ? String(wert) : ''
}

/** Eingabetext -> API-Wert; wirft `Error` mit Hinweistext bei ungültiger Eingabe. */
function umwandeln(typ: string, spalte: string, text: string): unknown {
  const name = spaltenName(spalte)
  if (istCent(spalte)) {
    const cent = parseEuro(text)
    if (cent === null) throw new Error(`${name}: bitte einen Betrag wie 12,50 eingeben`)
    return cent
  }
  if (typ === 'ganzzahl') {
    const zahl = /^\d+$/.test(text) ? Number(text) : NaN
    if (!Number.isSafeInteger(zahl)) throw new Error(`${name}: bitte eine ganze Zahl ab 0 eingeben`)
    return zahl
  }
  if (typ === 'datumzeit') {
    try {
      return ausEingabe(text)
    } catch {
      throw new Error(`${name}: ungültige Zeit`)
    }
  }
  return text
}

/** Formular für eine Zeile eines Baustein-Eintrags; die Eingaben folgen den Spaltentypen. */
export function BausteinFormular({
  info,
  firmaId,
  zeile,
  kontakte,
  refZeilen,
  onKontaktAngelegt,
  onSpeichern,
  onAbbrechen,
}: Props) {
  const basis = useId()
  const [start] = useState<Record<string, string>>(() =>
    Object.fromEntries(info.reihenfolge.map((s) => [s, startwert(info.felder[s] ?? 'text', s, zeile?.[s])])),
  )
  const [werte, setWerte] = useState<Record<string, string>>(start)
  const [fehler, setFehler] = useState<unknown>(null)
  const [hinweis, setHinweis] = useState('')
  const [laeuft, setLaeuft] = useState(false)
  // Inline-Kontakt: für welche Spalte das Mini-Formular offen ist.
  const [neuFuer, setNeuFuer] = useState<string | null>(null)
  const [kName, setKName] = useState('')
  const [kMail, setKMail] = useState('')
  const [kFehler, setKFehler] = useState<string>('')
  const [kApiFehler, setKApiFehler] = useState<unknown>(null)

  function setze(spalte: string, wert: string) {
    setWerte((w) => ({ ...w, [spalte]: wert }))
  }

  async function kontaktAnlegen() {
    setKFehler('')
    setKApiFehler(null)
    const name = kName.trim()
    if (!name) {
      setKFehler('Name fehlt')
      return
    }
    const mail = kMail.trim()
    try {
      const k = await api.senden<Kontakt>('POST', `/api/firmen/${firmaId}/kontakte`, {
        name,
        ...(mail ? { mail } : {}),
      })
      onKontaktAngelegt(k)
      if (neuFuer) setze(neuFuer, k.id)
      setNeuFuer(null)
      setKName('')
      setKMail('')
    } catch (e) {
      setKApiFehler(e)
    }
  }

  async function speichern(e: FormEvent) {
    e.preventDefault()
    setFehler(null)
    setHinweis('')
    const daten: Record<string, unknown> = {}
    try {
      for (const spalte of info.reihenfolge) {
        const typ = info.felder[spalte] ?? 'text'
        const text = (werte[spalte] ?? '').trim()
        const pflicht = info.pflicht.includes(spalte)
        if (text === '' || text === NEUER_KONTAKT) {
          if (pflicht) throw new Error(`${spaltenName(spalte)} ist ein Pflichtfeld`)
          // Beim Ändern wird ein geleertes Feld auf null gesetzt, beim Anlegen weggelassen.
          if (zeile && start[spalte] !== '') daten[spalte] = null
          continue
        }
        if (zeile && text === start[spalte]) continue
        daten[spalte] = umwandeln(typ, spalte, text)
      }
    } catch (err) {
      setHinweis(err instanceof Error ? err.message : 'Ungültige Eingabe')
      return
    }
    if (zeile && Object.keys(daten).length === 0) {
      onAbbrechen()
      return
    }
    setLaeuft(true)
    try {
      await onSpeichern(daten)
    } catch (err) {
      setFehler(err)
      setLaeuft(false)
    }
  }

  function eingabe(spalte: string, typ: string, id: string) {
    const wert = werte[spalte] ?? ''
    if (typ === 'kontakt') {
      return (
        <select
          id={id}
          value={wert}
          onChange={(e) => {
            if (e.target.value === NEUER_KONTAKT) {
              setNeuFuer(spalte)
              setze(spalte, '')
            } else {
              setNeuFuer(null)
              setze(spalte, e.target.value)
            }
          }}
          className={EINGABE}
        >
          <option value="">–</option>
          {kontakte.map((k) => (
            <option key={k.id} value={k.id}>
              {k.name}
            </option>
          ))}
          <option value={NEUER_KONTAKT}>+ Neuer Kontakt …</option>
        </select>
      )
    }
    if (typ.startsWith('ref:')) {
      const ziel = typ.slice('ref:'.length)
      return (
        <select id={id} value={wert} onChange={(e) => setze(spalte, e.target.value)} className={EINGABE}>
          <option value="">–</option>
          {(refZeilen[ziel] ?? []).map((z) => (
            <option key={z.id} value={z.id}>
              {refName(z)}
            </option>
          ))}
        </select>
      )
    }
    if (typ.startsWith('auswahl:')) {
      return (
        <select id={id} value={wert} onChange={(e) => setze(spalte, e.target.value)} className={EINGABE}>
          <option value="">–</option>
          {typ
            .slice('auswahl:'.length)
            .split(',')
            .map((o) => (
              <option key={o} value={o}>
                {wertText(o)}
              </option>
            ))}
        </select>
      )
    }
    if (istCent(spalte)) {
      return (
        <input
          id={id}
          type="text"
          inputMode="decimal"
          placeholder="0,00"
          value={wert}
          onChange={(e) => setze(spalte, e.target.value)}
          className={EINGABE}
        />
      )
    }
    return (
      <input
        id={id}
        type={EINGABEART[typ] ?? 'text'}
        min={typ === 'ganzzahl' ? 0 : undefined}
        value={wert}
        onChange={(e) => setze(spalte, e.target.value)}
        className={EINGABE}
      />
    )
  }

  return (
    <form
      noValidate
      onSubmit={(e) => void speichern(e)}
      className="space-y-3 rounded-xl bg-white p-4 outline outline-1 outline-neutral-200/60"
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {info.reihenfolge.map((spalte) => {
          const typ = info.felder[spalte] ?? 'text'
          const id = `${basis}-${spalte}`
          const marke = spaltenName(spalte) + (info.pflicht.includes(spalte) ? ' *' : '')
          return (
            <div key={spalte}>
              <label htmlFor={id} className={BESCHRIFTUNG}>
                {marke}
              </label>
              {eingabe(spalte, typ, id)}
              {neuFuer === spalte && (
                <div className="mt-2 space-y-2 rounded-lg bg-gray-50 p-3">
                  <div>
                    <label htmlFor={`${id}-kname`} className={BESCHRIFTUNG}>
                      Name
                    </label>
                    <input
                      id={`${id}-kname`}
                      value={kName}
                      onChange={(e) => setKName(e.target.value)}
                      className={EINGABE}
                    />
                  </div>
                  <div>
                    <label htmlFor={`${id}-kmail`} className={BESCHRIFTUNG}>
                      Mail
                    </label>
                    <input
                      id={`${id}-kmail`}
                      type="email"
                      value={kMail}
                      onChange={(e) => setKMail(e.target.value)}
                      className={EINGABE}
                    />
                  </div>
                  <Meldung text={kFehler || undefined} fehler={kApiFehler ?? undefined} />
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => void kontaktAnlegen()}
                      className="rounded-lg bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
                    >
                      Kontakt anlegen
                    </button>
                    <button
                      type="button"
                      onClick={() => setNeuFuer(null)}
                      className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
                    >
                      Abbrechen
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
      <Meldung text={hinweis || undefined} fehler={fehler ?? undefined} />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={laeuft}
          className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
        >
          Speichern
        </button>
        <button
          type="button"
          onClick={onAbbrechen}
          className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Abbrechen
        </button>
      </div>
    </form>
  )
}
