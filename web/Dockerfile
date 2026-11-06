# The trader portal or the back office as a static site behind Caddy
# (P2): built once, served with the API under /api on the same origin.
#   docker build -f web/Dockerfile --build-context web=web portal
FROM node:22.23.4-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . ./
RUN npm run build

FROM caddy:2.11.6-alpine@sha256:d44355d3c2149dc580ce2cac735955d1c08d3d00882c30489c241aa51a5c10d9
COPY --from=web Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv
# no state on disk: Caddy's data and config go to /tmp (no TLS here; the
# public edge terminates TLS in production)
ENV XDG_DATA_HOME=/tmp/caddy-data XDG_CONFIG_HOME=/tmp/caddy-config
USER 10001:10001
