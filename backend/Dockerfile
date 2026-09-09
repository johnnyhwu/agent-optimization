# Backend container: FastAPI + Alembic, deps installed with uv (not pip).
#
# **The build context is the repository root, not ./backend** (see
# docker-compose.yml), so every COPY below is written `backend/...`. What needs
# the wider context is `docs/`: the reference documentation the UI serves is the
# repository's own markdown, there is deliberately no second copy of it, and it
# sits a level above the backend — so it has to reach the image somehow. It used to arrive as a bind mount, which
# meant a host without the repository checked out beside the compose file — or a
# deployment that copied only the compose file over — started a container whose
# /app/docs was an empty directory the mount had just created. The Documentation
# page then answered "could not read agent-server-api.md [Errno 2]", which reads
# as a broken app rather than as a missing mount. Shipping the files in the image
# makes the page a property of the build.
#
# The base image pins CPython 3.12 — inside the container there is no need for
# the "find an interpreter in [3.10, 3.13]" dance the host script used to do,
# because the pinned deps always get the interpreter they have wheels for.
FROM python:3.12-slim

# uv is bootstrapped from PyPI (the one pip call in the whole project); every
# dependency install below goes through `uv pip`, which is what makes the image
# build fast and reproducible.
ENV UV_VERSION=0.8.17
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

# Install into the image's system site-packages: the container *is* the venv, so
# a nested virtualenv would only add a layer of indirection.
ENV UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# This image runs in two roles: the API server, and — with `command:` overridden
# to `python -m app.sandbox.server` — the sandbox container that executes
# uploaded eval-set scripts. See app/sandbox/__init__.py.
#
# There used to be a `scriptrunner` account here, created so the server could
# run as root and `setuid` the script's process down to it. That is gone, and its
# absence is the point: needing root is what made the arrangement undeployable
# under a restricted Pod Security Standard, and when the server could not be root
# the drop silently did not happen at all — leaving an uploaded script running as
# the server, able to read /proc/1/environ and with it every credential the
# process was started with.
#
# The boundary is a container now: the sandbox role runs as its own uid, with
# none of the API server's environment, no CA material and no database. Nothing
# in it is worth reading, so there is nothing to drop away from.
#
# Give that role a *dedicated* uid rather than 65534/nobody — RLIMIT_NPROC is
# counted per uid across the whole host, so sharing the node's catch-all account
# reintroduces the EAGAIN-on-execve failure `_preexec` documents. 10001 below is
# the uid the compose file and the deployment manifests use.
#
# app/sandbox/server.py refuses to start as root, so a manifest that forgets the
# uid fails loudly instead of quietly serving without a boundary.
ARG SANDBOX_UID=10001
RUN useradd --system --no-create-home --shell /usr/sbin/nologin \
            --uid "${SANDBOX_UID}" sandbox

# Where the sandbox role puts its socket and each run's staging directory. Created
# here, and owned by the sandbox uid, so that a fresh named volume mounted over
# either one inherits that ownership — which is what keeps compose working
# without a separate chown step. In Kubernetes the pod's `fsGroup` does the same
# job for the emptyDir.
RUN mkdir -p /run/sandbox /sandbox \
 && chown "${SANDBOX_UID}:${SANDBOX_UID}" /run/sandbox /sandbox \
 && chmod 0770 /run/sandbox \
 && chmod 0700 /sandbox

# Copy the requirements alone first so the dependency layer is cached across
# source-only edits.
COPY backend/requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

# The packages an *uploaded* script may import (pandas, tabulate), installed
# beside the server's rather than into it.
#
# --target, not --system, and that is the whole point: the two dependency trees
# are resolved separately, so adding pandas for user scripts can never move a
# version the API itself depends on, and a CVE in pandas does not sit in the
# server process's import path. app/sandbox/supervisor.py puts this directory
# on the sandbox child's sys.path and nowhere else.
#
# The chmod is load-bearing, not tidiness: this layer is built as root while
# the sandbox role runs as an unprivileged uid, and `uv pip install --target`
# writes with the build's umask — a strict one produces a tree that uid cannot
# read, which is exactly the bug _stage() in supervisor.py exists to document.
# a+rX (capital X: directories and already-executable files only) leaves nothing
# writable.
COPY backend/requirements-scripts.txt ./
RUN uv pip install --target /opt/scriptlibs --no-cache -r requirements-scripts.txt \
 && chmod -R a+rX /opt/scriptlibs

COPY backend/ .
# The reference documentation, at the path `routers/docs.py` looks for it. Last,
# and in its own layer, so editing a document rebuilds one small layer rather
# than invalidating the source copy above it.
COPY docs/ ./docs/
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000
# The entrypoint applies migrations when RUN_MIGRATIONS=1 and then execs the
# command below; with the variable unset it is a no-op passthrough, so
# `docker compose run backend pytest` behaves exactly as it did.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
