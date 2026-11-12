import clsx from 'clsx'
import Link from 'next/link'
import type { ReactNode } from 'react'
import { kategorieVerlauf } from '@/lib/farbe'
import { zeigeSpanne } from '@/lib/zeit'
import type { KatalogEvent, Phase } from '@/lib/typen'

export const BADGE = 'px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide rounded-full'

export const PHASE: Record<Phase, { text: string; klasse: string }> = {
  laeuft: { text: 'läuft', klasse: 'bg-green-100 text-green-700' },
  kommend: { text: 'kommend', klasse: 'bg-blue-100 text-blue-700' },
  vergangen: { text: 'vorbei', klasse: 'bg-gray-100 text-gray-500' },
}

function ortText(e: KatalogEvent): string {
  if (e.ort_adresse) return e.ort_adresse
  if (e.ort_link) return 'online'
  return 'Ort offen'
}

export function sichereUrl(url: string | null): string | null {
  if (!url) return null
  return /^https?:\/\//i.test(url) ? url : null
}

const KARTE =
  'block bg-white rounded-xl nice-shadow overflow-hidden hover:scale-[1.01] transition-all duration-300'

function Huelle({ e, children }: { e: KatalogEvent; children: ReactNode }) {
  if (e.quelle === 'legacy') {
    const url = sichereUrl(e.legacy_url)
    if (!url) return <div className={KARTE}>{children}</div>
    return (
      <a href={url} target="_blank" rel="noopener noreferrer" className={KARTE}>
        {children}
      </a>
    )
  }
  return (
    <Link href={`/events/${e.id}`} className={KARTE}>
      {children}
    </Link>
  )
}

export function EventKachel({ event: e }: { event: KatalogEvent }) {
  const { aufgaben_gesamt: gesamt, aufgaben_offen: offen, teilnehmende_zugesagt: zusagen } =
    e.kennzahlen
  const erledigt = gesamt - offen
  const phase = PHASE[e.phase]
  return (
    <Huelle e={e}>
      <div className={clsx('h-[46px] bg-gradient-to-r', kategorieVerlauf(e.kategorie))} />
      <div className="space-y-2 p-4">
        <div className="flex flex-wrap gap-1.5">
          <span className={clsx(BADGE, 'bg-gray-100 text-gray-700')}>{e.kategorie_name}</span>
          <span className={clsx(BADGE, phase.klasse)}>{phase.text}</span>
          {e.status === 'abgesagt' && (
            <span className={clsx(BADGE, 'bg-red-100 text-red-700')}>abgesagt</span>
          )}
          {e.quelle === 'legacy' && (
            <span className={clsx(BADGE, 'bg-yellow-100 text-yellow-700')}>Legacy</span>
          )}
        </div>
        <h3 className="text-base font-bold text-gray-900 line-clamp-1">{e.name}</h3>
        <p className="text-sm text-gray-500 line-clamp-1">
          {e.firma_name} · {zeigeSpanne(e.beginn, e.ende)} · {ortText(e)}
        </p>
        {gesamt > 0 && (
          <div
            className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={gesamt}
            aria-valuenow={erledigt}
          >
            <div
              className="h-full bg-emerald-500"
              style={{ width: `${Math.round((erledigt / gesamt) * 100)}%` }}
            />
          </div>
        )}
        <p className="text-xs text-gray-500">
          Aufgaben {erledigt}/{gesamt} · {zusagen} Zusagen
        </p>
      </div>
    </Huelle>
  )
}
