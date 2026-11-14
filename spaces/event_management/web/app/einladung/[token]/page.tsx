'use client'

import { useParams, useRouter } from 'next/navigation'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Meldung } from '@/components/Meldung'
import { api } from '@/lib/api'

const PASSWORT_MIN = 12

interface EinladungInfo {
  gueltig: boolean
  firma_name?: string
  braucht_passwort?: boolean
}

function Feld(props: {
  id: string
  label: string
  wert: string
  setze: (w: string) => void
  typ?: string
  autoComplete?: string
}) {
  return (
    <div>
      <label htmlFor={props.id} className="mb-1 block text-sm font-medium text-gray-700">
        {props.label}
      </label>
      <input
        id={props.id}
        type={props.typ ?? 'text'}
        autoComplete={props.autoComplete}
        value={props.wert}
        onChange={(e) => props.setze(e.target.value)}
        className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm"
      />
    </div>
  )
}

export default function EinladungEinloesen() {
  const router = useRouter()
  const roh = useParams<{ token: string | string[] }>().token
  const token = Array.isArray(roh) ? (roh[0] ?? '') : (roh ?? '')
  const pfad = `/api/einladung/${encodeURIComponent(token)}`
  const [info, setInfo] = useState<EinladungInfo | null>(null)
  const [ladefehler, setLadefehler] = useState<unknown>(null)
  const [name, setName] = useState('')
  const [passwort, setPasswort] = useState('')
  const [wiederholt, setWiederholt] = useState('')
  const [hinweis, setHinweis] = useState('')
  const [fehler, setFehler] = useState<unknown>(null)
  const [laeuft, setLaeuft] = useState(false)

  useEffect(() => {
    let aktiv = true
    api
      .holen<EinladungInfo>(pfad)
      .then((daten) => {
        if (aktiv) setInfo(daten)
      })
      .catch((e: unknown) => {
        if (aktiv) setLadefehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [pfad])

  async function annehmen(e: FormEvent) {
    e.preventDefault()
    setFehler(null)
    setHinweis('')
    const brauchtPasswort = info?.braucht_passwort === true
    if (brauchtPasswort) {
      if (passwort.length < PASSWORT_MIN) {
        setHinweis(`Das Passwort muss mindestens ${PASSWORT_MIN} Zeichen lang sein.`)
        return
      }
      if (passwort !== wiederholt) {
        setHinweis('Die Passwörter stimmen nicht überein.')
        return
      }
    }
    const daten: { passwort?: string; name?: string } = {}
    if (brauchtPasswort) {
      daten.passwort = passwort
      if (name.trim()) daten.name = name.trim()
    }
    setLaeuft(true)
    try {
      await api.sendenOeffentlich(pfad, daten)
      router.push('/anmelden?hinweis=konto-bereit')
    } catch (err: unknown) {
      setFehler(err)
      setLaeuft(false)
    }
  }

  let inhalt: ReactNode
  if (ladefehler) {
    inhalt = <Meldung fehler={ladefehler} />
  } else if (!info) {
    inhalt = <div className="h-24 animate-pulse rounded bg-gray-200" aria-busy="true" aria-label="Lädt" />
  } else if (!info.gueltig) {
    inhalt = (
      <p role="alert" className="text-sm text-gray-700">
        Diese Einladung ist ungültig oder abgelaufen.
      </p>
    )
  } else {
    inhalt = (
      <form onSubmit={annehmen} className="space-y-4">
        <h1 className="text-xl font-bold text-gray-900">Einladung zu {info.firma_name}</h1>
        {info.braucht_passwort === true && (
          <>
            <Feld id="name" label="Name" wert={name} setze={setName} autoComplete="name" />
            <Feld id="pw" label="Passwort" wert={passwort} setze={setPasswort} typ="password" autoComplete="new-password" />
            <Feld
              id="pw2"
              label="Passwort wiederholen"
              wert={wiederholt}
              setze={setWiederholt}
              typ="password"
              autoComplete="new-password"
            />
          </>
        )}
        {hinweis && (
          <p role="alert" className="text-sm text-red-800">
            {hinweis}
          </p>
        )}
        <Meldung fehler={fehler} />
        <button
          type="submit"
          disabled={laeuft}
          className="w-full rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-50"
        >
          Einladung annehmen
        </button>
      </form>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-xl bg-white p-6 nice-shadow">{inhalt}</div>
    </div>
  )
}
