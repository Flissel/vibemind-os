'use client'

import { AnmeldeSchutz } from '@/components/AnmeldeSchutz'
import { useIch } from '@/components/IchKontext'
import { Meldung } from '@/components/Meldung'
import { TelegramVerbinden } from '@/components/TelegramVerbinden'
import { darfDirektchat } from '@/lib/rechte'

/** Eigenen Telegram-Chat mit dem Bot verbinden (nur Leitung oder Betreiber). */
function TelegramSeite() {
  const ich = useIch()
  if (!darfDirektchat(ich)) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-6">
        <Meldung text="Den eigenen Telegram-Chat können nur Leitungen und Betreiber verbinden." />
      </div>
    )
  }
  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <h1 className="text-2xl font-bold text-gray-900">Meinen Telegram-Chat verbinden</h1>
      <p className="text-sm text-gray-600">
        Im Direktchat mit dem Bot kannst du Events suchen, wählen und verwalten – mit deinen
        Rechten als Leitung{ich.betreiber ? ' bzw. Betreiber' : ''}.
      </p>
      <TelegramVerbinden art="direkt" titel="Mein Telegram-Chat" pfad="/api/ich/verbinden" darf />
    </div>
  )
}

export default function Page() {
  return (
    <AnmeldeSchutz>
      <TelegramSeite />
    </AnmeldeSchutz>
  )
}
