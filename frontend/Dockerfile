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
# --chmod rather than a following `RUN chmod +x`, for two reasons.
#
# The mode is set by the builder as it writes the layer, so no chmod(2) runs
# inside a container. That matters because this stage is Alpine: its musl tries
# fchmodat2(2) first and falls back to fchmodat(2) on ENOSYS, but a seccomp
# profile that predates the syscall answers EPERM instead, the fallback never
# fires, and the build dies with "chmod: Operation not permitted". Docker only
# learned fchmodat2 in 26.0.0 (and 25.0.3/24.0.10/23.0.10), so any older daemon
# on a 6.6+ kernel hits it. The Debian-based backend image is unaffected — its
# glibc is old enough not to reach for the new syscall — which is why this was
# the only step that failed.
#
# It also pins the bit down rather than inheriting it from the build context: a
# checkout on a filesystem without a POSIX executable bit would otherwise ship a
# script the nginx entrypoint silently skips.
COPY --chmod=0755 docker-entrypoint.d/10-app-config.sh /docker-entrypoint.d/

COPY --from=builder /app/dist /var/www

EXPOSE 5173
# Inherits nginx's own ENTRYPOINT/CMD, which runs /docker-entrypoint.d/* first.
