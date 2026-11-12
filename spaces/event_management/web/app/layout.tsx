import type { Metadata } from 'next'
import { Wix_Madefor_Text } from 'next/font/google'
import './globals.css'

const schrift = Wix_Madefor_Text({
  subsets: ['latin'],
  display: 'swap',
  fallback: ['system-ui'],
})

export const metadata: Metadata = {
  title: 'VibeMind Events',
  description: 'Events planen, durchführen und nachverfolgen',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body className={`${schrift.className} min-h-screen bg-gray-50 text-gray-900 antialiased`}>
        <main className="pt-[60px]">{children}</main>
      </body>
    </html>
  )
}
