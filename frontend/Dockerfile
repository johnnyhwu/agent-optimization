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

# The account the container ends up running as. 1001 is the convention hardened
# nginx images use — an arbitrary non-root uid whose group is 0 — and it is a
# safe default for a base that runs as root too, since root is not bound by the
# permission bits this stage sets. Override it to match a different base:
#
#     docker compose build --build-arg WEB_UID=101 frontend
ARG WEB_UID=1001

# Build as root so the RUN below can hand the runtime paths over; the stage ends
# with USER ${WEB_UID}, so the *image* is still unprivileged. This is only needed
# because a base image that carries its own USER applies it to every RUN here,
# leaving the build unable to chown or chmod anything it did not create.
USER root

# The image's entrypoint substitutes ${API_URL} into this at start-up.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
# Rendered into the web root by the script below — not an nginx template, so it
# is staged outside /etc/nginx/templates.
COPY config.js.template /etc/nginx/config.js.template
# --chmod rather than a following `RUN chmod +x`, because a RUN cannot be relied
# on to have the privileges for it. A hardened base image carries its own USER,
# and every RUN in this stage then executes as that unprivileged account — which
# cannot chmod a file COPY brought in as root:root, so the build dies with
# "chmod: Operation not permitted". --chmod has the builder stamp the mode as it
# writes the layer, where the question of who is running never arises.
#
# It also pins the bit down rather than inheriting it from the build context: a
# checkout on a filesystem without a POSIX executable bit would otherwise ship a
# script the nginx entrypoint silently skips.
COPY --chmod=0755 docker-entrypoint.d/10-app-config.sh /docker-entrypoint.d/

# The web root is not read-only: 10-app-config.sh writes config.js into it at
# every start, which is the mechanism that lets one built image run in any
# environment. So it has to be owned by the account above, and COPY's default of
# root:root is only right for a base image that runs as root.
COPY --from=builder --chown=${WEB_UID}:0 /app/dist /var/www

# Hand over the paths nginx and its entrypoint write to at run time. An image
# that merely declares USER without doing this starts as root would and fails as
# soon as anything needs a file:
#
#   /etc/nginx/conf.d   20-envsubst-on-templates.sh renders the server config
#                       here from /etc/nginx/templates, which is how ${API_URL}
#                       reaches nginx. Refuses with "exists but is not writable".
#   /var/cache/nginx    the proxy and request-body temp paths. nginx will not
#                       finish starting without them: [emerg] mkdir() failed.
#   /run/nginx.pid      pre-created rather than made writable by giving away /run
#                       — nginx only needs to open the file, not create it.
#   /var/log/nginx      symlinks to stdout/stderr in the stock image, real files
#                       in some rebuilds; -h keeps chown off the symlink targets.
#
# Group 0 rather than a matching gid throughout, because that is the group these
# images put their non-root user in, so g+w reaches it whatever the uid turns out
# to be.
RUN set -eux; \
    mkdir -p /var/cache/nginx/client_temp /var/cache/nginx/proxy_temp \
             /var/cache/nginx/fastcgi_temp /var/cache/nginx/uwsgi_temp \
             /var/cache/nginx/scgi_temp; \
    touch /run/nginx.pid; \
    chown -Rh "${WEB_UID}:0" /var/cache/nginx /etc/nginx/conf.d /var/log/nginx \
                             /run/nginx.pid; \
    chmod -R g+w /var/cache/nginx /etc/nginx/conf.d; \
    chmod g+w /run/nginx.pid

# Back to unprivileged for the image itself. Everything above this line ran as
# root; nothing below it does.
USER ${WEB_UID}

EXPOSE 5173
# Inherits nginx's own ENTRYPOINT/CMD, which runs /docker-entrypoint.d/* first.
