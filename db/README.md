# The database image

`Dockerfile` is a thin layer over `postgres:16` whose only addition is
`initdb/`, and its own comments say why.

## `initdb/`

The official Postgres entrypoint runs everything in that directory exactly once:
on the first start against an **empty** data directory. It is not a migration
system, and there is no second chance.

Two consequences worth knowing before adding a file here:

- **An existing volume never sees a new script.** A developer who has been
  running the stack for a while, or a deployed cluster with a PVC, already has a
  populated `PGDATA` — adding a script here does nothing for them. Anything that
  must reach an existing database belongs in an Alembic migration instead.
- **These run as the bootstrap superuser.** That is the whole reason the
  directory exists rather than the work being done in a migration: it is where
  the things only a superuser may do can happen, once, before the application
  ever connects.

To pick these up on a machine that already has data, and only where throwing the
data away is fine:

```sh
docker compose down -v   # deletes the agentopt_pgdata volume
docker compose up
```
