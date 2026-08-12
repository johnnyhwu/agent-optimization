# Backend container: FastAPI + Alembic, deps installed with uv (not pip).
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

# The user an uploaded eval-set script is executed as (app/services/script_runner.py).
#
# A separate uid is not cosmetic here. The server runs as root, and RLIMIT_NPROC —
# the fork-bomb defence — is simply not enforced against a process holding
# CAP_SYS_RESOURCE, so a child left running as root would walk through it. The
# same uid split is what makes /proc/1/environ, the source tree under /app and any
# mounted CA material unreadable to the script.
#
# No shell, no home directory, no group memberships: it exists to be dropped to
# and to own nothing.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin scriptrunner

# Copy the requirements alone first so the dependency layer is cached across
# source-only edits.
COPY requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

# The packages an *uploaded* script may import (pandas, tabulate), installed
# beside the server's rather than into it.
#
# --target, not --system, and that is the whole point: the two dependency trees
# are resolved separately, so adding pandas for user scripts can never move a
# version the API itself depends on, and a CVE in pandas does not sit in the
# server process's import path. app/services/script_runner.py puts this directory
# on the sandbox child's sys.path and nowhere else.
#
# The chmod is load-bearing, not tidiness: the child is dropped to the
# `scriptrunner` uid, and `uv pip install --target` writes with this build's
# umask — a strict one produces a tree the runner uid cannot read, which is
# exactly the bug _stage() in script_runner.py exists to document. a+rX (capital
# X: directories and already-executable files only) leaves nothing writable.
COPY requirements-scripts.txt ./
RUN uv pip install --target /opt/scriptlibs --no-cache -r requirements-scripts.txt \
 && chmod -R a+rX /opt/scriptlibs

COPY . .
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000
# The entrypoint applies migrations when RUN_MIGRATIONS=1 and then execs the
# command below; with the variable unset it is a no-op passthrough, so
# `docker compose run backend pytest` behaves exactly as it did.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
