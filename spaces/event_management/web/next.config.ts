import type { NextConfig } from 'next'

const eventApi = process.env.EVENT_API_URL ?? 'http://127.0.0.1:8170'

const config: NextConfig = {
  output: 'standalone',
  async rewrites() {
    return [{ source: '/api/:pfad*', destination: `${eventApi}/api/:pfad*` }]
  },
}

export default config
