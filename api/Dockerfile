# syntax=docker/dockerfile:1

# Production image for the Hono API. Bun runs the TypeScript directly, so there
# is no build step. Env comes from the hosting platform (see .env.example);
# nothing in this image reads .env files.
#   docker build -t cadence-api .
#   docker run -p 4000:4000 --env-file ../.env.local cadence-api

# Same Bun version as local dev (`bun --version`).
ARG BUN_VERSION=1.3.5
FROM oven/bun:${BUN_VERSION}-slim AS deps
WORKDIR /app

# Production dependencies only (no drizzle-kit, types, tsx).
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile --production

FROM oven/bun:${BUN_VERSION}-slim
WORKDIR /app
ENV NODE_ENV=production

COPY --from=deps /app/node_modules ./node_modules
COPY package.json tsconfig.json ./
COPY src ./src

# The image's built-in non-root user.
USER bun

# `env.PORT` defaults to 4000; Bun.serve binds 0.0.0.0.
EXPOSE 4000
# Same as `bun run start`, but without the `bun run` wrapper process, so the
# platform's SIGTERM reaches the server directly.
CMD ["bun", "src/index.ts"]
