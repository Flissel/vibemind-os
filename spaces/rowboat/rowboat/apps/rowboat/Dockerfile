# syntax=docker.io/docker/dockerfile:1

# BUILD CONTEXT IS THE ROWBOAT REPO ROOT, not apps/rowboat.
#
# `apps/rowboat` depends on the plugin kernel as
# `"@rowboat/openai-plugin-runtime": "file:../../packages/openai-plugin-runtime"`,
# and the app consumes its compiled `dist/`, not its sources. With the old
# context (`./apps/rowboat`) that path pointed outside the build context, so
# the dependency could not be installed and the plugin runtime could never
# reach a built image at all. The context is therefore the repo root, and the
# kernel is built here before the app installs.

# Node 20 on Debian, not Node 18 on Alpine. Two reasons, both measured while
# building this image: the app's own `sharp` (0.34.2, via next) requires Node
# >= 20.9.0 and refuses to load on 18, and it ships no musl prebuild, so on
# Alpine it fails with "linuxmusl-x64 runtime". Either one aborts the Next.js
# page-data collection and the whole build.
FROM node:20-slim AS base

# The plugin kernel. Built first and copied into place at the exact relative
# path the app's `file:` dependency resolves to (/app/apps/rowboat ->
# ../../packages/openai-plugin-runtime).
FROM base AS kernel
WORKDIR /app/packages/openai-plugin-runtime
COPY packages/openai-plugin-runtime/package.json packages/openai-plugin-runtime/package-lock.json* ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY packages/openai-plugin-runtime/ ./
RUN npm run build

# Install dependencies only when needed
FROM base AS deps
# The `file:` dependency is resolved at install time, so the kernel (with its
# dist already built) must be in place before the app's install runs.
COPY --from=kernel /app/packages/openai-plugin-runtime /app/packages/openai-plugin-runtime
WORKDIR /app/apps/rowboat

# Install dependencies based on the preferred package manager
COPY apps/rowboat/package.json apps/rowboat/yarn.lock* apps/rowboat/package-lock.json* apps/rowboat/pnpm-lock.yaml* apps/rowboat/.npmrc* ./
RUN \
  if [ -f yarn.lock ]; then yarn --frozen-lockfile; \
  elif [ -f package-lock.json ]; then npm ci; \
  elif [ -f pnpm-lock.yaml ]; then corepack enable pnpm && pnpm i --frozen-lockfile; \
  else echo "Lockfile not found." && exit 1; \
  fi


# Rebuild the source code only when needed
FROM base AS builder
WORKDIR /app/apps/rowboat
COPY --from=kernel /app/packages/openai-plugin-runtime /app/packages/openai-plugin-runtime
COPY --from=deps /app/apps/rowboat/node_modules ./node_modules
COPY apps/rowboat/ ./

# Next.js collects completely anonymous telemetry data about general usage.
# Learn more here: https://nextjs.org/telemetry
# Uncomment the following line in case you want to disable telemetry during the build.
# ENV NEXT_TELEMETRY_DISABLED=1

RUN \
  if [ -f yarn.lock ]; then yarn run build; \
  elif [ -f package-lock.json ]; then npm run build; \
  elif [ -f pnpm-lock.yaml ]; then corepack enable pnpm && pnpm run build; \
  else echo "Lockfile not found." && exit 1; \
  fi

# The deploy-step image. The runner above is a standalone bundle with neither
# tsx nor the catalog lock, so index creation and catalog loading -- which a
# deployment must run before the app serves a single plugin page -- get their
# own target with the dev dependencies and the repo layout intact.
FROM base AS tools
WORKDIR /app/apps/rowboat
COPY --from=kernel /app/packages/openai-plugin-runtime /app/packages/openai-plugin-runtime
COPY --from=deps /app/apps/rowboat/node_modules ./node_modules
COPY config/ /app/config/
COPY apps/rowboat/ ./
ENV NODE_ENV=production
# Nothing runs by default: a deploy invokes the steps explicitly, in order.
CMD ["node", "-e", "console.error('run a deploy step explicitly, e.g. npm run mongodb-ensure-indexes'); process.exit(2)"]

# Production image, copy all the files and run next
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
# Uncomment the following line in case you want to disable telemetry during runtime.
# ENV NEXT_TELEMETRY_DISABLED=1

RUN groupadd --system --gid 1001 nodejs   && useradd --system --uid 1001 --gid nodejs nextjs

COPY --from=builder /app/apps/rowboat/public ./public

# Automatically leverage output traces to reduce image size
# https://nextjs.org/docs/advanced-features/output-file-tracing
#
# The standalone output of a monorepo build keeps the workspace layout, so the
# server lives at apps/rowboat/server.js with node_modules beside it -- the
# plugin kernel among them, materialized by the trace.
COPY --from=builder --chown=nextjs:nodejs /app/apps/rowboat/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/rowboat/.next/static ./apps/rowboat/.next/static

# sharp, twice over, and neither copy survives file tracing on its own.
#
# The plugin kernel decodes raster assets (`components/raster-decoder.ts`
# imports sharp 0.35.3), and Next pulls its own 0.34.x for image optimization.
# The trace materializes neither module's platform binaries, so every page
# that touches the kernel -- the workflow page as much as the plugins page --
# answered 500 with "Could not load the sharp module using the linux-x64
# runtime", while a plain require of the same module succeeded inside the very
# same image. Carry both copies over at the paths their callers resolve from.
COPY --from=builder --chown=nextjs:nodejs /app/packages/openai-plugin-runtime/node_modules/sharp ./packages/openai-plugin-runtime/node_modules/sharp
COPY --from=builder --chown=nextjs:nodejs /app/packages/openai-plugin-runtime/node_modules/@img ./packages/openai-plugin-runtime/node_modules/@img
COPY --from=builder --chown=nextjs:nodejs /app/apps/rowboat/node_modules/next/node_modules/sharp ./apps/rowboat/node_modules/sharp
COPY --from=builder --chown=nextjs:nodejs /app/apps/rowboat/node_modules/next/node_modules/@img ./apps/rowboat/node_modules/@img

USER nextjs

EXPOSE 3000

ENV PORT=3000

# server.js is created by next build from the standalone output
# https://nextjs.org/docs/pages/api-reference/config/next-config-js/output
ENV HOSTNAME="0.0.0.0"
ENV PORT=3000
CMD ["node", "apps/rowboat/server.js"]
