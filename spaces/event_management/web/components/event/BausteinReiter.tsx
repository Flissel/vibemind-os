'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bestaetigen } from '@/components/Bestaetigen'
import { Meldung } from '@/components/Meldung'
import {
  BausteinFormular,
  istCent,
  refName,
  spaltenName,
} from '@/components/formular/BausteinFormular'
import { api } from '@/lib/api'
import { bausteinName } from '@/lib/begriffe'
import { formatEuro } from '@/lib/euro'
import type { BausteineKatalog, BausteinInfo, Kontakt, Zeile } from '@/lib/typen'
import { zeigeDatum, zeigeZeit } from '@/lib/zeit'

interface Props {
  eventId: string
  firmaId: string
  baustein: string
  begriffe: Record<string, string>
  /** Antwort von `GET /api/bausteine`. */
  verzeichnis: BausteineKatalog
}

/** Abschnittsnamen für Einträge, die nicht selbst der Baustein sind. */
const EINTRAG_NAMEN: Record<string, string> = { aufgaben_kategorien: 'Kategorien' }

/** Einträge eines Bausteins: `info.baustein` nennt den Eltern-Baustein, `null` heißt „der Eintrag
 *  ist selbst der Baustein". Der gleichnamige Eintrag steht zuerst. */
function eintraegeVon(verzeichnis: BausteineKatalog, baustein: string): string[] {
  const namen = Object.entries(verzeichnis)
    .filter(([name, info]) => (info.baustein ?? name) === baustein)
    .map(([name]) => name)
  return namen.sort((a, b) => Number(b === baustein) - Number(a === baustein))
}

function refZiele(verzeichnis: BausteineKatalog, eintraege: string[]): string[] {
  const ziele = new Set<string>()
  for (const e of eintraege) {
    for (const typ of Object.values(verzeichnis[e]?.felder ?? {})) {
      if (typ.startsWith('ref:')) ziele.add(typ.slice('ref:'.length))
    }
  }
  return [...ziele]
}

interface Auswahl {
  eintrag: string
  zeile?: Zeile
}

export function BausteinReiter({ eventId, firmaId, baustein, begriffe, verzeichnis }: Props) {
  const eintraege = useMemo(() => eintraegeVon(verzeichnis, baustein), [verzeichnis, baustein])
  const zuLaden = useMemo(
    () => [...new Set([...eintraege, ...refZiele(verzeichnis, eintraege)])],
    [verzeichnis, eintraege],
  )
  const braucheKontakte = eintraege.some((e) =>
    Object.values(verzeichnis[e]?.felder ?? {}).includes('kontakt'),
  )

  const [zeilen, setZeilen] = useState<Record<string, Zeile[]>>({})
  const [kontakte, setKontakte] = useState<Kontakt[]>([])
  const [ladeFehler, setLadeFehler] = useState<unknown>(null)
  const [geladen, setGeladen] = useState(false)
  const [formular, setFormular] = useState<Auswahl | null>(null)
  const [loeschen, setLoeschen] = useState<Auswahl | null>(null)
  const [loeschLaeuft, setLoeschLaeuft] = useState(false)
  const [aktionFehler, setAktionFehler] = useState<unknown>(null)

  const laden = useCallback(
    async (nur?: string) => {
      const namen = nur ? [nur] : zuLaden
      const erg = await Promise.all(
        namen.map(async (n) => [n, await api.holen<Zeile[]>(`/api/events/${eventId}/b/${n}`)] as const),
      )
      setZeilen((alt) => ({ ...alt, ...Object.fromEntries(erg) }))
    },
    [eventId, zuLaden],
  )

  useEffect(() => {
    if (eintraege.length === 0) return
    let aktiv = true
    setGeladen(false)
    setLadeFehler(null)
    setFormular(null)
    const kontaktLaden = braucheKontakte
      ? api.holen<Kontakt[]>(`/api/firmen/${firmaId}/kontakte`)
      : Promise.resolve<Kontakt[]>([])
    Promise.all([laden(), kontaktLaden])
      .then(([, k]) => {
        if (!aktiv) return
        setKontakte(k)
        setGeladen(true)
      })
      .catch((e: unknown) => {
        if (aktiv) setLadeFehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [eintraege, braucheKontakte, firmaId, laden])

  if (eintraege.length === 0) {
    return <p className="text-sm text-gray-500">Dieser Bereich kommt mit dem Bot-Ausbau</p>
  }
  if (ladeFehler) return <Meldung fehler={ladeFehler} />
  if (!geladen) return <p className="text-sm text-gray-500">Lädt …</p>

  async function speichern(eintrag: string, daten: Record<string, unknown>, zeile?: Zeile) {
    const pfad = `/api/events/${eventId}/b/${eintrag}`
    if (zeile) await api.senden('PATCH', `${pfad}/${zeile.id}`, daten)
    else await api.senden('POST', pfad, daten)
    setFormular(null)
    await laden(eintrag)
  }

  async function loeschenBestaetigt() {
    if (!loeschen?.zeile) return
    const { eintrag, zeile } = loeschen
    setLoeschLaeuft(true)
    setAktionFehler(null)
    try {
      await api.senden('DELETE', `/api/events/${eventId}/b/${eintrag}/${zeile.id}`)
      setLoeschen(null)
      await laden(eintrag)
    } catch (e) {
      setLoeschen(null)
      setAktionFehler(e)
    } finally {
      setLoeschLaeuft(false)
    }
  }

  function zelle(typ: string, spalte: string, wert: unknown): string {
    if (wert === null || wert === undefined || wert === '') return '–'
    if (typ === 'kontakt') return kontakte.find((k) => k.id === wert)?.name ?? '–'
    if (typ.startsWith('ref:')) {
      return refName((zeilen[typ.slice('ref:'.length)] ?? []).find((z) => z.id === wert))
    }
    if (typ === 'datumzeit' && typeof wert === 'string') {
      return `${zeigeDatum(wert)} ${zeigeZeit(wert)}`
    }
    if (istCent(spalte) && typeof wert === 'number') return formatEuro(wert)
    return String(wert)
  }

  function abschnitt(eintrag: string, info: BausteinInfo) {
    const name = EINTRAG_NAMEN[eintrag] ?? bausteinName(eintrag, begriffe)
    const liste = zeilen[eintrag] ?? []
    const offen = formular?.eintrag === eintrag ? formular : null
    return (
      <section key={eintrag} className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-gray-900">{name}</h2>
          {!offen && (
            <button
              type="button"
              onClick={() => setFormular({ eintrag })}
              className="rounded-lg bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
            >
              + Hinzufügen
            </button>
          )}
        </div>
        {offen && (
          <BausteinFormular
            key={offen.zeile?.id ?? 'neu'}
            info={info}
            firmaId={firmaId}
            zeile={offen.zeile}
            kontakte={kontakte}
            refZeilen={zeilen}
            onKontaktAngelegt={(k) => setKontakte((alt) => [...alt, k])}
            onSpeichern={(daten) => speichern(eintrag, daten, offen.zeile)}
            onAbbrechen={() => setFormular(null)}
          />
        )}
        {liste.length === 0 ? (
          <p className="text-sm text-gray-500">Noch keine Einträge</p>
        ) : (
          <div className="overflow-x-auto rounded-xl bg-white shadow-md shadow-gray-300/25 outline outline-1 outline-neutral-200/40">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  {info.reihenfolge.map((s) => (
                    <th key={s} scope="col" className="px-4 py-2 font-semibold">
                      {spaltenName(s)}
                    </th>
                  ))}
                  <th scope="col" className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {liste.map((z) => (
                  <tr key={z.id}>
                    {info.reihenfolge.map((s) => (
                      <td key={s} className="px-4 py-2 text-gray-700">
                        {zelle(info.felder[s] ?? 'text', s, z[s])}
                      </td>
                    ))}
                    <td className="whitespace-nowrap px-4 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => setFormular({ eintrag, zeile: z })}
                        className="mr-3 text-sm font-medium text-gray-700 hover:text-gray-900"
                      >
                        Bearbeiten
                      </button>
                      <button
                        type="button"
                        onClick={() => setLoeschen({ eintrag, zeile: z })}
                        className="text-sm font-medium text-red-600 hover:text-red-800"
                      >
                        Löschen
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    )
  }

  return (
    <div className="space-y-8">
      <Meldung fehler={aktionFehler ?? undefined} />
      {eintraege.map((e) => {
        const info = verzeichnis[e]
        return info ? abschnitt(e, info) : null
      })}
      {loeschen && (
        <Bestaetigen
          titel="Eintrag löschen?"
          text="Der Eintrag wird endgültig entfernt."
          aktion="Löschen"
          gefaehrlich
          laeuft={loeschLaeuft}
          onBestaetigen={() => void loeschenBestaetigt()}
          onAbbrechen={() => setLoeschen(null)}
        />
      )}
    </div>
  )
}
