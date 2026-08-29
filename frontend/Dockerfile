# Multi-stage: dependencies, build, then a slim runtime containing only what
# the server actually needs.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# `npm ci` when a lockfile exists (reproducible), `npm install` otherwise.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi


FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* values are inlined at build time, not read at runtime, so the
# API URL must be present here rather than only in the compose environment.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build


FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000

# Non-root: a compromised render process has nothing to escalate to.
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# `output: "standalone"` emits a self-contained server plus only the node
# modules it actually imports, which keeps this image small.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
