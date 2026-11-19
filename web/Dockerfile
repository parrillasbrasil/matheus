# Painel/API (Next.js, saída "standalone"). Contexto de build: a RAIZ do repositório.
#   docker build -f web/Dockerfile --target runtime -t radar-web .
FROM node:22-alpine AS deps
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY web/ ./
RUN npm run build

# Testes: unitários/integração (banco descartável) e ponta a ponta contra o app compilado.
FROM build AS test
COPY db /app/db-migrations
ENV RADAR_MIGRATIONS_DIR=/app/db-migrations NEXT_TELEMETRY_DISABLED=1
CMD ["sh", "-c", "npm test && npm run test:e2e"]

# Produção (último estágio = padrão).
FROM node:22-alpine AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -S -g 10001 radar && adduser -S -u 10001 -G radar radar
COPY --from=build --chown=radar:radar /app/.next/standalone ./
COPY --from=build --chown=radar:radar /app/.next/static ./.next/static
USER radar
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD wget -qO- http://127.0.0.1:3000/api/health > /dev/null || exit 1
CMD ["node", "server.js"]
