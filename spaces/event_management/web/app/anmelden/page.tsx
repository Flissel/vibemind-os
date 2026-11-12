'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useState, type FormEvent } from 'react'
import { Meldung } from '@/components/Meldung'
import { ApiFehler, api } from '@/lib/api'
import { sichererWeiter } from '@/lib/weiter'

const FEHLER_401 = 'Mail oder Passwort falsch, oder zu viele Versuche. Bitte später erneut versuchen.'

export default function Anmelden() {
  const router = useRouter()
  const [mail, setMail] = useState('')
  const [passwort, setPasswort] = useState('')
  const [fehler, setFehler] = useState<unknown>(null)
  const [fehlerText, setFehlerText] = useState('')
  const [hinweis, setHinweis] = useState('')
  const [laeuft, setLaeuft] = useState(false)

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('hinweis') === 'konto-bereit') {
      setHinweis('Konto bereit, bitte anmelden')
    }
  }, [])

  async function absenden(e: FormEvent) {
    e.preventDefault()
    setFehler(null)
    setFehlerText('')
    setLaeuft(true)
    try {
      await api.anmelden(mail, passwort)
      const weiter = new URLSearchParams(window.location.search).get('weiter')
      router.push(sichererWeiter(weiter, window.location.origin))
    } catch (err: unknown) {
      if (err instanceof ApiFehler && err.status === 401) setFehlerText(FEHLER_401)
      else setFehler(err)
      setLaeuft(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={absenden} className="w-full max-w-sm space-y-4 rounded-xl bg-white p-6 nice-shadow">
        <h1 className="text-xl font-bold text-gray-900">Anmelden</h1>
        <Meldung erfolg={hinweis} />
        <Meldung fehler={fehler} text={fehlerText} />
        <div>
          <label htmlFor="mail" className="mb-1 block text-sm font-medium text-gray-700">
            Mail
          </label>
          <input
            id="mail"
            type="email"
            autoComplete="username"
            required
            value={mail}
            onChange={(e) => setMail(e.target.value)}
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label htmlFor="passwort" className="mb-1 block text-sm font-medium text-gray-700">
            Passwort
          </label>
          <input
            id="passwort"
            type="password"
            autoComplete="current-password"
            required
            value={passwort}
            onChange={(e) => setPasswort(e.target.value)}
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm"
          />
        </div>
        <button
          type="submit"
          disabled={laeuft}
          className="w-full rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-50"
        >
          Anmelden
        </button>
      </form>
    </div>
  )
}
