# Frontend container, in three stages:
#
#   dev      Vite dev server with HMR. What docker-compose.yml runs; unchanged.
#   builder  produces the static bundle.
#   runner   nginx serving that bundle and proxying /api. What
#            docker-compose.prod.yml runs.
#
# `docker build` with no --target builds the last stage, so a plain build gives
# the deployable image; compose names the target explicitly for both.
FROM node:20-slim AS dev

# pnpm ships with Node via corepack; pinning the version keeps the lockfile
# format stable across machines.
ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate

# Pin the content-addressable store to an absolute path. Without this, pnpm
# picks a store next to the project — which lands on the bind-mounted /app at
# runtime but on the image filesystem at build time, and the mismatch makes pnpm
# want to wipe node_modules on every start.
RUN pnpm config set --global store-dir /pnpm/store

WORKDIR /app

# Dependency layer first so source edits don't reinstall node_modules.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

EXPOSE 5173
# --host binds 0.0.0.0 so the mapped port is reachable from the host browser;
# the port itself is still 5173, as configured in vite.config.js.
CMD ["pnpm", "run", "dev", "--host", "0.0.0.0"]


# --- Build the static bundle -------------------------------------------------
# Deliberately takes no VITE_* build arguments. Everything environment-specific
# reaches the app through /config.js at container start (see
# docker-entrypoint.d/10-app-config.sh), which is what lets one image be deployed
# to any environment instead of one image per Keycloak URL.
FROM dev AS builder
RUN pnpm build


# --- Serve it ----------------------------------------------------------------
FROM nginx:1.27-alpine AS runner

# The image's entrypoint substitutes ${API_URL} into this at start-up.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
# Rendered into the web root by the script below — not an nginx template, so it
# is staged outside /etc/nginx/templates.
COPY config.js.template /etc/nginx/config.js.template
COPY docker-entrypoint.d/10-app-config.sh /docker-entrypoint.d/10-app-config.sh
RUN chmod +x /docker-entrypoint.d/10-app-config.sh

COPY --from=builder /app/dist /var/www

EXPOSE 5173
# Inherits nginx's own ENTRYPOINT/CMD, which runs /docker-entrypoint.d/* first.
