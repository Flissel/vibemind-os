import clsx from 'clsx'
import { TriangleAlert } from 'lucide-react'
import { BADGE, PHASE, sichereUrl } from '@/components/katalog/EventKachel'
import { zeigeSpanne } from '@/lib/zeit'
import type { EventDetail, EventStatus, Phase } from '@/lib/typen'

const STATUS: Record<EventStatus, { text: string; klasse: string }> = {
  entwurf: { text: 'Entwurf', klasse: 'bg-gray-100 text-gray-600' },
  geplant: { text: 'geplant', klasse: 'bg-blue-100 text-blue-700' },
  laeuft: { text: 'läuft', klasse: 'bg-green-100 text-green-700' },
  abgeschlossen: { text: 'abgeschlossen', klasse: 'bg-gray-100 text-gray-500' },
  abgesagt: { text: 'abgesagt', klasse: 'bg-red-100 text-red-700' },
  archiviert: { text: 'archiviert', klasse: 'bg-yellow-100 text-yellow-700' },
}

export function statusText(status: EventStatus): string {
  return STATUS[status].text
}

function phaseVon(e: EventDetail, jetzt: number): Phase {
  if (jetzt < new Date(e.beginn).getTime()) return 'kommend'
  if (jetzt > new Date(e.ende).getTime()) return 'vergangen'
  return 'laeuft'
}

function ort(e: EventDetail): string {
  if (e.ort_adresse) return e.ort_adresse
  if (e.ort_link) return 'online'
  return 'Ort offen'
}

interface Props {
  event: EventDetail
  firmaName: string
}

export function EventKopf({ event: e, firmaName }: Props) {
  const phase = PHASE[phaseVon(e, Date.now())]
  const status = STATUS[e.status]
  const url = sichereUrl(e.legacy_url)
  return (
    <header className="space-y-2">
      <p className="text-sm text-gray-500">
        {firmaName} › {e.schema.name}
      </p>
      <h1 className="text-2xl font-bold text-gray-900">{e.name}</h1>
      <p className="text-sm text-gray-600">
        {zeigeSpanne(e.beginn, e.ende)} · {ort(e)}
      </p>
      <div className="flex flex-wrap gap-1.5">
        <span className={clsx(BADGE, phase.klasse)}>{phase.text}</span>
        <span className={clsx(BADGE, status.klasse)}>{status.text}</span>
      </div>
      {e.quelle === 'legacy' && (
        <div
          role="note"
          className="flex items-start gap-2 rounded-lg border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-800"
        >
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>
            Dieses Event wird in der alten App gepflegt
            {url && (
              <>
                {' · '}
                <a href={url} target="_blank" rel="noopener noreferrer" className="font-semibold underline">
                  In der alten App öffnen
                </a>
              </>
            )}
          </span>
        </div>
      )}
    </header>
  )
}
