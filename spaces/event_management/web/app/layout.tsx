import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'], display: 'swap' })

export const metadata: Metadata = {
  title: 'VibeMind Events',
  description: 'Events planen, durchführen und nachverfolgen',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body className={`${inter.className} min-h-screen bg-gray-50 text-gray-900 antialiased`}>
        {children}
      </body>
    </html>
  )
}
