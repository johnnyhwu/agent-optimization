#!/bin/sh
# Applies schema migrations before handing over to the real command.
#
# Gated on RUN_MIGRATIONS, which only docker-compose.prod.yml sets, for a
# specific reason: `make test` runs
#
#     docker compose run --rm --no-deps backend pytest -q
#
# and `--no-deps` means Postgres is not up. An unconditional `alembic upgrade`
# here would make the test command fail before pytest ever started.
#
# Running migrations in the entrypoint is safe because exactly one backend
# container is ever started (see the single-worker note in
# docker-compose.prod.yml). Scaling that out means moving this to a one-shot job
# first, or two booting containers will race each other through Alembic.
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "==> alembic upgrade head"
    alembic upgrade head
fi

exec "$@"
