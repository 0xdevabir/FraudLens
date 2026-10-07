# The console image: build once, then run the self-contained server from .next/standalone.
# Multi-arch: node:22-slim publishes linux/amd64 and linux/arm64 (Apple Silicon).
FROM node:22-slim AS build
WORKDIR /app
RUN corepack enable
# Generous fetch timeout: large optional SWC binaries (esp. linux/arm64) can stall on slow links.
ENV PNPM_NETWORK_TIMEOUT=600000
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm config set fetch-timeout 600000 \
    && pnpm config set fetch-retries 5 \
    && pnpm install --frozen-lockfile
COPY . .
# Where the browser finds the API. It is compiled into the pages, so it is a build argument.
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8010
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3100
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3100
CMD ["node", "server.js"]

