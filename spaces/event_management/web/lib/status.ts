import type { EventStatus } from '@/lib/typen'

/** Anzeigetext und Badge-Farbe je Event-Status. */
export const STATUS: Record<EventStatus, { text: string; klasse: string }> = {
  entwurf: { text: 'Entwurf', klasse: 'bg-gray-100 text-gray-600' },
  geplant: { text: 'geplant', klasse: 'bg-blue-100 text-blue-700' },
  laeuft: { text: 'läuft', klasse: 'bg-green-100 text-green-700' },
  abgeschlossen: { text: 'abgeschlossen', klasse: 'bg-gray-100 text-gray-500' },
  abgesagt: { text: 'abgesagt', klasse: 'bg-red-100 text-red-700' },
  archiviert: { text: 'archiviert', klasse: 'bg-yellow-100 text-yellow-700' },
}
