import clsx from 'clsx'
import { TriangleAlert } from 'lucide-react'
import { BADGE, PHASE, sichereUrl } from '@/components/katalog/EventKachel'
import { ortText, phaseAus } from '@/lib/phase'
import { STATUS } from '@/lib/status'
import { zeigeSpanne } from '@/lib/zeit'
import type { EventDetail, EventStatus } from '@/lib/typen'

export function statusText(status: EventStatus): string {
  return STATUS[status].text
}

interface Props {
  event: EventDetail
  firmaName: string
}

export function EventKopf({ event: e, firmaName }: Props) {
  const phase = PHASE[phaseAus(e, Date.now())]
  const status = STATUS[e.status]
  const url = sichereUrl(e.legacy_url)
  return (
    <header className="space-y-2">
      <p className="text-sm text-gray-500">
        {firmaName} › {e.schema.name}
      </p>
      <h1 className="text-2xl font-bold text-gray-900">{e.name}</h1>
      <p className="text-sm text-gray-600">
        {zeigeSpanne(e.beginn, e.ende)} · {ortText(e)}
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
