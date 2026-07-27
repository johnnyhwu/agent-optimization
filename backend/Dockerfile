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

# Copy the requirements alone first so the dependency layer is cached across
# source-only edits.
COPY requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
