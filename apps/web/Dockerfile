# Dependency installation stage.
FROM node:25-alpine AS deps

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

# Build stage: compile the Next.js application.
FROM node:25-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# Runtime stage: minimal image with non-root user and health check.
FROM node:25-alpine AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000

RUN addgroup -S ledger && adduser -S ledger -G ledger \
    && apk add --no-cache curl

COPY --from=builder --chown=ledger:ledger /app/.next ./.next
COPY --from=builder --chown=ledger:ledger /app/node_modules ./node_modules
COPY --from=builder --chown=ledger:ledger /app/package.json ./package.json
COPY --from=builder --chown=ledger:ledger /app/public ./public

USER ledger

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD curl -fsS http://127.0.0.1:3000/ >/dev/null

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0"]
