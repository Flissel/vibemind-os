# Image event-management/web (Next.js, output standalone). Das Ziel der /api-Rewrites
# wird beim Bauen festgelegt: ARG EVENT_API_URL (Dienstname im Compose-Netz).
#   docker build --build-arg EVENT_API_URL=http://event-api:8170 \
#     -t event-management/web:<git-kurz-sha> web/
FROM node:22.11.0-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22.11.0-slim AS build
WORKDIR /app
ARG EVENT_API_URL=http://event-api:8170
ENV EVENT_API_URL=${EVENT_API_URL} \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22.11.0-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3170 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3170
CMD ["node", "server.js"]
