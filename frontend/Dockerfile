# Frontend container: Vite dev server, deps installed with pnpm (not npm).
FROM node:20-slim

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
