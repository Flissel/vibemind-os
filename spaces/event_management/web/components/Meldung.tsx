import clsx from 'clsx'
import { CircleAlert, CircleCheck } from 'lucide-react'
import { ApiFehler } from '@/lib/api'

/** Text für den Benutzer: 422 zeigt die datenfreie Meldung der Fachschicht, 403/404/5xx
 *  bekommen feste Sätze (die API-Texte nennen interne Aktionsnamen). */
export function fehlerText(fehler: unknown): string {
  if (fehler instanceof ApiFehler) {
    if (fehler.status === 403) return 'Dafür fehlt die Berechtigung.'
    if (fehler.status === 404) return 'Nicht gefunden.'
    if (fehler.status >= 500) return 'Interner Fehler. Bitte später erneut versuchen.'
    return fehler.detail
  }
  return 'Unerwarteter Fehler.'
}

interface Props {
  fehler?: unknown
  erfolg?: string
  className?: string
}

/** Fehler- oder Erfolgsbanner; zeigt nichts, wenn beides leer ist. */
export function Meldung({ fehler, erfolg, className }: Props) {
  const istFehler = fehler !== undefined && fehler !== null && fehler !== false
  if (!istFehler && !erfolg) return null
  const Symbol = istFehler ? CircleAlert : CircleCheck
  return (
    <div
      role={istFehler ? 'alert' : 'status'}
      className={clsx(
        'flex items-start gap-2 rounded-lg border px-3 py-2 text-sm',
        istFehler
          ? 'border-red-200 bg-red-50 text-red-800'
          : 'border-emerald-200 bg-emerald-50 text-emerald-800',
        className,
      )}
    >
      <Symbol className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <span>{istFehler ? fehlerText(fehler) : erfolg}</span>
    </div>
  )
}
